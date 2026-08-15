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
from pathlib import Path
from typing import Any, Callable

from crewai.crew import Crew

from invest_research.agents.crew_factory import build_live_research_crew, build_research_crew
from invest_research.agents.llm_factory import AnyLLM, LLMConfig
from invest_research.domain.models import (
    FinancialAnalysisPack,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
)
from invest_research.domain.quality import QualityAction, QualityRecommendation
from invest_research.flows.manifest import build_run_manifest
from invest_research.flows.quality import run_quality_gate
from invest_research.flows.reflection import ReflectionController
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.live_resources import FlowModeError
from invest_research.infrastructure.performance import PerformanceRecorder, extract_token_usage
from invest_research.infrastructure.queue.flow_adapter import ResearchFlowRunner
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

        # 1. 运行三 Agent 顺序 Crew（默认真实模型；测试可注入 fake crew）
        crew = self._crew_factory(self._config, self._research_tools)
        try:
            result = crew.kickoff()
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

        def _to_packed(
            obj: Any,
            model: type[ResearchPack] | type[FinancialAnalysisPack] | type[ReportDraft],
        ) -> Any:
            if isinstance(obj, model):
                return obj
            json_dict = getattr(obj, "json_dict", None) or getattr(obj, "exported_output", None)
            if isinstance(json_dict, dict):
                return model.model_validate(json_dict)
            raw = getattr(obj, "raw", None)
            if isinstance(raw, str):
                return model.model_validate_json(raw)
            if isinstance(obj, dict):
                return model.model_validate(obj)
            raise LiveFlowExecutionError(f"无法解析 {model.__name__} 输出")

        # 按顺序：research, analysis, writer
        if len(outputs) >= 1:
            state.research_pack = _to_packed(outputs[0], ResearchPack)
        if len(outputs) >= 2:
            state.analysis_pack = _to_packed(outputs[1], FinancialAnalysisPack)
        if len(outputs) >= 3:
            state.report_draft = _to_packed(outputs[2], ReportDraft)

        if state.research_pack is None or state.analysis_pack is None or state.report_draft is None:
            raise LiveFlowExecutionError(
                "Crew 输出不完整：需要 research/analysis/writer 三个 pack"
            )
        return state

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
    )
