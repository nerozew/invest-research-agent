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
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, TypeVar

from crewai.crew import Crew
from pydantic import BaseModel

from invest_research.agents.analysis_task import build_analysis_task
from invest_research.agents.crew_factory import build_live_research_crew, build_research_crew
from invest_research.agents.llm_factory import (
    AnyLLM,
    LLMConfig,
    StructuredOutputMode,
    structured_output_mode,
)
from invest_research.agents.pack_parsing import (
    PackBoundary,
    extract_candidate,
)
from invest_research.agents.research_task import build_research_task
from invest_research.agents.writer_task import ArtifactLoader, build_writer_task
from invest_research.application.analysis_assembler import (
    AnalysisAssemblerError,
    AnalysisPackAssembler,
    parse_fact_records,
)
from invest_research.application.progress import ProgressSink
from invest_research.application.report_draft_assembler import (
    ReportAssemblerError,
    ReportDraftAssembler,
)
from invest_research.application.research_assembler import (
    ResearchAssemblerError,
    ResearchPackAssembler,
)
from invest_research.application.structured_finalizer import FinalizerError
from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import (
    AnalysisSelectionDraft,
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    ResearchSelectionDraft,
    Source,
    SourceType,
)
from invest_research.domain.quality import QualityAction, QualityRecommendation
from invest_research.flows.manifest import build_run_manifest
from invest_research.flows.quality import run_quality_gate
from invest_research.flows.quality_classifier import classify_state
from invest_research.flows.reflection import ReflectionController
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.finalizers.deepseek_json_object_finalizer import (
    DeepSeekJsonObjectFinalizer,
)
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

_AGENT_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "research": ("research", "信息搜集"),
    "analysis": ("analysis", "financial analyst", "财报分析"),
    "writer": ("writer", "report writer", "报告撰写"),
}

_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "cached_prompt_tokens")


def _stable_agent_role(role_name: str | None) -> str | None:
    """把 CrewAI 可读角色名映射为稳定 research/analysis/writer。"""
    if not role_name:
        return None
    lowered = str(role_name).strip().lower()
    for role, aliases in _AGENT_ROLE_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return role
    return None


def _message_role(msg: Any) -> str | None:
    """兼容 dict/TypedDict 与普通对象读取 LLM 消息的 ``role``。

    P06-11G：CrewAI 1.6.1 的 ``agent.last_messages`` 是 ``list[LLMMessage]``，
    而 ``LLMMessage`` 是 ``typing.TypedDict``（运行时为普通 dict）。此前用
    ``getattr(msg, "role")`` 访问字典键会静默拿到 None，导致所有消息被跳过、
    工具循环中的长正文无法回收。这里统一读取方式（dict 用 ``.get``，对象用
    ``getattr``），供 ``_longest_writer_history`` / ``_dump_writer_tool_history``
    复用，禁止两套解析逻辑。
    """
    if isinstance(msg, dict):
        role = msg.get("role")
        return role if isinstance(role, str) else None
    role = getattr(msg, "role", None)
    return role if isinstance(role, str) else None


def _message_text(msg: Any) -> str:
    """提取 LLM 消息的纯文本内容（支持 str 与 OpenAI-style 分段 list）。

    - ``content`` 为 ``str``：直接返回；
    - ``content`` 为 ``list``：只提取 ``{"text": ...}`` 文本块或纯字符串块，
      按顺序拼接；忽略 ``tool_call`` / ``function_call`` / ``image`` 等非文本块；
    - 其它类型（数字/bool/None）：返回空字符串；
    - 禁止 ``str(dict)``——避免把工具参数/结构化对象误当成报告正文。

    P06-11G：与 ``_message_role`` 配套，统一 dict 与对象的读取方式。
    """
    if isinstance(msg, dict):
        content = msg.get("content")
    else:
        content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _validation_role(exc: Exception) -> str | None:
    """从 Pydantic ValidationError 的模型标题推导当前 Pack 阶段。"""
    title = str(getattr(exc, "title", "") or "").strip()
    return {
        "ResearchPack": "research",
        "FinancialAnalysisPack": "analysis",
        "ReportDraft": "writer",
    }.get(title)


def _iteration_limit_details(
    crew: Any,
    *,
    preferred_role: str | None = None,
) -> tuple[str, str] | None:
    """从 Crew 执行器真实计数判断哪个 Agent 已耗尽 max_iter。

    CrewAI 1.6.1 在耗尽时会额外请求一次 final answer，而不会抛专用异常；
    因此应用边界必须读取 executor.iterations/max_iter 保存稳定错误分类。
    """
    tasks = list(getattr(crew, "tasks", None) or [])
    for task in reversed(tasks):
        agent = getattr(task, "agent", None)
        if agent is None:
            continue
        executor = getattr(agent, "agent_executor", None)
        iterations = getattr(executor, "iterations", None)
        max_iter = getattr(agent, "max_iter", None)
        if not isinstance(iterations, int) or not isinstance(max_iter, int):
            continue
        if iterations < max_iter:
            continue
        role = _stable_agent_role(getattr(agent, "role", None))
        if role is not None and (preferred_role is None or role == preferred_role):
            return role, _AGENT_ROLE_TO_STEP[role]
    return None


def _looks_like_tool_input(exc: Exception) -> bool:
    """识别 Pydantic 错误中的已知工具参数，避免把它归为 INTERNAL_BUG。"""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return False
    try:
        entries = errors()
    except Exception:  # noqa: BLE001 - 第三方异常对象不可信
        return False
    tool_keys = {"artifact_key", "section_name", "action", "action_input"}
    return any(
        isinstance(entry, dict)
        and isinstance(entry.get("input"), dict)
        and bool(tool_keys.intersection(entry["input"]))
        for entry in entries
    )


def _is_structured_output_unsupported(exc_text: str) -> bool:
    """判断异常文本是否表示供应商拒绝远程结构化输出（P06-11B）。

    匹配 DeepSeek 实测错误 "HTTP 400: This response_format type is
    unavailable now"（普通 Chat Completion 不支持 OpenAI json_schema
    response_format）。输入为已脱敏文本，仅用于分类，不进入用户可见消息。
    """
    return (
        "response_format" in exc_text
        or "response format" in exc_text
        or "json_schema" in exc_text
        or "this response_format type is unavailable" in exc_text
        or ("structured output" in exc_text and "not support" in exc_text)
    )


def _structured_output_stage(exc: Exception) -> str | None:
    """从异常确定结构化输出失败阶段（P06-11B）。

    优先复用 Pydantic ValidationError 标题推导；否则回退到最后执行的 Agent
    （转换阶段通常在最后一个 Task 输出时触发，默认 writer）。
    """
    role = _validation_role(exc)
    if role is not None:
        return _AGENT_ROLE_TO_STEP[role]
    return "05_writer"


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


