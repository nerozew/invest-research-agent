"""P02-19 CitationVerifierTool（claim/source/locator 校验，P02-01 契约工具）。

职责：验证一条 claim（报表中的事实句）是否可被 source + locator 支撑，
且 claim 中的关键数字与 source.facts 提供的值一致（十进制比较）。
- 纯函数 ``verify_claim``：返回 (valid, failures)，可直接单测；
- ``CitationVerifierTool``：P02-01 契约工具，把纯逻辑包成 ToolResult；
  构造时可注入自定义 rules（依赖注入，便于扩展/替换校验规则）。

校验规则（内置）：
1. ``MISSING_SOURCE``：claim 没有 source → 不可验证（失败）；
2. ``INVALID_LOCATOR``：source.locators 非空且不包含所给 locator → 失败；
3. ``NUMBER_UNSUPPORTED``：claim 的 key_numbers 中任一数字无法被
   source.facts 中的某个值十进制匹配 → 失败（数字引用不可追溯）。

依赖边界：只依赖标准库、Pydantic 与 tools/base；
禁止导入 CrewAI/FastAPI/SQLAlchemy/httpx。
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from invest_research.tools.base import ToolResult, ToolSuccess

# 内置校验规则签名：返回 (valid, failures)
Rule = Callable[
    [str, list[str], "CitationSourceRef | None", str | None],
    tuple[bool, list[dict[str, str]]],
]


class CitationSourceRef(BaseModel):
    """校验所需的 source 引用元数据（URL/标题/事实表/允许的 locator）。"""

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    title: str | None = None
    facts: dict[str, str] = Field(default_factory=dict)  # concept → 值（字符串，保留精度）
    locators: tuple[str, ...] = Field(default_factory=tuple)


class CitationCheckRequest(BaseModel):
    """校验请求：claim 句子 + 关键数字 + source + locator。"""

    model_config = ConfigDict(frozen=True)

    claim: str = Field(min_length=1, max_length=2000)
    key_numbers: list[str] = Field(default_factory=list)  # 十进制字符串（如 "245100000000"）
    source: CitationSourceRef | None = None
    locator: str | None = None


class ChecklistFailure(BaseModel):
    """单条校验失败项（code + message）。"""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class CitationCheckResult(BaseModel):
    """校验结果：valid + failures（可测试、可追溯）。"""

    model_config = ConfigDict(frozen=True)

    valid: bool
    failures: list[ChecklistFailure] = Field(default_factory=list)


def _fact_matches(key_number: str, fact_value: str) -> bool:
    """十进制比较：'245100000000' 与 '245100000000.00' 视为一致。"""
    try:
        return Decimal(key_number) == Decimal(fact_value)
    except InvalidOperation:
        return False


def number_is_supported_by_facts(number: str, fact_values: Iterable[str]) -> bool:
    """公开纯函数：判断数字能否被一组事实值十进制匹配（供评测模块批量对账复用）。

    - ``'200'`` 与 ``'200.00'`` / ``'2.0E2'`` 视为一致；
    - 非法十进制输入返回 False（不抛异常）。
    """
    try:
        target = Decimal(number)
    except InvalidOperation:
        return False
    for fact_value in fact_values:
        try:
            if target == Decimal(fact_value):
                return True
        except InvalidOperation:
            continue
    return False


def verify_claim(
    claim: str,
    key_numbers: list[str],
    source: CitationSourceRef | None,
    locator: str | None,
) -> tuple[bool, list[ChecklistFailure]]:
    """纯函数：校验 claim 是否可被 source+locator 支撑、数字是否一致。"""
    failures: list[ChecklistFailure] = []

    # 规则 1：必须有 source
    if source is None:
        failures.append(ChecklistFailure(code="MISSING_SOURCE", message="claim 缺少来源"))
        return False, failures

    # 规则 2：locator 必须在允许集合内（允许集合为空则跳过，容忍未提供 locator 元数据）
    if source.locators and locator is not None and locator not in source.locators:
        failures.append(
            ChecklistFailure(code="INVALID_LOCATOR", message=f"locator 不在允许集合: {locator}")
        )

    # 规则 3：每个关键数字必须被 source.facts 的某个值十进制匹配
    fact_values = [v for v in source.facts.values()]
    for number in key_numbers:
        if not number_is_supported_by_facts(number, fact_values):
            failures.append(
                ChecklistFailure(code="NUMBER_UNSUPPORTED", message=f"数字无法被来源支撑: {number}")
            )

    return len(failures) == 0, failures


# 内置规则集（与 verify_claim 行为一致；便于工具层复用）
_DEFAULT_RULES: list[Rule] = [
    lambda claim, numbers, source, locator: (
        verify_claim(claim, numbers, source, locator)[0],
        [
            {"code": f.code, "message": f.message}
            for f in verify_claim(claim, numbers, source, locator)[1]
        ],
    )
]


class CitationVerifierTool:
    """P02-01 契约工具：把 verify_claim 包成 ToolResult；可注入自定义 rules。"""

    name = "citation_verifier"

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules = rules if rules is not None else _DEFAULT_RULES

    def execute(self, request: CitationCheckRequest) -> ToolResult[CitationCheckResult]:
        failures: list[ChecklistFailure] = []
        for rule in self._rules:
            valid, rule_failures = rule(
                request.claim, request.key_numbers, request.source, request.locator
            )
            failures.extend(ChecklistFailure(**f) for f in rule_failures)
        return ToolSuccess(value=CitationCheckResult(valid=len(failures) == 0, failures=failures))
