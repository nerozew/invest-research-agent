"""P05-12A/B FLOW_MODE=fake/live 生产 Flow wiring（composition root）。

职责：
- ``build_flow_runner(settings)``：按 ``settings.flow_mode`` 返回 FlowRunner 端口实现；
  - ``fake``（默认）：``ResearchFlowRunner``（P03 纯 fake 00-07 全链，不联网），
    普通测试/CI 不产生任何模型费用；
  - ``live``：fail-fast 校验真实 LLM API Key（缺失/为空即抛可读错误），
    返回 ``LiveResearchFlowRunner``（真实 Crew + 质量门禁 + 受控反思）。
- ``LiveResearchFlowRunner``（P05-12B 完整实现）：
  - ``run(request)``：执行真实生产 Crew/Flow——
    1. 接收 ResearchRequest；
    2. 运行三 Agent sequential Crew（默认真实模型；可注入 fake crew 离线验证）；
    3. 解析三个 pack 写入 ResearchFlowState；
    4. 执行确定性质量门禁（run_quality_gate）；
    5. 保留受控反思：修订 ≤1 次、补充研究 ≤1 次（ReflectionController）；
    6. 生成 RunManifest（确定性）；
    7. 保存各步骤中间产物（全部 pack / draft / 质量报告 / manifest）；
    8. 失败抛 ``LiveFlowExecutionError``（不降级 fake）。

授权边界：live 分支缺 key fail-fast；真实模型调用只发生在 Crew kickoff 时。
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, TypeVar

from crewai.crew import Crew
from pydantic import BaseModel, ValidationError

from invest_research.agents.crew_factory import build_live_research_crew, build_research_crew
from invest_research.agents.llm_factory import AnyLLM, LLMConfig
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

# 三 Agent 的执行顺序（与 crew_factory._assemble_tasks 保持一致）
_AGENT_ROLE_ORDER = ("research", "analysis", "writer")


class LiveFlowExecutionError(RuntimeError):
    """live 模式执行失败（上游/质量门禁不可恢复）。

    语义：live 失败后不得降级为 fake——抛此异常向 Worker/调用方表示真实失败。
    """


_PackModel = TypeVar("_PackModel", bound=BaseModel)


def _to_packed(obj: Any, model: type[_PackModel]) -> _PackModel:
    """从 Crew 输出对象解析为对应 pack（成功对象 / 字典 / JSON 文本）。

    Pydantic 校验失败（如 Action Input 被当成输出）统一转为 LiveFlowExecutionError。
    """
    if isinstance(obj, model):
        return obj
    json_dict = getattr(obj, "json_dict", None) or getattr(obj, "exported_output", None)
    try:
        if isinstance(json_dict, dict):
            return model.model_validate(json_dict)
        raw = getattr(obj, "raw", None)
        if isinstance(raw, str):
            return model.model_validate_json(raw)
        if isinstance(obj, dict):
            return model.model_validate(obj)
    except ValidationError as exc:
        raise LiveFlowExecutionError(
            f"无法解析 {model.__name__} 输出：输出不是合法结构化对象"
            "（Action/Action Input 是工具调用过程，不是最终答案）"
        ) from exc
    raise LiveFlowExecutionError(f"无法解析 {model.__name__} 输出：无法识别的输出类型")


class LiveResearchFlowRunner:
    """live 模式的 FlowRunner 契约实现（真实 Crew + 质量门禁 + 受控反思）。

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
        # 可注入 crew_factory：测试传 fake crew（返回预置 pack），离线验证完整控制流；
        # 默认 None 时用真实 ``build_live_research_crew``（生产真实模型调用）。
        self._crew_factory: Callable[..., Crew] = (
            crew_factory
            if crew_factory is not None
            else lambda cfg, rt: build_live_research_crew(cfg, rt, profile=self._profile)
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

    @property
    def config(self) -> LLMConfig:
        """暴露 LLMConfig 供审计/测试断言（api_key 为 SecretStr，不泄露明文）。"""
        return self._config

    def assemble_crew(self, fakes: dict[str, AnyLLM]) -> Crew:
        """用注入的 fake LLM 组装三 Agent 顺序 Crew（离线契约验证，不联网）。"""
        return build_research_crew(self._config, fakes)

    def run(self, request: ResearchRequest) -> None:
        """FlowRunner 端口实现：完整执行真实生产 Crew/Flow（同步）。"""
        state = self._run_live(request)
        self.last_state = state
        self.run_manifest = state.run_manifest

    def _run_live(self, request: ResearchRequest) -> ResearchFlowState:
        """真实执行：Crew → 解析 → 质量门禁 → 受控反思 → manifest。

        严格保持顺序；任一不可恢复失败抛 ``LiveFlowExecutionError``
        （绝不偷偷调用 fake）。
        """
        started_at = time.time()

        # 0. 公司身份确认后并行预取（best-effort，失败不影响 Agent 兜底）
        prefetch_result: PrefetchResult | None = None
        if self._prefetch is not None:
            try:
                prefetch_result = self._prefetch(request)
            except Exception:
                # 预取是纯优化：失败时 Research Agent 工具仍会自行拉取
                prefetch_result = None
        self._prefetch_result = prefetch_result

        # 0.5 组装 Crew 输入：ResearchRequest + 预取结果显式注入（禁止 Agent 猜公司/日期）
        inputs = self._build_crew_inputs(request, prefetch_result)

        # 1. 运行三 Agent 顺序 Crew（默认真实模型；测试可注入 fake crew）
        crew = self._crew_factory(self._config, self._research_tools)
        try:
            result = crew.kickoff(inputs=inputs)
        except Exception as exc:  # noqa: BLE001 - 应用边界：记录并转 fail-fast
            raise LiveFlowExecutionError(
                f"真实 Crew 执行失败: {type(exc).__name__}: {exc}"
            ) from exc

        # 2. 解析三个 pack 写入 state
        state = self._extract_packs(result, request)

        # 3. 确定性质量门禁
        state.quality_report = run_quality_gate(state)

        # 4. 受控反思（有界：revision ≤1、supplement ≤1），由 ReflectionController 路由
        reflection = self._run_reflection(state)

        # 5. 采集性能并生成 RunManifest（质量门禁通过才 published）
        self._record_performance(crew, result)
        performance = self._recorder.snapshot()
        state.run_manifest = build_run_manifest(
            state, self._config, started_at=started_at, performance=performance
        )
        # 合并反思审计记录（不丢失受控反思决策）
        state.run_manifest["reflection"] = reflection
        # 合并外部调用统计证据（P05-13 验收：SEC/Serper/LLM 等调用证据可见）
        if self._stats:
            state.run_manifest["evidence"] = {"invocation_summary": dict(self._stats)}

        # 6. 保存中间产物（确定性落盘）
        self._persist_intermediates(request, state)

        return state

    def _build_crew_inputs(
        self, request: ResearchRequest, prefetch_result: PrefetchResult | None
    ) -> dict[str, str]:
        """把 ResearchRequest 与预取结果组装为 Crew 输入（替换 Task 描述占位符）。

        所有值必须是 str/int/float/bool（CrewAI interpolate_only 的限制）：
        as_of_date 用 ISO 字符串、company_identity 用格式化文本。
        """
        forms = (
            ",".join(request.requested_forms) if request.requested_forms else "10-K,10-Q"
        )
        inputs: dict[str, str] = {
            "input_company": request.input_company,
            "as_of_date": request.as_of_date.isoformat(),
            "requested_forms": forms,
            "language": request.language,
            "company_identity": "未预解析（需先用 CompanyResolver 解析）",
            "prefetch_summary": prefetch_summary_text(prefetch_result),
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
                    self._recorder.record_agent(
                        role, int((end - start).total_seconds() * 1000)
                    )
        usage = getattr(result, "token_usage", None)
        self._recorder.set_token_usage(extract_token_usage(usage))

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
        if len(outputs) >= 3:
            state.report_draft = _to_packed(outputs[2], ReportDraft)

        if state.research_pack is None or state.analysis_pack is None or state.report_draft is None:
            raise LiveFlowExecutionError(
                "Crew 输出不完整：需要 research/analysis/writer 三个 pack"
            )
        return state

    def _extract_research_pack(self, obj: Any, request: ResearchRequest) -> ResearchPack:
        """解析 Research 输出；失败时尝试一次有界结构化收尾（不伪造来源）。"""
        try:
            return _to_packed(obj, ResearchPack)
        except LiveFlowExecutionError as exc:
            return self._finalize_research_pack(request, exc)

    def _finalize_research_pack(self, request: ResearchRequest, cause: Exception) -> ResearchPack:
        """有界结构化收尾：从缓存中的 SEC 申报结果构建 ResearchPack（只允许一次）。

        - 只允许一次；使用 Agent 已经取得的工具结果（缓存），不重新执行整套 Research；
        - 不伪造来源：无有效 SEC 来源时明确抛 LiveFlowExecutionError。
        """
        if self._finalize_used:
            raise LiveFlowExecutionError(
                "结构化收尾已使用过一次，禁止重复收尾"
            ) from cause
        self._finalize_used = True

        identity = self._resolved_identity(request)
        if identity is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无法确定公司身份，无法结构化收尾；"
                "禁止生成伪造 ResearchPack"
            ) from cause
        if self._cache is None:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无工具缓存，无法结构化收尾；"
                "禁止生成伪造 ResearchPack"
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
                )
            )
        if not sources:
            raise LiveFlowExecutionError(
                "Research 输出不可解析且无有效 SEC 来源，无法结构化收尾；"
                "禁止生成伪造 ResearchPack"
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
        if (
            self._prefetch_result is not None
            and self._prefetch_result.company_identity is not None
        ):
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
        """把中间产物写入工件目录（原子写，不覆盖）。"""
        from invest_research.tools.artifact_store import ArtifactStore

        job_root = self._artifact_root / f"{request.input_company}_{request.as_of_date.isoformat()}"
        store = ArtifactStore(job_root)

        payloads: dict[str, str] = {
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
            try:
                store.write(key, content.encode("utf-8"))
            except FileExistsError:
                continue  # 重复运行不覆盖已有工件（幂等）


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