@dataclass
class JobResearchComponents:
    """P06-11G：单个 Job 专属的可变研究组件（禁止跨 Job 复用）。

    - ``research_tools``：闭包捕获本 Job 的 cache/budget/recorder/stats；
    - ``cache`` / ``budget`` / ``recorder``：本 Job 独立实例；
    - ``prefetch``：捕获本 Job 组件的预取 callable；
    - ``stats``：本 Job 调用计数（写入当前 Job manifest 的证据）。
    """

    research_tools: list[Any]
    cache: ToolCallCache
    recorder: PerformanceRecorder
    budget: ToolBudget
    prefetch: Callable[[ResearchRequest], PrefetchResult | None] | None
    stats: dict[str, int]


@dataclass
class _RunContext:
    """P06-11E：每次 run(request) 独立的工作上下文（跨 Job 状态隔离）。

    - ``finalization_count``：结构化收尾次数（每 Job 从 0 开始，禁止跨 Job 泄漏）；
    - ``prefetch_result`` / ``analysis_facts``：本 Job 的预取结果与还原原始事实；
    - ``profile`` / ``effective_config``：本 Job 档位与有效 LLM 配置
      （fast 关闭思考模式等 Job 级覆盖不写回 runner 实例）；
    - ``components``（P06-11G）：本 Job 专属的工具/cache/budget/recorder/stats；
      构造注入的共享引用只在未提供 component_factory 时作为回退。
    """

    request: ResearchRequest
    profile: ResearchProfile
    effective_config: LLMConfig
    components: JobResearchComponents
    stats: dict[str, int]
    recorder: PerformanceRecorder
    budget: ToolBudget | None
    cache: ToolCallCache | None
    prefetch: Callable[[ResearchRequest], PrefetchResult | None] | None
    research_tools: list[Any] | None
    prefetch_result: PrefetchResult | None = None
    analysis_facts: list[FinancialFact] = field(default_factory=list)
    finalization_count: int = 0


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


