"""P03-09 schema guardrail 测试（纯函数，不联网）。

验证目标：
- validate_against_schema：合法 JSON 通过、非法 JSON 被拒且返回可读错误；
- build_fix_prompt：修复指令包含错误信息；
- run_with_guardrail：首次合法返回 attempt=1、先非法后合法返回 attempt 次数、始终非法按上限截断。
"""

from __future__ import annotations

from invest_research.agents.guardrails import (
    GuardrailOutcome,
    build_fix_prompt,
    run_with_guardrail,
    validate_against_schema,
)
from invest_research.domain.models import ReportDraft


def _valid_draft() -> dict[str, object]:
    return {
        "version": "report_draft_v1",
        "title": "微软研究初稿",
        "markdown": "# 微软\n> 非投资建议",
        "citation_keys": ["claim-1"],
    }


def test_valid_json_passes() -> None:
    valid, errors = validate_against_schema(_valid_draft(), ReportDraft)
    assert valid is True
    assert errors == []


def test_missing_field_rejected() -> None:
    payload: dict[str, object] = {"version": "report_draft_v1", "markdown": "# x"}  # 缺 title
    valid, errors = validate_against_schema(payload, ReportDraft)
    assert valid is False
    assert any("title" in e for e in errors)


def test_invalid_json_string() -> None:
    valid, errors = validate_against_schema("{not json", ReportDraft)
    assert valid is False
    assert errors  # 非空（包含"不是合法 JSON"或 Pydantic 解析错误）


def test_build_fix_prompt_contains_errors() -> None:
    hint = build_fix_prompt(["title: Field required"], "schema: ReportDraft")
    assert "上一次输出未通过结构化校验" in hint
    assert "title: Field required" in hint
    assert "schema: ReportDraft" in hint


def test_run_with_guardrail_first_attempt_valid() -> None:
    calls: list[str | None] = []

    def producer(hint: str | None) -> dict[str, object]:
        calls.append(hint)
        return _valid_draft()

    outcome = run_with_guardrail(producer, ReportDraft, "hint")
    assert outcome.valid is True
    assert outcome.attempts_used == 1
    assert calls == [None]  # 首次 hint=None，无修复指令


def test_run_with_guardrail_repairs_on_second_attempt() -> None:
    calls: list[str | None] = []

    def producer(hint: str | None) -> dict[str, object]:
        calls.append(hint)
        if hint is None:
            bad: dict[str, object] = {
                "version": "report_draft_v1",
                "markdown": "# x",
            }  # 非法，缺 title
            return bad
        return _valid_draft()  # 收到修复指令后产出合法

    outcome = run_with_guardrail(producer, ReportDraft, "hint")
    assert outcome.valid is True
    assert outcome.attempts_used == 2
    assert calls[0] is None
    assert calls[1] is not None and "title" in calls[1]  # 修复指令含错误


def test_run_with_guardrail_exhausts_attempts() -> None:
    calls = 0

    def producer(hint: str | None) -> dict[str, object]:
        nonlocal calls
        calls += 1
        bad: dict[str, object] = {"version": "bad"}  # 始终非法
        return bad

    outcome = run_with_guardrail(producer, ReportDraft, "hint", max_attempts=3)
    assert outcome.valid is False
    assert outcome.attempts_used == 3
    assert calls == 3  # 恰好 3 次，不无限重试
    assert outcome.errors  # 未决错误已记录


def test_guardrail_outcome_is_serializable() -> None:
    o = GuardrailOutcome(valid=False, attempts_used=3, errors=["a"])
    assert o.model_dump() == {"valid": False, "attempts_used": 3, "errors": ["a"]}
