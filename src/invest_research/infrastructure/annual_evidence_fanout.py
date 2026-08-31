"""P07-04：年度 10-K 与 Company Facts 的最小 fan-out / fan-in。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from invest_research.domain.annual_filing_selector import AnnualFilingSelection
from invest_research.domain.annual_pipeline import (
    AnnualReadiness,
    CoverageItem,
    CoverageLedger,
    CoverageRequirement,
    CoverageStatus,
    EvidenceArtifact,
    EvidenceKind,
    EvidenceValidationStatus,
    ResearchNodeKind,
)
from invest_research.domain.models import Filing
from invest_research.infrastructure.annual_company_facts_pipeline import (
    AnnualCompanyFactsArtifactResult,
    AnnualCompanyFactsStatus,
)
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualDocumentArtifactResult,
    AnnualDocumentPipelineStatus,
)
from invest_research.infrastructure.annual_web_search_pipeline import (
    WebSearchArtifactResult,
    WebSearchEvidencePipeline,
    WebSearchEvidenceStatus,
    WebSearchSectionEvidence,
)
from invest_research.tools.artifact_store import ArtifactStore
from invest_research.tools.sec_company_facts import build_company_facts_url

# 搜索工件 kind → CoverageRequirement 映射（material_event 无预留 requirement，
# 只进 evidence，不进 ledger）。
_WEB_REQ_BY_KIND: dict[str, CoverageRequirement] = {
    "business_overview": CoverageRequirement.CURRENT_BUSINESS_OVERVIEW,
    "risk_factors": CoverageRequirement.CURRENT_RISK_FACTORS,
    "management_discussion": CoverageRequirement.COMPARATOR_MANAGEMENT_DISCUSSION,
}


class AnnualEvidenceBundle(BaseModel):
    """P07-04 fan-in 的纯证据输出；P07-05 才允许构造比较指标。"""

    model_config = ConfigDict(frozen=True)

    target_document: AnnualDocumentArtifactResult | None = None
    comparator_document: AnnualDocumentArtifactResult | None = None
    company_facts: AnnualCompanyFactsArtifactResult | None = None
    web_search: WebSearchArtifactResult | None = None
    evidence_artifacts: tuple[EvidenceArtifact, ...] = ()
    coverage_ledger: CoverageLedger
    # WS3：工具调用统计（filing_downloader_calls / sec_company_facts_calls / web_search_calls），
    # 供 run_manifest.evidence.invocation_summary 消费 → job_tool_calls_total 指标。
    invocation_summary: dict[str, int] = Field(default_factory=dict)


class FilingArtifactPipeline(Protocol):
    """可供 fan-out 调度的单 filing 工件入口。"""

    def run(self, *, job_id: UUID, filing: Filing) -> AnnualDocumentArtifactResult: ...


class CompanyFactsArtifactPipeline(Protocol):
    """可供 fan-out 调度的 Company Facts 工件入口。"""

    def run(
        self,
        *,
        job_id: UUID,
        cik: str,
        as_of_date: str,
        target_fiscal_year: int,
        comparator_fiscal_year: int,
    ) -> AnnualCompanyFactsArtifactResult: ...


class AnnualEvidenceFanoutPipeline:
    """并发执行可独立的年度 I/O，随后按确定性规则生成 Coverage Ledger。"""

    def __init__(
        self,
        document_pipeline: FilingArtifactPipeline,
        company_facts_pipeline: CompanyFactsArtifactPipeline,
        *,
        web_search_pipeline: WebSearchEvidencePipeline | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        self._document_pipeline = document_pipeline
        self._company_facts_pipeline = company_facts_pipeline
        self._web_search_pipeline = web_search_pipeline
        self._artifact_root = artifact_root

    def run(
        self,
        *,
        job_id: UUID,
        selection: AnnualFilingSelection,
        cik: str,
        as_of_date: date,
        company: str | None = None,
    ) -> AnnualEvidenceBundle:
        target = selection.target_filing
        comparator = selection.comparator_filing
        target_result: AnnualDocumentArtifactResult | None = None
        comparator_result: AnnualDocumentArtifactResult | None = None
        facts_result: AnnualCompanyFactsArtifactResult | None = None
        web_result: WebSearchArtifactResult | None = None

        # 没有目标 FY 时无法定义两年 Facts 筛选范围；直接给出可解释的 narrative-only。
        if target is None or selection.target_fiscal_year is None:
            return AnnualEvidenceBundle(
                coverage_ledger=self._ledger(selection, None, None, None, target_fiscal_year=None)
            )

        comparator_year = selection.comparator_fiscal_year or selection.target_fiscal_year - 1
        # WS3：工具调用统计（供 manifest.evidence.invocation_summary / job_tool_calls_total）。
        invocation: dict[str, int] = {}
        invocation["filing_downloader_calls"] = 1 + (1 if comparator is not None else 0)
        invocation["sec_company_facts_calls"] = 1
        if self._web_search_pipeline is not None and company:
            invocation["web_search_calls"] = 1
        with ThreadPoolExecutor(max_workers=4) as executor:
            target_future = executor.submit(
                self._document_pipeline.run, job_id=job_id, filing=target
            )
            facts_future = executor.submit(
                self._company_facts_pipeline.run,
                job_id=job_id,
                cik=cik,
                as_of_date=as_of_date.isoformat(),
                target_fiscal_year=selection.target_fiscal_year,
                comparator_fiscal_year=comparator_year,
            )
            comparator_future = (
                executor.submit(self._document_pipeline.run, job_id=job_id, filing=comparator)
                if comparator is not None
                else None
            )
            web_future = (
                executor.submit(
                    self._web_search_pipeline.run,
                    job_id=job_id,
                    company=company,
                    cik=cik,
                    as_of_date=as_of_date.isoformat(),
                )
                if self._web_search_pipeline is not None and company
                else None
            )
            # 逐个取回而不是 fail-fast：任一 filing 失败不影响其他独立结果。
            target_result = target_future.result()
            facts_result = facts_future.result()
            if comparator_future is not None:
                comparator_result = comparator_future.result()
            if web_future is not None:
                web_result = web_future.result()

        ledger = self._ledger(
            selection,
            target_result,
            comparator_result,
            facts_result,
            target_fiscal_year=selection.target_fiscal_year,
            web_result=web_result,
        )
        evidence = self._evidence(
            target,
            comparator,
            target_result,
            comparator_result,
            facts_result,
            cik,
            job_id=job_id,
            web_result=web_result,
        )
        return AnnualEvidenceBundle(
            target_document=target_result,
            comparator_document=comparator_result,
            company_facts=facts_result,
            web_search=web_result,
            evidence_artifacts=tuple(evidence),
            coverage_ledger=ledger,
            invocation_summary=invocation,
        )

    @staticmethod
    def _completed_document(result: AnnualDocumentArtifactResult | None) -> bool:
        return (
            result is not None
            and result.status is AnnualDocumentPipelineStatus.COMPLETED
            and result.parsed_artifact is not None
        )

    @staticmethod
    def _completed_facts(result: AnnualCompanyFactsArtifactResult | None) -> bool:
        return (
            result is not None
            and result.status is AnnualCompanyFactsStatus.COMPLETED
            and result.selected_artifact is not None
        )

    def _ledger(
        self,
        selection: AnnualFilingSelection,
        target_result: AnnualDocumentArtifactResult | None,
        comparator_result: AnnualDocumentArtifactResult | None,
        facts_result: AnnualCompanyFactsArtifactResult | None,
        *,
        target_fiscal_year: int | None,
        web_result: WebSearchArtifactResult | None = None,
    ) -> CoverageLedger:
        target_ok = self._completed_document(target_result)
        comparator_ok = self._completed_document(comparator_result)
        target_year = target_fiscal_year
        comparator_year = selection.comparator_fiscal_year or (
            target_year - 1 if target_year is not None else None
        )
        facts_ok = self._completed_facts(facts_result)
        available_years = set(facts_result.available_fiscal_years) if facts_result else set()
        target_facts_ok = facts_ok and target_year in available_years
        comparator_facts_ok = facts_ok and comparator_year in available_years
        target_key = self._parsed_key(target_result) if target_ok else None
        comparator_key = self._parsed_key(comparator_result) if comparator_ok else None
        facts_key = self._selected_key(facts_result) if facts_ok else None

        items = (
            self._item(
                CoverageRequirement.TARGET_ANNUAL_FILING,
                target_ok,
                target_key,
                selection.missing_reason
                or self._document_reason(target_result, "目标 10-K 不可用"),
            ),
            self._item(
                CoverageRequirement.TARGET_FINANCIAL_FACTS,
                target_facts_ok,
                facts_key if target_facts_ok else None,
                self._facts_reason(facts_result, target_year),
            ),
            self._item(
                CoverageRequirement.COMPARATOR_FINANCIAL_FACTS,
                comparator_facts_ok,
                facts_key if comparator_facts_ok else None,
                self._facts_reason(facts_result, comparator_year),
            ),
            self._item(
                CoverageRequirement.COMPARATOR_ANNUAL_FILING,
                comparator_ok,
                comparator_key,
                (
                    selection.missing_reason or "上一年度 10-K 未找到"
                    if selection.comparator_filing is None
                    else self._document_reason(comparator_result, "上一年度 10-K 不可用")
                ),
            ),
            *self._web_items(web_result),
        )
        if not target_ok:
            readiness = AnnualReadiness.NARRATIVE_ONLY_READY
        elif not target_facts_ok or not comparator_facts_ok:
            readiness = AnnualReadiness.BLOCKED
        elif comparator_ok:
            readiness = AnnualReadiness.FULL_READY
        else:
            readiness = AnnualReadiness.FINANCIAL_ONLY_READY
        return CoverageLedger(target_fiscal_year=target_year, items=items, readiness=readiness)

    @staticmethod
    def _parsed_key(result: AnnualDocumentArtifactResult | None) -> str:
        if result is None or result.parsed_artifact is None:
            raise ValueError("完成的 filing 结果缺少 parsed artifact")
        return result.parsed_artifact.artifact_key

    @staticmethod
    def _selected_key(result: AnnualCompanyFactsArtifactResult | None) -> str:
        if result is None or result.selected_artifact is None:
            raise ValueError("完成的 Company Facts 结果缺少 selected artifact")
        return result.selected_artifact.artifact_key

    @staticmethod
    def _item(
        requirement: CoverageRequirement,
        satisfied: bool,
        artifact_key: str | None,
        missing_reason: str,
    ) -> CoverageItem:
        if satisfied and artifact_key is not None:
            return CoverageItem(
                requirement=requirement,
                status=CoverageStatus.SATISFIED,
                evidence_artifact_keys=(artifact_key,),
                consumable_by=(ResearchNodeKind.BUILD_ANNUAL_COMPARISON,),
            )
        return CoverageItem(
            requirement=requirement,
            status=CoverageStatus.MISSING,
            missing_reason=missing_reason,
            consumable_by=(ResearchNodeKind.BUILD_ANNUAL_COMPARISON,),
        )

    @staticmethod
    def _document_reason(result: AnnualDocumentArtifactResult | None, fallback: str) -> str:
        if result is not None and result.failure is not None:
            return result.failure.message
        return fallback

    @staticmethod
    def _facts_reason(result: AnnualCompanyFactsArtifactResult | None, year: int | None) -> str:
        if result is not None and result.failure is not None:
            return result.failure.message
        if year is None:
            return "目标财年不可得，无法筛选 Company Facts"
        return f"FY {year} 未找到截至截止日可用的年度 Company Facts"

    def _evidence(
        self,
        target: Filing,
        comparator: Filing | None,
        target_result: AnnualDocumentArtifactResult | None,
        comparator_result: AnnualDocumentArtifactResult | None,
        facts_result: AnnualCompanyFactsArtifactResult | None,
        cik: str,
        *,
        job_id: UUID,
        web_result: WebSearchArtifactResult | None,
    ) -> list[EvidenceArtifact]:
        evidence: list[EvidenceArtifact] = []
        for filing, result, kind in (
            (target, target_result, EvidenceKind.TARGET_ANNUAL_FILING),
            (comparator, comparator_result, EvidenceKind.COMPARATOR_ANNUAL_FILING),
        ):
            if filing is None or not self._completed_document(result) or result is None:
                continue
            parsed = result.parsed_artifact
            assert parsed is not None
            evidence.append(
                EvidenceArtifact(
                    artifact_key=parsed.artifact_key,
                    kind=kind,
                    source_url=filing.primary_document_url,
                    locator="document",
                    fiscal_year=filing.report_period.year if filing.report_period else None,
                    content_checksum=parsed.content_checksum,
                    parser_version="annual_parsed_document_v1",
                    validation_status=EvidenceValidationStatus.VALIDATED,
                )
            )
        if self._completed_facts(facts_result) and facts_result is not None:
            selected = facts_result.selected_artifact
            assert selected is not None
            evidence.append(
                EvidenceArtifact(
                    artifact_key=selected.artifact_key,
                    kind=EvidenceKind.COMPANY_FACTS,
                    source_url=build_company_facts_url(cik),
                    content_checksum=selected.content_checksum,
                    parser_version="annual_company_facts_selection_v1",
                    validation_status=EvidenceValidationStatus.VALIDATED,
                )
            )
            for fiscal_year in facts_result.available_fiscal_years:
                evidence.append(
                    EvidenceArtifact(
                        artifact_key=selected.artifact_key,
                        kind=EvidenceKind.FINANCIAL_FACT_SET,
                        source_url=build_company_facts_url(cik),
                        fiscal_year=fiscal_year,
                        content_checksum=selected.content_checksum,
                        parser_version="annual_company_facts_selection_v1",
                        validation_status=EvidenceValidationStatus.VALIDATED,
                    )
                )
        evidence.extend(self._web_evidence(job_id, web_result))
        return evidence

    @staticmethod
    def _web_items(web_result: WebSearchArtifactResult | None) -> tuple[CoverageItem, ...]:
        """网页搜索是可选的叙事增强覆盖项：未配置时不存在，缺失不改变核心 readiness。"""
        if web_result is None:
            return ()
        out: list[CoverageItem] = []
        for kind_str, req in _WEB_REQ_BY_KIND.items():
            ref = web_result.section_artifacts.get(kind_str)
            out.append(
                CoverageItem(
                    requirement=req,
                    status=(
                        CoverageStatus.SATISFIED if ref is not None else CoverageStatus.MISSING
                    ),
                    evidence_artifact_keys=(ref.artifact_key,) if ref is not None else (),
                    missing_reason=None if ref is not None else "未获取该章节网页搜索证据",
                    consumable_by=(ResearchNodeKind.WRITE_SECTION,),
                )
            )
        return tuple(out)

    def _web_evidence(
        self,
        job_id: UUID,
        web_result: WebSearchArtifactResult | None,
    ) -> list[EvidenceArtifact]:
        """把已完成搜索工件转为可路由的叙事 EvidenceArtifact（缺 artifact_root 时降级为空）。"""
        if web_result is None or web_result.status is not WebSearchEvidenceStatus.COMPLETED:
            return []
        if self._artifact_root is None:
            return []
        store = ArtifactStore(self._artifact_root / str(job_id))
        out: list[EvidenceArtifact] = []
        for kind_str, ref in web_result.section_artifacts.items():
            try:
                content = store.read(ref.artifact_key)
                section = WebSearchSectionEvidence.model_validate_json(content)
            except (KeyError, ValueError):
                continue
            if not section.entries:
                continue
            # 每个 entry 都进证据链（LLM 上下文会给全部 entry 的 [src_<hash(url)>]；
            # 若只登记首条，其余引用 key 不在 registry → 报告出现 [?]）。
            for entry in section.entries:
                out.append(
                    EvidenceArtifact(
                        artifact_key=ref.artifact_key,
                        kind=EvidenceKind(kind_str),
                        source_url=entry.url,
                        content_checksum=ref.content_checksum,
                        parser_version="annual_web_search_section_v1",
                        validation_status=EvidenceValidationStatus.VALIDATED,
                    )
                )
        return out
