"""P06-09A：FinancialAnalysisPack 结果完整性状态（complete/partial/unavailable）。

覆盖：
- 三种状态的合法构造（含跨字段 validator 的约束）；
- 状态与内容矛盾（缺字段 / 多余字段 / unavailable 含数据）被拒绝；
- v1 旧版工件兼容读取（宽松模式，不强行套新状态）；
- partial / unavailable 是合法业务结果，不导致 pack 解析失败 / 调用链崩溃；
- 质量门禁对 unavailable 含内容的兜底拒绝。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invest_research.agents.pack_parsing import PackParseError, parse_pack_output
from invest_research.domain.models import (
    AnalysisCompleteness,
    FinancialAnalysisPack,
    FinancialFact,
    MetricResult,
    MetricStatus,
    ResearchRequest,
)
from invest_research.flows.quality_classifier import classify_state
from invest_research.flows.state import ResearchFlowState

_PERIOD = date(2025, 9, 27)


def _fact() -> FinancialFact:
    return FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="Revenues",
        value=Decimal("100.0"),
        unit="USD",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 9, 27),
    )


def _metric() -> MetricResult:
    return MetricResult(
        job_id="j1",
        metric_name="revenue_growth",
        period_end=_PERIOD,
        value=Decimal("0.1"),
        unit="ratio",
        status=MetricStatus.COMPUTED,
        formula_version="financial_metrics_v1",
        inputs_json={},
    )


def _base(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "version": "analysis_pack_v2",
        "schema_version": "analysis_pack_v2",
        "period_end": _PERIOD,
        "facts": [_fact()],
        "metrics": [],
        "analysis_notes": None,
        "limitations": [],
        "completeness": AnalysisCompleteness.COMPLETE.value,
        "unavailable_reason": None,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 合法三态
# ---------------------------------------------------------------------------


def test_complete_requires_facts_or_metrics() -> None:
    pack = FinancialAnalysisPack.model_validate(_base(metrics=[_metric()]))
    assert pack.completeness == AnalysisCompleteness.COMPLETE
    assert pack.schema_version == "analysis_pack_v2"


def test_partial_allows_empty_facts_with_limitations() -> None:
    pack = FinancialAnalysisPack.model_validate(
        _base(
            completeness=AnalysisCompleteness.PARTIAL.value,
            facts=[],
            limitations=["未取得 Revenue 事实，无法计算收入增长率"],
        )
    )
    assert pack.completeness == AnalysisCompleteness.PARTIAL


def test_unavailable_is_legal_business_result() -> None:
    pack = FinancialAnalysisPack.model_validate(
        _base(
            completeness=AnalysisCompleteness.UNAVAILABLE.value,
            facts=[],
            metrics=[],
            limitations=[],
            unavailable_reason="未取得任何 SEC 财务事实",
        )
    )
    assert pack.completeness == AnalysisCompleteness.UNAVAILABLE
    assert pack.unavailable_reason is not None


# ---------------------------------------------------------------------------
# 状态与内容矛盾 → ValidationError（pack_parsing 层转 SCHEMA_INVALID）
# ---------------------------------------------------------------------------


def test_complete_without_content_rejected() -> None:
    with pytest.raises(ValidationError):
        FinancialAnalysisPack.model_validate(
            _base(completeness=AnalysisCompleteness.COMPLETE.value, facts=[], metrics=[])
        )


def test_complete_with_unavailable_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        FinancialAnalysisPack.model_validate(
            _base(unavailable_reason="不应出现的原因")
        )


def test_partial_without_limitations_rejected() -> None:
    with pytest.raises(ValidationError):
        FinancialAnalysisPack.model_validate(
            _base(
                completeness=AnalysisCompleteness.PARTIAL.value,
                facts=[],
                metrics=[],
            )
        )


def test_unavailable_without_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        FinancialAnalysisPack.model_validate(
            _base(
                completeness=AnalysisCompleteness.UNAVAILABLE.value,
                facts=[],
                metrics=[],
                unavailable_reason=None,
            )
        )


def test_unavailable_with_fabricated_facts_rejected() -> None:
    # 不得伪造财务指标：unavailable 状态带 facts → 拒绝
    with pytest.raises(ValidationError):
        FinancialAnalysisPack.model_validate(
            _base(
                completeness=AnalysisCompleteness.UNAVAILABLE.value,
                unavailable_reason="未取得任何 SEC 财务事实",
            )
        )


def test_parse_pack_output_rejects_extra_fields() -> None:
    """多余字段 → 稳定 SCHEMA_INVALID（P06-09 既有契约，P06-09A 保持）。"""
    data = dict(_base())
    data["extra_field"] = "boom"
    with pytest.raises(PackParseError) as exc_info:
        parse_pack_output(data, FinancialAnalysisPack)
    assert exc_info.value.error_code == "SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# v1 旧版兼容读取（宽松模式）
# ---------------------------------------------------------------------------


def test_v1_legacy_pack_readable() -> None:
    """旧版 v1 工件（无 completeness/schema_version）可正常读取，不强行套新状态。"""
    pack = FinancialAnalysisPack.model_validate(
        {
            "version": "analysis_pack_v1",
            "period_end": _PERIOD,
            "facts": [],
            "metrics": [],
            "limitations": [],
        }
    )
    assert pack.schema_version == "analysis_pack_v1"  # 自动推断


def test_v1_legacy_rejects_extra_fields_via_parser() -> None:
    """v1 兼容读取仍拒绝多余字段（契约不变）。"""
    with pytest.raises(PackParseError):
        parse_pack_output(
            {"version": "analysis_pack_v1", "period_end": "2025-09-27", "bogus": 1},
            FinancialAnalysisPack,
        )


# ---------------------------------------------------------------------------
# partial / unavailable 不导致调用链无意义崩溃
# ---------------------------------------------------------------------------


def test_parse_partial_and_unavailable_succeed() -> None:
    """partial / unavailable 都能被 parse_pack_output 正常解析为合法 pack。"""
    partial = parse_pack_output(
        _base(
            completeness=AnalysisCompleteness.PARTIAL.value,
            facts=[],
            metrics=[],
            limitations=["缺少 2024 年同期数据，增长率无法计算"],
        ),
        FinancialAnalysisPack,
    )
    assert partial.completeness == AnalysisCompleteness.PARTIAL

    unavailable = parse_pack_output(
        _base(
            completeness=AnalysisCompleteness.UNAVAILABLE.value,
            facts=[],
            metrics=[],
            unavailable_reason="未取得任何 SEC 财务事实",
        ),
        FinancialAnalysisPack,
    )
    assert unavailable.completeness == AnalysisCompleteness.UNAVAILABLE
    assert unavailable.unavailable_reason == "未取得任何 SEC 财务事实"


# ---------------------------------------------------------------------------
# 质量门禁：unavailable 含数据兜底拒绝
# ---------------------------------------------------------------------------


def _state_with(pack: FinancialAnalysisPack) -> ResearchFlowState:
    return ResearchFlowState(
        request=ResearchRequest(
            input_company="Test Corp",
            as_of_date=_PERIOD,
        ),
        analysis_pack=pack,
    )


def test_quality_gate_rejects_unavailable_with_content() -> None:
    """unavailable 但含 facts/metrics（绕过 Pydantic validator 直接构造）→ CRITICAL。"""
    # 用 model_construct 绕过模型层 validator，模拟"直接构造 state"的防御路径：
    # 正常解析已由模型层保证，但门禁仍应兜底拒绝这种畸形组合。
    forged = FinancialAnalysisPack.model_construct(
        schema_version="analysis_pack_v2",
        completeness=AnalysisCompleteness.UNAVAILABLE,
        facts=[_fact()],
        metrics=[],
        period_end=_PERIOD,
        version="analysis_pack_v2",
        analysis_notes=None,
        limitations=[],
        unavailable_reason="未取得任何 SEC 财务事实",
    )
    # 用 model_construct 绕过 ResearchFlowState 的嵌套校验，验证质量门禁的兜底判定：
    # classify_state 是独立纯函数，不经过 Pydantic 模型构造。
    forged_state = ResearchFlowState.model_construct(
        request=ResearchRequest(
            input_company="Test Corp",
            as_of_date=_PERIOD,
        ),
        analysis_pack=forged,
    )
    issues = classify_state(forged_state)
    codes = {i.code for i in issues}
    assert "unavailable_with_content" in codes


def test_quality_gate_allows_legal_unavailable() -> None:
    pack = FinancialAnalysisPack.model_validate(
        _base(
            completeness=AnalysisCompleteness.UNAVAILABLE.value,
            facts=[],
            metrics=[],
            unavailable_reason="未取得任何 SEC 财务事实",
        )
    )
    issues = classify_state(_state_with(pack))
    codes = {i.code for i in issues}
    # 仅 missing_report_draft（未提供报告）可能在列表；不得出现 unavailable_with_content
    assert "unavailable_with_content" not in codes