def _staged_artifact_loader(state: ResearchFlowState) -> ArtifactLoader:
    """P06-11E：分阶段 Writer 的 ArtifactReader loader（现场读取已组装 pack）。

    - ``research_pack`` / ``analysis_pack`` 从当前 state 读取（分阶段路径先执行
      Research/Analysis，Writer 阶段运行时上游 pack 已就绪）；
    - 保证 Writer 只读到最终组装后的 pack，绝不读到未经组装的草稿。
    """

    def loader(artifact_key: str) -> dict[str, Any] | None:
        if artifact_key == "research_pack":
            return (
                state.research_pack.model_dump(mode="json")
                if state.research_pack is not None
                else None
            )
        if artifact_key == "analysis_pack":
            return (
                state.analysis_pack.model_dump(mode="json")
                if state.analysis_pack is not None
                else None
            )
        return None

    return loader


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
        component_factory: Callable[[], JobResearchComponents] | None = None,
    ) -> None:
        self._config = config
        self._research_tools = research_tools
        self._artifact_root = Path(artifact_root)
        # P06-11G：每 Job 组件工厂。提供时每次 run() 都新建一套
        # job-local 组件（budget/cache/recorder/stats/research_tools/prefetch），
        # 禁止跨 Job 复用捕获旧预算/旧缓存的工具闭包；未提供时回退构造注入
        # 的共享引用（兼容既有测试与旧 wiring）。
        self._component_factory = component_factory
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
            else lambda cfg, rt: build_live_research_crew(
                cfg, rt, profile=self._current_profile, analysis_facts=self._analysis_facts
            )
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
        # P06-11C：本次预取的原始可信 FinancialFact 集合（供选择草稿现场组装）。
        self._analysis_facts: list[FinancialFact] = []
        # P06-11F-live：当前 Job 的 Writer agent（final answer 过短时回收其
        # last_messages 中最长正文；单 worker 串行 + 每 Job 独立 runner 安全）。
        self._writer_agent: Any | None = None
        # 结构化收尾只允许一次
        self._finalize_used = False
        # P06-11E：是否注入 fake crew_factory（测试路径）；None 时生产走分阶段执行。
        # 注意不能直接用 self._crew_factory is not None 判断——__init__ 总会赋 lambda。
        self._staged = crew_factory is None
        # P06-06B：实时进度端口（Worker 在 run 前注入；不注入则静默）
        self.progress: ProgressSink | None = None
        self.job_id: uuid.UUID | None = None

    @property
    def config(self) -> LLMConfig:
        """暴露 LLMConfig 供审计/测试断言（api_key 为 SecretStr，不泄露明文）。"""
        return self._config

    def assemble_crew(self, fakes: dict[str, AnyLLM]) -> Crew:
        """用注入的 fake LLM 组装三 Agent 顺序 Crew（离线契约验证，不联网）。"""
        return build_research_crew(
            self._config, fakes, analysis_facts=self._analysis_facts
        )

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

        P06-11E：每次 ``run(request)`` 创建**独立** ``_RunContext``——
        ``finalization_count`` / ``prefetch_result`` / ``analysis_facts`` 等
        Job 特有状态全部限定在当前 Job，禁止跨 Job 泄漏
        （修复旧版 ``_finalize_used`` 跨 Job 被复用的缺陷）。

        P06-07 前置修复：返回最终 ``ResearchFlowState``，供 Worker 在成功
        路径发布最终报告工件（fake/live 共用流程）。
        """
        # P06-06A：按任务档位选择当前预算（合法值由 domain.ResearchProfileMode 校验）。
        # 不在构造/全局环境做固定档位；任务不同、档位不同。
        profile = ResearchProfile.for_mode(
            "fast" if request.research_profile == "fast" else "deep"
        )
        # P06-11G：fast 档不再强制全局关闭思考——只给 Research/Analysis
        # 注入 enable_thinking=False 的角色覆盖；Writer 保留注册表/覆盖块中的
        # per-role 配置（例如 Writer 可单独开深度思考，不拖慢 Research/Analysis）。
        effective_config = self._config
        if profile.mode == "fast":
            from invest_research.agents.llm_factory import LLMRole

            role_overrides = dict(self._config.role_overrides)
            for role in (LLMRole.RESEARCH, LLMRole.ANALYSIS):
                existing = role_overrides.get(role.value)
                if existing is not None:
                    role_overrides[role.value] = existing.model_copy(
                        update={"enable_thinking": False}
                    )
            effective_config = self._config.model_copy(
                update={
                    "enable_thinking": False,
                    "role_overrides": role_overrides,
                }
            )
        # P06-11G：优先使用 component_factory 为当前 Job 创建全新组件
        # （budget/cache/recorder/stats/research_tools/prefetch 全部 job-local），
        # 禁止跨 Job 复用捕获旧预算/旧缓存的工具闭包；未提供时回退构造注入的共享引用。
        if self._component_factory is not None:
            components = self._component_factory()
            job_stats: dict[str, int] = components.stats
            job_recorder: PerformanceRecorder = components.recorder
            job_budget: ToolBudget | None = components.budget
            job_cache: ToolCallCache | None = components.cache
            job_prefetch: (
                Callable[[ResearchRequest], PrefetchResult | None] | None
            ) = components.prefetch
            job_tools: list[Any] | None = components.research_tools
        else:
            job_stats = self._stats
            job_recorder = self._recorder
            job_budget = self._budget
            job_cache = self._cache
            job_prefetch = self._prefetch
            job_tools = self._research_tools
            components = JobResearchComponents(
                research_tools=job_tools if job_tools is not None else [],
                cache=job_cache if job_cache is not None else ToolCallCache(),
                recorder=job_recorder,
                budget=job_budget if job_budget is not None else ToolBudget(),
                prefetch=job_prefetch,
                stats=job_stats,
            )
        ctx = _RunContext(
            request=request,
            profile=profile,
            effective_config=effective_config,
            components=components,
            stats=job_stats,
            recorder=job_recorder,
            budget=job_budget,
            cache=job_cache,
            prefetch=job_prefetch,
            research_tools=job_tools,
        )
        # P06-11G：让本 Job 的执行阶段都使用 ctx 内的 job-local 组件，
        # 不再读取 runner 构造期捕获的共享引用（避免闭包仍绑定旧预算/缓存）。
        self._research_tools = job_tools
        self._cache = job_cache
        self._prefetch = job_prefetch
        self._recorder = job_recorder
        self._stats = job_stats
        self._budget = job_budget
        # 兼容既有测试/观测辅助读取的实例字段：每次 run 重置（防跨 Job 泄漏）。
        self._current_profile = profile
        self._effective_config = effective_config
        self._prefetch_result = None
        self._analysis_facts = []
        self._active_ctx = ctx
        state = self._run_live(request, ctx)
        self.last_state = state
        self.run_manifest = state.run_manifest
        return state

    def _run_live(self, request: ResearchRequest, ctx: _RunContext) -> ResearchFlowState:
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
            return self._run_live_impl(request, ctx)

    def _run_live_impl(self, request: ResearchRequest, ctx: _RunContext) -> ResearchFlowState:
        """真实执行：Crew → 解析 → 质量门禁 → 受控反思 → manifest。

        P06-11E：生产路径（未注入 crew_factory）拆分为三阶段执行——
        Research → Finalize/Validate/Assemble → Analysis → Writer，前序成功后才执行
        下一步（Research 失败不执行 Analysis；Analysis 失败不执行 Writer）。
        注入 ``crew_factory``（测试 fake crew）时保持一次 kickoff 兼容路径。

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
        ctx.prefetch_result = prefetch_result
        self._prefetch_result = prefetch_result
        # P06-11C：把预取 financial_facts_summary（JSON 文本）还原为原始可信
        # FinancialFact 集合，供选择草稿现场组装（见 _extract_packs）。
        ctx.analysis_facts = self._parse_prefetched_facts(prefetch_result)
        self._analysis_facts = ctx.analysis_facts
        self._mark("01_company_resolve", "succeeded")

        # 0.5 组装 Crew 输入：ResearchRequest + 预取结果显式注入（禁止 Agent 猜公司/日期）
        inputs = self._build_crew_inputs(request, prefetch_result)

        # P06-11E：拆分执行阶段。生产路径走三阶段短路；测试注入 fake crew 走兼容。
        if not self._staged:
            state, crew, result = self._run_legacy_crew(request, ctx, inputs)
            # 兼容路径：kickoff 后记录 Agent 指标 + 性能（分阶段路径在各阶段内已记录）
            self._record_agent_metrics(crew, result)
            self._record_performance(crew, result)
        else:
            state = self._run_staged(request, ctx)
            crew, result = None, None

        # 3. 确定性质量门禁（P06-06B：06_quality_gate 边界）
        self._mark("06_quality_gate", "running")
        state.quality_report = run_quality_gate(state)
        self._mark("06_quality_gate", "succeeded")

        # 3.5 P06-11F：一次有界 Writer 修订（不重新执行 Research/Analysis/外部工具，
        # 不增加事实；修订后重新组装 + 重新质量门禁；第二次仍失败保持 rejected）。
        self._apply_bounded_revision(state)

        # 4. 受控反思（有界：revision ≤1、supplement ≤1），由 ReflectionController 路由
        reflection = self._run_reflection(state)

        # 5. 采集性能并生成 RunManifest（质量门禁通过才 published）
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

    # ------------------------------------------------------------------
    # P06-11E：执行阶段拆分（Research → Analysis → Writer 三阶段短路）
    # ------------------------------------------------------------------

    def _run_legacy_crew(
        self,
        request: ResearchRequest,
        ctx: _RunContext,
        inputs: dict[str, str],
    ) -> tuple[ResearchFlowState, Any, Any]:
        """兼容路径：一次 kickoff 运行三 Agent Crew（测试注入 fake crew 时使用）。

        保留注入 ``crew_factory`` 的离线契约验证（P05-12B 及既有测试），
        不改变 kickoff 后解析/落盘逻辑。
        """
        # 1. 运行三 Agent 顺序 Crew（注入 fake crew）。
        crew = self._crew_factory(ctx.effective_config, self._research_tools)
        scope = self._subscribe_task_progress(crew)
        llm_scope = self._subscribe_llm_calls(crew)
        usage_before = self._agent_token_snapshots(crew)
        try:
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:  # noqa: BLE001 - 应用边界：记录并转 fail-fast
            error_code = ErrorCode.INTERNAL_BUG.value
            failure_stage: str | None = None
            exc_text = f"{type(exc).__name__}: {exc}".lower()
            if _is_structured_output_unsupported(exc_text):
                error_code = ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED.value
                failure_stage = _structured_output_stage(exc)
                if _iteration_limit_details(crew) is not None:
                    _LOGGER.warning(
                        "kickoff 同时出现迭代耗尽与 response_format 拒绝，"
                        "按最接近根因分类为 STRUCTURED_OUTPUT_UNSUPPORTED"
                    )
            else:
                exhausted = _iteration_limit_details(
                    crew,
                    preferred_role=_validation_role(exc),
                )
                if exhausted is not None:
                    role, failure_stage = exhausted
                    error_code = ErrorCode.ITERATION_LIMIT.value
                    try:
                        from invest_research.infrastructure.observability.metrics_events import (
                            count_agent_iteration_limit,
                        )

                        count_agent_iteration_limit(role, self._current_profile.mode)
                    except Exception:  # noqa: BLE001 - 指标尽力而为
                        pass
                elif _looks_like_tool_input(exc):
                    error_code = ErrorCode.NOT_A_PACK.value
                    failure_stage = "05_writer"
            raise LiveFlowExecutionError(
                f"真实 Crew 执行失败: {type(exc).__name__}: {exc}",
                error_code=error_code,
                failure_stage=failure_stage,
            ) from exc
        finally:
            self._record_agent_token_deltas(crew, usage_before)
            if scope is not None:
                scope.__exit__(None, None, None)
            if llm_scope is not None:
                llm_scope.__exit__(None, None, None)

        self._mark("03_documents", "succeeded")
        state = self._extract_packs(result, request, ctx)
        return state, crew, result

    def _run_staged(self, request: ResearchRequest, ctx: _RunContext) -> ResearchFlowState:
        """P06-11E 三阶段执行：Research → Analysis → Writer（前序成功才执行下一步）。

        1. 执行 Research 工具循环；
        2. Finalize + Validate + Assemble → ResearchPack；
        3. ResearchPack 成功后才执行 Analysis；
        4. Finalize + Validate + Assemble → FinancialAnalysisPack；
        5. FinancialAnalysisPack 成功后才执行 Writer；
        6. Writer Markdown → ReportDraftAssembler → ReportDraft；
        7. 质量门禁由调用方（_run_live_impl）在 state 上执行。

        任一阶段失败立即抛 ``LiveFlowExecutionError``（短路，不执行后续 Agent）。
        """
        state = ResearchFlowState(request=request)
        # 1+2. Research
        self._mark("02_research", "running")
        state.research_pack = self._exec_research_stage(request, ctx)
        self._mark("02_research", "succeeded")
        self._mark("03_documents", "succeeded")
        # 3+4. Analysis（ResearchPack 成功后才执行）
        self._mark("04_analysis", "running")
        state.analysis_pack = self._exec_analysis_stage(request, ctx)
        self._mark("04_analysis", "succeeded")
        # 5+6. Writer（FinancialAnalysisPack 成功后才执行）
        self._mark("05_writer", "running")
        state.report_draft = self._exec_writer_stage(request, ctx, state)
        self._mark("05_writer", "succeeded")
        return state

    def _exec_research_stage(
        self, request: ResearchRequest, ctx: _RunContext
    ) -> ResearchPack:
        """Research 阶段：Agent 工具循环 → Finalize → Validate → Assemble。"""
        from crewai import Process

        from invest_research.agents.llm_factory import LLMRole

        task = build_research_task(
            ctx.effective_config,
            profile=ctx.profile,
            tools=self._research_tools,
        )
        crew = Crew(
            agents=[task.agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )
        inputs = self._build_crew_inputs(request, ctx.prefetch_result)
        raw = self._kickoff_single(crew, inputs)
        if (
            structured_output_mode(ctx.effective_config, LLMRole.RESEARCH)
            == StructuredOutputMode.NATIVE_PYDANTIC
        ):
            # Qwen：直接本地解析为 ResearchPack（Boundary 校验）
            return _normalize_research_sources(self._to_research_pack(raw, request, ctx))
        # DeepSeek/generic：独立 Finalizer → ResearchSelectionDraft → 确定性组装
        finalizer = DeepSeekJsonObjectFinalizer(ctx.effective_config)
        try:
            draft = finalizer.finalize(raw, ResearchSelectionDraft, role="research")
            assert isinstance(draft, ResearchSelectionDraft)
            return _normalize_research_sources(
                ResearchPackAssembler().assemble(
                    draft,
                    request=request,
                    company_identity=self._resolved_identity_or_fail(request, ctx),
                    source_filings=self._cache_filings_if_available(request, ctx),
                )
            )
        except ResearchAssemblerError:
            # 选草稿成功但无可信来源（缓存未预热）→ 回退有界结构化收尾：
            # 从缓存读取 SEC 申报记录构建 ResearchPack（不伪造来源），
            # 收尾也失败时给出「缓存无来源，禁止伪造」的可诊断错误。
            return self._finalize_research_pack(request, ctx, sys.exc_info()[1])  # type: ignore[arg-type]
        except FinalizerError as exc:
            error_code = getattr(exc, "error_code", "SCHEMA_INVALID")
            raise LiveFlowExecutionError(
                str(exc), error_code=error_code, failure_stage="02_research"
            ) from exc

    def _exec_analysis_stage(
        self, request: ResearchRequest, ctx: _RunContext
    ) -> FinancialAnalysisPack:
        """Analysis 阶段：Agent 工具循环 → Finalize → Validate → Assemble。"""
        from crewai import Process

        from invest_research.agents.llm_factory import LLMRole

        task = build_analysis_task(ctx.effective_config, profile=ctx.profile)
        crew = Crew(
            agents=[task.agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )
        inputs = self._build_crew_inputs(request, ctx.prefetch_result)
        raw = self._kickoff_single(crew, inputs)
        if (
            structured_output_mode(ctx.effective_config, LLMRole.ANALYSIS)
            == StructuredOutputMode.NATIVE_PYDANTIC
        ):
            return _to_packed(raw, FinancialAnalysisPack)
        finalizer = DeepSeekJsonObjectFinalizer(ctx.effective_config)
        try:
            draft = finalizer.finalize(raw, AnalysisSelectionDraft, role="analysis")
            assert isinstance(draft, AnalysisSelectionDraft)
            return AnalysisPackAssembler().assemble(draft, ctx.analysis_facts)
        except (FinalizerError, AnalysisAssemblerError) as exc:
            error_code = getattr(exc, "error_code", "SCHEMA_INVALID")
            failure_stage = getattr(exc, "failure_stage", None) or "04_analysis"
            raise LiveFlowExecutionError(
                str(exc), error_code=error_code, failure_stage=failure_stage
            ) from exc

    def _exec_writer_stage(
        self,
        request: ResearchRequest,
        ctx: _RunContext,
        state: ResearchFlowState,
    ) -> ReportDraft:
        """Writer 阶段：Markdown → ReportDraftAssembler（Qwen 保持原生路径）。

        P06-11F：Writer 执行前先构建确定性 CitationRegistry 并注入 state；
        - WriterContextReader 把完整注册表交给 Writer（只能复制 key）；
        - ReportDraftAssembler 使用**同一个 registry**提取正文实际出现的 key；
        - Quality Gate 用 state.citation_registry 校验非法 key。
        """
        from crewai import Process

        from invest_research.agents.llm_factory import LLMRole
        from invest_research.application.citation_registry import build_citation_registry

        registry = build_citation_registry(state.research_pack, state.analysis_pack)
        state.citation_registry = registry
        loader: ArtifactLoader = _staged_artifact_loader(state)
        task = build_writer_task(
            ctx.effective_config,
            profile=ctx.profile,
            artifact_loader=loader,
            citation_registry=registry,
        )
        crew = Crew(
            agents=[task.agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )
        # P06-11F-live：把 writer agent 保存到 runner 实例字段（Pydantic state 的
        # setattr 动态属性不生效——ResearchFlowState extra=ignore），供 final answer
        # 过短时从 agent.last_messages 回收工具循环中已生成的最长正文。
        self._writer_agent = task.agent
        inputs = self._build_crew_inputs(request, ctx.prefetch_result)
        raw = self._kickoff_single(crew, inputs)
        if (
            structured_output_mode(ctx.effective_config, LLMRole.WRITER)
            == StructuredOutputMode.NATIVE_PYDANTIC
        ):
            return _to_packed(raw, ReportDraft)
        # DeepSeek/generic：普通 Markdown → ReportDraftAssembler（复用同一 registry）。
        # P06-11F-live：真实 DeepSeek Writer 常把数千字正文生成在工具循环中间步骤
        # （thought/observations），最终 final answer 只有 20~160 字符 → REPORT_INVALID。
        # 修复：final answer 过短时，从 agent.last_messages 回收工具循环中已生成的
        # 最长 assistant 正文（模型已生成的内容，不新增事实、不重新调用外部工具）。
        raw_text = self._extract_markdown(raw)
        assembler_out = self._assemble_writer_markdown(
            raw_text, request, state, registry
        )
        return assembler_out

    def _assemble_writer_markdown(
        self,
        raw_text: str,
        request: ResearchRequest,
        state: ResearchFlowState,
        registry: Any,
    ) -> ReportDraft:
        """组装 Writer Markdown；final answer 过短时回收工具循环中的长文本。

        P06-11F-live：DeepSeek Writer 的真实长正文常出现在工具循环中间步骤
        （agent.last_messages），final answer 仅 20~160 字符。这里在
        ReportAssemblerError（REPORT_INVALID/REPORT_TRUNCATED）时，从
        ``state`` 关联的 writer agent 的 last_messages 中选取**最长** assistant
        文本重试一次。不新增事实、不重新执行 Research/Analysis/外部工具。

        P06-11G 观测：失败时把 final answer 与 writer 工具循环历史落盘为
        ``05_writer_tool_history.txt``，并打 DEBUG 日志（原始长度/回收长度/是否
        命中），用于确认"114 字符 vs 工具循环 875 tokens 长文"的根因。
        """
        try:
            return ReportDraftAssembler().assemble(
                raw_text,
                request,
                state.research_pack,
                state.analysis_pack,
                registry=registry,
            )
        except ReportAssemblerError:
            candidate = self._longest_writer_history()
            self._dump_writer_tool_history(raw_text, candidate)
            recovered = candidate is not None and candidate.strip() != raw_text.strip()
            _LOGGER.info(
                "writer reassemble: final_len=%d recovered_len=%s recovered=%s",
                len(raw_text),
                len(candidate) if candidate else None,
                recovered,
            )
            if candidate is None or not recovered:
                raise
            return ReportDraftAssembler().assemble(
                candidate,
                request,
                state.research_pack,
                state.analysis_pack,
                registry=registry,
            )

    def _longest_writer_history(self) -> str | None:
        """从 Writer Agent 的工具循环对话记录（last_messages）中取最长 assistant 文本。

        CrewAI 1.6.1：Agent 在工具循环中间生成的数千字正文会保存在
        ``agent.last_messages``（OpenAI-style chat message 列表），而 final answer
        常被压缩成 20~160 字符。这里回收模型已生成的最长 assistant content
        （不新增事实、不重新执行 Research/Analysis/外部工具）。

        P06-11G 观测：未命中时也记录（供 DEBUG 日志说明回收为何失败）。
        """
        agent = getattr(self, "_writer_agent", None)
        if agent is None:
            _LOGGER.info("writer history: no writer agent recorded")
            return None
        messages = getattr(agent, "last_messages", None) or []
        best: str | None = None
        for msg in messages:
            if _message_role(msg) not in ("assistant", "ai"):
                continue
            text = _message_text(msg).strip()
            if text and (best is None or len(text) > len(best)):
                best = text
        if best is None:
            _LOGGER.info(
                "writer history: no assistant text candidate (messages=%d)", len(messages)
            )
        return best

    def _dump_writer_tool_history(self, raw_text: str, recovered: str | None) -> None:
        """P06-11G 观测：把 Writer 工具循环历史与 final answer 落盘为工件。

        工件路径 ``<artifact_root>/<job_id>/05_writer_tool_history.txt``；
        内容含每条 last_messages（role + 长度 + 文本）+ final answer + 回收候选，
        用于对比"final 仅 23 tokens / 114 字符"与"工具循环 875 tokens 长文"。
        落盘失败不影响任务（尽力而为）。
        """
        try:
            if self.job_id is None:
                return
            root = self._artifact_root / str(self.job_id)
            root.mkdir(parents=True, exist_ok=True)
            agent = getattr(self, "_writer_agent", None)
            lines: list[str] = []
            messages = getattr(agent, "last_messages", None) or []
            for i, msg in enumerate(messages):
                role = _message_role(msg) or "?"
                text = _message_text(msg)
                lines.append(f"--- [{i}] role={role} len={len(text)} ---\n{text}")
            lines.append(f"--- FINAL ANSWER len={len(raw_text)} ---\n{raw_text}")
            if recovered is not None:
                lines.append(f"--- RECOVERED CANDIDATE len={len(recovered)} ---\n{recovered}")
            (root / "05_writer_tool_history.txt").write_text(
                "\n".join(lines), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("writer tool history dump skipped")

    def _kickoff_single(self, crew: Any, inputs: dict[str, str]) -> Any:
        """执行单 Task Crew 并返回该 Task 输出（无事件订阅，分阶段路径内联记录）。"""
        scope = self._subscribe_task_progress(crew)
        llm_scope = self._subscribe_llm_calls(crew)
        usage_before = self._agent_token_snapshots(crew)
        try:
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:  # noqa: BLE001 - 应用边界：统一转可读错误
            self._record_agent_token_deltas(crew, usage_before)
            error_code = ErrorCode.INTERNAL_BUG.value
            exc_text = f"{type(exc).__name__}: {exc}".lower()
            failure_stage = _structured_output_stage(exc)
            if _is_structured_output_unsupported(exc_text):
                error_code = ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED.value
            elif _iteration_limit_details(crew) is not None:
                error_code = ErrorCode.ITERATION_LIMIT.value
            raise LiveFlowExecutionError(
                f"分阶段 Agent 执行失败: {type(exc).__name__}: {exc}",
                error_code=error_code,
                failure_stage=failure_stage,
            ) from exc
        finally:
            if scope is not None:
                scope.__exit__(None, None, None)
            if llm_scope is not None:
                llm_scope.__exit__(None, None, None)
        self._record_agent_token_deltas(crew, usage_before)
        tasks_output = getattr(result, "tasks_output", None)
        if tasks_output:
            return tasks_output[0]
        return result

    def _to_research_pack(
        self, raw: Any, request: ResearchRequest, ctx: _RunContext
    ) -> ResearchPack:
        """Research 本地解析（Qwen / 非草稿路径 + 有界收尾）。"""
        try:
            return _to_packed(raw, ResearchPack)
        except LiveFlowExecutionError as exc:
            return self._finalize_research_pack(request, ctx, exc)

    def _resolved_identity_or_fail(
        self, request: ResearchRequest, ctx: _RunContext
    ) -> CompanyIdentity:
        """取预取/解析公司身份；失败抛可读错误（不生成伪造 pack）。

        P06-11G：公司解析失败归类稳定 error_code=COMPANY_NOT_FOUND
        （failure_stage=01_company_resolve），不再误报为 SCHEMA_INVALID。
        """
        identity = self._resolved_identity(request, ctx)
        if identity is None:
            raise LiveFlowExecutionError(
                "无法确定公司身份，禁止生成伪造 ResearchPack",
                error_code=ErrorCode.COMPANY_NOT_FOUND.value,
                failure_stage="01_company_resolve",
            )
        return identity

    def _cache_filings_if_available(
        self, request: ResearchRequest, ctx: _RunContext
    ) -> list[dict[str, Any]]:
        """从工具缓存取可信 SEC 申报记录（供 ResearchPackAssembler 使用）。"""
        identity = ctx.prefetch_result.company_identity if ctx.prefetch_result is not None else None
        if identity is None or self._cache is None:
            return []
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
            return []
        try:
            payload = json.loads(cached)
        except (TypeError, ValueError):
            return []
        return [f for f in (payload.get("filings") or []) if isinstance(f, dict)]

    @staticmethod
    def _extract_markdown(obj: Any) -> str:
        """从 Writer 输出提取 Markdown 正文（兼容 CrewAI TaskOutput/字符串）。"""
        candidate = extract_candidate(obj)
        if isinstance(candidate, str):
            return candidate
        raw = getattr(obj, "raw", None)
        return raw if isinstance(raw, str) else ""

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

    def _subscribe_llm_calls(self, crew: Crew) -> Any | None:
        """用 CrewAI LLM 事件总线记录每次真实模型调用（P06-11A）。

        - ``LLMCallStartedEvent`` / ``LLMCallCompletedEvent`` / ``LLMCallFailedEvent``
          在每次真实模型请求边界 emit（crewai.llm.LLM 官方事件，非 monkey patch）；
        - 每个模型请求只记录一次次数/耗时；Token 由 Agent TokenProcess
          在 kickoff 前后取差，避免 CrewAI 事件丢 usage 与 Crew 汇总三倍复制；
        - 返回 ``scoped_handlers()`` context manager；调用方必须在 kickoff 完成后
          退出清理（防止 handler 泄漏到下一个 Job）。
        """
        try:
            from invest_research.infrastructure.observability.llm_call_observer import (
                LlmCallObserver,
            )
        except ImportError:
            return None
        try:
            agent_roles: dict[str, str] = {}
            for stable_role, agent in zip(
                _AGENT_ROLE_ORDER, getattr(crew, "agents", None) or []
            ):
                agent_id = str(getattr(agent, "id", "") or "").strip()
                if agent_id:
                    agent_roles[agent_id] = stable_role
            return LlmCallObserver(
                self._effective_config,
                agent_roles=agent_roles,
            ).subscribe()
        except Exception:  # noqa: BLE001 - 观测尽力而为
            return None

    @staticmethod
    def _agent_token_snapshots(crew: Any) -> dict[str, dict[str, int]]:
        """读取每个 Agent 的 CrewAI TokenProcess 累计值。

        CrewAI 1.6.1 的 completed 事件在多数非流式路径只携带文本，
        但 Agent ``_token_process`` 由官方 TokenCalcHandler 从真实响应 usage
        累加。这里仅作鸭子类型读取，不修改第三方对象。
        """
        snapshots: dict[str, dict[str, int]] = {}
        for agent in getattr(crew, "agents", None) or []:
            role = _stable_agent_role(getattr(agent, "role", None))
            process = getattr(agent, "_token_process", None)
            summary_getter = getattr(process, "get_summary", None)
            if role is None or not callable(summary_getter):
                continue
            try:
                summary = summary_getter()
                snapshots[role] = {
                    field: max(0, int(getattr(summary, field, 0) or 0))
                    for field in _TOKEN_FIELDS
                }
            except Exception:  # noqa: BLE001 - 观测尽力而为
                continue
        return snapshots

    def _record_agent_token_deltas(
        self,
        crew: Any,
        before: dict[str, dict[str, int]],
    ) -> None:
        """按角色记录真实 TokenProcess 前后差值（成功/失败都记）。"""
        try:
            from invest_research.agents.llm_factory import LLMRole
            from invest_research.infrastructure.observability.metrics_events import (
                count_llm_tokens,
                label_provider_model,
            )

            after = self._agent_token_snapshots(crew)
            field_to_type = {
                "prompt_tokens": "input",
                "completion_tokens": "output",
                "cached_prompt_tokens": "cached_input",
            }
            for role, current in after.items():
                try:
                    role_enum = LLMRole(role)
                except ValueError:
                    continue
                model = self._effective_config.model_for(role_enum)
                provider, model_lbl = label_provider_model(
                    self._effective_config.vendor,
                    model,
                    base_url=self._effective_config.base_url,
                )
                previous = before.get(role, {})
                for field, token_type in field_to_type.items():
                    delta = max(current.get(field, 0) - previous.get(field, 0), 0)
                    count_llm_tokens(provider, model_lbl, role, token_type, delta)
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("agent token metrics recording skipped")

    @staticmethod
    def _role_to_step(role_name: str | None) -> str | None:
        """把 Agent role 名映射到步骤名（未知 role 返回 None 不标记）。"""
        if not role_name:
            return None
        role = _stable_agent_role(role_name)
        return _AGENT_ROLE_TO_STEP.get(role) if role is not None else None

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
        """Agent 耗时 Prometheus 指标（P06-09C，尽力而为）。

        P06-11B 说明：LLM 次数/耗时/span 由 ``LlmCallObserver`` 负责；
        Token 由 ``_record_agent_token_deltas`` 按 Agent 真实累计值取差。这里只保留
        Agent 级耗时指标，**不再**把 Crew 汇总 usage 复制给三个角色。
        """
        try:
            from invest_research.agents.llm_factory import LLMRole
            from invest_research.infrastructure.observability.metrics_events import (
                count_agent_run,
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
                provider, model_lbl = label_provider_model(
                    self._config.vendor, model, base_url=self._config.base_url
                )
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
        except Exception:  # noqa: BLE001 - 指标写入尽力而为
            _LOGGER.warning("agent/llm metrics recording skipped")

    @staticmethod
    def _parse_prefetched_facts(prefetch_result: PrefetchResult | None) -> list[FinancialFact]:
        """从预取 financial_facts_summary JSON 文本还原原始可信 FinancialFact 集合。

        ``_serialize_facts`` 输出结构：``{"ok": true, "mapping_version": ...,
        "count": ..., "facts": [...]}``。这里剥离包装取 ``facts`` 数组，
        交给 ``parse_fact_records`` 反序列化为 FinancialFact。
        """
        if prefetch_result is None or prefetch_result.financial_facts_summary is None:
            return []
        try:
            payload = json.loads(prefetch_result.financial_facts_summary)
        except (TypeError, ValueError):
            return []
        records = payload.get("facts", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return []
        try:
            return parse_fact_records(records)
        except (KeyError, TypeError, ValueError):
            return []

    def _extract_packs(
        self, result: Any, request: ResearchRequest, ctx: _RunContext
    ) -> ResearchFlowState:
        """从 Crew 结果解析三个 pack 到 state。

        - CrewAI 1.6.1 的 ``CrewOutput`` 通过 ``tasks_output`` 按顺序暴露各 Task 输出；
        - 兼容 ``result.output``/``result.json_dict`` 兜底解析；
        - P06-11C：Analysis 输出若是 ``AnalysisSelectionDraft``（含 selected_fact_refs），
          先经 ``AnalysisPackAssembler`` 确定性组装为完整 ``FinancialAnalysisPack``；
          若草稿组装失败（ref 未解析/歧义/来源不一致）→ LiveFlowExecutionError
          （稳定错误码，failure_stage=04_analysis），不降级。
        - 任一 pack 缺失视为不可恢复失败（不降级）。
        """
        state = ResearchFlowState(request=request)

        outputs: list[Any] = []
        tasks_output = getattr(result, "tasks_output", None)
        if tasks_output:
            outputs = list(tasks_output)

        # 按顺序：research, analysis, writer
        if len(outputs) >= 1:
            state.research_pack = self._extract_research_pack(outputs[0], request, ctx)
        if len(outputs) >= 2:
            state.analysis_pack = self._extract_analysis_pack(outputs[1], ctx)
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
            state.report_draft = self._extract_report_draft(outputs[2], request, state)

        if state.research_pack is None or state.analysis_pack is None or state.report_draft is None:
            raise LiveFlowExecutionError("Crew 输出不完整：需要 research/analysis/writer 三个 pack")
        return state

    def _extract_report_draft(
        self,
        obj: Any,
        request: ResearchRequest,
        state: ResearchFlowState,
    ) -> ReportDraft:
        """解析 Writer 输出为 ReportDraft（P06-11D 统一供应商路径）。

        数据流：Writer ContextReader → Markdown 报告正文 → ReportDraftAssembler
        → ReportDraft（确定性生成 version/title/citation_keys）。

        - 已是 ReportDraft 实例（Qwen NATIVE_PYDANTIC 或旧路径）直接复用；
        - 输出是合法 ReportDraft JSON/dict（历史结构化路径）直接复用；
        - 否则视为普通 Markdown 报告正文，经 ReportDraftAssembler 确定性组装；
        - 空文本 / 过短说明 / 工具参数 / 拒绝说明 / 明显截断 / 缺任何必需章节
          → ReportAssemblerError → LiveFlowExecutionError（稳定错误码）。
        """
        if isinstance(obj, ReportDraft):
            return obj
        try:
            candidate = extract_candidate(obj)
        except Exception:  # noqa: BLE001 - 仅提取失败，回退 Markdown 组装
            candidate = None
        if isinstance(candidate, dict):
            try:
                return ReportDraft.model_validate(candidate)
            except Exception:
                # 不是合法 ReportDraft 结构 → 按普通 Markdown 组装
                pass
        raw_text: str = candidate if isinstance(candidate, str) else ""
        if raw_text:
            # 历史结构化路径兼容：字符串本身是合法 ReportDraft JSON 时优先解析。
            try:
                return ReportDraft.model_validate_json(raw_text)
            except Exception:
                pass
        if not raw_text:
            raw_obj = getattr(obj, "raw", None)
            if isinstance(raw_obj, str):
                raw_text = raw_obj
            else:
                raw_text = ""
        finish_reason = getattr(obj, "finish_reason", None)
        try:
            return ReportDraftAssembler().assemble(
                raw_text,
                request,
                state.research_pack,
                state.analysis_pack,
                finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            )
        except ReportAssemblerError as exc:
            raise LiveFlowExecutionError(
                str(exc),
                error_code=exc.error_code,
                failure_stage=exc.failure_stage,
            ) from exc

    def _extract_analysis_pack(self, obj: Any, ctx: _RunContext) -> FinancialAnalysisPack:
        """解析 Analysis 输出；P06-11C：先检测 SelectionDraft，再走 PackBoundary。

        - 输出含 ``selected_fact_refs`` 键（AnalysisSelectionDraft）→ 现场组装；
        - 否则（Qwen 原生 FinancialAnalysisPack / 旧路径完整 facts）→ 原 _to_packed。
        """
        candidate = extract_candidate(obj)
        if isinstance(candidate, dict) and "selected_fact_refs" in candidate:
            try:
                draft = AnalysisSelectionDraft.model_validate(candidate)
            except Exception as exc:
                raise LiveFlowExecutionError(
                    "Analysis 输出不是合法 AnalysisSelectionDraft",
                    error_code="SCHEMA_INVALID",
                    failure_stage="04_analysis",
                ) from exc
            try:
                return AnalysisPackAssembler().assemble(draft, ctx.analysis_facts)
            except AnalysisAssemblerError as exc:
                raise LiveFlowExecutionError(
                    str(exc),
                    error_code=exc.error_code,
                    failure_stage="04_analysis",
                ) from exc
        return _to_packed(obj, FinancialAnalysisPack)

    def _extract_research_pack(
        self, obj: Any, request: ResearchRequest, ctx: _RunContext
    ) -> ResearchPack:
        """解析 Research 输出；失败时尝试一次有界结构化收尾（不伪造来源）。"""
        try:
            pack = _to_packed(obj, ResearchPack)
        except LiveFlowExecutionError as exc:
            pack = self._finalize_research_pack(request, ctx, exc)
        return _normalize_research_sources(pack)

    def _finalize_research_pack(
        self, request: ResearchRequest, ctx: _RunContext, cause: Exception
    ) -> ResearchPack:
        """有界结构化收尾：从缓存中的 SEC 申报结果构建 ResearchPack（只允许一次）。

        - 只允许一次（计数限定在当前 Job 的 ``ctx.finalization_count``，
          禁止跨 Job 复用旧实例的 ``_finalize_used``）；使用 Agent 已经取得的
          工具结果（缓存），不重新执行整套 Research；
        - 不伪造来源：无有效 SEC 来源时明确抛 LiveFlowExecutionError。
        """
        if ctx.finalization_count >= 1:
            raise LiveFlowExecutionError("结构化收尾已使用过一次，禁止重复收尾") from cause
        ctx.finalization_count += 1

        identity = self._resolved_identity(request, ctx)
        if identity is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无法确定公司身份，无法结构化收尾；"
                "禁止生成伪造 ResearchPack",
                error_code=ErrorCode.COMPANY_NOT_FOUND.value,
                failure_stage="01_company_resolve",
            ) from cause
        cache = self._cache
        if cache is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无工具缓存，无法结构化收尾；禁止生成伪造 ResearchPack",
                error_code=ErrorCode.SEC_PREFETCH_UNAVAILABLE.value,
                failure_stage="02_research",
            ) from cause

        forms = ",".join(request.requested_forms) if request.requested_forms else "10-K,10-Q"
        key = cache.key(
            "sec_submissions",
            {
                "cik": identity.cik,
                "as_of_date": request.as_of_date.isoformat(),
                "requested_forms": forms,
            },
        )
        cached = cache.get(key)
        if cached is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且缓存无 SEC 申报结果，无法结构化收尾；"
                "禁止生成伪造 ResearchPack",
                error_code=ErrorCode.SEC_PREFETCH_UNAVAILABLE.value,
                failure_stage="02_research",
            ) from cause
        try:
            payload = json.loads(cached)
        except (TypeError, ValueError) as parse_exc:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且缓存 SEC 结果损坏，无法结构化收尾；"
                "禁止生成伪造 ResearchPack",
                error_code=ErrorCode.SEC_PREFETCH_UNAVAILABLE.value,
                failure_stage="02_research",
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
                "Research 输出不可解析且无有效 SEC 来源，无法结构化收尾；禁止生成伪造 ResearchPack",
                error_code=ErrorCode.SEC_PREFETCH_UNAVAILABLE.value,
                failure_stage="02_research",
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

    def _resolved_identity(
        self, request: ResearchRequest, ctx: _RunContext | None = None
    ) -> CompanyIdentity | None:
        """优先取预取结果中的公司身份；否则确定性本地解析（不联网）。

        ``ctx`` 为本 Job 上下文；缺省时回退实例字段（兼容既有测试）。
        """
        prefetch_result = (
            ctx.prefetch_result
            if ctx is not None
            else self._prefetch_result
        )
        if prefetch_result is not None and prefetch_result.company_identity is not None:
            return prefetch_result.company_identity
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

    def _apply_bounded_revision(self, state: ResearchFlowState) -> None:
        """P06-11F：质量失败时执行**至多一次**确定性修订。

        约束：
        - 仅当报告只因可修复 issue（missing_citation_keys / invalid_citation_key /
          forbidden_advice）且 revision 未被尝试过时执行；
        - 修订输入只含原稿、issue codes、CitationRegistry、必需章节；
        - 不重新执行 Research/Analysis，不调用 SEC/Serper/FinancialCalculator，
          不增加事实（确定性修订是纯函数）；
        - 修订后重新运行 ReportDraftAssembler 与 Quality Gate；
        - 第二次仍失败 → 保持 rejected，并保留原稿、修订稿与质量报告；
        - 无条件设置 revision_attempted；revision_succeeded 表示修订后通过。
        """
        report = state.quality_report
        if report is None or report.all_passed:
            return
        if state.report_draft is None or state.revision_attempted:
            return
        registry = state.citation_registry
        if registry is None:
            return

        from invest_research.application.deterministic_revision import (
            REVISABLE_ISSUE_CODES,
            deterministic_revise,
        )

        issue_codes: list[str] = []
        q_issues = classify_state(state)
        for issue in q_issues:
            if issue.code in REVISABLE_ISSUE_CODES:
                issue_codes.append(issue.code)
        if not issue_codes:
            return

        # 记录原稿（修订审计）。
        original_markdown = state.report_draft.markdown
        state.revision_original_markdown = original_markdown
        state.revision_attempted = True
        count_revision_attempted: Callable[[], None] | None = None
        count_revision_succeeded: Callable[[bool], None] | None = None
        try:
            from invest_research.infrastructure.observability.metrics_events import (
                count_revision_attempted as _attempted,
            )
            from invest_research.infrastructure.observability.metrics_events import (
                count_revision_succeeded as _succeeded,
            )

            count_revision_attempted = _attempted
            count_revision_succeeded = _succeeded
        except Exception:  # noqa: BLE001 - 指标写入尽力而为
            pass

        if count_revision_attempted is not None:
            count_revision_attempted()

        revised = deterministic_revise(
            markdown=original_markdown,
            issue_codes=issue_codes,
            registry=registry,
        )
        if revised is None or revised.strip() == original_markdown.strip():
            # 无法修复：保持 rejected，保留原稿。
            if count_revision_succeeded is not None:
                count_revision_succeeded(False)
            return

        # 修订后重新组装为 ReportDraft（复用原 request/packs/registry）。
        request = state.request
        if request is None:
            if count_revision_succeeded is not None:
                count_revision_succeeded(False)
            return
        try:
            new_draft = ReportDraftAssembler().assemble(
                revised,
                request,
                state.research_pack,
                state.analysis_pack,
                registry=registry,
            )
        except ReportAssemblerError:
            # 修订稿无法组装（如变空/过短）→ 保持原 rejected。
            if count_revision_succeeded is not None:
                count_revision_succeeded(False)
            return
        state.report_draft = new_draft
        # 重新执行确定性质量门禁。
        state.quality_report = run_quality_gate(state)
        state.revision_succeeded = bool(
            state.quality_report is not None and state.quality_report.all_passed
        )
        if count_revision_succeeded is not None:
            count_revision_succeeded(state.revision_succeeded)
        # 修订后仍失败：保留原稿 + 修订稿 + 质量报告（rejected 语义由调用方保持）。

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
    component_factory: Callable[[], JobResearchComponents] | None = None,
) -> ResearchFlowRunner | LiveResearchFlowRunner:
    """按 settings.flow_mode 返回 FlowRunner 端口实现（P05-12A 入口）。

    - fake：返回 ``ResearchFlowRunner``（默认，离线确定性，普通测试/CI 用）；
    - live：校验 API Key 后返回 ``LiveResearchFlowRunner``（真实 Crew + 门禁 + 反思，
      可注入 ``research_tools`` 生产工具白名单与 ``stats`` 调用统计）；
    - ``component_factory``（P06-11G）：提供时每次 ``run()`` 为当前 Job 重建
      job-local 组件（budget/cache/recorder/stats/research_tools/prefetch），
      禁止跨 Job 复用捕获旧预算/旧缓存的工具闭包。
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
        component_factory=component_factory,
    )
