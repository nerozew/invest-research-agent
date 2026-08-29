"""P07-10A 年度研究运行协调器。

该模块把既有 P07 的纯组件接入一个 Job-local 执行入口。它不触碰 legacy Flow：
调用方只在 ``research_mode=annual_deep`` 时选择本协调器。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, cast
from urllib.parse import urlparse

from invest_research.application.annual_finalization_gate import AnnualFinalizationGate
from invest_research.application.annual_node_progress import AnnualNodeProgressService
from invest_research.application.citation_registry import build_citation_registry
from invest_research.domain.annual_filing_selector import (
    AnnualFilingSelection,
    AnnualFilingSelector,
)
from invest_research.domain.annual_pipeline import (
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
    ResearchDecision,
    ResearchDecisionAction,
    ResearchDecisionReasonCode,
    ResearchNode,
    ResearchNodeKind,
    ResearchNodeStatus,
    ResearchObservation,
)
from invest_research.domain.annual_research_policy import AnnualResearchDecisionPolicy
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    AnnualSectionPlan,
    SectionAnalysisPack,
    SectionDraft,
    SectionInputPack,
    SectionWorkStatus,
)
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnnualComparisonPack,
    CompanyIdentity,
    FinancialAnalysisPack,
    QualityReport,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.domain.quality import QualityRecommendation
from invest_research.financial.annual_statements import (
    FinancialStatementSet,
    extract_statements,
    load_statement_mapping,
)
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.annual_active_research import (
    _PAGES_PREFIX,
    ActiveResearchArtifactResult,
    ActiveResearchStatus,
)
from invest_research.infrastructure.annual_company_facts_pipeline import AnnualCompanyFactsSelection
from invest_research.infrastructure.annual_comparison_builder import AnnualComparisonBuilder
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualParsedDocument,
    AnnualParsedTextBlock,
)
from invest_research.infrastructure.annual_evidence_fanout import (
    AnnualEvidenceBundle,
    AnnualEvidenceFanoutPipeline,
)
from invest_research.infrastructure.annual_evidence_router import AnnualEvidenceRouter
from invest_research.infrastructure.annual_llm_fact_extraction import LLMFactExtractor
from invest_research.infrastructure.annual_llm_writing import AnnualSectionExecutor
from invest_research.infrastructure.annual_section_planner import AnnualSectionPlanner
from invest_research.infrastructure.annual_web_search_pipeline import WebSearchSectionEvidence
from invest_research.reporting.annual_report_renderer import (
    AnnualOfficialStatements,
    build_annual_cover,
    build_mda_section,
    build_mda_summary_section,
    build_reference_list,
    extract_mda,
    extract_official_statements,
    human_kind,
    render_citation_numbers,
    strip_locator_markers,
)
from invest_research.reporting.annual_statements_renderer import render_statements
from invest_research.tools.artifact_store import ArtifactStore
from invest_research.tools.base import ToolFailure, ToolSuccess
from invest_research.tools.company_resolver import ResolveCompanyRequest
from invest_research.tools.sec_submissions import FetchAnnualFilingsRequest

__all__ = [
    "AnnualRuntimeBlocked",
    "AnnualRuntimeComponents",
    "AnnualResearchRuntime",
]


# L2：报表行中文 label → 10-K 原文英文行名（供 excerpt 验证，避免驼峰 concept 名不匹配）。
_STATEMENT_EN_LABELS: dict[str, str] = {
    "汇率变动影响": "effect of exchange rate",
    "现金及等价物净变动": "net change in cash",
    "营业费用": "operating expenses",
    "毛利润": "gross profit",
    "负债合计": "total liabilities",
}


# 网页/分析师/评级等叙事证据的 kind（映射为 SourceType.WEB，而非错误地标成 SEC_XBRL）。
_WEB_EVIDENCE_KINDS = frozenset(
    {
        EvidenceKind.BUSINESS_OVERVIEW,
        EvidenceKind.RISK_FACTORS,
        EvidenceKind.MANAGEMENT_DISCUSSION,
        EvidenceKind.MATERIAL_EVENT,
        EvidenceKind.ANALYST_OPINION,
        EvidenceKind.RATING_AGENCY,
    }
)


def _source_type_for_kind(kind: EvidenceKind) -> SourceType:
    """按证据 kind 映射来源类型（修复 web 证据被错标为 SEC_XBRL 的 bug）。"""
    if kind in {EvidenceKind.TARGET_ANNUAL_FILING, EvidenceKind.COMPARATOR_ANNUAL_FILING}:
        return SourceType.SEC_FILING
    if kind in {EvidenceKind.COMPANY_FACTS, EvidenceKind.FINANCIAL_FACT_SET}:
        return SourceType.SEC_XBRL
    if kind in _WEB_EVIDENCE_KINDS:
        return SourceType.WEB
    return SourceType.SEC_XBRL


def _publisher_for_source_type(source_type: SourceType, url: str) -> str:
    """来源发布方：SEC 来源标 ``SEC``；web 来源用 URL 域名（如 investopedia.com）。

    修复此前所有来源统一硬编码 ``SEC``、连 investopedia/Fitch 等 web 来源也被
    误标 SEC 的问题。``urlparse`` 解析失败回退 ``"web"``（不抛异常）。
    """
    if source_type in {SourceType.SEC_FILING, SourceType.SEC_XBRL}:
        return "SEC"
    try:
        host = urlparse(url).netloc
    except ValueError:
        host = ""
    return host or "web"


class AnnualRuntimeBlocked(RuntimeError):
    """关键证据未满足，年度任务不得伪造完整报告。"""

    failure_stage = "annual_evidence"


class CompanyResolver(Protocol):
    def execute(self, request: ResolveCompanyRequest) -> object: ...


class AnnualFilingsFetcher(Protocol):
    def fetch_annual_filings(self, request: FetchAnnualFilingsRequest) -> object: ...


@dataclass(frozen=True)
class AnnualRuntimeComponents:
    """年度运行所需的 Job-local 工具与 P07 已实现组件。"""

    resolver: CompanyResolver
    filings_fetcher: AnnualFilingsFetcher
    evidence_fanout: AnnualEvidenceFanoutPipeline
    comparison_builder: AnnualComparisonBuilder
    artifact_root: Path
    progress: AnnualNodeProgressService | None = None
    section_executor: AnnualSectionExecutor | None = None
    # 可选的主动研究 Agent：CONTINUE_SEARCH 时真正执行补证搜索（不注入则保持现状）。
    active_research: Any | None = None
    # L2：LLM 从 10-K 原文提取缺失数值（报表行兜底；开关开启才注入）。
    fact_extractor: LLMFactExtractor | None = None


@dataclass(frozen=True)
class _AnnualNodeOutput:
    """允许年度节点在状态事件中登记其输出工件。"""

    value: Any
    artifact_keys: tuple[str, ...] = ()


class AnnualResearchRuntime:
    """将年度证据链编排为可发布的 ``ResearchFlowState``。

    P07-10B 的 LLM 只消费 ``SectionInputPack`` 的最小权限输入。没有注入
    ``section_executor`` 时保留确定性草稿替身，供前序单元测试和离线回归使用。
    """

    def __init__(self, components: AnnualRuntimeComponents) -> None:
        self._components = components

    def run(self, *, job_id: uuid.UUID, request: ResearchRequest) -> ResearchFlowState:
        identity = self._resolve(request)
        self._create_graph(job_id)
        selection = self._node(job_id, "annual_selection", self._select, identity.cik, request)
        evidence = self._node(
            job_id,
            "annual_evidence_fanout",
            self._components.evidence_fanout.run,
            job_id=job_id,
            selection=selection,
            cik=identity.cik,
            as_of_date=request.as_of_date,
            company=request.input_company,
        )
        comparison = self._node(
            job_id,
            "annual_comparison",
            self._components.comparison_builder.build,
            job_id=job_id,
            selection=selection,
            evidence=evidence,
        ).pack
        decision = self._decide(evidence.coverage_ledger)
        self._record_decision(job_id, decision)
        if decision.action is ResearchDecisionAction.CONTINUE_SEARCH:
            # 真正执行一次受控补证搜索（若注入了主动研究 Agent）：把新证据并入
            # evidence bundle，供后续路由/章节消费；随后仍按策略收口，不允许无界
            # 重复搜索（P07-06 首次补证 I/O 已由本运行轮次覆盖）。
            evidence = self._run_supplement(job_id, request, identity, evidence, decision)
            decision = AnnualResearchDecisionPolicy.decide(
                ResearchObservation(
                    coverage_ledger=evidence.coverage_ledger,
                    attempted_requirements=decision.gap_requirements,
                    decisions_used=1,
                    consecutive_no_gain=1,
                )
            )
            self._record_decision(job_id, decision)
        if decision.action is ResearchDecisionAction.BLOCKED:
            self._write_runtime_state(job_id, selection, decision, "blocked", ())
            self._block_remaining(job_id, "ANNUAL_EVIDENCE_BLOCKED")
            raise AnnualRuntimeBlocked(decision.reason)

        routing = self._node(
            job_id,
            "annual_route_evidence",
            AnnualEvidenceRouter.route,
            evidence=evidence,
            comparison_pack=comparison,
            decision=decision,
        )
        plan = self._node(job_id, "annual_section_plan", AnnualSectionPlanner.build, routing)
        drafts = self._write_sections(job_id, plan)
        finalization = self._node(
            job_id, "annual_finalization", AnnualFinalizationGate.evaluate, drafts
        )
        if finalization.revision_request is not None:
            drafts = self._write_sections(
                job_id, plan, force_sections=set(finalization.revision_request.target_sections)
            )
            finalization = self._node(
                job_id,
                "annual_finalization",
                lambda: AnnualFinalizationGate.evaluate(drafts, revision_attempt=1),
            )
        if finalization.finalization_pack is None:
            self._write_runtime_state(job_id, selection, decision, "blocked", drafts)
            self._block_remaining(job_id, "ANNUAL_QUALITY_BLOCKED")
            raise AnnualRuntimeBlocked(
                "; ".join(issue.message for issue in finalization.issues)
                or "年度章节未通过发布门禁"
            )
        final_markdown, source_titles = self._finalize_report(
            job_id,
            identity,
            finalization.finalization_pack,
            drafts,
            evidence,
            comparison,
            as_of_date=request.as_of_date,
        )
        state = self._build_state(
            request=request,
            identity=identity,
            evidence=evidence,
            comparison=comparison,
            drafts=drafts,
            finalization_status=finalization.status.value,
            final_markdown=final_markdown,
            source_titles=source_titles,
            llm_performance=(
                self._components.section_executor.performance_summary()
                if self._components.section_executor is not None
                else _empty_llm_performance()
            ),
        )
        self._persist_standard_artifacts(job_id, request, state)
        self._write_runtime_state(job_id, selection, decision, finalization.status.value, drafts)
        return state

    def _resolve(self, request: ResearchRequest) -> CompanyIdentity:
        result = self._components.resolver.execute(
            ResolveCompanyRequest(input_company=request.input_company)
        )
        if not isinstance(result, ToolSuccess) or not result.value.resolved:
            message = (
                result.error.message if isinstance(result, ToolFailure) else "公司身份无法唯一确认"
            )
            raise AnnualRuntimeBlocked(message)
        return cast(CompanyIdentity, result.value.candidates[0])

    def _select(self, cik: str, request: ResearchRequest) -> AnnualFilingSelection:
        result = self._components.filings_fetcher.fetch_annual_filings(
            FetchAnnualFilingsRequest(cik=cik, as_of_date=request.as_of_date)
        )
        if isinstance(result, ToolFailure):
            raise AnnualRuntimeBlocked(result.error.message)
        if not isinstance(result, ToolSuccess):
            raise AnnualRuntimeBlocked("年度 SEC filings 返回无效结果")
        return AnnualFilingSelector.select(result.value.filings, as_of_date=request.as_of_date)

    @staticmethod
    def _decide(ledger: Any) -> ResearchDecision:
        return AnnualResearchDecisionPolicy.decide(ResearchObservation(coverage_ledger=ledger))

    def _create_graph(self, job_id: uuid.UUID) -> None:
        if self._components.progress is None:
            return
        nodes = (
            ResearchNode(
                node_key="annual_selection", kind=ResearchNodeKind.DISCOVER_ANNUAL_FILINGS
            ),
            ResearchNode(
                node_key="annual_evidence_fanout", kind=ResearchNodeKind.VALIDATE_EVIDENCE
            ),
            ResearchNode(
                node_key="annual_comparison", kind=ResearchNodeKind.BUILD_ANNUAL_COMPARISON
            ),
            ResearchNode(node_key="annual_route_evidence", kind=ResearchNodeKind.VALIDATE_EVIDENCE),
            ResearchNode(node_key="annual_section_plan", kind=ResearchNodeKind.WRITE_SECTION),
            ResearchNode(
                node_key="annual_financial_analysis", kind=ResearchNodeKind.ANALYZE_FINANCIALS
            ),
            ResearchNode(node_key="annual_write_financial", kind=ResearchNodeKind.WRITE_SECTION),
            ResearchNode(node_key="annual_write_business", kind=ResearchNodeKind.WRITE_SECTION),
            ResearchNode(node_key="annual_write_risk", kind=ResearchNodeKind.WRITE_SECTION),
            ResearchNode(node_key="annual_write_events", kind=ResearchNodeKind.WRITE_SECTION),
            ResearchNode(node_key="annual_finalization", kind=ResearchNodeKind.FINALIZE_REPORT),
            ResearchNode(node_key="annual_final_writer", kind=ResearchNodeKind.FINALIZE_REPORT),
        )
        from invest_research.domain.annual_pipeline import NodeDependency

        dependencies = tuple(
            NodeDependency(upstream_node_key=upstream, downstream_node_key=downstream)
            for upstream, downstream in (
                ("annual_selection", "annual_evidence_fanout"),
                ("annual_evidence_fanout", "annual_comparison"),
                ("annual_comparison", "annual_route_evidence"),
                ("annual_route_evidence", "annual_section_plan"),
                ("annual_section_plan", "annual_financial_analysis"),
                ("annual_financial_analysis", "annual_write_financial"),
                ("annual_section_plan", "annual_write_business"),
                ("annual_section_plan", "annual_write_risk"),
                ("annual_section_plan", "annual_write_events"),
                ("annual_write_financial", "annual_finalization"),
                ("annual_write_business", "annual_finalization"),
                ("annual_write_risk", "annual_finalization"),
                ("annual_write_events", "annual_finalization"),
                ("annual_finalization", "annual_final_writer"),
            )
        )
        self._components.progress.create_graph(
            job_id=job_id, nodes=nodes, dependencies=dependencies
        )

    def _node(
        self,
        job_id: uuid.UUID,
        key: str,
        function: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        progress = self._components.progress
        if progress is None:
            value = function(*args, **kwargs)
            return value.value if isinstance(value, _AnnualNodeOutput) else value
        snapshot = progress.snapshot(job_id=job_id)
        existing = next(node for node in snapshot.nodes if node.node_key == key)
        if existing.status is ResearchNodeStatus.SUCCEEDED:
            # 运行状态工件是跨进程恢复事实；该轮只复用其下游 ArtifactStore 缓存。
            value = function(*args, **kwargs)
            return value.value if isinstance(value, _AnnualNodeOutput) else value
        progress.transition(job_id=job_id, node_key=key, target=ResearchNodeStatus.RUNNING)
        try:
            value = function(*args, **kwargs)
        except Exception:
            progress.transition(
                job_id=job_id,
                node_key=key,
                target=ResearchNodeStatus.FAILED_TERMINAL,
                error_code="ANNUAL_NODE_FAILED",
            )
            raise
        artifact_keys: tuple[str, ...] = ()
        if isinstance(value, _AnnualNodeOutput):
            artifact_keys = value.artifact_keys
            value = value.value
        progress.transition(
            job_id=job_id,
            node_key=key,
            target=ResearchNodeStatus.SUCCEEDED,
            artifact_keys=artifact_keys,
        )
        return value

    def _record_decision(self, job_id: uuid.UUID, decision: ResearchDecision) -> None:
        if (
            self._components.progress is None
            or decision.reason_code not in {
                ResearchDecisionReasonCode.CONFIRMED_UNAVAILABLE,
                ResearchDecisionReasonCode.DECISION_BUDGET_EXHAUSTED,
                ResearchDecisionReasonCode.TIME_BUDGET_EXHAUSTED,
                ResearchDecisionReasonCode.SUPPLEMENT_ALREADY_ATTEMPTED,
                ResearchDecisionReasonCode.NO_EVIDENCE_GAIN,
                ResearchDecisionReasonCode.TOOL_BUDGET_EXHAUSTED,
            }
        ):
            return
        self._components.progress.record_budget_stop(
            job_id=job_id, node_key="annual_evidence_fanout", reason_code=decision.reason_code.value
        )

    def _run_supplement(
        self,
        job_id: uuid.UUID,
        request: ResearchRequest,
        identity: CompanyIdentity,
        evidence: AnnualEvidenceBundle,
        decision: ResearchDecision,
    ) -> AnnualEvidenceBundle:
        """CONTINUE_SEARCH 时真正执行一次受控补证搜索，把新证据并入 bundle。

        未注入主动研究 Agent 时原样返回（保持旧行为）；补证失败/无产出也原样返回
        （搜索证据缺失不阻塞，后续按原决策收口）。
        """
        active = self._components.active_research
        if active is None:
            return evidence
        result = active.run(
            job_id=job_id,
            company=request.input_company,
            cik=identity.cik,
            as_of_date=request.as_of_date.isoformat(),
            targets=(
                EvidenceKind.BUSINESS_OVERVIEW,
                EvidenceKind.MANAGEMENT_DISCUSSION,
                EvidenceKind.MATERIAL_EVENT,
                EvidenceKind.RISK_FACTORS,
            ),
        )
        if result.status is not ActiveResearchStatus.COMPLETED or not result.section_artifacts:
            return evidence
        extra = self._active_evidence(job_id, result)
        if not extra:
            return evidence
        return evidence.model_copy(
            update={"evidence_artifacts": tuple([*evidence.evidence_artifacts, *extra])}
        )

    def _active_evidence(
        self, job_id: uuid.UUID, result: ActiveResearchArtifactResult
    ) -> list[EvidenceArtifact]:
        """把主动研究工件转成可路由的 EvidenceArtifact（缺工件时降级为空）。"""
        store = ArtifactStore(self._components.artifact_root / str(job_id))
        out: list[EvidenceArtifact] = []
        for kind_str, ref in result.section_artifacts.items():
            try:
                content = store.read(ref.artifact_key)
                section = WebSearchSectionEvidence.model_validate_json(content)
            except (KeyError, ValueError):
                continue
            if not section.entries:
                continue
            source_url = section.entries[0].url
            # WS2.5：web 来源 locator 指向其网页快照（可证明"引用真实存在于该 URL"）。
            snapshot_locator = (
                f"snapshot:{_PAGES_PREFIX}/{hashlib.sha256(source_url.encode()).hexdigest()}.json"
            )
            out.append(
                EvidenceArtifact(
                    artifact_key=ref.artifact_key,
                    kind=EvidenceKind(kind_str),
                    source_url=source_url,
                    locator=snapshot_locator,
                    content_checksum=ref.content_checksum,
                    parser_version="annual_web_search_section_v1",
                    validation_status=EvidenceValidationStatus.VALIDATED,
                )
            )
        return out

    def _block_remaining(self, job_id: uuid.UUID, reason_code: str) -> None:
        """Evidence/quality rejection must not leave an apparently runnable DAG.

        Called only at fan-in boundaries, after all scheduled work has completed.
        Successful nodes stay immutable; the store propagates blockers downstream.
        """
        progress = self._components.progress
        if progress is None:
            return
        for node in progress.snapshot(job_id=job_id).nodes:
            if not node.status.is_terminal:
                progress.transition(
                    job_id=job_id, node_key=node.node_key,
                    target=ResearchNodeStatus.BLOCKED,
                    error_code=reason_code, blocked_reason=reason_code,
                )

    def _write_sections(
        self,
        job_id: uuid.UUID,
        plan: AnnualSectionPlan,
        *,
        force_sections: set[AnnualSectionKind] | None = None,
    ) -> tuple[SectionDraft, ...]:
        """按依赖并发生成章节；每次最多三项模型工作。"""
        executor = self._components.section_executor
        if executor is None:
            # 保留 P07-10A 离线/旧测试的确定性替身，但生产 worker 始终注入 LLM executor。
            return tuple(
                self._node(
                    job_id,
                    _section_node_key(pack.section),
                    lambda pack=pack: self._persist_section(
                        job_id, pack, self._fallback_draft(pack)
                    ),
                )
                for pack in plan.sections
            )

        forced = force_sections or set()
        packs = {pack.section: pack for pack in plan.sections}
        drafts: dict[AnnualSectionKind, SectionDraft] = {}

        # 已成功且输入未变的章节从独立工件恢复；不要再次消耗模型额度。
        for section, pack in packs.items():
            if section not in forced:
                cached = self._read_section(job_id, pack)
                if cached is not None:
                    drafts[section] = cached
            if section not in drafts and pack.status in {
                SectionWorkStatus.BLOCKED,
                SectionWorkStatus.NOT_APPLICABLE,
            }:
                drafts[section] = self._node(
                    job_id,
                    _section_node_key(section),
                    lambda pack=pack: self._persist_section(
                        job_id, pack, self._fallback_draft(pack)
                    ),
                )

        financial = packs[AnnualSectionKind.FINANCIAL_PERFORMANCE]
        business = packs[AnnualSectionKind.BUSINESS_OVERVIEW]
        risk = packs[AnnualSectionKind.RISK_FACTORS]
        events = packs[AnnualSectionKind.MATERIAL_EVENTS]

        with ThreadPoolExecutor(max_workers=3) as pool:
            analysis_future = None
            if AnnualSectionKind.FINANCIAL_PERFORMANCE not in drafts:
                analysis_future = pool.submit(
                    self._node,
                    job_id,
                    "annual_financial_analysis",
                    executor.analyze_financial,
                    financial,
                )
            narrative_futures = {
                section: pool.submit(
                    self._node,
                    job_id,
                    _section_node_key(section),
                    lambda pack=pack: self._persist_section(
                        job_id,
                        pack,
                        executor.write_narrative(job_id=str(job_id), pack=pack),
                    ),
                )
                for section, pack in (
                    (AnnualSectionKind.BUSINESS_OVERVIEW, business),
                    (AnnualSectionKind.RISK_FACTORS, risk),
                )
                if section not in drafts
            }
            analysis = analysis_future.result() if analysis_future is not None else None
            finance_future = None
            if analysis is not None:
                finance_future = pool.submit(
                    self._node,
                    job_id,
                    "annual_write_financial",
                    lambda: self._persist_section(
                        job_id,
                        financial,
                        executor.write_financial(financial, analysis),
                    ),
                )
            event_future = None
            if AnnualSectionKind.MATERIAL_EVENTS not in drafts:
                event_future = pool.submit(
                    self._node,
                    job_id,
                    _section_node_key(AnnualSectionKind.MATERIAL_EVENTS),
                    lambda: self._persist_section(
                        job_id,
                        events,
                        executor.write_narrative(job_id=str(job_id), pack=events),
                    ),
                )
            for section, future in narrative_futures.items():
                drafts[section] = future.result()
            if finance_future is not None:
                drafts[AnnualSectionKind.FINANCIAL_PERFORMANCE] = finance_future.result()
            if event_future is not None:
                drafts[AnnualSectionKind.MATERIAL_EVENTS] = event_future.result()

        return tuple(drafts[pack.section] for pack in plan.sections)

    def _persist_section(
        self, job_id: uuid.UUID, pack: SectionInputPack, draft: SectionDraft
    ) -> _AnnualNodeOutput:
        store = ArtifactStore(self._components.artifact_root / str(job_id))
        key = f"annual/sections/{pack.section.value}.json"
        executor = self._components.section_executor
        metadata = (
            executor.manifest_metadata(pack)
            if executor is not None
            else {
                "prompt_version": "annual_llm_writing_v1",
                "models": {"mode": "deterministic_fallback"},
                "allowed_artifact_keys": sorted(pack.allowed_artifact_keys),
                "section_status": pack.status.value,
            }
        )
        payload = {
            "schema_version": "annual_section_output_v1",
            "input_fingerprint": self._section_fingerprint(pack),
            **metadata,
            "draft": draft.model_dump(mode="json"),
        }
        ref = store.write(
            key,
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            overwrite=True,
        )
        return _AnnualNodeOutput(draft, (ref.artifact_key,))

    def _read_section(self, job_id: uuid.UUID, pack: SectionInputPack) -> SectionDraft | None:
        store = ArtifactStore(self._components.artifact_root / str(job_id))
        try:
            payload = json.loads(store.read(f"annual/sections/{pack.section.value}.json"))
            if (
                payload.get("schema_version") != "annual_section_output_v1"
                or payload.get("prompt_version") != "annual_llm_writing_v1"
                or payload.get("input_fingerprint") != self._section_fingerprint(pack)
                or payload.get("allowed_artifact_keys") != sorted(pack.allowed_artifact_keys)
            ):
                return None
            return SectionDraft.model_validate(payload["draft"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _section_fingerprint(pack: SectionInputPack) -> str:
        encoded = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"annual_llm_writing_v1:{encoded}".encode("utf-8")).hexdigest()

    @staticmethod
    def _fallback_draft(pack: SectionInputPack) -> SectionDraft:
        if pack.section is AnnualSectionKind.FINANCIAL_PERFORMANCE:
            if pack.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
                analysis = SectionAnalysisPack(
                    section_input=pack,
                    status=SectionWorkStatus.BLOCKED,
                    limitations=pack.limitations,
                )
                return SectionDraft(
                    section=pack.section,
                    status=pack.status,
                    financial_analysis=analysis,
                    limitations=pack.limitations,
                )
            assert pack.comparison_pack is not None
            citations = tuple(artifact.artifact_key for artifact in pack.evidence_artifacts)
            analysis = SectionAnalysisPack(
                section_input=pack,
                status=pack.status,
                analysis_notes="指标由 SEC/XBRL 事实与 Decimal 确定性公式生成。",
                citation_artifact_keys=citations,
                limitations=pack.limitations,
            )
            return SectionDraft(
                section=pack.section,
                status=pack.status,
                markdown="\n".join(
                    [
                        "## 财务表现",
                        *(
                            f"- {metric.metric_name}: {metric.value} {metric.unit}"
                            for metric in pack.comparison_pack.metrics
                        ),
                    ]
                ),
                citation_artifact_keys=citations,
                financial_analysis=analysis,
                limitations=pack.limitations,
            )
        if pack.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
            return SectionDraft(
                section=pack.section,
                status=pack.status,
                section_input=pack,
                limitations=pack.limitations,
            )
        return SectionDraft(
            section=pack.section,
            status=pack.status,
            markdown=(
                f"## {_section_heading(pack.section)}\n本章节仅基于已验证并授权的年度申报工件整理。"
            ),
            citation_artifact_keys=tuple(
                artifact.artifact_key for artifact in pack.evidence_artifacts
            ),
            section_input=pack,
            limitations=pack.limitations,
        )

    def _finalize_report(
        self,
        job_id: uuid.UUID,
        identity: CompanyIdentity,
        finalization: Any,
        drafts: tuple[SectionDraft, ...],
        evidence: AnnualEvidenceBundle,
        comparison: AnnualComparisonPack,
        *,
        as_of_date: date,
    ) -> tuple[str, dict[str, str]]:
        """组装年度最终报告并返回 ``(markdown, title_by_url)``。

        - 封面（分析基准日 / 财报期间 / 生成时间 / 官方声明）确定性生成；
        - 来源清单带标题+可点击链接（标题 best-effort，缺失回退稳定短标签）；
        - 正文仍保留原始 ``[src_xxx]`` 引用：链接化在 ``_build_state`` 提取
          citation_keys 之后进行，避免展示层转换破坏引用门禁/提取。
        """
        executor = self._components.section_executor
        if executor is None:
            edited = "\n\n".join(draft.markdown for draft in drafts if draft.markdown)
        else:
            edited = self._node(
                job_id,
                "annual_final_writer",
                lambda: executor.edit_final(finalization=finalization, identity=identity),
            )
        # 三张财务报表章节确定性追加（数字直取 SEC 事实，不经 LLM）；
        # MD&A 在 executor 存在时用 LLM 摘译中文要点，否则回退原文直取节选（best-effort）。
        parsed = self._load_target_parsed(job_id, evidence)
        statements_markdown = self._financial_statements_markdown(job_id, evidence, comparison)
        if parsed is not None and executor is not None:
            mda = extract_mda(parsed.blocks)
            summary = executor.summarize_mda(parsed.blocks)
            if summary:
                mda_section = build_mda_summary_section(
                    summary, locator=mda.locator if mda else None
                )
            else:
                # 摘译过短/失败 → 回退原文直取节选（不显示敷衍的元说明）。
                mda_section = build_mda_section(parsed.blocks)
        else:
            mda_section = build_mda_section(parsed.blocks) if parsed is not None else ""
        body_parts = [edited]
        if statements_markdown:
            body_parts.append(statements_markdown)
        if mda_section:
            body_parts.append(mda_section)
        body = "\n\n".join(part for part in body_parts if part)
        limitations = "\n".join(f"- {item}" for item in finalization.limitations) or "- 无"

        store = ArtifactStore(self._components.artifact_root / str(job_id))
        kind_by_url = {
            artifact.source_url: artifact.kind.value for artifact in evidence.evidence_artifacts
        }
        titled = (
            executor.source_entries_with_titles(evidence.evidence_artifacts, store=store)
            if executor
            else ()
        )
        title_by_url = {
            url: (title or human_kind(kind_by_url.get(url, url))) for _key, title, url in titled
        }

        official = (
            extract_official_statements(parsed.blocks)
            if parsed is not None
            else AnnualOfficialStatements()
        )
        if executor is not None:
            # 官方声明中英对照：LLM 翻译失败/离线时原样保留英文（best-effort）。
            official = executor.translate_official_statements(official)
        cover = build_annual_cover(
            legal_name=identity.legal_name,
            ticker=identity.ticker,
            cik=identity.cik,
            as_of_date=as_of_date,
            target_fiscal_year=comparison.target_fiscal_year,
            comparator_fiscal_year=comparison.comparator_fiscal_year,
            generated_at=datetime.now(),
            official_statements=official,
        )
        return (
            f"# {identity.legal_name} 年度投资研究报告\n\n{cover}\n\n{body}\n\n"
            f"## 数据限制\n{limitations}\n\n"
            # 来源清单已由 _build_state 的论文式"## 引用"编号列表承担（可点击链接 + 编号对应）。
            "## 非投资建议声明\n本报告仅基于已验证公开资料整理，不构成投资建议。",
            title_by_url,
        )

    def _persist_standard_artifacts(
        self, job_id: uuid.UUID, request: ResearchRequest, state: ResearchFlowState
    ) -> None:
        """把年度 state 落成与 legacy 对齐的标准工件（供 API/前端/评测消费）。

        关键：年度 run_manifest（含 performance/token）此前从不落盘，这里首次持久化，
        使成本可视化的数据源与 legacy 一致。落盘尽力而为，失败不阻塞发布。
        """
        store = ArtifactStore(self._components.artifact_root / str(job_id))
        payloads = {
            "00_request.json": request.model_dump_json(),
            "02_research_pack.json": (
                state.research_pack.model_dump_json()
                if state.research_pack is not None
                else "null"
            ),
            "04_financial_analysis_pack.json": (
                state.analysis_pack.model_dump_json()
                if state.analysis_pack is not None
                else "null"
            ),
            "05_report_draft.json": (
                state.report_draft.model_dump_json() if state.report_draft is not None else "null"
            ),
            "06_quality_report.json": (
                state.quality_report.model_dump_json()
                if state.quality_report is not None
                else "null"
            ),
            "07_manifest.json": json.dumps(state.run_manifest, ensure_ascii=False, default=str),
        }
        for key, data in payloads.items():
            try:
                store.write(key, data.encode("utf-8"), overwrite=True)
            except Exception:  # noqa: BLE001 - 工件落盘尽力而为，不阻塞发布
                pass

    def _load_target_parsed(
        self, job_id: uuid.UUID, evidence: AnnualEvidenceBundle
    ) -> AnnualParsedDocument | None:
        """读取目标 10-K 的 parsed.json（官方声明定位）；缺失/损坏返回 None。"""
        result = evidence.target_document
        if result is None or result.parsed_artifact is None:
            return None
        try:
            content = ArtifactStore(self._components.artifact_root / str(job_id)).read(
                result.parsed_artifact.artifact_key
            )
            return AnnualParsedDocument.model_validate_json(content)
        except (KeyError, ValueError):
            return None

    def _financial_statements_markdown(
        self,
        job_id: uuid.UUID,
        evidence: AnnualEvidenceBundle,
        comparison: AnnualComparisonPack,
    ) -> str:
        """确定性生成三张财务报表章节 markdown（数字直取 SEC 事实，不经 LLM）。"""
        if (
            comparison.target_accession_number is None
            or comparison.target_fiscal_year is None
        ):
            return ""
        facts_result = evidence.company_facts
        if facts_result is None or facts_result.selected_artifact is None:
            return ""
        try:
            content = ArtifactStore(self._components.artifact_root / str(job_id)).read(
                facts_result.selected_artifact.artifact_key
            )
            selection = AnnualCompanyFactsSelection.model_validate_json(content)
        except (KeyError, ValueError):
            return ""
        def _year_date(year: int | None) -> date | None:
            if year is None:
                return None
            return next(
                (
                    fact.period_end or fact.instant_date
                    for fact in comparison.facts
                    if fact.fiscal_year == year
                    and (fact.period_end is not None or fact.instant_date is not None)
                ),
                None,
            )

        sets = extract_statements(
            list(selection.facts),
            load_statement_mapping(),
            target_year=comparison.target_fiscal_year,
            target_accession=comparison.target_accession_number,
            target_report_date=_year_date(comparison.target_fiscal_year),
            comparator_year=comparison.comparator_fiscal_year,
            comparator_accession=comparison.comparator_accession_number,
            comparator_report_date=_year_date(comparison.comparator_fiscal_year),
        )
        # L2：报表行缺失时，LLM 从 10-K 原文提取补值（best-effort，带 locator/来源）。
        if self._components.fact_extractor is not None and sets:
            parsed = self._load_target_parsed(job_id, evidence)
            if parsed is not None:
                sets = self._supplement_statement_rows(sets, parsed.blocks)
        return render_statements(sets)

    def _supplement_statement_rows(
        self,
        sets: tuple[FinancialStatementSet, ...],
        blocks: Iterable[AnnualParsedTextBlock],
    ) -> tuple[FinancialStatementSet, ...]:
        """对 value 缺失的报表行调 LLMFactExtractor 从 10-K 原文补值（有界、可追溯）。"""
        extractor = self._components.fact_extractor
        if extractor is None:
            return sets
        supplemented: list[FinancialStatementSet] = []
        for statement in sets:
            rows = list(statement.rows)
            for index, row in enumerate(rows):
                if row.value is not None:
                    continue
                label_en = _STATEMENT_EN_LABELS.get(row.label, row.label)
                extracted = extractor.extract(
                    blocks=blocks,
                    metric_name=row.label,
                    label_cn=row.label,
                    label_en=label_en,
                )
                if extracted is not None:
                    rows[index] = row.model_copy(
                        update={
                            "value": extracted.value,
                            "derivation_source": (f"10-K原文提取:{extracted.locator}",),
                        }
                    )
            supplemented.append(statement.model_copy(update={"rows": tuple(rows)}))
        return tuple(supplemented)

    @staticmethod
    def _draft_sections(plan: AnnualSectionPlan) -> tuple[SectionDraft, ...]:
        drafts: list[SectionDraft] = []
        for pack in plan.sections:
            if pack.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
                drafts.append(
                    SectionDraft(
                        section=pack.section,
                        status=pack.status,
                        section_input=None
                        if pack.section is AnnualSectionKind.FINANCIAL_PERFORMANCE
                        else pack,
                        financial_analysis=(
                            SectionAnalysisPack(
                                section_input=pack,
                                status=SectionWorkStatus.BLOCKED,
                                limitations=pack.limitations,
                            )
                            if pack.section is AnnualSectionKind.FINANCIAL_PERFORMANCE
                            else None
                        ),
                        limitations=pack.limitations,
                    )
                )
                continue
            citations = tuple(artifact.artifact_key for artifact in pack.evidence_artifacts)
            if pack.section is AnnualSectionKind.FINANCIAL_PERFORMANCE:
                assert pack.comparison_pack is not None
                lines = ["## 财务表现"]
                for metric in pack.comparison_pack.metrics:
                    value = "不可计算" if metric.value is None else str(metric.value)
                    lines.append(f"- {metric.metric_name}: {value} {metric.unit}")
                analysis = SectionAnalysisPack(
                    section_input=pack,
                    status=pack.status,
                    analysis_notes="指标由 SEC/XBRL 事实与 Decimal 确定性公式生成。",
                    citation_artifact_keys=citations,
                    limitations=pack.limitations,
                )
                drafts.append(
                    SectionDraft(
                        section=pack.section,
                        status=pack.status,
                        markdown="\n".join(lines),
                        citation_artifact_keys=citations,
                        financial_analysis=analysis,
                        limitations=pack.limitations,
                    )
                )
            else:
                headings = {
                    AnnualSectionKind.BUSINESS_OVERVIEW: "业务概览",
                    AnnualSectionKind.RISK_FACTORS: "风险因素",
                    AnnualSectionKind.MATERIAL_EVENTS: "重大事件",
                }
                drafts.append(
                    SectionDraft(
                        section=pack.section,
                        status=pack.status,
                        markdown=(
                            f"## {headings[pack.section]}\n"
                            "本章节仅基于已验证并授权的年度申报工件整理；"
                            "未被证据支持的判断不作陈述。"
                        ),
                        citation_artifact_keys=citations,
                        section_input=pack,
                        limitations=pack.limitations,
                    )
                )
        return tuple(drafts)

    @staticmethod
    def _build_state(
        *,
        request: ResearchRequest,
        identity: CompanyIdentity,
        evidence: AnnualEvidenceBundle,
        comparison: AnnualComparisonPack,
        drafts: tuple[SectionDraft, ...],
        finalization_status: str,
        final_markdown: str,
        llm_performance: dict[str, object],
        source_titles: dict[str, str] | None = None,
    ) -> ResearchFlowState:
        sources = [
            Source(
                source_type=_source_type_for_kind(artifact.kind),
                canonical_url=artifact.source_url,
                title=(source_titles or {}).get(artifact.source_url)
                or human_kind(artifact.kind.value),
                publisher=_publisher_for_source_type(
                    _source_type_for_kind(artifact.kind), artifact.source_url
                ),
                accessed_at=request.as_of_date,
                content_checksum=artifact.content_checksum,
                locator=artifact.locator,
            )
            for artifact in evidence.evidence_artifacts
        ]
        if not sources:
            raise AnnualRuntimeBlocked("没有已验证来源，拒绝生成年度报告")
        partial = finalization_status.startswith("partial") or comparison.status.value == "partial"
        analysis_partial = comparison.status.value == "partial"
        analysis_pack = FinancialAnalysisPack(
            version="annual_analysis_pack_v1",
            period_end=request.as_of_date,
            facts=list(comparison.facts),
            metrics=list(comparison.metrics),
            limitations=list(comparison.limitations),
            completeness=(
                AnalysisCompleteness.PARTIAL if analysis_partial else AnalysisCompleteness.COMPLETE
            ),
        )
        research_pack = ResearchPack(
            version="annual_research_pack_v1",
            company_identity=identity,
            as_of_date=request.as_of_date,
            sources=sources,
            coverage_notes="年度双期间 SEC 证据链",
        )
        registry = build_citation_registry(research_pack, analysis_pack)
        # 引用提取用原始正文（[src_xxx]）；编号化是发布层展示转换，必须在其后执行，
        # 否则 [src_xxx] 文本消失会导致 citation_keys 变空（引用门禁/提取被破坏）。
        citations = [key for key in registry.keys() if f"[{key}]" in final_markdown]
        # 论文式：正文 [src_xxx]/[fr_xxx] → [1][2]…，未知 key → [?]；移除 locator 标记。
        presentation_markdown, key_to_number = render_citation_numbers(
            final_markdown, registry
        )
        presentation_markdown = strip_locator_markers(presentation_markdown)
        reference_list = build_reference_list(registry, key_to_number)
        if reference_list:
            presentation_markdown = presentation_markdown.rstrip() + "\n\n" + reference_list
        return ResearchFlowState(
            request=request,
            company_identity=identity,
            research_pack=research_pack,
            analysis_pack=analysis_pack,
            report_draft=ReportDraft(
                version="annual_report_draft_v1",
                title=f"{identity.legal_name} 年度投资研究报告",
                markdown=presentation_markdown,
                citation_keys=citations,
            ),
            quality_report=QualityReport(
                version="annual_quality_v1",
                all_passed=True,
                warnings=list(comparison.limitations),
                recommendation=(
                    QualityRecommendation.PUBLISH_PARTIAL
                    if partial
                    else QualityRecommendation.PUBLISH
                ),
            ),
            run_manifest={
                "status": "published",
                "research_mode": "annual_deep",
                "finalization_status": finalization_status,
                "annual_llm_prompt_version": "annual_llm_writing_v1",
                "performance": llm_performance,
                "evidence": (
                    {"invocation_summary": evidence.invocation_summary}
                    if evidence.invocation_summary
                    else None
                ),
            },
        )

    def _write_runtime_state(
        self,
        job_id: uuid.UUID,
        selection: AnnualFilingSelection,
        decision: ResearchDecision,
        finalization_status: str,
        drafts: tuple[SectionDraft, ...],
    ) -> None:
        store = ArtifactStore(self._components.artifact_root / str(job_id))
        payload = {
            "schema_version": "annual_runtime_state_v1",
            "selection": selection.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "finalization_status": finalization_status,
            "sections": [draft.model_dump(mode="json") for draft in drafts],
        }
        store.write(
            "annual/runtime_state.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            overwrite=True,
        )


def _empty_llm_performance() -> dict[str, object]:
    """未注入 LLM 执行器时保持 usage 缺失，不把确定性替身记为模型用量。"""
    return {
        "llm_calls": 0,
        "llm_duration_seconds": 0.0,
        "models": {},
        "token_usage": {
            "prompt_tokens": None,
            "completion_tokens": None,
            "cached_prompt_tokens": None,
            "total_tokens": None,
        },
        "token_usage_complete": False,
        "usage_missing_calls": 0,
    }


def _section_node_key(section: AnnualSectionKind) -> str:
    return {
        AnnualSectionKind.FINANCIAL_PERFORMANCE: "annual_write_financial",
        AnnualSectionKind.BUSINESS_OVERVIEW: "annual_write_business",
        AnnualSectionKind.RISK_FACTORS: "annual_write_risk",
        AnnualSectionKind.MATERIAL_EVENTS: "annual_write_events",
    }[section]


def _section_heading(section: AnnualSectionKind) -> str:
    return {
        AnnualSectionKind.FINANCIAL_PERFORMANCE: "财务表现",
        AnnualSectionKind.BUSINESS_OVERVIEW: "业务概览",
        AnnualSectionKind.RISK_FACTORS: "风险因素",
        AnnualSectionKind.MATERIAL_EVENTS: "重大事件",
    }[section]
