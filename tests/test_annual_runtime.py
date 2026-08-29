"""Offline integration of the real annual runtime, storage, gates and publisher.

Only the external SEC tools and completion boundary are fixtures. Network access
is forbidden, including accidental calls through an uninjected production client.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from collections import Counter
from datetime import date, datetime
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from invest_research.agents.llm_factory import LLMRole
from invest_research.application.annual_node_progress import AnnualNodeProgressService
from invest_research.domain.annual_pipeline import ResearchNodeStatus
from invest_research.domain.models import CompanyIdentity, ResearchRequest
from invest_research.infrastructure.annual_active_research import ActiveResearchAgent
from invest_research.infrastructure.annual_company_facts_pipeline import (
    AnnualCompanyFactsArtifactPipeline,
)
from invest_research.infrastructure.annual_comparison_builder import AnnualComparisonBuilder
from invest_research.infrastructure.annual_document_pipeline import AnnualDocumentArtifactPipeline
from invest_research.infrastructure.annual_evidence_fanout import AnnualEvidenceFanoutPipeline
from invest_research.infrastructure.annual_llm_writing import AnnualLlmResult, AnnualSectionExecutor
from invest_research.infrastructure.annual_runtime import (
    AnnualResearchRuntime,
    AnnualRuntimeBlocked,
    AnnualRuntimeComponents,
)
from invest_research.infrastructure.annual_web_search_pipeline import WebSearchEvidencePipeline
from invest_research.infrastructure.db.annual_node_store import SqlAnnualNodeStore
from invest_research.infrastructure.db.models import Base, ResearchJob
from invest_research.reporting.artifact_publisher import ReportArtifactPublisher
from invest_research.tools.base import ToolSuccess
from invest_research.tools.company_resolver import ResolveCompanyResponse
from invest_research.tools.google_search import SearchQuery, SearchResponse, SearchResult
from invest_research.tools.sec_company_facts import FetchFactsResponse
from invest_research.tools.sec_downloader import DownloadedDocument
from invest_research.tools.sec_submissions import FetchSubmissionsResponse
from tests.test_annual_comparison_builder import _facts, _selection


def _raw_facts(*, missing_year: int | None = None) -> bytes:
    concepts: dict = {}
    for fact in _facts():
        if fact.fiscal_year == missing_year:
            continue
        entry = {
            "fy": fact.fiscal_year,
            "fp": "FY",
            "accn": fact.accession_number,
            "form": fact.form_type,
            "filed": f"{fact.fiscal_year + 1}-02-15",
            "end": str(fact.period_end or fact.instant_date),
            "val": int(fact.value),
        }
        if fact.period_start:
            entry["start"] = str(fact.period_start)
        concepts.setdefault(fact.concept, {"units": {"USD": []}})["units"]["USD"].append(entry)
    return json.dumps({"facts": {"us-gaap": concepts}}).encode()


class OfflineSec:
    def __init__(self, *, comparator=True, no_target=False, missing_year=None):
        selected = _selection(comparator=comparator)
        self.filings = [] if no_target else [
            filing for filing in (selected.target_filing, selected.comparator_filing) if filing
        ]
        self.raw = _raw_facts(missing_year=missing_year)
        self.calls = Counter()
        self.lock = Lock()
        self.barrier = None

    def execute(self, request):
        if hasattr(request, "input_company"):
            return ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=[
                CompanyIdentity(cik="0000789019", ticker="FIX", legal_name="Offline Fixture")
            ]))
        kind = "download" if hasattr(request, "url") else "facts"
        with self.lock:
            self.calls[kind] += 1
        if self.barrier:
            self.barrier.wait(timeout=10)
        if kind == "facts":
            return ToolSuccess(value=FetchFactsResponse(
                facts=[], source_content=self.raw,
                source_checksum=hashlib.sha256(self.raw).hexdigest(),
            ))
        content = (
            b"<html><body><h1>Item 1 Business</h1><p>NARRATIVE_SENTINEL product business.</p>"
            b"<h1>Item 1A Risk Factors</h1><p>NARRATIVE_SENTINEL risk uncertainty.</p>"
            b"</body></html>"
        )
        return ToolSuccess(value=DownloadedDocument(
            content=content, media_type="text/html", byte_size=len(content),
            content_checksum=hashlib.sha256(content).hexdigest(),
        ))

    def fetch_annual_filings(self, _request):
        return ToolSuccess(value=FetchSubmissionsResponse(filings=self.filings))


class OfflineWeb:
    name = "offline_web"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _request: SearchQuery) -> ToolSuccess[SearchResponse]:
        self.calls += 1
        items = (
            SearchResult(
                title="FIX web business product",
                url="https://news.example.com/fix-business",
                publisher="news.example.com",
                published_at=date(2025, 10, 1),
                accessed_at=datetime(2025, 10, 31, 12, 0, 0),
                snippet="FIX business web evidence snippet.",
            ),
            SearchResult(
                title="FIX web risk signal",
                url="https://news.example.com/fix-risk",
                publisher="news.example.com",
                published_at=date(2025, 10, 2),
                accessed_at=datetime(2025, 10, 31, 12, 0, 0),
                snippet="FIX risk web evidence snippet.",
            ),
            SearchResult(
                title="FIX web event announcement",
                url="https://news.example.com/fix-event",
                publisher="news.example.com",
                published_at=date(2025, 10, 3),
                accessed_at=datetime(2025, 10, 31, 12, 0, 0),
                snippet="FIX event web evidence snippet.",
            ),
        )
        return ToolSuccess(value=SearchResponse(items=items, total=len(items), page=1))


class FakeActiveCompletion:
    """给 ActiveResearchAgent 用的 completion：返回查询计划 + 提取结果 JSON。"""

    def complete(
        self, *, role: object, system_prompt: str, user_prompt: str, max_tokens: int = 4000
    ) -> AnnualLlmResult:
        if "请给出不超过 4 条精准" in user_prompt:
            return AnnualLlmResult(
                markdown='{"queries":[{"query":"FIX active research","kind":"business_overview"}]}'
            )
        return AnnualLlmResult(
            markdown=(
                '{"extracts":[{"kind":"business_overview",'
                '"url":"https://news.example.com/fix-business",'
                '"excerpt":"补证发现的管理层讨论摘要。"}]}'
            )
        )


class FakeActiveDownloader:
    name = "fake_active_downloader"

    def execute(self, _request: object) -> ToolSuccess[DownloadedDocument]:
        content = b"<html><body><p>Active research page content.</p></body></html>"
        return ToolSuccess(
            value=DownloadedDocument(
                content=content,
                media_type="text/html",
                byte_size=len(content),
                content_checksum=hashlib.sha256(content).hexdigest(),
            )
        )


class OfflineCompletion:
    def __init__(self):
        self.calls = []
        self.lock = Lock()
        self.barrier = None
        self.analysis_done = False
        self.missing_business_citations = 0

    def complete(self, *, role, system_prompt, user_prompt, max_tokens=4000):
        final = "# 已通过门禁的章节" in user_prompt
        stage = "final" if final else "analysis" if role == LLMRole.ANALYSIS else (
            "business" if "# 章节\n业务概览" in user_prompt else
            "risk" if "# 章节\n风险因素" in user_prompt else
            "event" if "# 章节\n重大事件" in user_prompt else "financial"
        )
        with self.lock:
            self.calls.append((stage, role, user_prompt))
        if stage == "financial":
            assert self.analysis_done, "Financial writing must follow analysis"
        if self.barrier and stage in {"analysis", "business", "risk"}:
            self.barrier.wait(timeout=10)
        if stage == "analysis":
            self.analysis_done = True
        refs = list(dict.fromkeys(re.findall(r"\[((?:src_|fr_)[A-Za-z0-9_]+)\]", user_prompt)))
        if final:
            sections = user_prompt.split("# 已通过门禁的章节\n", 1)[1].split("\n\n# 数据限制", 1)[0]
            return AnnualLlmResult(
                markdown="## 执行摘要\n年度报告仅解释已验证的章节内容。\n\n" + sections
            )
        heading = {
            "analysis": "财务分析",
            "financial": "财务表现",
            "business": "业务概览",
            "risk": "风险因素",
            "event": "重大事件",
        }[stage]
        if stage == "business" and self.missing_business_citations:
            self.missing_business_citations -= 1
            refs = []
        prose = (
            "本节仅依据已授权材料解释业务情况，指标沿用确定性计算结果，不引入外部信息。"
            "现有资料只能支持所列范围的分析，未覆盖事项不能据此推断。"
            "报告披露证据边界，并保留来源以供复核。"
        )
        return AnnualLlmResult(
            markdown=f"## {heading}\n{prose}\n" + " ".join(f"[{key}]" for key in refs)
        )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    def no_network(*_args, **_kwargs):
        pytest.fail("Offline runtime attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    # Separate connections, not StaticPool: sections write their nodes concurrently.
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}", connect_args={"timeout": 20})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=True)

    def build(**options):
        web_enabled = bool(options.pop("web", False))
        active_enabled = bool(options.pop("active", False))
        job_id = uuid4()
        with factory() as session:
            session.add(ResearchJob(id=job_id, input_company="FIX", as_of_date=date(2025, 10, 31),
                                    requested_forms=["10-K"], research_mode="annual_deep"))
            session.commit()
        sec, completion = OfflineSec(**options), OfflineCompletion()
        web_tool: OfflineWeb | None = None
        web_pipeline = None
        if web_enabled:
            web_tool = OfflineWeb()
            web_pipeline = WebSearchEvidencePipeline(tmp_path, web_tool)
        active_research = None
        active_web: OfflineWeb | None = None
        if active_enabled:
            active_web = OfflineWeb()
            active_research = ActiveResearchAgent(
                tmp_path, FakeActiveCompletion(), active_web, FakeActiveDownloader()
            )
        progress = AnnualNodeProgressService(SqlAnnualNodeStore(factory))
        runtime = AnnualResearchRuntime(AnnualRuntimeComponents(
            resolver=sec, filings_fetcher=sec,
            evidence_fanout=AnnualEvidenceFanoutPipeline(
                AnnualDocumentArtifactPipeline(tmp_path, sec),
                AnnualCompanyFactsArtifactPipeline(tmp_path, sec),
                web_search_pipeline=web_pipeline,
                artifact_root=tmp_path,
            ),
            comparison_builder=AnnualComparisonBuilder(tmp_path), artifact_root=tmp_path,
            progress=progress, section_executor=AnnualSectionExecutor(tmp_path, completion),
            active_research=active_research,
        ))
        request = ResearchRequest(input_company="FIX", as_of_date=date(2025, 10, 31),
                                  requested_forms=["10-K"], research_mode="annual_deep",
                                  research_profile="deep")
        return SimpleNamespace(runtime=runtime, job_id=job_id, request=request, sec=sec,
                               completion=completion, progress=progress,
                               root=tmp_path / str(job_id),
                               publisher=ReportArtifactPublisher(tmp_path),
                               web=web_tool,
                               active_web=active_web)

    yield build
    engine.dispose()


def _run(h):
    return h.runtime.run(job_id=h.job_id, request=h.request)


def test_complete_runtime_parallel_permissions_and_real_publication(harness):
    h = harness()
    h.sec.barrier = Barrier(3)
    h.completion.barrier = Barrier(3)
    state = _run(h)
    published = h.publisher.publish(h.job_id, state)
    assert len(published) == 2
    assert h.sec.calls == {"download": 2, "facts": 1}
    assert Counter(stage for stage, _, _ in h.completion.calls) == {
        "analysis": 1, "business": 1, "risk": 1, "financial": 1, "final": 1,
    }
    for stage, _, prompt in h.completion.calls:
        if stage in {"analysis", "financial", "final"}:
            assert "NARRATIVE_SENTINEL" not in prompt
        if stage in {"business", "risk", "final"}:
            assert "concept=Revenues" not in prompt
            assert "# 可引用原始事实" not in prompt
    nodes = h.progress.snapshot(job_id=h.job_id).nodes
    assert all(node.status == ResearchNodeStatus.SUCCEEDED for node in nodes)
    assert (h.root / "08_report.md").read_text(encoding="utf-8").find("数据限制") >= 0
    with fitz.open(h.root / "09_report.pdf") as document:
        assert document.page_count > 0
    comparison = json.loads((h.root / "annual/comparison.json").read_bytes())
    assert len(comparison["metrics"]) == 10
    # 回归 annual_runtime._build_state：citation_keys 提取自原始 [src_xxx] 正文，
    # 链接化展示不得破坏引用提取（每个 key 必须仍是合法格式）。
    assert state.report_draft.citation_keys
    assert all(
        re.fullmatch(r"(?:src|fr)_[0-9a-f]{12}", key)
        for key in state.report_draft.citation_keys
    )
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    # 封面时间信息（优化 3）。
    assert "分析基准日" in report
    assert "财报期间" in report
    assert "报告生成时间" in report
    # 来源清单是 [标题](url) 链接形式，不再有裸 `- [src_` 行（优化 1）。
    assert "](https://" in report
    assert "- [src_" not in report
    # 年度 PDF 的 [text](url) 链接可点击（link annotation，优化 1/f）。
    with fitz.open(h.root / "09_report.pdf") as document:
        uris = {link.get("uri") for page in document for link in page.get_links()}
    assert any(uri.startswith("https://") for uri in uris)


def test_report_includes_financial_statements_section(harness):
    """报告包含三张财务报表章节，数字直取 SEC 事实（未经 LLM 改写）。"""
    h = harness()
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    assert "## 财务报表" in report
    assert "### 资产负债表" in report
    assert "### 利润表" in report
    assert "### 现金流量表" in report
    # 资产总计 = 200，来自 OfflineSec 的 SEC 事实（Assets=200），原封不动。
    assert "| 资产总计 | 200" in report


def test_annual_cover_official_statements_degrade_gracefully(harness):
    """OfflineSec 的 10-K fixture 无封面/审计意见文本 → 官方声明降级为空，报告仍发布。"""
    h = harness()
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    # 封面概览仍存在（优化 3），但官方声明/MD&A 未命中时整段省略（best-effort 不阻塞）。
    assert "## 报告概览" in report
    assert "官方声明摘录" not in report
    assert "302" not in report
    assert "## 管理层讨论与分析" not in report  # fixture 10-K 无 MD&A → 降级为空
    with fitz.open(h.root / "09_report.pdf") as document:
        assert document.page_count > 0


def test_report_source_list_uses_human_titles(harness):
    """论文式引用列表：`[n] [标题](url)` 可点击；SEC 标题回退为稳定短标签。"""
    h = harness()
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    ref_section = report.split("## 引用", 1)[1]
    assert "- [src_" not in ref_section
    assert "[目标年度 10-K](" in ref_section  # SEC 工件标题回退（human_kind）
    assert "](https://" in ref_section


def test_web_sources_are_marked_as_web_type(harness):
    """修复：web 证据不应再被错标为 SEC_XBRL。"""
    h = harness(web=True)
    state = _run(h)
    web_sources = [
        s for s in state.research_pack.sources
        if str(s.canonical_url).startswith("https://news.example.com")
    ]
    assert web_sources
    assert all(s.source_type.value == "web" for s in web_sources)


def test_complete_runtime_with_web_search_adds_narrative_evidence(harness):
    """搜索证据作为增强进入业务/风险章节；10-K 原文保留，搜索是叠加不是替代。"""
    h = harness(web=True)
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    assert h.web is not None and h.web.calls > 0
    business_prompts = [p for stage, _, p in h.completion.calls if stage == "business"]
    assert any("FIX web business product" in p for p in business_prompts)
    risk_prompts = [p for stage, _, p in h.completion.calls if stage == "risk"]
    assert any("FIX web risk signal" in p for p in risk_prompts)
    assert any("NARRATIVE_SENTINEL" in p for p in business_prompts)
    # 网页来源标题（WebSearchEntry.title）进入来源清单。
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    assert "[FIX web business product](https://news.example.com/fix-business)" in report


def test_continue_search_triggers_active_research_and_routes_evidence(harness):
    """CONTINUE_SEARCH 时真正执行补证搜索（工具真实调用），新证据进业务章节。"""
    h = harness(comparator=False, active=True)
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    # 补证搜索工具真实被调用（这是"Agent 干活"的直接证据）。
    assert h.active_web is not None and h.active_web.calls > 0
    business_prompts = [p for stage, _, p in h.completion.calls if stage == "business"]
    assert any("补证发现的管理层讨论摘要" in p for p in business_prompts)


def test_missing_comparator_publishes_partial_with_budget_stop(harness):
    h = harness(comparator=False)
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    manifest = json.loads((h.root / "annual/runtime_state.json").read_bytes())
    assert manifest["finalization_status"] == "partial_ready_for_final_writer"
    assert "同比" in (h.root / "08_report.md").read_text(encoding="utf-8")
    snapshot = h.progress.snapshot(job_id=h.job_id)
    assert "supplement_already_attempted" in snapshot.budget_stop_reasons


@pytest.mark.parametrize(
    "options",
    [{"no_target": True}, {"missing_year": 2023}, {"missing_year": 2024}],
)
def test_missing_essential_evidence_blocks_without_model_or_report(harness, options):
    h = harness(**options)
    with pytest.raises(AnnualRuntimeBlocked):
        _run(h)
    assert not h.completion.calls
    assert not (h.root / "08_report.md").exists()
    assert not (h.root / "09_report.pdf").exists()
    assert (
        json.loads((h.root / "annual/runtime_state.json").read_bytes())["finalization_status"]
        == "blocked"
    )
    snapshot = h.progress.snapshot(job_id=h.job_id)
    assert all(
        node.status not in {ResearchNodeStatus.PENDING, ResearchNodeStatus.RUNNING}
        for node in snapshot.nodes
    )


def test_one_targeted_revision_only_rewrites_bad_section(harness):
    h = harness()
    h.completion.missing_business_citations = 1
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    counts = Counter(stage for stage, _, _ in h.completion.calls)
    assert counts == {"analysis": 1, "business": 2, "risk": 1, "financial": 1, "final": 1}


def test_restart_before_final_editor_reuses_completed_sections(harness, monkeypatch):
    h = harness()

    class Interrupted(BaseException):
        pass

    def interrupt(*_args, **_kwargs):
        raise Interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(h.runtime, "_finalize_report", interrupt)
        with pytest.raises(Interrupted):
            _run(h)
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    assert h.sec.calls == {"download": 2, "facts": 1}
    assert Counter(stage for stage, _, _ in h.completion.calls) == {
        "analysis": 1, "business": 1, "risk": 1, "financial": 1, "final": 1,
    }


def test_web_source_publisher_uses_domain_not_sec(harness):
    """修复：web 来源 publisher 用域名，不再所有来源统一标 SEC。"""
    h = harness(web=True)
    state = _run(h)
    web_sources = [
        s for s in state.research_pack.sources
        if str(s.canonical_url).startswith("https://news.example.com")
    ]
    sec_sources = [
        s for s in state.research_pack.sources
        if s.source_type.value in {"sec_filing", "sec_xbrl"}
    ]
    assert web_sources, "web 证据应存在"
    assert all(s.publisher == "news.example.com" for s in web_sources), [
        s.publisher for s in web_sources
    ]
    assert sec_sources, "SEC 证据应存在"
    assert all(s.publisher == "SEC" for s in sec_sources), [s.publisher for s in sec_sources]


def test_annual_report_raw_publish_single_structure(harness):
    """P2：年度报告自包含发布，不再套 legacy 模板造成标题/来源/限制/声明重复。"""
    h = harness(web=True)
    state = _run(h)
    published = h.publisher.publish(h.job_id, state)
    assert len(published) == 2
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    # 模板专属节不再出现。
    assert "模板自动生成" not in report
    assert "数据限制（结构化）" not in report
    assert "引用键：" not in report
    # 年度自包含结构保留。
    assert "## 引用" in report
    assert "## 数据限制" in report
    assert "## 非投资建议声明" in report


def test_reference_list_urls_are_unique(harness):
    """论文式引用列表同 URL 只列一条（src_key=URL hash，同 URL 天然去重）。"""
    h = harness(web=True)
    state = _run(h)
    h.publisher.publish(h.job_id, state)
    report = (h.root / "08_report.md").read_text(encoding="utf-8")
    ref_section = report.split("## 引用", 1)[1].split("\n\n## ", 1)[0]
    urls = re.findall(r"\]\((https?://[^)]+)\)", ref_section)
    assert urls, "引用列表应有可点击链接"
    assert len(urls) == len(set(urls)), f"同 URL 应去重: {urls}"
