"""P06-11G: per-job closures bind current job components."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.infrastructure.flow_wiring import (
    JobResearchComponents,
    LiveResearchFlowRunner,
)
from invest_research.infrastructure.performance import PerformanceRecorder
from invest_research.infrastructure.prefetch import PrefetchResult
from invest_research.infrastructure.real_tools import _cached_execute
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache


def _config() -> LLMConfig:
    return LLMConfig(vendor="qwen", api_key=SecretStr("sk-test"),
        base_url="https://example.com", model_research="qwen-test",
        model_analysis="qwen-test", model_writer="qwen-test")


def _request(ticker: str) -> ResearchRequest:
    return ResearchRequest(input_company=ticker, as_of_date=date(2025, 12, 31))


def _pack() -> tuple[ResearchPack, FinancialAnalysisPack, ReportDraft]:
    as_of = date(2025, 12, 31)
    identity = CompanyIdentity(cik="0000789019", legal_name="X Corp")
    research = ResearchPack(version="research_pack_v1",
        company_identity=identity, as_of_date=as_of,
        sources=[Source(source_type=SourceType.SEC_FILING,
            canonical_url="https://example.com/filing", title="10-K",
            accessed_at=as_of, locator="10-K")])
    analysis = FinancialAnalysisPack(version="analysis_pack_v1", period_end=as_of,
        facts=[FinancialFact(company_id="0000789019", source_id="fake-source",
            taxonomy="us-gaap", concept="Revenue", value=100, unit="USD",
            period_start=date(2024, 1, 1), period_end=as_of)], analysis_notes="fake")
    draft = ReportDraft(version="report_draft_v1", title="X",
        markdown="# x\n## exec\ncontent", citation_keys=["k1"])
    return research, analysis, draft


def _make() -> JobResearchComponents:
    cache = ToolCallCache()
    recorder = PerformanceRecorder()
    budget = ToolBudget()
    stats: dict[str, int] = {}

    def _serialize(result: Any) -> str:
        return "ok"

    def sec_submissions(cik: str, as_of_date: str, requested_forms: str = "10-K,10-Q") -> str:
        return _cached_execute(cache=cache, recorder=recorder,
            tool_name="sec_submissions",
            params={"cik": cik, "as_of_date": as_of_date, "requested_forms": requested_forms},
            serialize_fn=_serialize,
            execute_fn=lambda: SimpleNamespace(kind="success",
                value=SimpleNamespace(model_dump=lambda mode="json": {"filings": []})),
            budget=budget)

    def prefetch(request: ResearchRequest) -> PrefetchResult | None:
        identity = CompanyIdentity(cik="0000789019", legal_name="x")
        key = cache.key("sec_submissions", {"cik": identity.cik,
            "as_of_date": request.as_of_date.isoformat(), "requested_forms": "10-K,10-Q"})
        cache.put(key, '{"ok": true, "filings": []}')
        return PrefetchResult(company_identity=identity, submissions_summary="s",
            search_summary="se", status="ok", financial_facts_summary=None)

    return JobResearchComponents(research_tools=[sec_submissions], cache=cache,
        recorder=recorder, budget=budget, prefetch=prefetch, stats=stats)


def test_closures_bind_current_job_components(tmp_path_factory: pytest.TempPathFactory) -> None:
    factory_calls: list[JobResearchComponents] = []
    holder: dict[str, Any] = {}

    def factory() -> JobResearchComponents:
        components = _make()
        factory_calls.append(components)
        return components

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            runner = holder["runner"]
            holder["cache"] = runner._cache  # noqa: SLF001
            holder["budget"] = runner._budget  # noqa: SLF001
            holder["stats"] = runner._stats  # noqa: SLF001
            return SimpleNamespace(tasks_output=list(_pack()), token_usage=None)

    runner = LiveResearchFlowRunner(config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("p06_11g")),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
        component_factory=factory)
    holder["runner"] = runner

    runner.run(_request("MSFT"))
    assert holder["cache"] is factory_calls[0].cache
    assert holder["budget"] is factory_calls[0].budget
    assert holder["stats"] is factory_calls[0].stats

    runner.run(_request("AAPL"))
    assert holder["cache"] is factory_calls[1].cache
    assert holder["budget"] is factory_calls[1].budget
    assert holder["stats"] is factory_calls[1].stats
    assert factory_calls[0].cache is not factory_calls[1].cache
    assert factory_calls[0].budget is not factory_calls[1].budget
    assert factory_calls[0].recorder is not factory_calls[1].recorder
    assert factory_calls[0].stats is not factory_calls[1].stats
