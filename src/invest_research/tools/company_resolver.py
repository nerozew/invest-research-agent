"""CompanyResolverTool（P02-04）：名称/ticker → 10 位 CIK，歧义返回候选。

设计目标对齐：
- docs/05-DEVELOPMENT-ROADMAP.md P02-04：tool + SEC ticker fixture，
  MSFT → 10 位 CIK；歧义返回候选（不静默猜测）；
- docs/01-PRD.md FR-002：公司身份解析，歧义时返回候选交给用户；
- P02-01 Tool 契约：输入/输出均为 Pydantic，成功/失败返回 ToolResult。

范围：本工具只做**本地实体解析**（基于内置 SEC ticker fixture），
不发起真实 SEC 网络请求（真实 submissions 调用属于 P02-05）。
外部服务在测试中用 fixture，符合 .clinerules"外部服务必须使用 mock/fixture"。

依赖边界：本模块只依赖 pydantic、domain 层与 tools/base.py 契约；
禁止导入 CrewAI、FastAPI、httpx 等。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import CompanyIdentity
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess

# ---------------------------------------------------------------------------
# 输入/输出契约
# ---------------------------------------------------------------------------


class ResolveCompanyRequest(BaseModel):
    """公司解析请求：接受公司名称或股票代码（均去空白后非空）。"""

    model_config = {"frozen": True}

    input_company: str = Field(min_length=1, max_length=200)

    @field_validator("input_company")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("input_company 不能为空或纯空白")
        return cleaned


class ResolveCompanyResponse(BaseModel):
    """公司解析结果。

    - resolved=True：candidates 恰好 1 个（唯一命中）；
    - resolved=False：candidates 有多个（歧义，交给用户选择）。
    """

    model_config = {"frozen": True}

    resolved: bool
    candidates: list[CompanyIdentity]


# ---------------------------------------------------------------------------
# 本地 SEC ticker fixture（P02-04 演示与契约测试用）
# ---------------------------------------------------------------------------
# 格式：(key, CompanyIdentity)，key 为大小写不敏感的 ticker/常用名。
# 唯一命中样例：MSFT/Microsoft → CIK 0000789019。
# 歧义样例："Delta" 对应多家公司 —— 演示"歧义返回候选，不静默猜测"。

_SEC_TICKER_FIXTURE: tuple[tuple[str, CompanyIdentity], ...] = (
    (
        "msft",
        CompanyIdentity(
            cik="0000789019", ticker="MSFT", legal_name="MICROSOFT CORP", exchange="NASDAQ"
        ),
    ),
    (
        "microsoft",
        CompanyIdentity(
            cik="0000789019", ticker="MSFT", legal_name="MICROSOFT CORP", exchange="NASDAQ"
        ),
    ),
    (
        "aapl",
        CompanyIdentity(cik="0000320193", ticker="AAPL", legal_name="APPLE INC", exchange="NASDAQ"),
    ),
    (
        "apple",
        CompanyIdentity(cik="0000320193", ticker="AAPL", legal_name="APPLE INC", exchange="NASDAQ"),
    ),
    (
        "delta",
        CompanyIdentity(
            cik="0000027904", ticker="DAL", legal_name="DELTA AIR LINES INC", exchange="NYSE"
        ),
    ),
    (
        "delta",
        CompanyIdentity(
            cik="0000329987", ticker="DLA", legal_name="DELTA APPAREL INC", exchange="NASDAQ"
        ),
    ),
)


# ---------------------------------------------------------------------------
# 实体解析器
# ---------------------------------------------------------------------------


class CompanyIndex:
    """大小写不敏感的公司索引：key（ticker/名称）→ 候选 list[CompanyIdentity]。

    同一 key 可能映射到多个公司（歧义场景），故值为列表。
    """

    def __init__(self, entries: tuple[tuple[str, CompanyIdentity], ...]) -> None:
        self._index: dict[str, list[CompanyIdentity]] = {}
        for key, identity in entries:
            self._index.setdefault(key.lower(), []).append(identity)

    def lookup(self, query: str) -> list[CompanyIdentity]:
        """按（去空白+小写后的）查询词返回候选；无匹配返回空列表。"""
        return list(self._index.get(query.strip().lower(), []))


class CompanyResolverTool:
    """本地公司解析工具：唯一命中→成功；歧义→候选交给用户；无→失败。

    结构上满足 P02-01 的 Tool 契约（name + execute）。
    """

    name = "company_resolver"

    def __init__(self, index: CompanyIndex | None = None) -> None:
        self._index = index if index is not None else CompanyIndex(_SEC_TICKER_FIXTURE)

    def execute(self, request: ResolveCompanyRequest) -> ToolResult[ResolveCompanyResponse]:
        candidates = self._index.lookup(request.input_company)

        if not candidates:
            return ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.INPUT_INVALID,
                    message=f"未找到公司: {request.input_company}",
                )
            )
        if len(candidates) == 1:
            return ToolSuccess(value=ResolveCompanyResponse(resolved=True, candidates=candidates))
        # 歧义：不静默猜测，返回所有候选交给用户（对应 PRD FR-002）
        return ToolSuccess(value=ResolveCompanyResponse(resolved=False, candidates=candidates))
