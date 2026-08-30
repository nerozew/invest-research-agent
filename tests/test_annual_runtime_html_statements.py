"""Task 3：``_financial_statements_markdown`` 优先 HTML 原表，XBRL 回退。

构造一个带 ``target_document.source_artifact`` 的 ``AnnualEvidenceBundle``，
把含三张报表表格的 source.html 写入 ArtifactStore，验证报表章节走 HTML 原表
（``## 财务报表`` + 中文行名 ``净利润``，且不含 XBRL 注释文本）。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import cast

from invest_research.domain.annual_pipeline import (
    AnnualComparisonStatus,
    AnnualReadiness,
    CoverageItem,
    CoverageLedger,
    CoverageRequirement,
    CoverageStatus,
)
from invest_research.domain.models import (
    AnnualComparisonInputFingerprint,
    AnnualComparisonPack,
)
from invest_research.infrastructure.annual_comparison_builder import AnnualComparisonBuilder
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualDocumentArtifactResult,
    AnnualDocumentPipelineStatus,
)
from invest_research.infrastructure.annual_evidence_fanout import (
    AnnualEvidenceBundle,
    AnnualEvidenceFanoutPipeline,
)
from invest_research.infrastructure.annual_runtime import (
    AnnualResearchRuntime,
    AnnualRuntimeComponents,
)
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore
from invest_research.tools.company_resolver import ResolveCompanyRequest
from invest_research.tools.sec_submissions import FetchAnnualFilingsRequest

_HTML_SOURCE = """
<div>NVIDIA Corporation<br>Consolidated Statements of Income</div>
<table>
<tr><td>Revenue</td><td>72,880</td></tr>
<tr><td>Net income</td><td>29,760</td></tr>
</table>
<div>NVIDIA Corporation<br>Consolidated Balance Sheets</div>
<table>
<tr><td>Total assets</td><td>200</td></tr>
</table>
<div>NVIDIA Corporation<br>Consolidated Statements of Cash Flows</div>
<table>
<tr><td>Net cash provided by operating activities</td><td>50</td></tr>
</table>
"""

_TWO_STATEMENT_HTML = """
<div>NVIDIA Corporation<br>Consolidated Statements of Income</div>
<table>
<tr><td>Revenue</td><td>72,880</td></tr>
<tr><td>Net income</td><td>29,760</td></tr>
</table>
<div>NVIDIA Corporation<br>Consolidated Balance Sheets</div>
<table>
<tr><td>Total assets</td><td>200</td></tr>
</table>
"""

_SOURCE_KEY = "annual/0001234567/source.html"


class _StubResolver:
    """占位 resolver：本测试只走 HTML 优先路径，不触碰公司解析。"""

    def execute(self, request: ResolveCompanyRequest) -> object:
        return None


class _StubFilingsFetcher:
    """占位 filings fetcher：本测试只走 HTML 优先路径，不触碰 filing 抓取。"""

    def fetch_annual_filings(self, request: FetchAnnualFilingsRequest) -> object:
        return None


def _ledger() -> CoverageLedger:
    """构造全满足的 CoverageLedger（FULL_READY），供 EvidenceBundle 使用。"""

    def item(requirement: CoverageRequirement) -> CoverageItem:
        return CoverageItem(
            requirement=requirement,
            status=CoverageStatus.SATISFIED,
            evidence_artifact_keys=(requirement.value,),
        )

    return CoverageLedger(
        target_fiscal_year=2025,
        readiness=AnnualReadiness.FULL_READY,
        items=(
            item(CoverageRequirement.TARGET_ANNUAL_FILING),
            item(CoverageRequirement.TARGET_FINANCIAL_FACTS),
            item(CoverageRequirement.COMPARATOR_FINANCIAL_FACTS),
            item(CoverageRequirement.COMPARATOR_ANNUAL_FILING),
        ),
    )


def _comparison() -> AnnualComparisonPack:
    """BLOCKED 比较包：HTML 优先路径不会触碰 XBRL 链路，故只要求合法即可。"""
    return AnnualComparisonPack(
        status=AnnualComparisonStatus.BLOCKED,
        concept_mapping_version="v1",
        input_fingerprint=AnnualComparisonInputFingerprint(concept_mapping_version="v1"),
        limitations=("仅验证 HTML 原表优先路径",),
    )


def _make_runtime(tmp_path: Path) -> AnnualResearchRuntime:
    """只注入 artifact_root 的轻量 runtime；其余组件用占位（本测试不调用）。"""
    return AnnualResearchRuntime(
        AnnualRuntimeComponents(
            resolver=_StubResolver(),
            filings_fetcher=_StubFilingsFetcher(),
            evidence_fanout=cast(AnnualEvidenceFanoutPipeline, object()),
            comparison_builder=cast(AnnualComparisonBuilder, object()),
            artifact_root=tmp_path,
        )
    )


def _evidence_with_source(
    tmp_path: Path, html: str = _HTML_SOURCE
) -> tuple[AnnualEvidenceBundle, uuid.UUID]:
    """把 source.html 写入工件库，返回带 source_artifact 的 evidence 与 job_id。"""
    job_id = uuid.uuid4()
    content = html.encode("utf-8")
    source = ArtifactStore(tmp_path / str(job_id)).write(_SOURCE_KEY, content, overwrite=True)
    assert source.artifact_key == _SOURCE_KEY
    target = AnnualDocumentArtifactResult(
        status=AnnualDocumentPipelineStatus.COMPLETED,
        accession_number="0001234567",
        source_artifact=ArtifactRef(
            artifact_key=_SOURCE_KEY,
            byte_size=len(content),
            content_checksum=hashlib.sha256(content).hexdigest(),
        ),
        manifest_artifact=ArtifactRef(
            artifact_key="annual/0001234567/manifest.json",
            byte_size=1,
            content_checksum="0" * 64,
        ),
    )
    evidence = AnnualEvidenceBundle(
        target_document=target,
        coverage_ledger=_ledger(),
    )
    return evidence, job_id


def test_html_statements_take_priority_over_xbrl(tmp_path: Path) -> None:
    """source.html 含三张原表时，报表章节用 HTML 原表而非 XBRL 事实。"""
    runtime = _make_runtime(tmp_path)
    evidence, job_id = _evidence_with_source(tmp_path)
    markdown = runtime._financial_statements_markdown(job_id, evidence, _comparison())
    assert "## 财务报表" in markdown
    assert "净利润" in markdown  # 原表行名 "Net income" 译中文
    assert "### 利润表" in markdown
    assert "### 资产负债表" in markdown
    assert "### 现金流量表" in markdown
    assert "XBRL" not in markdown  # 未走 XBRL 渲染链路


def test_html_statements_markdown_returns_none_without_source(tmp_path: Path) -> None:
    """无 target_document 时返回 None（调用方回退 XBRL）。"""
    runtime = _make_runtime(tmp_path)
    evidence = AnnualEvidenceBundle(
        target_document=None,
        coverage_ledger=_ledger(),
    )
    assert runtime._html_statements_markdown(uuid.uuid4(), evidence) is None


def test_html_statements_markdown_requires_all_three_statements(tmp_path: Path) -> None:
    """source.html 只含两张报表时返回 None（调用方回退 XBRL，而非输出残缺原表）。

    计划契约：HTML 原表提取是全有或全无——三张报表（利润表/资产负债表/现金流量表）
    任一缺失即视为找不到完整报表，回退 XBRL + L2 链路。
    """
    runtime = _make_runtime(tmp_path)
    evidence, job_id = _evidence_with_source(tmp_path, _TWO_STATEMENT_HTML)
    assert runtime._html_statements_markdown(job_id, evidence) is None
    # XBRL 回退链路被触达：BLOCKED comparison 无 accession → 报表章节为空串（未用残缺原表）。
    assert runtime._financial_statements_markdown(job_id, evidence, _comparison()) == ""
