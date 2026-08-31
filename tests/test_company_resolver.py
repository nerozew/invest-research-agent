"""CompanyResolverTool 本地 SEC 快照与确定性解析契约测试。

验证目标（docs/05 P02-04 验收）：
- MSFT → 10 位 CIK（唯一命中，resolved=True）；
- 注入的歧义查询 → 返回多个候选（resolved=False），不静默猜测；
- 无匹配 → ToolFailure（INPUT_INVALID）；
- 大小写不敏感（"msft"/"Microsoft" 均命中）；
- 工具满足 P02-01 Tool 契约（name + execute，可 `isinstance(tool, Tool)`）；
- 返回的 CompanyIdentity.cik 均为 10 位数字（对齐 domain CompanyIdentity 校验）。

不发起真实网络请求；生产默认读取仓库内的版本化 SEC 快照。
"""

from __future__ import annotations

from invest_research.domain.models import CompanyIdentity
from invest_research.tools.base import Tool, ToolFailure, ToolSuccess
from invest_research.tools.company_resolver import (
    CompanyIndex,
    CompanyResolverTool,
    ResolveCompanyRequest,
    ResolveCompanyResponse,
    load_sec_company_index,
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
    """注入歧义场景 → resolved=False，返回多个候选，不猜测。"""
    custom = CompanyIndex(
        (
            (
                "delta",
                CompanyIdentity(cik="0000027904", ticker="DAL", legal_name="DELTA AIR LINES INC"),
            ),
            (
                "delta",
                CompanyIdentity(cik="0000329987", ticker="DLA", legal_name="DELTA APPAREL INC"),
            ),
        )
    )
    result = CompanyResolverTool(index=custom).execute(ResolveCompanyRequest(input_company="delta"))

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
    custom = CompanyIndex(
        (("tst", CompanyIdentity(cik="0000111111", ticker="TST", legal_name="TEST CO")),)
    )
    tool = CompanyResolverTool(index=custom)
    result = tool.execute(ResolveCompanyRequest(input_company="tst"))
    assert isinstance(result, ToolSuccess)
    assert result.value.resolved is True
    assert result.value.candidates[0].cik == "0000111111"


def test_live_benchmark_tickers_all_resolve_from_bundled_snapshot() -> None:
    """P06-11 固定 10 家公司不能再因演示 fixture 覆盖不足而失败。"""
    expected = {
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "AMZN": "0001018724",
        "JPM": "0000019617",
        "JNJ": "0000200406",
        "WMT": "0000104169",
        # 当前官方 SEC ticker 快照把 XOM 绑到子公司 0002115436；主实体覆盖表
        # 补入母公司 0000034088 并在精确 ticker 查询时唯一胜出。
        "XOM": "0000034088",
        "BA": "0000012927",
        "KO": "0000021344",
        "TSLA": "0001318605",
    }
    for ticker, cik in expected.items():
        result = _resolve(ticker)
        assert isinstance(result, ToolSuccess), ticker
        assert result.value.resolved is True, ticker
        assert result.value.candidates[0].cik == cik


def test_exact_cik_and_normalized_legal_name_resolve() -> None:
    """CIK 可省略前导零，法定名称允许大小写/标点差异但不做模糊猜测。"""
    by_cik = _resolve("1018724")
    by_name = _resolve("Amazon.com, Inc.")

    assert isinstance(by_cik, ToolSuccess)
    assert isinstance(by_name, ToolSuccess)
    assert by_cik.value.candidates[0].ticker == "AMZN"
    assert by_name.value.candidates[0].cik == "0001018724"


def test_snapshot_is_versioned_large_and_loaded_once() -> None:
    """防止生产包意外退化回少量演示 fixture，并验证进程内复用。"""
    first = load_sec_company_index()
    second = load_sec_company_index()

    assert first is second
    assert first.company_count is not None and first.company_count >= 5_000
    assert first.source_url == "https://www.sec.gov/files/company_tickers.json"
    assert first.retrieved_at


def test_lookup_scores_highest_match_for_partial_name() -> None:
    """部分名称匹配按最相符评分：名称前缀命中 parent（高分），包含命中 subsidiary（低分）。"""
    parent = CompanyIdentity(cik="0000034088", ticker="XOM", legal_name="EXXON MOBIL CORP")
    subsidiary = CompanyIdentity(
        cik="0002115436", ticker="XOM", legal_name="ExxonMobil Holdings Corp"
    )
    index = CompanyIndex([("XOM", parent), ("XOM", subsidiary)])
    # 查询 "Exxon Mobil" → 归一化后与 parent 名称前缀匹配（高分），与 subsidiary 包含匹配（低分）
    result = index.lookup("Exxon Mobil")
    assert [c.cik for c in result] == ["0000034088"]  # 最高分命中 parent


def test_lookup_exact_ticker_wins_over_name_substring() -> None:
    """精确 ticker 命中 → 两个实体同分（都是精确 ticker）→ 歧义，全部返回。"""
    index = CompanyIndex(
        [
            (
                "XOM",
                CompanyIdentity(cik="0000034088", ticker="XOM", legal_name="EXXON MOBIL CORP"),
            ),
            (
                "XOM",
                CompanyIdentity(
                    cik="0002115436", ticker="XOM", legal_name="ExxonMobil Holdings Corp"
                ),
            ),
        ]
    )
    result = index.lookup("XOM")
    assert len(result) == 2
    assert {c.cik for c in result} == {"0000034088", "0002115436"}


def test_lookup_no_match_returns_empty() -> None:
    """无任何 >0 分候选 → 返回空列表（上层据此 ToolFailure）。"""
    index = CompanyIndex([])
    assert index.lookup("TotallyUnknown Corp") == []


def test_override_entity_added_when_snapshot_missing() -> None:
    """主实体覆盖表把缺失的母公司补入快照索引（XOM → 0000034088）。"""
    from invest_research.tools.company_resolver import (
        MAIN_ENTITY_OVERRIDES,
        load_sec_company_index,
    )

    # 真实快照里 XOM 绑到子公司 0002115436，母公司 0000034088 不在 → 覆盖表应补入
    index = load_sec_company_index()
    parent = next((c for c in index.lookup("EXXON MOBIL CORP")), None)
    assert parent is not None and parent.cik == "0000034088"
    # XOM 精确 ticker 应唯一解析到主实体（覆盖表主实体 tie-break 胜出，不与子公司歧义）
    xom = index.lookup("XOM")
    assert [c.cik for c in xom] == ["0000034088"]
    # 覆盖表数据形状契约：ticker → {"cik", "legal_name"}
    assert MAIN_ENTITY_OVERRIDES["XOM"]["cik"] == "0000034088"
    assert MAIN_ENTITY_OVERRIDES["XOM"]["legal_name"] == "EXXON MOBIL CORP"


def test_override_exact_ticker_resolves_to_main_entity_uniquely() -> None:
    """XOM 精确 ticker 唯一解析到覆盖表主实体（不与 SEC 快照原绑定的子公司歧义）。"""
    from invest_research.tools.company_resolver import load_sec_company_index

    index = load_sec_company_index()
    assert [c.cik for c in index.lookup("XOM")] == ["0000034088"]


def test_execute_falls_back_to_online_search_when_local_miss() -> None:
    """本地索引未命中时，注入的在线搜索兜底：唯一候选 → resolved=True。"""
    from invest_research.tools.company_resolver import CompanyIndex, CompanyResolverTool

    index = CompanyIndex([])  # 本地空
    found = [CompanyIdentity(cik="9999999999", ticker="", legal_name="New Company Corp")]

    def online(query: str) -> list[CompanyIdentity]:
        assert query == "New Company"
        return found

    tool = CompanyResolverTool(index, online_search=online)
    result = tool.execute(ResolveCompanyRequest(input_company="New Company"))
    assert isinstance(result, ToolSuccess)
    assert result.value.resolved is True
    assert result.value.candidates[0].cik == "9999999999"


def test_execute_online_search_no_result_returns_failure() -> None:
    """在线兜底也无候选 → ToolFailure（本地未命中，在线搜索亦无结果）。"""
    from invest_research.domain.errors import ErrorCode
    from invest_research.tools.company_resolver import CompanyIndex, CompanyResolverTool

    tool = CompanyResolverTool(CompanyIndex([]), online_search=lambda q: [])
    result = tool.execute(ResolveCompanyRequest(input_company="Unknown"))
    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INPUT_INVALID


def test_execute_online_search_multiple_candidates_returns_ambiguity() -> None:
    """在线兜底返回多个候选 → resolved=False + candidates，不静默猜测。"""
    from invest_research.tools.company_resolver import CompanyIndex, CompanyResolverTool

    found = [
        CompanyIdentity(cik="1111111111", ticker="", legal_name="Alpha Co"),
        CompanyIdentity(cik="2222222222", ticker="", legal_name="Beta Co"),
    ]
    tool = CompanyResolverTool(CompanyIndex([]), online_search=lambda q: found)
    result = tool.execute(ResolveCompanyRequest(input_company="Alpha"))
    assert isinstance(result, ToolSuccess)
    assert result.value.resolved is False
    assert len(result.value.candidates) == 2


def test_execute_online_search_raises_returns_failure() -> None:
    """在线兜底抛异常 → 不崩溃，回退为 ToolFailure（INPUT_INVALID）。"""
    from invest_research.domain.errors import ErrorCode
    from invest_research.tools.company_resolver import CompanyIndex, CompanyResolverTool

    def online(query: str) -> list[CompanyIdentity]:
        raise RuntimeError("network down")

    tool = CompanyResolverTool(CompanyIndex([]), online_search=online)
    result = tool.execute(ResolveCompanyRequest(input_company="Unknown"))
    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INPUT_INVALID
