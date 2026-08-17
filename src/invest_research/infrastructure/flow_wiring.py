"""P05-12A/B FLOW_MODE=fake/live 生产 Flow wiring（composition root）。

职责：
- ``build_flow_runner(settings)``：按 ``settings.flow_mode`` 返回 FlowRunner 端口实现；
  - ``fake``（默认）：``ResearchFlowRunner``（P03 纯 fake 00-07 全链，不联网），
    普通测试/CI 不产生任何模型费用；
  - ``live``：fail-fast 校验真实 LLM API Key（缺失/为空即抛可读错误），
    返回 ``LiveResearchFlowRunner``（真实 Crew + 质量门禁 + 受控反思）。
- ``LiveResearchFlowRunner``（P05-12B 完整实现 + P06-06B 进度标记）：
  - ``run(request)``：执行真实生产 Crew/Flow——
    1. 接收 ResearchRequest；
    2. 运行三 Agent sequential Crew（默认真实模型；可注入 fake crew 离线验证）；
    3. 解析三个 pack 写入 ResearchFlowState；
    4. 执行确定性质量门禁（run_quality_gate）；
    5. 保留受控反思：修订 ≤1 次、补充研究 ≤1 次（ReflectionController）；
    6. 生成 RunManifest（确定性）；
    7. 保存各步骤中间产物（全部 pack / draft / 质量报告 / manifest）；
    8. 失败抛 ``LiveFlowExecutionError``（不降级 fake）。

P06-06B 进度边界（live）：
- ``progress`` / ``job_id`` 由 Worker 在 run 前注入（可选）；
- 真实步骤边界：01_company_resolve（预取前/后）、02_research / 04_analysis / 05_writer
  用 CrewAI Task 的 TaskStartedEvent 与完成回调精确标记；
- 06_quality_gate / 07_manifest 在确定性门禁前后标记；
- 进度写入失败绝不中断任务（尽力而为，脱敏日志由调用方负责）。

授权边界：live 分支缺 key fail-fast；真实模型调用只发生在 Crew kickoff 时。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Callable, TypeVar

from crewai.crew import Crew
from pydantic import BaseModel

from invest_research.agents.crew_factory import build_live_research_crew, build_research_crew
from invest_research.agents.llm_factory import AnyLLM, LLMConfig
from invest_research.agents.pack_parsing import (
    PackBoundary,
)
from invest_research.application.progress import ProgressSink
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.domain.quality import QualityAction, QualityRecommendation
from invest_research.flows.manifest import build_run_manifest
from invest_research.flows.quality import run_quality_gate
from invest_research.flows.reflection import ReflectionController
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.live_resources import FlowModeError
from invest_research.infrastructure.performance import PerformanceRecorder, extract_token_usage
from invest_research.infrastructure.prefetch import PrefetchResult, prefetch_summary_text
from invest_research.infrastructure.queue.flow_adapter import ResearchFlowRunner
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache
from invest_research.settings import ResearchProfile, Settings

__all__ = [
    "FlowModeError",
    "LiveFlowExecutionError",
    "LiveResearchFlowRunner",
    "build_flow_runner",
]

_LOGGER = logging.getLogger(__name__)

# 三 Agent 的执行顺序（与 crew_factory._assemble_tasks 保持一致；任务角色 → 步骤名）
_AGENT_ROLE_ORDER = ("research", "analysis", "writer")
_AGENT_ROLE_TO_STEP = {
    "research": "02_research",
    "analysis": "04_analysis",
    "writer": "05_writer",
}

class LiveFlowExecutionError(RuntimeError):
    """live 模式执行失败（上游/质量门禁不可恢复）。

    语义：live 失败后不得降级为 fake——抛此异常向 Worker/调用方表示真实失败。
    P06-09：携带 ``error_code`` 与 ``failure_stage``，供 Worker 保存稳定错误分类。
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "INTERNAL_BUG",
        failure_stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_stage = failure_stage


_PackModel = TypeVar("_PackModel", bound=BaseModel)


