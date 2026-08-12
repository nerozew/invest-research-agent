"""P02-19 CitationVerifierTool 契约测试。

验证目标（docs/05 P02-19 验收）：
- claim/source/locator 匹配：验证 claim 可被 source+locator 支撑；
- 数字引用一致性：claim 中的关键数字与 source.facts 一致（十进制比较）；
- 缺失 source：claim 无 source → 校验失败（不可接受）；
- locator 不匹配：source 存在但 locator 不在允许集合 → 校验失败；
- 空 claim：模型层拒绝；
- 满足 P02-01 Tool 契约（name + execute → ToolResult）。

不联网、不修改数据库、不依赖外部服务。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from invest_research.tools.base import Tool, ToolSuccess
from invest_research.tools.citation_verifier import (
    CitationCheckRequest,
    CitationCheckResult,
    CitationSourceRef,
    CitationVerifierTool,
    _fact_matches,
    verify_claim,
)


def _source(
    url: str = "https://www.microsoft.com/", facts: dict[str, str] | None = None
) -> CitationSourceRef:
    return CitationSourceRef(
        url=url,
        title="Microsoft",
        facts=facts or {"revenue": "245100000000"},
        locators=("page1", "section-2"),
    )


def _request(
    claim: str,
    key_numbers: list[str] | None = None,
    source: CitationSourceRef | None = None,
    locator: str | None = "page1",
) -> CitationCheckRequest:
    return CitationCheckRequest(
        claim=claim,
        key_numbers=key_numbers or [],
        source=source,
        locator=locator,
    )


def test_tool_satisfies_tool_contract() -> None:
    """CitationVerifierTool 满足 P02-01 Tool 契约。"""
    tool = CitationVerifierTool()
    assert tool.name == "citation_verifier"
    assert isinstance(tool, Tool)


def test_valid_claim_with_source_and_locator() -> None:
    """claim 有 source + 合法 locator + 数字可被 facts 支撑 → 校验通过。"""
    tool = CitationVerifierTool()
    result = tool.execute(
        _request(
            claim="Microsoft 2024 财年营收为 2451 亿美元",
            key_numbers=["245100000000"],
            source=_source(),
            locator="page1",
        )
    )

    assert isinstance(result, ToolSuccess)
    value = result.value
    assert isinstance(value, CitationCheckResult)
    assert value.valid is True
    assert value.failures == []


def test_missing_source_fails() -> None:
    """claim 无 source → 校验失败（MISSING_SOURCE）。"""
    tool = CitationVerifierTool()
    result = tool.execute(_request(claim="无来源的 claims 不可接受", key_numbers=[]))

    assert isinstance(result, ToolSuccess)
    assert result.value.valid is False
    assert any(f.code == "MISSING_SOURCE" for f in result.value.failures)


def test_invalid_locator_fails() -> None:
    """source 存在但 locator 不在允许集合 → 校验失败（INVALID_LOCATOR）。"""
    tool = CitationVerifierTool()
    result = tool.execute(
        _request(claim="微软官网首页", key_numbers=[], source=_source(), locator="page99")
    )

    assert isinstance(result, ToolSuccess)
    assert result.value.valid is False
    assert any(f.code == "INVALID_LOCATOR" for f in result.value.failures)


def test_number_not_supported_by_source_facts() -> None:
    """claim 的数字不在 source facts → 校验失败（NUMBER_UNSUPPORTED）。"""
    tool = CitationVerifierTool()
    result = tool.execute(
        _request(
            claim="营收为 999 亿美元",
            key_numbers=["99900000000"],
            source=_source(facts={"revenue": "245100000000"}),
        )
    )

    assert isinstance(result, ToolSuccess)
    assert result.value.valid is False
    assert any(f.code == "NUMBER_UNSUPPORTED" for f in result.value.failures)


def test_empty_claim_rejected_by_model() -> None:
    """空 claim → Pydantic ValidationError（模型层拒绝）。"""
    with pytest.raises(ValidationError):
        CitationCheckRequest(claim="", key_numbers=[], source=None, locator=None)


def test_verify_claim_pure_function() -> None:
    """纯函数 verify_claim：直接返回 (valid, failures)。"""
    ok, failures_ok = verify_claim(
        claim="微软 2024 财年营收 2451 亿美元",
        key_numbers=["245100000000"],
        source=_source(),
        locator="page1",
    )
    assert ok is True
    assert failures_ok == []

    bad, failures_bad = verify_claim(
        claim="微软营收 999 亿美元",
        key_numbers=["99900000000"],
        source=_source(),
        locator="page1",
    )
    assert bad is False
    assert any(f.code == "NUMBER_UNSUPPORTED" for f in failures_bad)


def test_fact_matches_decimal_comparison() -> None:
    """十进制比较：'245100000000' 与 '245100000000.00' 视为一致。"""
    assert _fact_matches("245100000000", "245100000000.00")
    assert not _fact_matches("245100000000", "999")


def test_constructor_with_injected_rules() -> None:
    """工具可注入自定义校验规则函数（依赖注入、可替换）。"""
    strict_rules = [
        lambda claim, numbers, source, locator: (
            False,
            [{"code": "CUSTOM_RULE", "message": "自定义规则"}],
        )
    ]
    tool = CitationVerifierTool(rules=strict_rules)
    result = tool.execute(_request(claim="通过自定义规则校验", key_numbers=[], source=_source()))

    assert isinstance(result, ToolSuccess)
    assert result.value.valid is False
    assert any(f.code == "CUSTOM_RULE" for f in result.value.failures)
