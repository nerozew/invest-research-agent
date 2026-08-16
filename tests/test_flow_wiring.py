"""P05-12A FLOW_MODE=fake/live 生产 Flow wiring contract tests（离线，不联网）。

验收（对齐路线图 P05-12A）：
- fake/live 模式切换正确；普通测试/CI 默认 fake，不产生模型费用；
- live 缺 LLM_API_KEY 时 fail-fast（可读错误）；
- live 只做 wiring 契约验证（注入 fake LLM 组装 Crew），不触发真实模型调用。
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
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
    FlowModeError,
    LiveFlowExecutionError,
    LiveResearchFlowRunner,
    build_flow_runner,
)
from invest_research.infrastructure.prefetch import PrefetchResult
from invest_research.infrastructure.queue.flow_adapter import ResearchFlowRunner
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache
from invest_research.settings import Settings


def _settings(
    *,
    flow_mode: str = "fake",
    api_key: str = "sk-test",
    research_profile: str | None = None,
) -> Settings:
    """构造不读 .env 的 Settings（必需字段 + flow_mode/llm_api_key/research_profile 覆盖）。"""
    kwargs: dict[str, object] = {"flow_mode": flow_mode}
    if research_profile is not None:
        kwargs["research_profile"] = research_profile
    return Settings(
        _env_file=None,
        llm_api_key=api_key,
        sec_user_agent_contact="test@example.com",
        **kwargs,
    )


def _config() -> LLMConfig:
    return LLMConfig.from_settings(_settings())


def _fakes() -> dict[str, FakeLLM]:
    """构造三个 fake LLM（research/analysis/writer），不联网。"""
    config = _config()
    return {
        "research": FakeLLM(config=config, role=LLMRole.RESEARCH),
        "analysis": FakeLLM(config=config, role=LLMRole.ANALYSIS),
        "writer": FakeLLM(config=config, role=LLMRole.WRITER),
    }


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))


# ---- 1. 默认 fake 模式（普通测试/CI 不产生费用）----


def test_default_flow_mode_is_fake() -> None:
    assert _settings().flow_mode == "fake"
    assert _settings(flow_mode="live").flow_mode == "live"


def test_invalid_flow_mode_rejected_by_pydantic() -> None:
    with pytest.raises(ValidationError):
        _settings(flow_mode="invalid")


# ---- 2. fake 分支：返回 ResearchFlowRunner 并可离线跑全链 ----


def test_build_flow_runner_fake_returns_offline_runner() -> None:
    runner = build_flow_runner(_settings(flow_mode="fake"))
    assert isinstance(runner, ResearchFlowRunner)


def test_fake_runner_runs_offline_full_chain() -> None:
    runner = build_flow_runner(_settings(flow_mode="fake"))
    runner.run(_request())
    assert runner.last_state is not None
    assert runner.last_state.quality_report is not None
    assert runner.last_state.run_manifest is not None


# ---- 3. live 分支：缺 key fail-fast；带 key 返回 live runner ----


def test_live_mode_missing_key_fails_fast() -> None:
    with pytest.raises(FlowModeError) as exc_info:
        build_flow_runner(_settings(flow_mode="live", api_key=""))
    assert "LLM_API_KEY" in str(exc_info.value)


def test_live_mode_whitespace_key_fails_fast() -> None:
    with pytest.raises(FlowModeError):
        build_flow_runner(_settings(flow_mode="live", api_key="   "))


def test_live_mode_with_key_returns_live_runner() -> None:
    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner, LiveResearchFlowRunner)


def test_live_runner_config_does_not_leak_key() -> None:
    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-super-secret"))
    assert isinstance(runner, LiveResearchFlowRunner)
    assert "sk-super-secret" not in str(runner.config)
    assert "sk-super-secret" not in repr(runner.config)


# ---- 4. live wiring 契约：Crew 组装不联网，run 暂不允许 ----


def test_live_runner_assembles_crew_contract() -> None:
    from crewai.crew import Crew

    runner = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner, LiveResearchFlowRunner)

    crew = runner.assemble_crew(_fakes())
    assert isinstance(crew, Crew)
    assert len(crew.agents) == 3
    assert len(crew.tasks) == 3


def test_live_runner_run_with_injected_fake_crew_offline(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """run() 可通过注入 fake crew 离线跑完整控制流（不联网，不调真实模型）。"""
    from types import SimpleNamespace

    from invest_research.domain.models import (
        CompanyIdentity,
        FinancialAnalysisPack,
        FinancialFact,
        ReportDraft,
        ResearchPack,
        Source,
        SourceType,
    )

    as_of = _request().as_of_date
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="Latest 10-K",
                accessed_at=as_of,
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="0000789019",
                source_id="fake-source",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100000000000,
                unit="USD",
                period_start=date(2024, 1, 1),
                period_end=as_of,
            )
        ],
        analysis_notes="fake 分析占位",
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft Corp 投资研究初稿",
        markdown=(
            "# Microsoft Corp\n\n"
            "## 执行摘要\n内容\n"
            "## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n"
            "## 风险\n内容\n"
            "## 数据限制\n内容\n"
            "## 非投资建议\n内容"
        ),
        citation_keys=["fake-claim-1"],
    )

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(tasks_output=[research, analysis, draft])

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("live_artifacts")),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    runner.run(_request())

    assert runner.last_state is not None
    assert runner.last_state.research_pack is not None
    assert runner.last_state.analysis_pack is not None
    assert runner.last_state.report_draft is not None
    assert runner.last_state.quality_report is not None
    assert runner.last_state.run_manifest.get("status") in ("published", "rejected")


def test_live_failure_does_not_degrade_to_fake(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """live 失败抛 LiveFlowExecutionError，绝不偷偷调用 fake。"""
    from invest_research.infrastructure.flow_wiring import LiveFlowExecutionError

    class _BrokenCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("live_broken")),
        crew_factory=lambda cfg, rt: _BrokenCrew(),  # type: ignore[no-any-return]
    )
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    assert runner.last_state is None  # 不残留运行状态


def test_live_runner_persists_rendered_markdown_report(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """P06-01/02：live 运行后工件目录含 08_report.md（模板渲染）与 09_report.pdf（PyMuPDF）。"""
    from pathlib import Path

    artifact_root = str(tmp_path_factory.mktemp("report_md"))

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return _fake_outputs(date(2025, 12, 31))

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=artifact_root,
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    runner.run(_request())

    job_dir = Path(artifact_root) / "MSFT_2025-12-31"
    report_path = job_dir / "08_report.md"
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert "# Microsoft Corp 报告" in content
    assert "| 公司 | Microsoft Corp |" in content
    assert "| CIK | 0000789019 |" in content
    assert "引用与来源（模板自动生成）" in content
    assert "非投资建议声明（固定文本）" in content
    # 正文（Writer 初稿）原样透传
    assert "## 执行摘要" in content

    pdf_path = job_dir / "09_report.pdf"
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF")


# ---------------------------------------------------------------------------
# P05.5-fix：ResearchRequest 注入 / prefetch 注入 / 结构化收尾 / Action 拒绝 / 工具预算
# ---------------------------------------------------------------------------


def _fake_outputs(as_of: date) -> SimpleNamespace:
    """构造可通过解析（不保证通过质量门禁）的三个 pack 输出。"""
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="Latest 10-K",
                accessed_at=as_of,
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="0000789019",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100,
                unit="USD",
                period_start=date(as_of.year - 1, 1, 1),
                period_end=as_of,
            )
        ],
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft Corp 报告",
        markdown=(
            "# t\n\n## 执行摘要\n内容\n## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n## 风险\n内容\n## 数据限制\n内容\n"
            "## 非投资建议\n内容"
        ),
        citation_keys=["c1"],
    )
    return SimpleNamespace(tasks_output=[research, analysis, draft])


def test_runner_injects_request_into_crew_inputs(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """ResearchRequest（AAPL + 2025-10-31 + 10-K/10-Q + zh-CN）必须注入 Crew。"""
    captured: dict[str, object] = {}

    class _CapturingCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            captured["inputs"] = dict(inputs or {})
            return _fake_outputs(date(2025, 10, 31))

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("inject_request")),
        crew_factory=lambda cfg, rt: _CapturingCrew(),  # type: ignore[no-any-return]
    )
    runner.run(
        ResearchRequest(
            input_company="AAPL",
            as_of_date=date(2025, 10, 31),
            language="zh-CN",
            requested_forms=("10-K", "10-Q"),
        )
    )

    inputs = captured["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["input_company"] == "AAPL"
    assert inputs["as_of_date"] == "2025-10-31"  # Agent 不得自行改成其它日期
    assert inputs["requested_forms"] == "10-K,10-Q"
    assert inputs["language"] == "zh-CN"


def test_runner_injects_prefetch_result_into_crew_inputs(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """预取结果（公司身份 + SEC/Serper 摘要）必须注入 Research Task 输入。"""
    captured: dict[str, object] = {}
    identity = CompanyIdentity(
        cik="0000320193", ticker="AAPL", legal_name="APPLE INC", exchange="NASDAQ"
    )
    prefetch_result = PrefetchResult(
        company_identity=identity,
        submissions_summary="- 10-K | filed 2025-09-27 | https://www.sec.gov/10k.htm",
        search_summary="- Apple News | https://example.com | apple.com",
        status="ok",
        financial_facts_summary=(
            '{"ok":true,"facts":[{"concept":"Revenues","value":"100",'
            '"unit":"USD","period_end":"2025-09-27"}]}'
        ),
    )

    class _CapturingCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            captured["inputs"] = dict(inputs or {})
            return _fake_outputs(date(2025, 10, 31))

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("inject_prefetch")),
        crew_factory=lambda cfg, rt: _CapturingCrew(),  # type: ignore[no-any-return]
        prefetch=lambda request: prefetch_result,
    )
    runner.run(ResearchRequest(input_company="AAPL", as_of_date=date(2025, 10, 31)))

    inputs = captured["inputs"]
    assert inputs["company_identity"] == (
        "ticker=AAPL, CIK=0000320193, legal_name=APPLE INC, exchange=NASDAQ"
    )
    assert "0000320193" in inputs["prefetch_summary"]
    assert "10-K" in inputs["prefetch_summary"]
    assert "Revenues" in inputs["financial_facts"]
    assert "100" in inputs["financial_facts"]


def test_action_input_output_not_treated_as_research_pack_without_cache(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Action Input 不会被当作 ResearchPack；无缓存可收尾时明确失败（不伪造）。"""
    analysis_raw = json.dumps(
        {
            "version": "analysis_pack_v1",
            "period_end": "2025-10-31",
            "facts": [
                {
                    "company_id": "0000320193",
                    "source_id": "s1",
                    "taxonomy": "us-gaap",
                    "concept": "Revenue",
                    "value": 100,
                    "unit": "USD",
                    "period_start": "2024-01-01",
                    "period_end": "2025-10-31",
                }
            ],
        },
        ensure_ascii=False,
    )
    draft_raw = json.dumps(
        {"version": "report_draft_v1", "title": "t", "markdown": "m"}, ensure_ascii=False
    )

    class _ActionCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                tasks_output=[
                    SimpleNamespace(raw='Action: WebSearch\nAction Input: {"query": "AAPL"}'),
                    SimpleNamespace(raw=analysis_raw),
                    SimpleNamespace(raw=draft_raw),
                ]
            )

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("action_input")),
        crew_factory=lambda cfg, rt: _ActionCrew(),  # type: ignore[no-any-return]
    )
    with pytest.raises(LiveFlowExecutionError):
        runner.run(ResearchRequest(input_company="AAPL", as_of_date=date(2025, 10, 31)))


