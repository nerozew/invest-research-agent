"""P07 补充：年度主动研究 Agent（受控工具循环）测试。

核心验证：搜索 Agent 真实调用工具（搜索/下载）、有界循环、来源可追溯、
不输出财务数值。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from invest_research.domain.annual_pipeline import EvidenceKind
from invest_research.infrastructure.annual_active_research import (
    _EXTRACT_SYSTEM_PROMPT,
    ActiveResearchAgent,
    ActiveResearchStatus,
)
from invest_research.infrastructure.annual_llm_writing import AnnualLlmResult
from invest_research.infrastructure.annual_web_search_pipeline import WebSearchSectionEvidence
from invest_research.tools.artifact_store import ArtifactStore
from invest_research.tools.base import ToolSuccess
from invest_research.tools.google_search import SearchResponse, SearchResult
from invest_research.tools.sec_downloader import DownloadedDocument

JOB_ID = UUID("00000000-0000-0000-0000-000000000706")
CIK = "0000789019"
AS_OF = "2026-08-26"
COMPANY = "MSFT"
_TARGETS = (EvidenceKind.BUSINESS_OVERVIEW,)


class FakeCompletion:
    def __init__(self, plan_json: str, extract_json: str) -> None:
        self.calls: list[tuple[str, str]] = []
        self.plan_json = plan_json
        self.extract_json = extract_json

    def complete(
        self, *, role: object, system_prompt: str, user_prompt: str, max_tokens: int = 4000
    ) -> AnnualLlmResult:
        self.calls.append((system_prompt, user_prompt))
        if "请给出不超过 4 条精准" in user_prompt:
            return AnnualLlmResult(markdown=self.plan_json)
        return AnnualLlmResult(markdown=self.extract_json)


class FakeSearch:
    name = "fake_search"

    def __init__(self, items: tuple[SearchResult, ...]) -> None:
        self.items = items
        self.queries: list[str] = []

    def execute(self, request: object) -> ToolSuccess[SearchResponse]:
        query = getattr(request, "query", "")
        self.queries.append(str(query))
        return ToolSuccess(
            value=SearchResponse(items=self.items, total=len(self.items), page=1)
        )


class FakeDownloader:
    name = "fake_downloader"

    def __init__(self, html: bytes) -> None:
        self.html = html
        self.urls: list[str] = []

    def execute(self, request: object) -> ToolSuccess[DownloadedDocument]:
        url = getattr(request, "url", "")
        self.urls.append(str(url))
        return ToolSuccess(
            value=DownloadedDocument(
                content=self.html,
                media_type="text/html",
                byte_size=len(self.html),
                content_checksum=hashlib.sha256(self.html).hexdigest(),
            )
        )


def _result_item(url: str, title: str = "FIX page") -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        publisher="news.example.com",
        published_at=date(2026, 8, 1),
        accessed_at=datetime(2026, 8, 26, 12, 0, 0),
        snippet="snippet",
    )


def _web_page() -> bytes:
    return (
        b"<html><body><h1>Business</h1>"
        b"<p>Management discussed strong cloud growth and AI investment.</p>"
        b"</body></html>"
    )


def _read_section(root: Path, kind: str) -> WebSearchSectionEvidence:
    store = ArtifactStore(root / str(JOB_ID))
    return WebSearchSectionEvidence.model_validate_json(
        store.read(f"annual/active-research/{kind}.json")
    )


def test_active_research_invokes_tools_and_writes_artifacts(tmp_path: Path) -> None:
    """搜索 Agent 真实调用搜索/下载工具，并把提炼摘要写入工件。"""
    url = "https://news.example.com/msft-business"
    completion = FakeCompletion(
        plan_json=(
            '{"queries":[{"query":"MSFT business model","kind":"business_overview"}]}'
        ),
        extract_json=(
            '{"extracts":[{"kind":"business_overview","url":"' + url + '",'
            '"excerpt":"管理层表示云业务增长强劲，AI 基础设施投资将持续。"}]}'
        ),
    )
    search = FakeSearch((_result_item(url),))
    downloader = FakeDownloader(_web_page())
    agent = ActiveResearchAgent(tmp_path, completion, search, downloader)

    result = agent.run(
        job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF, targets=_TARGETS
    )

    assert result.status is ActiveResearchStatus.COMPLETED
    assert search.queries == ["MSFT business model"]  # 搜索工具真实被调用
    assert downloader.urls == [url]  # 下载工具真实被调用
    assert result.rounds == 1
    section = _read_section(tmp_path, "business_overview")
    assert section.kind == "business_overview"
    assert section.entries[0].url == url
    assert "云业务增长强劲" in section.entries[0].snippet


def test_active_research_writes_web_snapshot(tmp_path: Path) -> None:
    """WS2.5：下载解析后的网页原文留快照，支持内容级溯源（可证明引用存在于该 URL）。"""
    url = "https://news.example.com/msft-snapshot"
    completion = FakeCompletion(
        plan_json='{"queries":[{"query":"MSFT snapshot","kind":"business_overview"}]}',
        extract_json=(
            '{"extracts":[{"kind":"business_overview","url":"' + url + '",'
            '"excerpt":"快照摘要"}]}'
        ),
    )
    search = FakeSearch((_result_item(url),))
    downloader = FakeDownloader(_web_page())
    agent = ActiveResearchAgent(tmp_path, completion, search, downloader)
    result = agent.run(
        job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF, targets=_TARGETS
    )
    assert result.status is ActiveResearchStatus.COMPLETED
    store = ArtifactStore(tmp_path / str(JOB_ID))
    snapshot_key = f"annual/active-research/pages/{hashlib.sha256(url.encode()).hexdigest()}.json"
    snapshot = json.loads(store.read(snapshot_key))
    assert snapshot["url"] == url
    assert "text" in snapshot and snapshot["text"]


def test_active_research_rejects_fabricated_urls(tmp_path: Path) -> None:
    """LLM 编造的 URL（不在搜索结果里）被丢弃，不产生伪造来源。"""
    real_url = "https://news.example.com/real"
    fake_url = "https://fabricated.example.com/nope"
    completion = FakeCompletion(
        plan_json='{"queries":[{"query":"MSFT risk","kind":"risk_factors"}]}',
        extract_json=(
            '{"extracts":[{"kind":"risk_factors","url":"' + fake_url + '",'
            '"excerpt":"编造的风险摘要。"}]}'
        ),
    )
    search = FakeSearch((_result_item(real_url),))
    downloader = FakeDownloader(_web_page())
    agent = ActiveResearchAgent(tmp_path, completion, search, downloader)

    result = agent.run(
        job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF, targets=_TARGETS
    )

    # 唯一摘要是伪造 URL → 无有效工件 → FETCH_FAILED（不产生伪造证据）。
    assert result.status is ActiveResearchStatus.FETCH_FAILED
    assert result.section_artifacts == {}


def test_active_research_respects_round_budget(tmp_path: Path) -> None:
    """轮数上限到点即停：即使 LLM 一直给计划，也只执行 max_rounds 轮。"""
    url = "https://news.example.com/x"
    completion = FakeCompletion(
        plan_json='{"queries":[{"query":"MSFT a","kind":"business_overview"}]}',
        extract_json=(
            '{"extracts":[{"kind":"business_overview","url":"' + url + '",'
            '"excerpt":"摘要。"}]}'
        ),
    )
    search = FakeSearch((_result_item(url),))
    downloader = FakeDownloader(_web_page())
    agent = ActiveResearchAgent(
        tmp_path, completion, search, downloader, max_rounds=2, max_queries_per_round=1
    )

    result = agent.run(
        job_id=JOB_ID, company=COMPANY, cik=CIK, as_of_date=AS_OF, targets=_TARGETS
    )

    # 第一轮已产出 business_overview → pending 清空 → 只执行 1 轮。
    assert result.rounds == 1
    assert result.status is ActiveResearchStatus.COMPLETED
    assert len(search.queries) == 1


def test_active_research_prompts_forbid_financial_numbers(tmp_path: Path) -> None:
    """搜索 Agent 的提炼提示词明确禁止财务数值/结论。"""
    assert "禁止输出财务指标数值" in _EXTRACT_SYSTEM_PROMPT
    assert "禁止给出买入/卖出建议" in _EXTRACT_SYSTEM_PROMPT
    assert "禁止编造" in _EXTRACT_SYSTEM_PROMPT
