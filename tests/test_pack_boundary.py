"""P06-09B：统一 PackBoundary 测试。

覆盖：合法三态 / 非法 JSON / 缺必填 / 多余字段 / 代码围栏 / Action Input 拒绝 /
一次修复成功 / 修复一次仍失败 / 数据缺失转 partial+unavailable / 修复无外部工具 /
Writer 读真实上游 Pack / 错误脱敏（不泄露 API Key 与本地绝对路径）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.agents.pack_parsing import (
    BoundaryError,
    PackBoundary,
    PackSourceKind,
    identify_source_kind,
)
from invest_research.agents.writer_task import make_artifact_reader
from invest_research.domain.models import (
    AnalysisCompleteness,
    FinancialAnalysisPack,
    FinancialFact,
    MetricResult,
    MetricStatus,
)

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
        "period_end": _PERIOD.isoformat(),
        "facts": [_fact().model_dump(mode="json")],
        "metrics": [],
        "analysis_notes": None,
        "limitations": [],
        "completeness": AnalysisCompleteness.COMPLETE.value,
        "unavailable_reason": None,
    }
    data.update(overrides)
    return data


def _boundary() -> PackBoundary:
    return PackBoundary(max_repairs=1)


# ---------------------------------------------------------------------------
# 合法三态
# ---------------------------------------------------------------------------


def test_complete_parses() -> None:
    pack, errors = _boundary().parse(_base(), FinancialAnalysisPack, stage="analysis")
    assert errors == []
    assert pack is not None
    assert pack.completeness == AnalysisCompleteness.COMPLETE


def test_partial_parses() -> None:
    pack, errors = _boundary().parse(
        _base(completeness="partial", facts=[], limitations=["缺少 2024 年同期数据"]),
        FinancialAnalysisPack,
        stage="analysis",
    )
    assert errors == []
    assert pack is not None
    assert pack.completeness == AnalysisCompleteness.PARTIAL


def test_unavailable_parses_as_legal_business_result() -> None:
    pack, errors = _boundary().parse(
        _base(
            completeness="unavailable",
            facts=[],
            metrics=[],
            limitations=[],
            unavailable_reason="未取得任何 SEC 财务事实",
        ),
        FinancialAnalysisPack,
        stage="analysis",
    )
    assert errors == []
    assert pack is not None
    assert pack.completeness == AnalysisCompleteness.UNAVAILABLE
    assert pack.unavailable_reason == "未取得任何 SEC 财务事实"


# ---------------------------------------------------------------------------
# 结构错误
# ---------------------------------------------------------------------------


def test_invalid_json_reports_json_invalid() -> None:
    pack, errors = _boundary().parse(
        "{ not json", FinancialAnalysisPack, stage="analysis"
    )
    assert pack is None
    assert any(e.error_code == "JSON_INVALID" for e in errors)


def test_missing_required_field_reports_missing() -> None:
    bad = dict(_base())
    del bad["period_end"]
    pack, errors = _boundary().parse(bad, FinancialAnalysisPack, stage="analysis")
    assert pack is None
    assert any(e.error_code in ("MISSING_FIELD", "VALUE_INVALID") for e in errors)


def test_extra_field_reports_extra() -> None:
    bad = dict(_base())
    bad["not_a_field"] = 1
    pack, errors = _boundary().parse(bad, FinancialAnalysisPack, stage="analysis")
    assert pack is None
    assert any(e.error_code == "EXTRA_FIELD" for e in errors)


def test_code_fence_json_parses() -> None:
    text = "```json\n" + _base_json_text() + "\n```"
    pack, errors = _boundary().parse(text, FinancialAnalysisPack, stage="analysis")
    assert errors == []
    assert pack is not None
    assert pack.completeness == AnalysisCompleteness.COMPLETE


def _base_json_text() -> str:
    import json

    return json.dumps(_base(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 工具调用 / Action Input 拒绝
# ---------------------------------------------------------------------------


def test_action_input_rejected() -> None:
    obj = {"action": "FinancialCalculator", "action_input": {"metric_name": "revenue_growth"}}
    assert identify_source_kind(obj) == PackSourceKind.ACTION_INPUT
    pack, errors = _boundary().parse(obj, FinancialAnalysisPack, stage="analysis")
    assert pack is None
    assert any(e.error_code == "NOT_A_PACK" for e in errors)


def test_tool_params_rejected() -> None:
    obj = {"tool_name": "FinancialCalculator", "arguments": {"metric_name": "gross_margin"}}
    assert identify_source_kind(obj) == PackSourceKind.TOOL_PARAMS
    pack, errors = _boundary().parse(obj, FinancialAnalysisPack, stage="analysis")
    assert pack is None
    assert any(e.error_code == "NOT_A_PACK" for e in errors)


def test_plain_text_rejected() -> None:
    assert identify_source_kind("这里是一些自然语言说明，没有结构。") == PackSourceKind.PLAIN_TEXT
    pack, errors = _boundary().parse(
        "这里是一些自然语言说明，没有结构。", FinancialAnalysisPack, stage="analysis"
    )
    assert pack is None
    assert any(e.error_code == "NOT_A_PACK" for e in errors)


# ---------------------------------------------------------------------------
# 有限修复（至多一次）
# ---------------------------------------------------------------------------


def test_repair_once_success() -> None:
    """一次修复成功：修复器把非法 JSON 修正为合法输出。"""
    calls: list[int] = []

    def fixer(_raw: str, _errors: list[BoundaryError]) -> object:
        calls.append(1)
        return _base()

    pack, errors = _boundary().parse(
        "{ malformed", FinancialAnalysisPack, stage="analysis", fixer=fixer
    )
    assert len(calls) == 1
    assert errors == []
    assert pack is not None


def test_repair_fails_after_one_attempt_returns_original_errors() -> None:
    """修复一次仍失败：不循环修复，返回稳定错误分类。"""
    calls: list[int] = []

    def fixer(_raw: str, _errors: list[BoundaryError]) -> object:
        calls.append(1)
        return {"still": "not-a-pack"}

    pack, errors = _boundary().parse(
        "{ malformed", FinancialAnalysisPack, stage="analysis", fixer=fixer
    )
    assert len(calls) == 1  # 至多一次，禁止循环修复
    assert pack is None
    assert any(e.error_code == "JSON_INVALID" for e in errors)


def test_semantics_errors_are_not_repairable() -> None:
    """业务跨字段矛盾（completeness 与内容冲突）不进入格式修复，修复器不被调用。"""
    calls: list[int] = []

    def fixer(_raw: str, _errors: list[BoundaryError]) -> object:
        calls.append(1)
        return _base()

    bad = _base(completeness="unavailable", facts=[_fact().model_dump(mode="json")])
    pack, errors = _boundary().parse(bad, FinancialAnalysisPack, stage="analysis", fixer=fixer)
    assert calls == []  # semantics 错误不触发修复器（禁止修复器发明/改写业务数据）
    assert pack is None
    assert any(e.stage == "semantics" for e in errors)


def test_fixer_has_no_external_tool_access() -> None:
    """修复器只能拿到原始文本+结构化错误，无法调用 SEC/Serper/文件/计算器。"""
    captured: list[tuple[str, list[BoundaryError]]] = []

    def fixer(raw: str, errors: list[BoundaryError]) -> object:
        captured.append((raw, errors))
        return _base()

    _boundary().parse("{bad", FinancialAnalysisPack, stage="analysis", fixer=fixer)
    assert len(captured) == 1
    raw, errors = captured[0]
    assert isinstance(raw, str)
    assert isinstance(errors, list)
    assert all(isinstance(e, BoundaryError) for e in errors)


# ---------------------------------------------------------------------------
# 数据缺失 → partial/unavailable（由模型层表达，不伪造 complete）
# ---------------------------------------------------------------------------


def test_missing_data_becomes_partial_with_limitations() -> None:
    pack, errors = _boundary().parse(
        _base(
            completeness="partial",
            facts=[],
            metrics=[],
            limitations=["未取得 Revenue 事实，无法计算收入增长率"],
        ),
        FinancialAnalysisPack,
        stage="analysis",
    )
    assert errors == []
    assert pack is not None
    assert pack.completeness == AnalysisCompleteness.PARTIAL
    assert pack.limitations


def test_no_data_becomes_unavailable_with_reason() -> None:
    pack, errors = _boundary().parse(
        _base(
            completeness="unavailable",
            facts=[],
            metrics=[],
            limitations=[],
            unavailable_reason="未取得任何 SEC 财务事实",
        ),
        FinancialAnalysisPack,
        stage="analysis",
    )
    assert errors == []
    assert pack is not None
    assert pack.completeness == AnalysisCompleteness.UNAVAILABLE
    assert pack.unavailable_reason


# ---------------------------------------------------------------------------
# Writer 能读取真实上游 Pack（ArtifactReader 统一读取顺序）
# ---------------------------------------------------------------------------


def test_writer_artifact_reader_reads_real_upstream_pack() -> None:
    """Writer 的 ArtifactReader 与解析侧读到同一份真实上游内容（P06-09 收口）。"""
    real_pack = FinancialAnalysisPack.model_validate(_base())
    store = {"analysis_pack": real_pack.model_dump(mode="json")}
    reader = make_artifact_reader(lambda key: store.get(key))
    result = reader.run("analysis_pack")
    assert result["status"] == "readable"
    content = result["content"]
    assert isinstance(content, dict)
    assert content["schema_version"] == "analysis_pack_v2"
    assert content["completeness"] == "complete"
    assert content["facts"][0]["concept"] == "Revenues"


# ---------------------------------------------------------------------------
# 错误脱敏：不泄露 API Key 与本地绝对路径
# ---------------------------------------------------------------------------

_SENSITIVE_OUTPUT = (
    '{"version": "analysis_pack_v2", "api_key": "sk-ABCD1234", "token": "tok_sec", '
    '"path": "C:\\\\Users\\\\NeroZe\\\\secret\\\\file", '
    '"period_end": "2025-09-27", '
    '"completeness": "complete", "facts": [], "metrics": [], '
    '"schema_version": "analysis_pack_v2", "unavailable_reason": null, "limitations": []}'
)


def test_error_message_redacts_api_key_and_absolute_paths() -> None:
    """BoundaryError.actual/detail 不包含 API Key 明文或本地绝对路径。"""
    pack, errors = _boundary().parse(
        _SENSITIVE_OUTPUT, FinancialAnalysisPack, stage="analysis"
    )
    assert pack is None
    blob = " | ".join(
        f"{e.actual or ''} {e.detail or ''} {e.expected or ''}" for e in errors
    )
    assert "sk-ABCD1234" not in blob
    assert "tok_sec" not in blob
    assert "NeroZe" not in blob
    assert "C:\\Users\\" not in blob
