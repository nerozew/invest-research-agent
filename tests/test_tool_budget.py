"""P05.5-fix ToolBudget（每 Job 工具硬上限）测试。"""

from __future__ import annotations

from invest_research.infrastructure.tool_budget import (
    DEFAULT_TOOL_CAPS,
    ToolBudget,
)


def test_default_caps_match_requirement() -> None:
    """默认硬上限：CompanyResolver≤1、SECSubmissions≤2、Facts≤1、WebSearch≤2、Downloader≤2。"""
    assert DEFAULT_TOOL_CAPS["company_resolver"] == 1
    assert DEFAULT_TOOL_CAPS["sec_submissions"] == 2
    assert DEFAULT_TOOL_CAPS["sec_company_facts"] == 1
    assert DEFAULT_TOOL_CAPS["web_search"] == 2
    assert DEFAULT_TOOL_CAPS["filing_downloader"] == 2


def test_try_acquire_respects_cap() -> None:
    budget = ToolBudget(caps={"sec_submissions": 2})
    assert budget.try_acquire("sec_submissions") is True
    assert budget.try_acquire("sec_submissions") is True
    assert budget.try_acquire("sec_submissions") is False  # 超限
    assert budget.used("sec_submissions") == 2


def test_uncapped_tool_never_exhausted() -> None:
    budget = ToolBudget(caps={"sec_submissions": 1})
    for _ in range(100):
        assert budget.try_acquire("document_parser") is True  # 未列出的工具无上限


def test_budget_is_per_job_not_shared() -> None:
    """两个 Job 各自独立的 ToolBudget，互不影响（无全局计数）。"""
    budget_a = ToolBudget(caps={"web_search": 2})
    budget_b = ToolBudget(caps={"web_search": 2})
    assert budget_a.try_acquire("web_search") is True
    assert budget_a.try_acquire("web_search") is True
    assert budget_a.try_acquire("web_search") is False
    # B 完全不受 A 影响
    assert budget_b.try_acquire("web_search") is True
    assert budget_b.used("web_search") == 1


def test_snapshot_reports_usage() -> None:
    budget = ToolBudget(caps={"sec_submissions": 2, "web_search": 2})
    budget.try_acquire("sec_submissions")
    budget.try_acquire("web_search")
    budget.try_acquire("web_search")
    assert budget.snapshot() == {"sec_submissions": 1, "web_search": 2}