def test_finalize_research_pack_from_cached_sec_results(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Research 输出非法时，从缓存 SEC 申报结果做一次有界结构化收尾（as_of 不漂移）。"""
    cache = ToolCallCache()
    cache.put(
        cache.key(
            "sec_submissions",
            {
                "cik": "0000320193",
                "as_of_date": "2025-10-31",
                "requested_forms": "10-K,10-Q",
            },
        ),
        json.dumps(
            {
                "ok": True,
                "filings": [
                    {
                        "accession_number": "000032019325000020",
                        "form_type": "10-K",
                        "filing_date": "2025-09-27",
                        "primary_document_url": (
                            "https://www.sec.gov/Archives/edgar/data/320193/"
                            "000032019325000020/aapl-20250927.htm"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    identity = CompanyIdentity(
        cik="0000320193", ticker="AAPL", legal_name="APPLE INC", exchange="NASDAQ"
    )
    prefetch_result = PrefetchResult(
        company_identity=identity,
        submissions_summary="- 10-K | filed 2025-09-27 | https://www.sec.gov/10k.htm",
        search_summary=None,
        status="partial",
    )

    class _ActionCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                tasks_output=[
                    SimpleNamespace(raw='Action: WebSearch\nAction Input: {"query": "AAPL"}'),
                    SimpleNamespace(
                        raw=json.dumps(
                            {
                                "version": "analysis_pack_v1",
                                "period_end": "2025-10-31",
                                "facts": [
                                    {
                                        "company_id": "0000320193",
                                        "source_id": "s1",
                                        "taxonomy": "us-gaap",
                                        "concept": "Revenue",
                                        "value": 100,
                                        "unit": "USD",
                                        "period_start": "2024-01-01",
                                        "period_end": "2025-10-31",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    ),
                    SimpleNamespace(
                        raw=json.dumps(
                            {"version": "report_draft_v1", "title": "t", "markdown": "m"},
                            ensure_ascii=False,
                        )
                    ),
                ]
            )

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("finalize")),
        crew_factory=lambda cfg, rt: _ActionCrew(),  # type: ignore[no-any-return]
        cache=cache,
        prefetch=lambda request: prefetch_result,
    )
    runner.run(ResearchRequest(input_company="AAPL", as_of_date=date(2025, 10, 31)))

    state = runner.last_state
    assert state is not None
    assert state.research_pack is not None
    # as_of 不漂移：收尾结果仍使用请求的 2025-10-31
    assert state.research_pack.as_of_date == date(2025, 10, 31)
    assert state.research_pack.company_identity.cik == "0000320193"
    assert any("sec.gov" in s.canonical_url for s in state.research_pack.sources)


def test_tool_budget_exhausted_returns_typed_result() -> None:
    """工具预算达到上限后返回 typed BUDGET_EXHAUSTED，禁止无限重复调用。"""
    from invest_research.infrastructure.real_tools import (
        ResearchToolkit,
        build_research_tools,
    )
    from invest_research.tools.base import ToolSuccess
    from invest_research.tools.sec_submissions import FetchSubmissionsResponse

    class _CountingSubmissions:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, request: object) -> object:
            self.calls += 1
            return ToolSuccess(value=FetchSubmissionsResponse(filings=[]))

    submissions = _CountingSubmissions()
    toolkit = ResearchToolkit(
        resolver=None,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        facts=None,  # type: ignore[arg-type]
        downloader=None,  # type: ignore[arg-type]
        search=None,  # type: ignore[arg-type]
    )
    cache = ToolCallCache()
    budget = ToolBudget(caps={"sec_submissions": 2})
    tools = build_research_tools(toolkit=toolkit, cache=cache, budget=budget)
    sec_tool = tools[1]  # SECSubmissions

    sec_tool.run(cik="0000320193", as_of_date="2025-10-31", requested_forms="10-K,10-Q")
    assert submissions.calls == 1
    sec_tool.run(cik="0000320193", as_of_date="2025-01-01", requested_forms="10-K,10-Q")
    assert submissions.calls == 2
    # 第三次（不同参数，缓存未命中）→ 预算耗尽
    result = sec_tool.run(cik="0000320193", as_of_date="2024-01-01", requested_forms="10-K,10-Q")
    assert submissions.calls == 2
    assert "BUDGET_EXHAUSTED" in json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# P05.5-fix：fast 强制非思考模式 / deep 由 LLM_ENABLE_THINKING 环境变量决定
# ---------------------------------------------------------------------------


def test_fast_profile_forces_non_thinking() -> None:
    """fast 模式明确使用非思考模式（enable_thinking=False，Qwen3.5 提速）。"""
    settings = _settings(flow_mode="live", api_key="sk-live", research_profile="fast")
    runner = build_flow_runner(settings)
    assert isinstance(runner, LiveResearchFlowRunner)
    assert runner.config.enable_thinking is False


def test_deep_profile_thinking_follows_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """deep 模式是否开启思考由 LLM_ENABLE_THINKING 环境变量决定，不写死。"""
    runner_none = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner_none, LiveResearchFlowRunner)
    assert runner_none.config.enable_thinking is None  # 未配置 → 不传供应商专有参数

    monkeypatch.setenv("LLM_ENABLE_THINKING", "true")
    runner_true = build_flow_runner(_settings(flow_mode="live", api_key="sk-live"))
    assert isinstance(runner_true, LiveResearchFlowRunner)
    assert runner_true.config.enable_thinking is True


def test_research_sec_sources_get_deterministic_locator(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """SEC 来源缺 locator 时确定性补全；财年 period_end 门禁放行（P05.5-fix）。"""
    as_of = date(2025, 10, 31)
    identity = CompanyIdentity(
        cik="0000320193", ticker="AAPL", legal_name="APPLE INC", exchange="NASDAQ"
    )
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url=(
                    "https://www.sec.gov/Archives/edgar/data/320193/"
                    "000032019325000079/aapl-20250927.htm"
                ),
                title="10-K filed 2025-10-31",
                accessed_at=as_of,
                # locator 缺失：应被确定性补全为表单类型 "10-K"
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2025, 9, 27),  # 财年结束日 < as_of，门禁应放行
        facts=[
            FinancialFact(
                company_id="0000320193",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100,
                unit="USD",
                period_start=date(2024, 9, 29),
                period_end=date(2025, 9, 27),
            )
        ],
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="t",
        markdown=(
            "# t\n\n## 执行摘要\n内容\n## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n## 风险\n内容\n## 数据限制\n内容\n"
            "## 非投资建议\n内容"
        ),
        citation_keys=["c1"],
    )

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(tasks_output=[research, analysis, draft])

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("locator")),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    runner.run(ResearchRequest(input_company="AAPL", as_of_date=as_of))

    state = runner.last_state
    assert state is not None
    assert state.research_pack is not None
    sec_sources = [s for s in state.research_pack.sources if "sec.gov" in (s.canonical_url or "")]
    assert sec_sources, "应保留 SEC 来源"
    assert all(s.locator for s in sec_sources), "SEC 来源应补全非空 locator"
    # 门禁放行：财年 period_end ≤ as_of + 章节/引用齐全 → published
    assert state.quality_report is not None
    assert state.quality_report.all_passed is True
    assert state.run_manifest.get("status") == "published"


def test_manifest_evidence_written_when_stats_wired(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """工具调用计入 stats 后，manifest 必须含 evidence.invocation_summary（P05-13 验收7）。"""
    as_of = date(2025, 10, 31)
    stats: dict[str, int] = {"sec_submissions_calls": 1, "web_search_calls": 1}

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return _fake_outputs(as_of)

    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path_factory.mktemp("evidence")),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
        stats=stats,
    )
    runner.run(ResearchRequest(input_company="AAPL", as_of_date=as_of))

    manifest = runner.last_state.run_manifest
    assert isinstance(manifest, dict)
    evidence = manifest.get("evidence")
    assert isinstance(evidence, dict)
    inv = evidence.get("invocation_summary")
    assert isinstance(inv, dict)
    assert inv.get("sec_submissions_calls", 0) > 0
    assert inv.get("web_search_calls", 0) > 0
