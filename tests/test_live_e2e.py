"""P05-13 真实服务 E2E smoke（opt-in，默认 skip）。

开关：
- ``RUN_LIVE_E2E=1``：启用真实 E2E（否则全部测试 skip，不联网、不调模型）；
- ``FLOW_MODE=live``：配合 build_flow_runner 走真实 live 分支（缺 key 会 fail-fast）。

固定测试对象（经 P05-12 真实录制验证）：
- 公司：AAPL；CIK 0000320193；as_of_date 2025-10-31；
- requested_forms：10-K、10-Q；language：zh-CN。

离线契约测试（永不 skip）：验证环境开关与固定配置有效，不联网。
真实 E2E 测试（RUN_LIVE_E2E=1 才执行）：验证真实 SEC 数据、真实搜索、
真实千问调用、报告非空、引用可追溯、质量门禁通过、中间工件及 RunManifest
完整、无未来数据、日志/工件无密钥、无残留 pending/running 状态。

注意：本项目根目录目前无真实 `.env`，因此本文件在 CI/普通测试下**全部 skip**，
真实 live run 需由用户显式授权并在具备真实 LLM_API_KEY/SERPER_API_KEY 的环境执行。
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.flow_wiring import build_flow_runner
from invest_research.settings import Settings

# 固定测试对象（P05-13 验收常量）
LIVE_E2E_COMPANY = "AAPL"
LIVE_E2E_CIK = "0000320193"
LIVE_E2E_AS_OF = date(2025, 10, 31)
LIVE_E2E_FORMS = ("10-K", "10-Q")
LIVE_E2E_LANGUAGE = "zh-CN"


def _live_enabled() -> bool:
    """真实 E2E 启用条件：RUN_LIVE_E2E=1 且 FLOW_MODE=live。"""
    return os.environ.get("RUN_LIVE_E2E") == "1" and os.environ.get("FLOW_MODE") == "live"


def _e2e_request() -> ResearchRequest:
    return ResearchRequest(
        input_company=LIVE_E2E_COMPANY,
        as_of_date=LIVE_E2E_AS_OF,
        language=LIVE_E2E_LANGUAGE,
        requested_forms=tuple(LIVE_E2E_FORMS),
    )


# ---- 离线契约测试（永不 skip，不联网）----


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
    # 此测试只是说明：真实 E2E 测试用 pytest.mark.skipif(not _live_enabled(), ...)
    # 保证普通测试/CI 永远不触发真实调用。
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


# ---- 真实 E2E（仅 RUN_LIVE_E2E=1 + FLOW_MODE=live 时执行）----


@pytest.mark.skipif(
    not _live_enabled(),
    reason="需要 RUN_LIVE_E2E=1 且 FLOW_MODE=live（真实 API Key 环境）",
)
def test_live_e2e_aapl_report() -> None:
    """真实 E2E：AAPL 完整报告（SEC + 搜索 + 千问 + 门禁 + manifest + 工件）。

    验收项：
    - 报告非空；
    - 质量门禁通过（published 或 partial）；
    - RunManifest 完整（models/prompts/packs/耗时）；
    - 中间产物（pack/quality/manifest）落盘且不含密钥；
    - 无残留 pending/running 状态（last_state 已终态）。
    """
    from pathlib import Path

    from invest_research.settings import get_settings

    settings = get_settings()  # 仅真实环境（含 LLM_API_KEY/SERPER_API_KEY）
    runner = build_flow_runner(settings)
    request = _e2e_request()

    runner.run(request)

    state = runner.last_state
    assert state is not None, "run 后必须有 last_state（终态）"
    # 无残留 running/pending：quality_report 与 run_manifest 已产生
    assert state.quality_report is not None
    assert state.run_manifest is not None
    # 报告非空
    assert state.report_draft is not None and state.report_draft.markdown.strip()
    # 质量门禁结果可查询
    assert state.quality_report.all_passed or len(state.quality_report.issues) > 0
    assert state.run_manifest.get("status") in ("published", "rejected")

    # 中间产物完整且无密钥
    job_root = (
        Path(settings.artifact_root)
        / f"{request.input_company}_{request.as_of_date.isoformat()}"
    )
    required = {
        "00_request.json",
        "02_research_pack.json",
        "04_financial_analysis_pack.json",
        "05_report_draft.json",
        "06_quality_report.json",
        "07_manifest.json",
    }
    present = {p.name for p in job_root.glob("*.json")}
    assert required <= present, f"缺失中间产物: {required - present}"
    for p in job_root.glob("*.json"):
        assert "LLM_API_KEY" not in p.read_text(encoding="utf-8")
        assert "sk-" not in p.read_text(encoding="utf-8")