def _to_packed(obj: Any, model: type[_PackModel]) -> _PackModel:
    """从 Crew 输出对象解析为对应 pack（P06-09B 统一 PackBoundary）。

    - 复用 ``pack_parsing.PackBoundary``（提取 → 来源分类 → 分层校验），
      **单一读取顺序**（pydantic → json_dict/exported → raw），与 ArtifactReader
      /dump_task_output 保持一致，禁止另起第二套读取路径；
    - Action Input / 工具调用参数 / 纯文本被 PackBoundary 拒绝（不是最终 Pack）；
    - 解析失败统一转 ``LiveFlowExecutionError``（error_code 取首个结构化错误码，
      failure_stage 供 Worker 保存失败阶段）；不注入 fixer（确定性 parse，
      提示词已约束 schema；有需要时调用方按需注入一次修复器）。
    """
    from invest_research.infrastructure.observability.tracing import span as _span

    boundary = PackBoundary(max_repairs=0)
    stage = _stage_for_model(model)
    with _span(f"pack.boundary.{model.__name__}", {"pack.stage": stage}):
        pack, errors = boundary.parse(obj, model, stage=stage)
    if pack is not None:
        # P06-09C：PackBoundary 校验成功（尽力而为）
        try:
            from invest_research.infrastructure.observability.metrics_events import (
                count_pack_validation,
            )

            count_pack_validation(_stage_for_model(model), model.__name__, "success", "NONE")
        except Exception:  # noqa: BLE001 - 指标写入尽力而为
            pass
        return pack
    first = errors[0] if errors else None
    # P06-09C：PackBoundary 校验失败（尽力而为）
    try:
        from invest_research.infrastructure.observability.metrics_events import (
            count_pack_validation,
        )

        count_pack_validation(
            _stage_for_model(model),
            model.__name__,
            "failed",
            first.error_code if first is not None else "SCHEMA_INVALID",
        )
    except Exception:  # noqa: BLE001 - 指标写入尽力而为
        pass
    raise LiveFlowExecutionError(
        (first.detail if first is not None else None) or "无法解析 pack",
        error_code=first.error_code if first is not None else "SCHEMA_INVALID",
        failure_stage=_stage_for_model(model),
    )


def _stage_for_model(model: type[_PackModel]) -> str:
    """按 pack 模型映射失败阶段（供 SCHEMA_INVALID 分类展示）。"""
    name = getattr(model, "__name__", "")
    if name == "ResearchPack":
        return "02_research"
    if name == "FinancialAnalysisPack":
        return "04_analysis"
    if name == "ReportDraft":
        return "05_writer"
    return "flow"


def _is_sec_source(src: Source) -> bool:
    """判断来源是否为 SEC 官方来源（URL 域名或 source_type）。"""
    url = src.canonical_url or ""
    return src.source_type in (SourceType.SEC_FILING, SourceType.SEC_XBRL) or "sec.gov" in url


def _normalize_research_sources(research_pack: ResearchPack) -> ResearchPack:
    """确定性来源规范化：SEC 来源缺 locator 时用表单类型补全（如 10-K）。

    P05-13 验收要求至少一个 SEC 来源带非空 locator；LLM 或收尾产物可能缺
    locator，这里做确定性补全（不伪造 URL/日期，locator 取表单类型）。
    """
    changed = False
    sources: list[Source] = []
    for src in research_pack.sources:
        if src.locator or not _is_sec_source(src):
            sources.append(src)
            continue
        form = (src.title or "").split(" filed ")[0].strip()
        sources.append(src.model_copy(update={"locator": form or "SEC filing"}))
        changed = True
    if not changed:
        return research_pack
    return research_pack.model_copy(update={"sources": sources})


