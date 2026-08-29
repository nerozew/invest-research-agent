"""P07-04 补充：年度网页搜索证据管道测试（确定性、可恢复、可降级）。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from invest_research.domain.errors import ErrorCode
from invest_research.infrastructure import annual_web_search_pipeline as pipeline_module
from invest_research.infrastructure.annual_web_search_pipeline import (
    WebSearchEvidencePipeline,
    WebSearchEvidenceStatus,
    WebSearchSectionEvidence,
)
from invest_research.tools.artifact_store import ArtifactStore
from invest_research.tools.base import ToolError, ToolFailure, ToolSuccess
from invest_research.tools.google_search import SearchQuery, SearchResponse, SearchResult

JOB_ID = UUID("00000000-0000-0000-0000-000000000705")
CIK = "0000789019"
AS_OF = "2026-08-26"
COMPANY = "MSFT"


class RecordingSearchTool:
    name = "recording_search"

    def __init__(
        self,
        items_by_query: dict[str, tuple[SearchResult, ...]] | None = None,
        fail_queries: frozenset[str] = frozenset(),
    ) -> None:
        self.calls = 0
        self.items_by_query = items_by_query or {}
        self.fail_queries = fail_queries

    def execute(self, request: SearchQuery) -> ToolSuccess[SearchResponse] | ToolFailure:
        self.calls += 1
        if request.query in self.fail_queries:
            return ToolFailure(
                error=ToolError(error_code=ErrorCode.UPSTREAM_5XX, message="搜索服务失败")
            )
        items = self.items_by_query.get(request.query, ())
        return ToolSuccess(
            value=SearchResponse(items=tuple(items), total=len(items), page=1)
        )


def _item(
    title: str,
    url: str,
    *,
    publisher: str | None = None,
    published: date | None = None,
    snippet: str | None = None,
) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        publisher=publisher,
        snippet=snippet,
        published_at=published or date(2026, 8, 1),
        accessed_at=datetime(2026, 8, 26, 12, 0, 0),
    )


def _default_items() -> tuple[SearchResult, ...]:
    return (
        _item(
            "MSFT IR business segments",
            "https://www.microsoft.com/investor/segments",
            publisher="microsoft.com",
            snippet="MSFT reports three business segments.",
        ),
        _item(
            "Reuters: MSFT cloud growth",
            "https://www.reuters.com/technology/msft-cloud",
            publisher="reuters.com",
        ),
        _item("Blog: MSFT analysis", "https://example.com/msft-analysis", publisher="example.com"),
        # 未来日期：按 as_of 兜底过滤
        _item("Future dated", "https://example.com/future", published=date(2026, 9, 1)),
        # 重复 URL：去重
        _item(
            "Reuters duplicate",
            "https://www.reuters.com/technology/msft-cloud",
            publisher="reuters.com",
        ),
    )


def _read_section(root: Path, kind: str) -> WebSearchSectionEvidence:
    store = ArtifactStore(root / str(JOB_ID))
    return WebSearchSectionEvidence.model_validate_json(
        store.read(f"annual/web-search/{kind}.json")
    )


def test_new_kinds_supported_and_human_readable() -> None:
    """分析师观点 / 评级机构是受支持搜索 kind，且映射为人类可读来源名。"""
    from invest_research.infrastructure.annual_web_search_pipeline import _SUPPORTED_KINDS
    from invest_research.reporting.annual_report_renderer import human_kind

    assert "analyst_opinion" in _SUPPORTED_KINDS
    assert "rating_agency" in _SUPPORTED_KINDS
    assert human_kind("analyst_opinion") == "分析师观点（网页搜索）"
    assert human_kind("rating_agency") == "评级机构（网页搜索）"


def test_persists_all_section_artifacts_and_reuses_completed(tmp_path: Path) -> None:
    tool = RecordingSearchTool(items_by_query=_default_query_map())
    pipeline = WebSearchEvidencePipeline(tmp_path, tool)
    first = pipeline.run(job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF)
    assert first.status is WebSearchEvidenceStatus.COMPLETED
    assert set(first.section_artifacts) == {
        "business_overview",
        "management_discussion",
        "material_event",
        "risk_factors",
        "analyst_opinion",
        "rating_agency",
    }
    assert first.reused_completed_artifacts is False
    calls_after_first = tool.calls

    second = pipeline.run(job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF)
    assert second.reused_completed_artifacts is True
    assert second.section_artifacts == first.section_artifacts
    assert tool.calls == calls_after_first  # 复用已完成工件，不重复搜索

    section = _read_section(tmp_path, "business_overview")
    assert section.kind == "business_overview"
    assert section.as_of_date == AS_OF
    assert len(section.entries) == 3  # 5 条 - 未来 1 - 重复 1


def test_filters_future_dates_and_deduplicates_urls(tmp_path: Path) -> None:
    pipeline = WebSearchEvidencePipeline(
        tmp_path, RecordingSearchTool(items_by_query=_default_query_map())
    )
    pipeline.run(job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF)
    section = _read_section(tmp_path, "business_overview")
    urls = [entry.url for entry in section.entries]
    assert urls == [
        "https://www.microsoft.com/investor/segments",
        "https://www.reuters.com/technology/msft-cloud",
        "https://example.com/msft-analysis",
    ]
    assert all(
        entry.published_at is not None and entry.published_at <= date.fromisoformat(AS_OF)
        for entry in section.entries
    )


def test_credibility_grading(tmp_path: Path) -> None:
    business_query = pipeline_module._SEARCH_TEMPLATES[0][1].format(company=COMPANY)
    items = (
        _item("IR", "https://www.microsoft.com/investor/x", publisher="microsoft.com"),
        _item("Reuters", "https://www.reuters.com/x", publisher="reuters.com"),
        _item("Sec", "https://www.sec.gov/x", publisher="sec.gov"),
        _item("Blog", "https://blog.example.com/x", publisher="blog.example.com"),
    )
    pipeline = WebSearchEvidencePipeline(
        tmp_path, RecordingSearchTool(items_by_query={business_query: items})
    )
    pipeline.run(job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF)
    section = _read_section(tmp_path, "business_overview")
    assert {entry.title: entry.credibility for entry in section.entries} == {
        "IR": "authoritative",
        "Reuters": "authoritative",
        "Sec": "authoritative",
        "Blog": "general",
    }


def test_single_section_failure_skips_only_that_kind(tmp_path: Path) -> None:
    fail_query = pipeline_module._SEARCH_TEMPLATES[3][1].format(company=COMPANY)  # risk
    pipeline = WebSearchEvidencePipeline(
        tmp_path,
        RecordingSearchTool(
            items_by_query=_default_query_map(),
            fail_queries=frozenset({fail_query}),
        ),
    )
    result = pipeline.run(job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF)
    assert result.status is WebSearchEvidenceStatus.COMPLETED
    assert "risk_factors" not in result.section_artifacts
    assert "business_overview" in result.section_artifacts


def test_all_sections_failed_writes_fetch_failed_manifest(tmp_path: Path) -> None:
    all_queries = frozenset(
        template.format(company=COMPANY) for _, template, _ in pipeline_module._SEARCH_TEMPLATES
    )
    tool = RecordingSearchTool(fail_queries=all_queries)
    pipeline = WebSearchEvidencePipeline(tmp_path, tool)
    result = pipeline.run(job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF)
    assert result.status is WebSearchEvidenceStatus.FETCH_FAILED
    assert result.failure is not None
    assert result.section_artifacts == {}


def _default_query_map() -> dict[str, tuple[SearchResult, ...]]:
    return {
        template.format(company=COMPANY): _default_items()
        for _, template, _ in pipeline_module._SEARCH_TEMPLATES
    }
