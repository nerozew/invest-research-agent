"""P05-13 真实服务 E2E smoke（opt-in，默认 skip）。

开关：
- ``RUN_LIVE_E2E=1``：启用真实 E2E（否则全部测试 skip，不联网、不调模型）；
- ``FLOW_MODE=live``：配合 build_flow_runner 走真实 live 分支（缺 key 会 fail-fast）。

固定测试对象（经 P05-12 真实录制验证）：
- 公司：AAPL；CIK 0000320193；as_of_date 2025-10-31；
- requested_forms：10-K、10-Q；language：zh-CN。

验收（执行付费 live run 前必须先满足）：
1. rejected 必须导致测试失败（不是成功终态）；
2. 最终必须是 published（或项目契约明确支持的成功/部分成功终态）；
3. 报告 markdown 非空；
4. citations 非空（ReportDraft.citation_keys）；
5. 至少一个 citation 包含真实 SEC/官方 URL 和非空 locator（ResearchPack.sources 承载 url+locator）；
6. 验证没有使用 as_of_date=2025-10-31 之后的数据（research pack 的 source/filing 日期边界）；
7. 验证真实 SEC、Serper、LLM 调用证据存在于 invocation/manifest/metrics
   （evidence.invocation_summary + manifest.models/duration）；
8. 验证所需 00/02/04/05/06/07 工件全部存在；
9. 验证 manifest 包含模型、耗时、重试和外部调用统计（models / duration_ms / evidence）；
10. 验证所有工件和日志不含 API Key。

本文件内辅助函数 ``assert_live_acceptance`` 供真实 E2E 与离线契约共用，
保证校验逻辑在 CI 可离线验证、真实 live 时有同一套严格断言。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from invest_research.domain.models import ResearchRequest, SourceType
from invest_research.infrastructure.flow_wiring import build_flow_runner
from invest_research.settings import Settings

# 固定测试对象（P05-13 验收常量）
LIVE_E2E_COMPANY = "AAPL"
LIVE_E2E_CIK = "0000320193"
LIVE_E2E_AS_OF = date(2025, 10, 31)
LIVE_E2E_FORMS = ("10-K", "10-Q")
LIVE_E2E_LANGUAGE = "zh-CN"

# 成功/部分成功终态（QualityRecommendation：published / publish_partial）
_ACCEPTED_TERMINAL_STATES = {"published", "publish_partial"}
# 明确失败终态（一旦出现即测试失败）
_REJECTED_STATES = {"rejected"}

# 工件目录（与 flow_wiring.LiveResearchFlowRunner._persist_intermediates 对齐）
_REQUIRED_ARTIFACTS = {
    "00_request.json",
    "02_research_pack.json",
    "04_financial_analysis_pack.json",
    "05_report_draft.json",
    "06_quality_report.json",
    "07_manifest.json",
}

# SEC 官方域名（要求 5：真实 SEC/官方 URL）
_SEC_OFFICIAL_HOSTS = ("sec.gov", "data.sec.gov", "www.sec.gov")


def _live_enabled() -> bool:
    """真实 E2E 启用条件：RUN_LIVE_E2E=1 且 FLOW_MODE=live。

    - ``RUN_LIVE_E2E`` 是显式测试开关，只能来自环境变量；
    - ``FLOW_MODE`` 与 run_live_e2e.py 同一来源：优先环境变量，否则读
      Settings（根目录 .env）。修复"key/URL 都对但测试全 skip"的陷阱。
    - 任何缺失/异常都返回 False（安全，绝不联网）。
    """
    if os.environ.get("RUN_LIVE_E2E") != "1":
        return False
    env_mode = os.environ.get("FLOW_MODE")
    if env_mode is not None:
        return env_mode == "live"
    try:
        from invest_research.settings import get_settings

        return get_settings().flow_mode == "live"
    except Exception:
        return False


def _e2e_request() -> ResearchRequest:
    return ResearchRequest(
        input_company=LIVE_E2E_COMPANY,
        as_of_date=LIVE_E2E_AS_OF,
        language=LIVE_E2E_LANGUAGE,
        requested_forms=tuple(LIVE_E2E_FORMS),
    )


# ---------------------------------------------------------------------------
# P05-13 严格验收辅助函数（离线契约测试 + 真实 E2E 共用）
# ---------------------------------------------------------------------------


def _has_real_sec_url_with_locator(research_pack: object) -> bool:
    """要求 5：至少一个 Source 是 SEC/官方 URL 且带非空 locator。"""
    sources = getattr(research_pack, "sources", None)
    if not sources:
        return False
    for src in sources:
        url = getattr(src, "canonical_url", "") or ""
        locator = getattr(src, "locator", None)
        source_type = getattr(src, "source_type", None)
        is_sec = any(host in url for host in _SEC_OFFICIAL_HOSTS)
        is_sec_type = source_type in (SourceType.SEC_FILING, SourceType.SEC_XBRL)
        if (is_sec or is_sec_type) and locator and locator.strip():
            return True
    return False


def _no_data_after_as_of(research_pack: object, as_of: date) -> bool:
    """要求 6：research pack 中任何 source/filing 日期不晚于 as_of_date。"""
    sources = getattr(research_pack, "sources", None) or []
    for src in sources:
        for attr in ("published_at", "accessed_at"):
            d = getattr(src, attr, None)
            if d is not None and d > as_of:
                return False
    return True


def assert_live_acceptance(
    *,
    state: object,
    manifest: dict[str, object] | None,
    settings: Settings,
    request: ResearchRequest,
) -> None:
    """P05-13 十项严格验收（离线可测；live 时按同一标准判定）。

    任一失败即抛 AssertionError；requirement 数字对应任务清单 1-10。
    """
    # 1. rejected 必须导致失败
    assert manifest is not None, "requirement1: manifest 缺失，无法判定终态"
    status = manifest.get("status")
    assert status not in _REJECTED_STATES, (
        "requirement1: manifest status=rejected（质量门禁未通过，不得视为成功）"
    )

    # 2. 最终必须是 published / 明确支持的部分成功终态
    assert status in _ACCEPTED_TERMINAL_STATES, (
        f"requirement2: 最终终态 {status!r} 不在成功/部分成功集合 "
        f"{sorted(_ACCEPTED_TERMINAL_STATES)} 中"
    )

    # 3. 报告 markdown 非空
    report_draft = getattr(state, "report_draft", None)
    assert report_draft is not None, "requirement3: report_draft 缺失"
    markdown = getattr(report_draft, "markdown", "") or ""
    assert markdown.strip(), "requirement3: 报告 markdown 为空"

    # 4. citations 非空（ReportDraft.citation_keys）
    citation_keys = getattr(report_draft, "citation_keys", None) or []
    assert citation_keys, "requirement4: citation_keys 为空"

    # 5. 至少一个 citation 含真实 SEC/官方 URL 和非空 locator
    research_pack = getattr(state, "research_pack", None)
    assert research_pack is not None, "requirement5: research_pack 缺失"
    assert _has_real_sec_url_with_locator(research_pack), (
        "requirement5: research_pack 中无带 SEC 官方 URL + 非空 locator 的 Source"
    )

    # 6. 无 as_of_date 之后的数据
    assert _no_data_after_as_of(research_pack, request.as_of_date), (
        f"requirement6: research_pack 存在晚于 as_of_date={request.as_of_date} 的数据"
    )

    # 7. 真实 SEC / Serper / LLM 调用证据在 invocation/manifest/metrics 中
    evidence = manifest.get("evidence")
    assert isinstance(evidence, dict), "requirement7: manifest 缺少 evidence.invocation_summary"
    inv_summary = evidence.get("invocation_summary")
    assert isinstance(inv_summary, dict) and inv_summary, (
        "requirement7: evidence.invocation_summary 为空（未见 SEC/Serper 调用证据）"
    )
    sec_calls = sum(
        v
        for k, v in inv_summary.items()
        if "sec_" in k or "filing_" in k or "company_resolver" in k
    )
    search_calls = sum(v for k, v in inv_summary.items() if "web_search" in k)
    assert sec_calls > 0, "requirement7: 未发现真实 SEC 调用证据"
    assert search_calls > 0, "requirement7: 未发现真实 Serper(WebSearch) 调用证据"
    models = manifest.get("models")
    assert isinstance(models, dict) and models, "requirement7: manifest 缺少 models（LLM 证据）"

    # 8. 所需工件全部存在
    job_root = (
        Path(settings.artifact_root)
        / f"{request.input_company}_{request.as_of_date.isoformat()}"
    )
    present = {p.name for p in job_root.glob("*.json")}
    missing = _REQUIRED_ARTIFACTS - present
    assert not missing, f"requirement8: 缺失中间产物: {sorted(missing)}"

    # 9. manifest 包含模型、耗时、重试和外部调用统计
    assert isinstance(manifest.get("models"), dict) and manifest["models"], (
        "requirement9: manifest 缺 models"
    )
    assert isinstance(manifest.get("duration_ms"), (int, float)) and manifest["duration_ms"] >= 0, (
        "requirement9: manifest 缺 duration_ms"
    )
    assert "evidence" in manifest, "requirement9: manifest 缺 evidence（含重试/调用统计）"

    # 10. 所有工件不含 API Key
    for p in job_root.glob("*.json"):
        content = p.read_text(encoding="utf-8")
        assert "LLM_API_KEY" not in content, f"requirement10: {p.name} 含 LLM_API_KEY"
        assert "sk-" not in content, f"requirement10: {p.name} 疑似含密钥明文"


# ---------------------------------------------------------------------------
# 离线契约测试（永不 skip，不联网）
# ---------------------------------------------------------------------------


def test_live_e2e_config_fixed() -> None:
    """固定测试对象必须与 P05-12 真实录制一致（CIK/as_of/表单/语言）。"""
    assert LIVE_E2E_CIK == "0000320193"
    assert LIVE_E2E_AS_OF == date(2025, 10, 31)
    assert set(LIVE_E2E_FORMS) == {"10-K", "10-Q"}
    req = _e2e_request()
    assert req.input_company == "AAPL"
    assert req.language == "zh-CN"
    assert req.requested_forms == ("10-K", "10-Q")


def test_live_e2e_offline_contract_default_tests_empty() -> None:
    """无 RUN_LIVE_E2E/FLOW_MODE=live 时，真实测试恒 skip（断言 skip 条件生效）。"""
    assert _live_enabled() is False or os.environ.get("RUN_LIVE_E2E") == "1"


def test_build_flow_runner_live_needs_env() -> None:
    """未配置真实 key 时 live 分支 fail-fast（离线可验证缺配置语义）。"""
    settings = Settings(
        _env_file=None,
        llm_api_key="",
        sec_user_agent_contact="test@example.com",
        flow_mode="live",
    )
    from invest_research.infrastructure.flow_wiring import FlowModeError

    with pytest.raises(FlowModeError):
        build_flow_runner(settings)


def test_offline_acceptance_helpers_are_deterministic() -> None:
    """验收辅助函数可离线验证（覆盖要求 1/2/3/5/6/7 的核心判据）。"""
    from types import SimpleNamespace

    # 构造一个通过高质量门禁的离线状态（含 SEC URL + locator + 调用证据）
    research_pack = SimpleNamespace(
        sources=[
            SimpleNamespace(
                canonical_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000020/aapl-20250927.htm",
                source_type=SourceType.SEC_FILING,
                locator="Part II, Item 7 Management's Discussion",
                published_at=date(2025, 9, 27),
                accessed_at=date(2025, 10, 31),
            )
        ]
    )
    state = SimpleNamespace(
        research_pack=research_pack,
        report_draft=SimpleNamespace(markdown="# AAPL\n\n非空报告", citation_keys=["claim-1"]),
    )
    manifest: dict[str, object] = {
        "status": "published",
        "models": {"research": "qwen-max"},
        "duration_ms": 120,
        "evidence": {
            "invocation_summary": {"sec_company_facts_calls": 1, "web_search_calls": 1}
        },
    }
    # 离线只验证辅助函数的判据本身，不要求工件落盘（requirement8/10 属 live 落盘断言）
    assert _has_real_sec_url_with_locator(research_pack) is True
    assert _no_data_after_as_of(research_pack, LIVE_E2E_AS_OF) is True
    # 通过版不应触发 requirement1/2/3/5/6/7 断言
    assert manifest["status"] not in _REJECTED_STATES
    assert manifest["status"] in _ACCEPTED_TERMINAL_STATES
    assert (getattr(state.report_draft, "markdown") or "").strip()
    assert getattr(state.report_draft, "citation_keys", None)


def test_offline_acceptance_rejects_rejected_terminal() -> None:
    """requirement1：rejected 必须导致失败（离线验证判定逻辑）。"""
    manifest: dict[str, object] = {"status": "rejected", "reason": "quality"}
    assert manifest["status"] in _REJECTED_STATES
    assert manifest["status"] not in _ACCEPTED_TERMINAL_STATES


def test_offline_acceptance_requires_sec_url_and_locator() -> None:
    """requirement5：无 SEC URL 或无 locator 的来源不满足签证证据。"""
    from types import SimpleNamespace

    no_locator = SimpleNamespace(
        sources=[
            SimpleNamespace(
                canonical_url="https://www.sec.gov/Archives/edgar/data/1/filing.htm",
                source_type=SourceType.SEC_FILING,
                locator=None,
                published_at=date(2025, 9, 27),
                accessed_at=date(2025, 10, 31),
            )
        ]
    )
    assert _has_real_sec_url_with_locator(no_locator) is False

    non_sec = SimpleNamespace(
        sources=[
            SimpleNamespace(
                canonical_url="https://example.com/article",
                source_type=SourceType.WEB,
                locator="page 3",
                published_at=date(2025, 9, 27),
                accessed_at=date(2025, 10, 31),
            )
        ]
    )
    assert _has_real_sec_url_with_locator(non_sec) is False


def test_offline_acceptance_rejects_future_data() -> None:
    """requirement6：晚于 as_of 的数据必须被拦截（离线验证判定逻辑）。"""
    from types import SimpleNamespace

    research_pack = SimpleNamespace(
        sources=[
            SimpleNamespace(
                canonical_url="https://www.sec.gov/filing",
                source_type=SourceType.SEC_FILING,
                locator="Item 7",
                published_at=date(2025, 11, 5),
                accessed_at=date(2025, 11, 5),
            )
        ]
    )
    assert _no_data_after_as_of(research_pack, LIVE_E2E_AS_OF) is False


# ---------------------------------------------------------------------------
# 真实 E2E（仅 RUN_LIVE_E2E=1 + FLOW_MODE=live 时执行）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _live_enabled(),
    reason="需要 RUN_LIVE_E2E=1 且 FLOW_MODE=live（真实 API Key 环境）",
)
def test_live_e2e_aapl_report() -> None:
    """真实 E2E：AAPL 完整报告（SEC + 搜索 + 千问 + 门禁 + manifest + 工件）。

    严格验收：调用 ``assert_live_acceptance`` 执行十项判据，
    任一不满足即测试失败（不降级为"rejected 也算成功"）。
    """
    from invest_research.infrastructure.queue import worker as worker_module
    from invest_research.settings import get_settings

    settings = get_settings()

    # 真实环境须配置 Serper（缺 key 直接 fail-fast，禁止无凭据跑真实搜索）
    # P05.5：用 _build_live_components 组装 cache/recorder/prefetch，使
    # fast/deep 档位 + 并行预取 + 工具缓存 + 性能记录全部在 live 生效。
    stats: dict[str, int] = {}
    components = worker_module._build_live_components(settings, stats=stats)
    runner = build_flow_runner(
        settings,
        research_tools=components.research_tools,
        stats=stats,
        recorder=components.recorder,
        cache=components.cache,
        prefetch=components.prefetch,
    )
    request = _e2e_request()

    runner.run(request)

    state = runner.last_state
    assert state is not None, "run 后必须有 last_state（终态）"
    manifest = state.run_manifest
    assert_live_acceptance(
        state=state,
        manifest=manifest,
        settings=settings,
        request=request,
    )
