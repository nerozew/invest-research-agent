"""P03-06 Analysis Task 测试（fake LLM，不联网）。

验证目标（docs/05 P03-06 验收）：
- Agent 可构建，只暴露 FinancialFactQuery + FinancialCalculator 工具（最小权限白名单）；
- Task 绑定 output_pydantic=FinancialAnalysisPack；
- FinancialCalculator 是确定性计算（不依赖 LLM），且只接受白名单指标；
- FakeLLM 可通过 response_model=FinancialAnalysisPack 实例化分析 pack；
- pair 返回同一 Agent 实例。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from invest_research.agents.analysis_task import (
    build_analysis_agent,
    build_analysis_pair,
    build_analysis_task,
    financial_calculator,
)
from invest_research.agents.llm_factory import FakeLLM, LLMConfig, LLMRole
from invest_research.domain.models import FinancialAnalysisPack, FinancialFact, MetricStatus
from invest_research.financial.metrics import FORMULA_VERSION
from invest_research.settings import Settings


def _config() -> LLMConfig:
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )
    return LLMConfig.from_settings(settings)


def _fake() -> FakeLLM:
    return FakeLLM(config=_config(), role=LLMRole.ANALYSIS)


def test_analysis_agent_exposes_only_whitelisted_tools() -> None:
    """最小权限白名单：Agent 只暴露 FinancialFactQuery + FinancialCalculator。"""
    agent = build_analysis_agent(_config(), fake=_fake())
    tool_names = {t.name for t in agent.tools or []}
    assert tool_names == {"FinancialFactQuery", "FinancialCalculator"}
    # 绝不暴露搜索/下载等无关工具
    assert not tool_names & {"GoogleSearch", "FilingDownloader", "SECSubmissions"}


def test_analysis_task_binds_output_pydantic() -> None:
    """Task 绑定 output_pydantic=FinancialAnalysisPack（类，非实例）。"""
    task = build_analysis_task(_config(), fake=_fake())
    assert task.output_pydantic is FinancialAnalysisPack


def test_analysis_pair_returns_same_agent_instance() -> None:
    """pair 复用同一 Agent 实例（task.agent is agent）。"""
    fake = _fake()
    agent, task = build_analysis_pair(_config(), fake=fake)
    assert task.agent is agent


def test_financial_calculator_computes_gross_margin_deterministically() -> None:
    """确定性计算：毛利率 40/100 = 0.4，公式版本可追溯。"""
    result = financial_calculator.run(
        "gross_margin", "job-1", date(2025, 6, 30), revenue="100", gross_profit="40"
    )
    assert result["status"] == MetricStatus.COMPUTED.value
    assert Decimal(str(result["value"])) == Decimal("0.4")
    assert result["formula_version"] == FORMULA_VERSION


def test_financial_calculator_rejects_unknown_metric() -> None:
    """指标白名单：不在 PRD §8 的指标名 → 明确 not_computable，不静默。"""
    result = financial_calculator.run("some_made_up_metric", "job-1", date(2025, 6, 30))
    assert result["status"] == "not_computable"
    assert "不支持" in str(result["reason"])


def test_financial_calculator_rejects_zero_denominator() -> None:
    """零分母 → NOT_COMPUTABLE（不产生 inf/NaN）。"""
    result = financial_calculator.run(
        "current_ratio", "job-1", date(2025, 6, 30), current_assets="100", current_liabilities="0"
    )
    assert result["status"] == MetricStatus.NOT_COMPUTABLE.value


def test_fake_llm_instantiates_analysis_pack() -> None:
    """fake LLM 通过 response_model 实例化 FinancialAnalysisPack。"""
    pack = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2025, 6, 30),
        facts=[
            FinancialFact(
                company_id="c1",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=Decimal("100"),
                unit="USD",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 6, 30),
            )
        ],
    )
    fake = FakeLLM(config=_config(), role=LLMRole.ANALYSIS, responses=[pack])
    result = fake.call(messages="请分析", response_model=FinancialAnalysisPack)
    assert isinstance(result, FinancialAnalysisPack)
    assert result.version == "analysis_pack_v1"


def test_build_analysis_agent_requires_fake() -> None:
    with pytest.raises(NotImplementedError):
        build_analysis_agent(_config(), fake=None)