class LiveResearchFlowRunner:
    """live 模式的 FlowRunner 契约实现（真实 Crew + 质量门禁 + 受控反思 + 进度）。

    构造时只保存配置与可注入工具（不发请求）；``run`` 按 FlowRunner 端口语义
    返回 None，执行结果写入 ``last_state`` / ``run_manifest``。

    离线验收：``run`` 接受 ``crew_factory`` 注入
    （测试传 fake crew 返回预置 pack），因此完整控制流可在不联网下验证。
    """

    def __init__(
        self,
        config: LLMConfig,
        research_tools: list[Any] | None = None,
        artifact_root: str = "artifacts",
        crew_factory: Callable[..., Crew] | None = None,
        stats: dict[str, int] | None = None,
        recorder: PerformanceRecorder | None = None,
        profile: ResearchProfile | None = None,
        cache: ToolCallCache | None = None,
        prefetch: Callable[[ResearchRequest], PrefetchResult | None] | None = None,
        budget: ToolBudget | None = None,
    ) -> None:
        self._config = config
        self._research_tools = research_tools
        self._artifact_root = Path(artifact_root)
        self.last_state: ResearchFlowState | None = None
        self.run_manifest: dict[str, object] = {}
        self._reflection = ReflectionController()
        # 研究档位（P05.5）：fast/deep 预算，默认 deep（向后兼容）
        self._profile = profile if profile is not None else ResearchProfile.for_mode("deep")
        # P06-06A：每次 run 按任务档位（request.research_profile）覆盖的当前档位。
        # 构造参数 profile 保留为默认回退（deep）；任务档位优先级更高（不改全局环境变量）。
        self._current_profile = self._profile
        # P06-06A：按任务档位解析后的有效 LLM 配置（fast 关闭思考模式）。
        # 单 worker 串行处理任务且每 job 一个 runner 实例，run() 内更新是安全的。
        self._effective_config = self._config
        # 可注入 crew_factory：测试传 fake crew（返回预置 pack），离线验证完整控制流；
        # 默认 None 时用真实 ``build_live_research_crew``（生产真实模型调用），
        # profile 动态读取当前任务档位。
        self._crew_factory: Callable[..., Crew] = (
            crew_factory
            if crew_factory is not None
            else lambda cfg, rt: build_live_research_crew(cfg, rt, profile=self._current_profile)
        )
        # 外部调用统计（P05-13）：真实工具经 build_research_tools 写入该 dict，
        # runner 在生成 manifest 时并入 evidence（不泄露任何密钥）。
        self._stats = stats if stats is not None else {}
        # 性能记录（P05.5）：工具耗时/Agent 耗时/token usage 汇总到 manifest.performance
        self._recorder = recorder if recorder is not None else PerformanceRecorder()
        # 工具缓存（P05.5：相同工具名+规范化参数单 Job 只执行一次；可选）
        self._cache = cache
        # 公司解析后并行预取（P05.5 best-effort；可选）
        self._prefetch = prefetch
        # 工具硬预算（P05.5-fix：每 Job 独立上限，prefetch 与 Agent 调用共用）
        self._budget = budget
        # 本次运行的预取结果（供任务注入与结构化收尾复用）
        self._prefetch_result: PrefetchResult | None = None
        # 结构化收尾只允许一次
        self._finalize_used = False
        # P06-06B：实时进度端口（Worker 在 run 前注入；不注入则静默）
        self.progress: ProgressSink | None = None
        self.job_id: uuid.UUID | None = None

    @property
    def config(self) -> LLMConfig:
        """暴露 LLMConfig 供审计/测试断言（api_key 为 SecretStr，不泄露明文）。"""
        return self._config

    def assemble_crew(self, fakes: dict[str, AnyLLM]) -> Crew:
        """用注入的 fake LLM 组装三 Agent 顺序 Crew（离线契约验证，不联网）。"""
        return build_research_crew(self._config, fakes)

    # ------------------------------------------------------------------
    # P06-06B：进度标记辅助（写入失败不中断任务）
    # ------------------------------------------------------------------

    def _mark(self, step_name: str, action: str) -> None:
        """标记步骤运行中/成功；进度未注入或写入失败均静默（尽力而为）。"""
        if self.progress is None or self.job_id is None:
            return
        try:
            if action == "running":
                self.progress.mark_step_running(self.job_id, step_name)
            elif action == "succeeded":
                self.progress.mark_step_succeeded(self.job_id, step_name)
        except Exception as exc:  # noqa: BLE001 - 进度尽力而为
            _LOGGER.warning(
                "进度标记失败 job=%s step=%s action=%s: %s",
                self.job_id,
                step_name,
                action,
                exc,
            )

    def run(self, request: ResearchRequest) -> ResearchFlowState:
        """FlowRunner 端口实现：完整执行真实生产 Crew/Flow（同步）。

        P06-07 前置修复：返回最终 ``ResearchFlowState``，供 Worker 在成功
        路径发布最终报告工件（fake/live 共用流程）。
        """
        # P06-06A：按任务档位选择当前预算（合法值由 domain.ResearchProfileMode 校验）。
        # 不在构造/全局环境做固定档位；任务不同、档位不同。
        self._current_profile = ResearchProfile.for_mode(
            "fast" if request.research_profile == "fast" else "deep"
        )
        # fast 任务显式关闭思考模式（Qwen3.5 等默认思考极慢）；deep 保留构造时配置。
        self._effective_config = self._config
        if self._current_profile.mode == "fast":
            self._effective_config = self._config.model_copy(update={"enable_thinking": False})
        state = self._run_live(request)
        self.last_state = state
        self.run_manifest = state.run_manifest
        return state

    def _run_live(self, request: ResearchRequest) -> ResearchFlowState:
        """P06-05：真实执行包在 ``flow.run`` OTel span 内（属性只含低基数字段）。"""
        from invest_research.infrastructure.observability.tracing import span

        with span(
            "flow.run",
            {
                "input_company": request.input_company,
                "as_of_date": request.as_of_date.isoformat(),
                "language": request.language,
            },
        ):
            return self._run_live_impl(request)

    def _run_live_impl(self, request: ResearchRequest) -> ResearchFlowState:
        """真实执行：Crew → 解析 → 质量门禁 → 受控反思 → manifest。

        严格保持顺序；任一不可恢复失败抛 ``LiveFlowExecutionError``
        （绝不偷偷调用 fake）。
        """
        started_at = time.time()

        # P06-06B：01_company_resolve 开始（确定性本地解析/预取边界）
        self._mark("01_company_resolve", "running")

        # 0. 公司身份确认后并行预取（best-effort，失败不影响 Agent 兜底）
        prefetch_result: PrefetchResult | None = None
        if self._prefetch is not None:
            try:
                prefetch_result = self._prefetch(request)
            except Exception:
                # 预取是纯优化：失败时 Research Agent 工具仍会自行拉取
                prefetch_result = None
        self._prefetch_result = prefetch_result
        self._mark("01_company_resolve", "succeeded")

        # 0.5 组装 Crew 输入：ResearchRequest + 预取结果显式注入（禁止 Agent 猜公司/日期）
        inputs = self._build_crew_inputs(request, prefetch_result)

        # 1. 运行三 Agent 顺序 Crew（默认真实模型；测试可注入 fake crew）。
        #    P06-06A：按任务档位解析后的有效配置（fast 已关闭思考模式）。
        crew = self._crew_factory(self._effective_config, self._research_tools)
        #    P06-06B：用 CrewAI TaskStartedEvent 标记 Task 开始（task.py:521 可靠 emit）。
        #    scope 在 kickoff 期间保持活跃，结束后显式退出清除本轮 handler。
        scope = self._subscribe_task_progress(crew)
        try:
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:  # noqa: BLE001 - 应用边界：记录并转 fail-fast
            if scope is not None:
                scope.__exit__(None, None, None)
            raise LiveFlowExecutionError(
                f"真实 Crew 执行失败: {type(exc).__name__}: {exc}"
            ) from exc
        if scope is not None:
            scope.__exit__(None, None, None)

        # P06-09C：Agent 耗时 + LLM token usage 指标（尽力而为，不改变业务结果）
        self._record_agent_metrics(crew, result)

        # P06-06B：03_documents 在 Research Task 完成后由本 runner 标记
        # （Research 行为内含文档处理，Crew 内无独立 documents Task）。
        self._mark("03_documents", "succeeded")

        # 2. 解析三个 pack 写入 state
        state = self._extract_packs(result, request)

        # 3. 确定性质量门禁（P06-06B：06_quality_gate 边界）
        self._mark("06_quality_gate", "running")
        state.quality_report = run_quality_gate(state)
        self._mark("06_quality_gate", "succeeded")

        # 4. 受控反思（有界：revision ≤1、supplement ≤1），由 ReflectionController 路由
        reflection = self._run_reflection(state)

        # 5. 采集性能并生成 RunManifest（质量门禁通过才 published）
        self._record_performance(crew, result)
        performance = self._recorder.snapshot()
        # P06-06B：07_manifest 边界
        self._mark("07_manifest", "running")
        state.run_manifest = build_run_manifest(
            state, self._config, started_at=started_at, performance=performance
        )
        # 合并反思审计记录（不丢失受控反思决策）
        state.run_manifest["reflection"] = reflection
        # 合并外部调用统计证据（P05-13 验收：SEC/Serper/LLM 等调用证据可见）。
        # stats 为空时（Agent 直接用预取结果、未调工具）从性能记录器补全真实调用。
        invocation: dict[str, int] = dict(self._stats)
        if not invocation:
            for tool_name, metrics in self._recorder.snapshot()["tools"].items():
                invocation[f"{tool_name}_calls"] = int(metrics["calls"])
        if invocation:
            state.run_manifest["evidence"] = {"invocation_summary": invocation}
        self._mark("07_manifest", "succeeded")

        # 6. 保存中间产物（确定性落盘）
        self._persist_intermediates(request, state)

        return state

    def _subscribe_task_progress(
        self, crew: Crew
    ) -> Any | None:
        """用 CrewAI 事件总线标记 Task 开始/完成边界（P06-06B）。

        - TaskStartedEvent（task.py:521）在 Task 真正开始时 emit；
        - Task.callback（task.py:567-568）在 Task 完成时同步调用。
        返回值是 ``scoped_handlers()`` 的 context manager；调用方必须在 kickoff
        完成后调用 ``scope.__exit__(...)`` 清除本轮 handler（防止泄漏到下一个 Job）。

        无事件总线（旧版 CrewAI）时退化为仅按顺序推断完成边界（不做提前 running），
        返回 None。
        """
        if self.progress is None or self.job_id is None:
            return None
        try:
            from crewai.events.event_bus import crewai_event_bus
            from crewai.events.types.task_events import TaskCompletedEvent, TaskStartedEvent
        except ImportError:
            # 旧版 CrewAI 无事件总线：退化为仅按顺序推断完成边界（不做提前 running）
            self._attach_task_callbacks(crew)
            return None

        def _on_start(source: Any, event: TaskStartedEvent) -> None:
            task = event.task
            role = getattr(task, "role", None)
            agent = getattr(task, "agent", None)
            role_name = (
                role
                if isinstance(role, str) and role
                else (getattr(agent, "role", None) if agent is not None else None)
            )
            step = self._role_to_step(role_name)
            if step:
                self._mark(step, "running")

        def _on_completed(source: Any, event: TaskCompletedEvent) -> None:
            task = event.task
            role_name = None
            agent = getattr(task, "agent", None)
            if agent is not None:
                role_name = getattr(agent, "role", None)
            step = self._role_to_step(role_name)
            if step:
                self._mark(step, "succeeded")

        scope = crewai_event_bus.scoped_handlers()
        scope.__enter__()
        crewai_event_bus.on(TaskStartedEvent)(_on_start)
        crewai_event_bus.on(TaskCompletedEvent)(_on_completed)
        return scope

    def _attach_task_callbacks(self, crew: Crew) -> None:
        """备用：无事件总线时给每个 Task 设置完成回调（只标记完成边界，不提前 running）。"""
        for task in getattr(crew, "tasks", []):
            role = getattr(task, "role", None)
            agent = getattr(task, "agent", None)
            role_name = (
                role
                if isinstance(role, str) and role
                else (getattr(agent, "role", None) if agent is not None else None)
            )
            step = self._role_to_step(role_name)
            if step is None:
                continue
            original = getattr(task, "callback", None)

            def _cb(output: Any, _step: str = step) -> None:
                self._mark(_step, "succeeded")
                if original is not None:
                    original(output)

            task.callback = _cb

    @staticmethod
    def _role_to_step(role_name: str | None) -> str | None:
        """把 Agent role 名映射到步骤名（未知 role 返回 None 不标记）。"""
        if not role_name:
            return None
        lowered = str(role_name).lower()
        for role, step in _AGENT_ROLE_TO_STEP.items():
            if role in lowered or lowered in role:
                return step
        return None

    def _build_crew_inputs(
        self, request: ResearchRequest, prefetch_result: PrefetchResult | None
    ) -> dict[str, str]:
        """把 ResearchRequest 与预取结果组装为 Crew 输入（替换 Task 描述占位符）。

        所有值必须是 str/int/float/bool（CrewAI interpolate_only 的限制）：
        as_of_date 用 ISO 字符串、company_identity 用格式化文本。
        """
        forms = ",".join(request.requested_forms) if request.requested_forms else "10-K,10-Q"
        inputs: dict[str, str] = {
            "input_company": request.input_company,
            "as_of_date": request.as_of_date.isoformat(),
            "requested_forms": forms,
            "language": request.language,
            "company_identity": "未预解析（需先用 CompanyResolver 解析）",
            "prefetch_summary": prefetch_summary_text(prefetch_result),
            "financial_facts": (
                prefetch_result.financial_facts_summary
                if prefetch_result is not None
                and prefetch_result.financial_facts_summary is not None
                else "[]"
            ),
        }
        if prefetch_result is not None and prefetch_result.company_identity is not None:
            identity = prefetch_result.company_identity
            inputs["company_identity"] = (
                f"ticker={identity.ticker}, CIK={identity.cik}, "
                f"legal_name={identity.legal_name}, exchange={identity.exchange}"
            )
        return inputs

    def _record_performance(self, crew: Any, result: Any) -> None:
        """采集三 Agent 耗时与 LLM token usage 到 recorder。

        - Agent 耗时读 CrewAI Task.start_time/end_time（按 research/analysis/writer 顺序）；
        - token usage 读 CrewOutput.token_usage（fake/不可得时为 None）；
        - 工具耗时/次数由 real_tools 的 recorder 包装层直接累计。
        """
        tasks = getattr(crew, "tasks", None)
        if tasks:
            for role, task in zip(_AGENT_ROLE_ORDER, tasks):
                start = getattr(task, "start_time", None)
                end = getattr(task, "end_time", None)
                if start is not None and end is not None:
                    self._recorder.record_agent(role, int((end - start).total_seconds() * 1000))
        usage = getattr(result, "token_usage", None)
        self._recorder.set_token_usage(extract_token_usage(usage))

    def _record_agent_metrics(self, crew: Any, result: Any) -> None:
        """Agent 耗时 + LLM token usage Prometheus 指标（P06-09C，尽力而为）。

        - role 白名单（research/analysis/writer）由 metrics_events 过滤；
        - provider/model 用脱敏配置名（label_provider_model），绝不暴露 base_url；
        - token 只来自真实模型响应 usage；缺失时记录 usage_missing，不伪造 0。
        """
        try:
            from invest_research.agents.llm_factory import LLMRole
            from invest_research.infrastructure.observability.metrics_events import (
                count_agent_run,
                count_llm_request,
                count_llm_tokens,
                count_llm_usage_missing,
                label_provider_model,
                observe_agent_duration,
            )
            from invest_research.infrastructure.observability.tracing import get_tracer

            profile = getattr(self._current_profile, "mode", "deep")
            tasks = getattr(crew, "tasks", None) or []
            agent_tracer = get_tracer("agent")
            for role, task in zip(_AGENT_ROLE_ORDER, tasks):
                try:
                    role_enum = LLMRole(role)
                except ValueError:
                    role_enum = None
                model = (
                    self._config.model_for(role_enum) if role_enum is not None else str(role)
                )
                provider, model_lbl = label_provider_model(self._config.base_url, model)
                start = getattr(task, "start_time", None)
                end = getattr(task, "end_time", None)
                if start is not None and end is not None:
                    duration_s = max((end - start).total_seconds(), 0.0)
                    # P06-09C：agent span 作为 flow.run 的子 span（start_as_current_span），
                    # 属性只放低基数/非敏感字段（role/profile/provider/model）。
                    with agent_tracer.start_as_current_span(
                        f"agent.{role}",
                        attributes={
                            "agent.role": role,
                            "agent.profile": profile,
                            "llm.provider": provider,
                            "llm.model": model_lbl,
                            "agent.duration_s": duration_s,
                        },
                    ):
                        count_agent_run(role, profile, provider, model_lbl, "success")
                        observe_agent_duration(
                            role, profile, provider, model_lbl, "success", duration_s
                        )

            # LLM token usage：只从真实模型响应 usage 提取（与 _record_performance 一致）
            usage = extract_token_usage(getattr(result, "token_usage", None))
            for role in _AGENT_ROLE_ORDER:
                try:
                    role_enum = LLMRole(role)
                except ValueError:
                    role_enum = None
                model = (
                    self._config.model_for(role_enum) if role_enum is not None else str(role)
                )
                provider, model_lbl = label_provider_model(self._config.base_url, model)
                if usage:
                    count_llm_request(provider, model_lbl, role, "success")
                    count_llm_tokens(
                        provider, model_lbl, role, "input",
                        int(usage.get("prompt_tokens", 0) or 0),
                    )
                    count_llm_tokens(
                        provider, model_lbl, role, "output",
                        int(usage.get("completion_tokens", 0) or 0),
                    )
                    count_llm_tokens(
                        provider, model_lbl, role, "cached_input",
                        int(usage.get("cached_prompt_tokens", 0) or 0),
                    )
                else:
                    count_llm_usage_missing(provider, model_lbl, role)
        except Exception:  # noqa: BLE001 - 指标写入尽力而为
            _LOGGER.warning("agent/llm metrics recording skipped")

    def _extract_packs(self, result: Any, request: ResearchRequest) -> ResearchFlowState:
        """从 Crew 结果解析三个 pack 到 state。

        - CrewAI 1.6.1 的 ``CrewOutput`` 通过 ``tasks_output`` 按顺序暴露各 Task 输出；
        - 兼容 ``result.output``/``result.json_dict`` 兜底解析；
        - 任一 pack 缺失视为不可恢复失败（不降级）。
        """
        state = ResearchFlowState(request=request)

        outputs: list[Any] = []
        tasks_output = getattr(result, "tasks_output", None)
        if tasks_output:
            outputs = list(tasks_output)

        # 按顺序：research, analysis, writer
        if len(outputs) >= 1:
            state.research_pack = self._extract_research_pack(outputs[0], request)
        if len(outputs) >= 2:
            state.analysis_pack = _to_packed(outputs[1], FinancialAnalysisPack)
            # P06-09C：Analysis pack 完整性分布（尽力而为）
            try:
                from invest_research.infrastructure.observability.metrics_events import (
                    count_analysis_completeness,
                )

                completeness = getattr(state.analysis_pack, "completeness", None)
                if completeness is not None:
                    count_analysis_completeness(str(completeness))
            except Exception:  # noqa: BLE001 - 指标写入尽力而为
                pass
        if len(outputs) >= 3:
            state.report_draft = _to_packed(outputs[2], ReportDraft)

        if state.research_pack is None or state.analysis_pack is None or state.report_draft is None:
            raise LiveFlowExecutionError("Crew 输出不完整：需要 research/analysis/writer 三个 pack")
        return state

    def _extract_research_pack(self, obj: Any, request: ResearchRequest) -> ResearchPack:
        """解析 Research 输出；失败时尝试一次有界结构化收尾（不伪造来源）。"""
        try:
            pack = _to_packed(obj, ResearchPack)
        except LiveFlowExecutionError as exc:
            pack = self._finalize_research_pack(request, exc)
        return _normalize_research_sources(pack)

    def _finalize_research_pack(self, request: ResearchRequest, cause: Exception) -> ResearchPack:
        """有界结构化收尾：从缓存中的 SEC 申报结果构建 ResearchPack（只允许一次）。

        - 只允许一次；使用 Agent 已经取得的工具结果（缓存），不重新执行整套 Research；
        - 不伪造来源：无有效 SEC 来源时明确抛 LiveFlowExecutionError。
        """
        if self._finalize_used:
            raise LiveFlowExecutionError("结构化收尾已使用过一次，禁止重复收尾") from cause
        self._finalize_used = True

        identity = self._resolved_identity(request)
        if identity is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无法确定公司身份，无法结构化收尾；禁止生成伪造 ResearchPack"
            ) from cause
        if self._cache is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无工具缓存，无法结构化收尾；禁止生成伪造 ResearchPack"
            ) from cause

        forms = ",".join(request.requested_forms) if request.requested_forms else "10-K,10-Q"
        key = self._cache.key(
            "sec_submissions",
            {
                "cik": identity.cik,
                "as_of_date": request.as_of_date.isoformat(),
                "requested_forms": forms,
            },
        )
        cached = self._cache.get(key)
        if cached is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且缓存无 SEC 申报结果，无法结构化收尾；"
                "禁止生成伪造 ResearchPack"
            ) from cause
        try:
            payload = json.loads(cached)
        except (TypeError, ValueError) as parse_exc:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且缓存 SEC 结果损坏，无法结构化收尾；"
                "禁止生成伪造 ResearchPack"
            ) from parse_exc
        raw_filings = payload.get("filings", []) if payload.get("ok") else []
        sources: list[Source] = []
        for f in raw_filings:
            url = (f or {}).get("primary_document_url")
            if not url:
                continue
            form_type = f.get("form_type") or "SEC"
            filing_date = f.get("filing_date")
            sources.append(
                Source(
                    source_type=SourceType.SEC_FILING,
                    canonical_url=url,
                    title=f"{form_type} filed {filing_date}",
                    published_at=(
                        date.fromisoformat(filing_date) if filing_date else request.as_of_date
                    ),
                    accessed_at=request.as_of_date,
                    # P05.5-fix：表单类型作确定性 locator（P05-13 验收要求 SEC 来源带 locator）
                    locator=form_type,
                )
            )
        if not sources:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无有效 SEC 来源，无法结构化收尾；禁止生成伪造 ResearchPack"
            ) from cause
        return ResearchPack(
            version="research_pack_v1",
            company_identity=identity,
            as_of_date=request.as_of_date,
            sources=sources,
            coverage_notes=(
                "Research Agent 未产出合法 ResearchPack，"
                "由有界结构化收尾基于已取得的 SEC 申报结果构建"
            ),
        )

    def _resolved_identity(self, request: ResearchRequest) -> CompanyIdentity | None:
        """优先取预取结果中的公司身份；否则确定性本地解析（不联网）。"""
        if self._prefetch_result is not None and self._prefetch_result.company_identity is not None:
            return self._prefetch_result.company_identity
        from invest_research.tools.company_resolver import (
            CompanyResolverTool,
            ResolveCompanyRequest,
        )

        result = CompanyResolverTool().execute(
            ResolveCompanyRequest(input_company=request.input_company)
        )
        if result.kind == "success" and result.value.resolved:
            return result.value.candidates[0]
        return None

    def _run_reflection(self, state: ResearchFlowState) -> dict[str, object]:
        """受控反思：按质量建议路由，修订/补证各有 ≤1 次上限。

        返回反思审计记录（并入 manifest）。有界的修订/补证重跑属于 P05-13 live smoke。
        """
        report = state.quality_report
        if report is None or report.all_passed:
            return {"outcome": "publish", "revision_used": 0, "supplement_used": 0}

        action = self._recommendation_to_action(report.recommendation)
        attempt = self._reflection.step(
            action=action,
            revision_used=0,
            supplement_used=0,
            has_warnings=bool(report.warnings),
        )
        return {
            "outcome": attempt.outcome,
            "revision_used": attempt.revision_used,
            "supplement_used": attempt.supplement_used,
            "action": action.value,
        }

    @staticmethod
    def _recommendation_to_action(rec: QualityRecommendation) -> QualityAction:
        if rec == QualityRecommendation.REVISE:
            return QualityAction.REVISE_REPORT
        if rec == QualityRecommendation.PUBLISH_PARTIAL:
            return QualityAction.NONE
        if rec == QualityRecommendation.REJECT:
            return QualityAction.REJECT
        return QualityAction.NONE

    def _persist_intermediates(self, request: ResearchRequest, state: ResearchFlowState) -> None:
        """把中间产物写入 ``artifacts/<job_id>/``（原子写，overwrite）。

        P06-07 前置修复：
        - 目录统一为 ``<artifact_root>/<job_id>/``（不再用 company_as_of，避免
          并发/重复任务互相覆盖）；
        - 08_report.md / 09_report.pdf 不再在这里生成——最终报告由
          ``reporting.artifact_publisher.ReportArtifactPublisher`` 统一发布
          （fake/live 共用，且发布失败走 Worker failed 语义）。
        """
        from invest_research.tools.artifact_store import ArtifactStore

        job_root = self._artifact_root / str(self.job_id)
        store = ArtifactStore(job_root)

        payloads: dict[str, str | bytes] = {
            "00_request.json": request.model_dump_json(),
            "02_research_pack.json": (
                state.research_pack.model_dump_json() if state.research_pack else "null"
            ),
            "04_financial_analysis_pack.json": (
                state.analysis_pack.model_dump_json() if state.analysis_pack else "null"
            ),
            "05_report_draft.json": (
                state.report_draft.model_dump_json() if state.report_draft else "null"
            ),
            "06_quality_report.json": (
                state.quality_report.model_dump_json() if state.quality_report else "null"
            ),
            "07_manifest.json": json.dumps(state.run_manifest, ensure_ascii=False, default=str),
        }
        for key, content in payloads.items():
            # P05.5-fix：live 单次运行覆盖旧工件，保证工件反映本次运行（诊断不误导）
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            store.write(key, data, overwrite=True)


