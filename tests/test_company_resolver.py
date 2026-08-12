"""P02-04 CompanyResolverTool 契约测试。

验证目标（docs/05 P02-04 验收）：
- MSFT → 10 位 CIK（唯一命中，resolved=True）；
- 歧义查询（如 "delta"）→ 返回多个候选（resolved=False），不静默猜测；
- 无匹配 → ToolFailure（INPUT_INVALID）；
- 大小写不敏感（"msft"/"Microsoft" 均命中）；
- 工具满足 P02-01 Tool 契约（name + execute，可 `isinstance(tool, Tool)`）；
- 返回的 CompanyIdentity.cik 均为 10 位数字（对齐 domain CompanyIdentity 校验）。

不发起真实网络请求（本地 fixture，符合 .clinerules"外部服务使用 mock/fixture"）。
"""

from __future__ import annotations

from invest_research.tools.base import Tool, ToolFailure, ToolSuccess
from invest_research.tools.company_resolver import (
    CompanyIndex,
    CompanyResolverTool,
    ResolveCompanyRequest,
    ResolveCompanyResponse,
)


def _resolve(input_company: str) -> ToolSuccess[ResolveCompanyResponse] | ToolFailure:
    """便捷执行：返回 ToolResult（成功或失败）。"""
    tool = CompanyResolverTool()
    return tool.execute(ResolveCompanyRequest(input_company=input_company))


def test_msft_resolves_to_10_digit_cik() -> None:
    """验收主场景：MSFT → 唯一命中，CIK 为 10 位。"""
    result = _resolve("MSFT")

    assert isinstance(result, ToolSuccess)
    assert not isinstance(result, ToolFailure)
    assert result.value.resolved is True
    assert len(result.value.candidates) == 1

    identity = result.value.candidates[0]
    assert identity.ticker == "MSFT"
    assert identity.cik == "0000789019"
    assert len(identity.cik) == 10  # 10 位数字（domain 校验保证）

    # 返回的 CompanyIdentity 本身就是 10 位数字字符串
    assert identity.cik.isdigit()


def test_company_name_also_resolves() -> None:
    """名称 'Microsoft' 同样唯一命中（ticker/名称双索引）。"""
    result = _resolve("Microsoft")

    assert isinstance(result, ToolSuccess)
    assert result.value.resolved is True
    assert result.value.candidates[0].cik == "0000789019"
    assert result.value.candidates[0].legal_name == "MICROSOFT CORP"


def test_case_insensitive_lookup() -> None:
    """大小写不敏感：'msft'/'MSFT'/'MsFt' 均命中同一 CIK。"""
    for query in ("msft", "MSFT", "MsFt"):
        result = _resolve(query)
        assert isinstance(result, ToolSuccess)
        assert result.value.resolved is True
        assert result.value.candidates[0].cik == "0000789019"


def test_ambiguous_query_returns_candidates() -> None:
    """歧义场景（'delta' 多家公司）→ resolved=False，返回多个候选，不猜测。"""
    result = _resolve("delta")

    assert isinstance(result, ToolSuccess)
    assert result.value.resolved is False
    assert len(result.value.candidates) >= 2

    # 所有候选都含 10 位 CIK
    for identity in result.value.candidates:
        assert len(identity.cik) == 10
        assert identity.cik.isdigit()


def test_no_match_returns_failure() -> None:
    """无匹配 → ToolFailure（INPUT_INVALID，不静默猜测）。"""
    result = _resolve("nonexistent-company-xyz")

    assert isinstance(result, ToolFailure)
    assert not isinstance(result, ToolSuccess)
    from invest_research.domain.errors import ErrorCode

    assert result.error.error_code == ErrorCode.INPUT_INVALID
    assert result.error.is_retryable is False


def test_resolver_satisfies_tool_contract() -> None:
    """CompanyResolverTool 满足 P02-01 Tool 契约（结构 + 运行时）。"""
    tool = CompanyResolverTool()
    assert tool.name == "company_resolver"
    assert isinstance(tool, Tool)


def test_resolve_company_response_roundtrip() -> None:
    """响应模型 JSON 往返无损（不可变契约）。"""
    result = _resolve("MSFT")
    assert isinstance(result, ToolSuccess)
    data = result.value.model_dump_json()
    restored = ResolveCompanyResponse.model_validate_json(data)
    assert restored == result.value


def test_custom_index_injectable() -> None:
    """可注入自定义索引（依赖注入，便于测试隔离/扩展）。"""
    from invest_research.domain.models import CompanyIdentity

    custom = CompanyIndex(
        (("tst", CompanyIdentity(cik="0000111111", ticker="TST", legal_name="TEST CO")),)
    )
    tool = CompanyResolverTool(index=custom)
    result = tool.execute(ResolveCompanyRequest(input_company="tst"))
    assert isinstance(result, ToolSuccess)
    assert result.value.resolved is True
    assert result.value.candidates[0].cik == "0000111111"
