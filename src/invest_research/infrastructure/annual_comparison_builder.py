"""P07-05：把已验证年度证据构建为可恢复的确定性比较包。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from invest_research.domain.annual_filing_selector import AnnualFilingSelection
from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    CoverageRequirement,
    CoverageStatus,
)
from invest_research.domain.models import AnnualComparisonInputFingerprint, AnnualComparisonPack
from invest_research.financial.annual_comparison import compute_annual_comparison
from invest_research.financial.concept_mapping import (
    CONCEPTS_V1_PATH,
    load_concept_mapping,
)
from invest_research.infrastructure.annual_company_facts_pipeline import AnnualCompanyFactsSelection
from invest_research.infrastructure.annual_document_pipeline import AnnualParsedDocument
from invest_research.infrastructure.annual_evidence_fanout import AnnualEvidenceBundle
from invest_research.infrastructure.annual_llm_fact_extraction import LLMFactExtractor
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore

# L2 指标中文名映射（供 LLM 提取 prompt 使用）。
_METRIC_LABELS: dict[str, str] = {
    "gross_margin": "毛利率",
    "asset_liability_ratio": "资产负债率",
    "current_ratio": "流动比率",
    "net_margin": "净利率",
    "operating_margin": "营业利润率",
    "return_on_assets": "资产收益率",
    "operating_cash_flow_ratio": "经营现金流比率",
    "free_cash_flow": "自由现金流",
    "revenue_growth": "收入增长率",
    "net_income_growth": "净利润增长率",
}
# L2 指标对应的 10-K 报表行英文名（供 excerpt 验证；不匹配指标名的 metric_name）。
_METRIC_EN: dict[str, str] = {
    "gross_margin": "gross profit",
    "asset_liability_ratio": "total liabilities",
    "current_ratio": "current liabilities",
    "net_margin": "net income",
    "operating_margin": "operating income",
    "return_on_assets": "total assets",
    "operating_cash_flow_ratio": "operating activities",
    "free_cash_flow": "free cash flow",
    "revenue_growth": "net sales",
    "net_income_growth": "net income",
}


class AnnualComparisonArtifactResult(BaseModel):
    """持久化比较包与其工件引用。"""

    model_config = {"frozen": True}

    pack: AnnualComparisonPack
    artifact: ArtifactRef
    reused: bool = False


class AnnualComparisonBuilder:
    """仅从 P07-04 的已验证工件构建财务比较包。"""

    def __init__(
        self,
        artifact_root: Path,
        *,
        fact_extractor: LLMFactExtractor | None = None,
    ) -> None:
        self._artifact_root = artifact_root
        self._fact_extractor = fact_extractor

    def build(
        self,
        *,
        job_id: UUID,
        selection: AnnualFilingSelection,
        evidence: AnnualEvidenceBundle,
    ) -> AnnualComparisonArtifactResult:
        store = ArtifactStore(self._artifact_root / str(job_id))
        mapping = load_concept_mapping(CONCEPTS_V1_PATH)
        fingerprint = self._fingerprint(selection, evidence, mapping.version)
        artifact_key = "annual/comparison.json"
        cached = self._read_cached(store, artifact_key, fingerprint)
        if cached is not None:
            return cached

        selected = self._load_selected_facts(store, evidence)
        if not self._financial_base_ready(selection, evidence) or selected is None:
            reasons = self._blocked_reasons(selection, evidence, selected)
            pack = AnnualComparisonPack(
                status=AnnualComparisonStatus.BLOCKED,
                target_fiscal_year=selection.target_fiscal_year,
                comparator_fiscal_year=selection.comparator_fiscal_year,
                target_accession_number=(
                    selection.target_filing.accession_number if selection.target_filing else None
                ),
                comparator_accession_number=(
                    selection.comparator_filing.accession_number
                    if selection.comparator_filing
                    else None
                ),
                concept_mapping_version=mapping.version,
                input_fingerprint=fingerprint,
                limitations=tuple(reasons),
            )
            return self._write(store, artifact_key, pack)

        target = selection.target_filing
        assert target is not None and target.report_period is not None
        target_year = selection.target_fiscal_year
        assert target_year is not None
        comparator_year = selection.comparator_fiscal_year or target_year - 1
        comparator = selection.comparator_filing
        calculation = compute_annual_comparison(
            list(selected.facts),
            mapping=mapping,
            job_id=str(job_id),
            target_fiscal_year=target_year,
            target_accession=target.accession_number,
            target_report_date=target.report_period,
            comparator_fiscal_year=comparator_year,
            comparator_accession=comparator.accession_number if comparator else None,
            comparator_report_date=comparator.report_period if comparator else None,
        )
        metrics = list(calculation.metrics)
        limitations = list(calculation.limitations)
        # L2：L1 推导后仍缺失的指标，用 LLM 从 10-K 原文提取（best-effort、强验证、可追溯）。
        if self._fact_extractor is not None:
            parsed = self._load_target_parsed(store, selection)
            if parsed is not None:
                for index, metric in enumerate(metrics):
                    if metric.value is not None:
                        continue
                    label_cn = _METRIC_LABELS.get(metric.metric_name, metric.metric_name)
                    label_en = _METRIC_EN.get(metric.metric_name, metric.metric_name)
                    extracted = self._fact_extractor.extract(
                        blocks=parsed.blocks,
                        metric_name=metric.metric_name,
                        label_cn=label_cn,
                        label_en=label_en,
                    )
                    if extracted is not None:
                        metrics[index] = metric.model_copy(
                            update={
                                "value": extracted.value,
                                "explanation": (
                                    f"{metric.explanation or ''}；由 10-K 原文提取，"
                                    f"locator={extracted.locator}"
                                ).strip("；"),
                            }
                        )
                        limitations.append(
                            f"{metric.metric_name}: 由 10-K 原文提取（locator={extracted.locator}）"
                        )
                    else:
                        limitations.append(
                            f"{metric.metric_name}: LLM_REJECTED 无法从 10-K 原文验证提取"
                        )
        status = (
            AnnualComparisonStatus.READY
            if all(metric.value is not None for metric in metrics)
            else AnnualComparisonStatus.PARTIAL
        )
        pack = AnnualComparisonPack(
            status=status,
            target_fiscal_year=target_year,
            comparator_fiscal_year=comparator_year,
            target_accession_number=target.accession_number,
            comparator_accession_number=calculation.comparator_accession_number,
            concept_mapping_version=mapping.version,
            input_fingerprint=fingerprint,
            facts=calculation.facts,
            metrics=metrics,
            limitations=limitations,
        )
        return self._write(store, artifact_key, pack)

    def _load_target_parsed(
        self, store: ArtifactStore, selection: AnnualFilingSelection
    ) -> AnnualParsedDocument | None:
        """读 target 10-K 的解析块（供 L2 LLM 提取）；缺失/损坏返回 None。"""
        target = selection.target_filing
        if target is None or target.accession_number is None:
            return None
        try:
            content = store.read(f"annual/{target.accession_number}/parsed.json")
        except KeyError:
            return None
        try:
            return AnnualParsedDocument.model_validate_json(content)
        except ValueError:
            return None

    @staticmethod
    def _json_bytes(model: BaseModel) -> bytes:
        return json.dumps(
            model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _ref_for(store: ArtifactStore, key: str) -> ArtifactRef:
        content = store.read(key)
        return ArtifactRef(
            artifact_key=key,
            byte_size=len(content),
            content_checksum=hashlib.sha256(content).hexdigest(),
        )

    def _write(
        self, store: ArtifactStore, artifact_key: str, pack: AnnualComparisonPack
    ) -> AnnualComparisonArtifactResult:
        ref = store.write(artifact_key, self._json_bytes(pack), overwrite=True)
        return AnnualComparisonArtifactResult(pack=pack, artifact=ref)

    def _read_cached(
        self,
        store: ArtifactStore,
        key: str,
        fingerprint: AnnualComparisonInputFingerprint,
    ) -> AnnualComparisonArtifactResult | None:
        try:
            content = store.read(key)
            pack = AnnualComparisonPack.model_validate_json(content)
        except (KeyError, ValueError):
            return None
        if pack.input_fingerprint != fingerprint:
            return None
        return AnnualComparisonArtifactResult(
            pack=pack, artifact=self._ref_for(store, key), reused=True
        )

    @staticmethod
    def _fingerprint(
        selection: AnnualFilingSelection,
        evidence: AnnualEvidenceBundle,
        mapping_version: str,
    ) -> AnnualComparisonInputFingerprint:
        facts = evidence.company_facts
        target_document = evidence.target_document
        comparator_document = evidence.comparator_document
        return AnnualComparisonInputFingerprint(
            company_facts_artifact_key=(
                facts.selected_artifact.artifact_key if facts and facts.selected_artifact else None
            ),
            company_facts_checksum=(
                facts.selected_artifact.content_checksum
                if facts and facts.selected_artifact
                else None
            ),
            target_document_checksum=(
                target_document.parsed_artifact.content_checksum
                if target_document and target_document.parsed_artifact
                else None
            ),
            comparator_document_checksum=(
                comparator_document.parsed_artifact.content_checksum
                if comparator_document and comparator_document.parsed_artifact
                else None
            ),
            target_accession_number=(
                selection.target_filing.accession_number if selection.target_filing else None
            ),
            comparator_accession_number=(
                selection.comparator_filing.accession_number
                if selection.comparator_filing
                else None
            ),
            concept_mapping_version=mapping_version,
        )

    @staticmethod
    def _financial_base_ready(
        selection: AnnualFilingSelection, evidence: AnnualEvidenceBundle
    ) -> bool:
        if selection.target_filing is None or selection.target_fiscal_year is None:
            return False
        statuses = {item.requirement: item.status for item in evidence.coverage_ledger.items}
        return all(
            statuses.get(requirement) is CoverageStatus.SATISFIED
            for requirement in (
                CoverageRequirement.TARGET_ANNUAL_FILING,
                CoverageRequirement.TARGET_FINANCIAL_FACTS,
                CoverageRequirement.COMPARATOR_FINANCIAL_FACTS,
            )
        )

    @staticmethod
    def _load_selected_facts(
        store: ArtifactStore, evidence: AnnualEvidenceBundle
    ) -> AnnualCompanyFactsSelection | None:
        facts = evidence.company_facts
        if facts is None or facts.selected_artifact is None:
            return None
        try:
            content = store.read(facts.selected_artifact.artifact_key)
        except KeyError:
            return None
        if (
            len(content) != facts.selected_artifact.byte_size
            or hashlib.sha256(content).hexdigest() != facts.selected_artifact.content_checksum
        ):
            return None
        try:
            return AnnualCompanyFactsSelection.model_validate_json(content)
        except ValueError:
            return None

    @staticmethod
    def _blocked_reasons(
        selection: AnnualFilingSelection,
        evidence: AnnualEvidenceBundle,
        selected: AnnualCompanyFactsSelection | None,
    ) -> list[str]:
        reasons: list[str] = []
        if selection.target_filing is None:
            reasons.append(selection.missing_reason or "目标年度 10-K 不可用")
        statuses = {item.requirement: item for item in evidence.coverage_ledger.items}
        for requirement in (
            CoverageRequirement.TARGET_ANNUAL_FILING,
            CoverageRequirement.TARGET_FINANCIAL_FACTS,
            CoverageRequirement.COMPARATOR_FINANCIAL_FACTS,
        ):
            item = statuses.get(requirement)
            if item is None or item.status is not CoverageStatus.SATISFIED:
                reasons.append(
                    item.missing_reason if item and item.missing_reason else f"缺少 {requirement}"
                )
        if selected is None:
            reasons.append("Company Facts 筛选工件缺失、损坏或 checksum 不一致")
        return list(dict.fromkeys(reasons)) or ["年度财务比较的基础证据未满足"]