def _ensure_live_api_key(settings: Settings) -> str:
    """live 模式 fail-fast：API Key 缺失或为空时抛出可读错误。"""
    key = settings.llm_api_key.get_secret_value()
    if not key or not key.strip():
        raise FlowModeError(
            "FLOW_MODE=live 需要配置 LLM_API_KEY（仅从环境变量/.env 读取，"
            "缺失或为空时禁止启动真实模型运行）。"
        )
    return key


def build_flow_runner(
    settings: Settings,
    research_tools: list[Any] | None = None,
    stats: dict[str, int] | None = None,
    recorder: PerformanceRecorder | None = None,
    profile: ResearchProfile | None = None,
    cache: ToolCallCache | None = None,
    prefetch: Callable[[ResearchRequest], PrefetchResult | None] | None = None,
    budget: ToolBudget | None = None,
) -> ResearchFlowRunner | LiveResearchFlowRunner:
    """按 settings.flow_mode 返回 FlowRunner 端口实现（P05-12A 入口）。

    - fake：返回 ``ResearchFlowRunner``（默认，离线确定性，普通测试/CI 用）；
    - live：校验 API Key 后返回 ``LiveResearchFlowRunner``（真实 Crew + 门禁 + 反思，
      可注入 ``research_tools`` 生产工具白名单与 ``stats`` 调用统计）。
    """
    if settings.flow_mode == "fake":
        return ResearchFlowRunner()

    _ensure_live_api_key(settings)
    config = LLMConfig.from_settings(settings)
    resolved_profile = profile if profile is not None else settings.build_research_profile()
    if resolved_profile.mode == "fast":
        # fast 模式明确使用非思考模式：Qwen3.5 等默认思考模式（reasoning）响应极慢，
        # 显式关闭后显著提速；deep 模式是否开启由 LLM_ENABLE_THINKING 环境变量决定。
        config = config.model_copy(update={"enable_thinking": False})
    return LiveResearchFlowRunner(
        config=config,
        research_tools=research_tools,
        artifact_root=settings.artifact_root,
        stats=stats,
        recorder=recorder,
        profile=resolved_profile,
        cache=cache,
        prefetch=prefetch,
        budget=budget,
    )
