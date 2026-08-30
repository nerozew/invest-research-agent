"""L2 兜底：LLM 从 10-K 原文提取缺失数值（有界、可追溯、强验证）。

触发：L1 确定性推导（annual_statements / annual_comparison 的候选概念 + 勾稽推导）
之后仍缺失的指标。LLM 在 10-K 解析块（``AnnualParsedTextBlock``）的财务报表区域内
定位指标所在行并读取数值；代码强验证后才采纳，避免 LLM 凭空造数：

- ``value`` 必须是可解析的有限 Decimal（现金流量表净变动允许为负，符号语义由调用方判定）；
- ``locator`` 必须真实存在于输入块（LLM 不能编造位置）；
- ``excerpt`` 必须包含指标的英文关键字或其措辞变体（证明数值来自文件中对应行）。

输入脱敏：只给块文本 + 指标描述，不含 company/accession 等高基数。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from invest_research.agents.llm_factory import LLMRole

# 财务报表区域宽泛关键字（用于选取有界块子集，避免把整份 10-K 喂给 LLM）。
# 注意：这些宽泛关键字常被 10-K 前部叙述章节（Risk Factors / MD&A）大量引用，
# 因此仅作为二级选择（低价值），见 ``_select_financial_blocks`` 的两阶段设计。
_FINANCIAL_KEYWORDS = (
    "consolidated",
    "statement of operations",
    "statements of operations",
    "income statement",
    "balance sheet",
    "statement of cash flows",
    "statements of cash flows",
    "statement of comprehensive income",
)
# 报表表名标题关键字：命中即视为真实报表区域（一级选择，高价值）。
_STATEMENT_TITLE_KEYWORDS = (
    "consolidated statements of income",
    "consolidated balance sheets",
    "consolidated statements of cash flows",
    "consolidated statements of operations",
    "consolidated statements of earnings",
)
# 每个财务报表关键字块前后纳入的邻居块数（补全表格行上下文）。
_NEIGHBOR_BLOCKS = 5
# 单块截断字符上限（控制 LLM 输入体积）。
_BLOCK_CHARS = 300
# 报表命中块配额上限：报表区域优先占满（远小于 10-K 数千块）。
_MAX_BLOCKS = 200
# 纯叙述输入（无报表表名/行命中）时的有界上限：沿用旧 60 块，避免叙述引用语无限占配额。
_MAX_NARRATIVE_BLOCKS = 60
# 报表区域锚定半径（块数）：以报表标题块为中心，行级关键字命中只在此窗口内收集，
# 防止 10-K 前部叙述章节（MD&A 等）大量引用通用行词（net income / gross profit）
# 耗尽配额、挤掉 offset 更靠后的真实报表区域。
_STATEMENT_REGION_RADIUS = 60
# 无标题锚点时的行级关键字子配额（纯 row 命中、无报表标题的退化输入）。
_ROW_KEYWORD_BUDGET = 60
# 报表行级关键字：现金流量表/利润表的特定行可能远离标题块（如末尾的"汇率影响"），
# 需额外把含这些关键字的块选入，否则 LLM 看不到 → 提取失败。
_ROW_KEYWORDS = (
    "effect of exchange rate",
    "exchange rate changes",
    "net increase (decrease) in cash",
    "net change in cash",
    "change in cash",  # NVDA 原文措辞（如 "Change in cash and cash equivalents"）
    "cash and cash equivalents, end",
    "cash and cash equivalents at end",  # 无逗号变体
    "net cash provided by operating activities",
    "net cash used in operating activities",
    "net cash provided by (used in) investing activities",
    "operating expenses",
    "cost of revenue",
    "net income",  # 利润表/现金流量表锚点
    "gross profit",
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ExtractedFact:
    """LLM 从 10-K 原文提取的单个数值（已验证可追溯）。"""

    metric_name: str
    value: Decimal
    locator: str
    excerpt: str


def _select_financial_blocks(blocks: Iterable[Any]) -> list[tuple[str, str]]:
    """选取含财务报表关键字的块及其邻居，报表区域优先、每块截断。

    三阶段选择（根因：10-K 前部叙述章节大量引用 "consolidated financial
    statements" 及通用行词 net income / gross profit 等，若按出现顺序收集会占满
    配额，导致 offset 更靠后的真实报表区域进不了 LLM 输入）：

    - 阶段一：收集报表表名标题命中的块（数量少，保证进选中集合并排在最前）；
    - 阶段二：收集行级关键字命中的块——有标题锚点时限定在标题附近的报表区域
      （``_STATEMENT_REGION_RADIUS``），无锚点时按 ``_ROW_KEYWORD_BUDGET`` 子配额；
    - 阶段三：收集宽泛财务关键字命中的块（低价值，叙述章节引用语）；
    - 合并时报表命中块排在前面，``_MAX_BLOCKS`` 截断发生在报表命中块之后，
      保证报表内容不被叙述块挤掉；纯叙述输入（无报表命中）沿用旧有界上限。

    返回 ``(locator, text)`` 列表。
    """
    items = list(blocks)

    def collect(hits: Iterable[int], into: dict[str, str]) -> None:
        """把命中块及其邻居写入 ``into``，每块截断到 ``_BLOCK_CHARS``。"""
        for index in hits:
            start = max(0, index - _NEIGHBOR_BLOCKS)
            end = min(len(items), index + _NEIGHBOR_BLOCKS + 1)
            for neighbor in range(start, end):
                ntext = (items[neighbor].text or "").strip()
                nloc = items[neighbor].locator
                if ntext and nloc not in into:
                    into[nloc] = ntext[:_BLOCK_CHARS]

    title_hits: list[int] = []
    row_hits: list[int] = []
    secondary_hits: list[int] = []
    for index, block in enumerate(items):
        lowered = (block.text or "").strip().lower()
        if any(kw in lowered for kw in _STATEMENT_TITLE_KEYWORDS):
            title_hits.append(index)
        elif any(kw in lowered for kw in _ROW_KEYWORDS):
            row_hits.append(index)
        elif any(kw in lowered for kw in _FINANCIAL_KEYWORDS):
            secondary_hits.append(index)

    priority: dict[str, str] = {}
    # 阶段一：报表标题命中（数量少）先收集，保证进选中集合且排在最前，截断时不被尾部丢掉。
    collect(title_hits, priority)
    if title_hits:
        # 阶段二（有锚点）：行级关键字命中限定在标题块附近的报表区域，MD&A 远处
        # 的 row 词不占配额。
        lo = max(0, min(title_hits) - _STATEMENT_REGION_RADIUS)
        hi = min(len(items), max(title_hits) + _STATEMENT_REGION_RADIUS + 1)
        collect([i for i in row_hits if lo <= i < hi], priority)
    else:
        # 阶段二（无锚点）：行级关键字命中按子配额有界收集。
        collect(row_hits[:_ROW_KEYWORD_BUDGET], priority)
    # 阶段三：宽泛财务关键字命中（低价值，叙述章节引用语）。
    secondary: dict[str, str] = {}
    collect(secondary_hits, secondary)
    ordered_priority = [(loc, priority[loc]) for loc in priority]
    ordered_secondary = [(loc, secondary[loc]) for loc in secondary if loc not in priority]
    if priority:
        # 报表命中块在前；合并结果整体按 _MAX_BLOCKS 有界截断（超配额时截尾部），
        # 避免大量报表行命中（net income / operating expenses 等在报表与 MD&A 同现）
        # 拉邻居后优先级列表本身超过 _MAX_BLOCKS。
        return (ordered_priority + ordered_secondary)[:_MAX_BLOCKS]
    # 纯叙述输入：沿用旧有界上限，避免引用语无限占配额。
    return ordered_secondary[:_MAX_NARRATIVE_BLOCKS]


# label_en 的措辞变体集合（excerpt 校验时任一变体命中即通过）。
# 实测根因：NVDA 现金净变动行原文是 "Change in cash and cash equivalents"，
# 而 label_en 为 "net change in cash"，严格子串匹配会误拒。这里按已知措辞差异
# 登记变体；未登记的 label 用其本身做子串匹配。
_LABEL_EN_VARIANTS: dict[str, frozenset[str]] = {
    "net change in cash": frozenset(
        {
            "change in cash",
            "net increase (decrease) in cash",
            "increase (decrease) in cash",
        }
    ),
    "effect of exchange rate": frozenset({"exchange rate"}),
}


def _label_matches(label_en: str, excerpt: str) -> bool:
    """excerpt 是否命中 label_en 或其英文措辞变体。"""
    lowered_excerpt = excerpt.lower()
    normalized = label_en.strip().lower()
    variants = set(_LABEL_EN_VARIANTS.get(normalized, ())) | {normalized}
    return any(variant in lowered_excerpt for variant in variants)


class LLMFactExtractor:
    """有界地让 LLM 在 10-K 解析块中定位并提取缺失指标数值（best-effort）。"""

    def __init__(self, completion: Any) -> None:
        self._completion = completion

    def extract(
        self,
        *,
        blocks: Iterable[Any],
        metric_name: str,
        label_cn: str,
        label_en: str,
    ) -> ExtractedFact | None:
        """返回已验证提取值；任何失败（无财务块 / LLM 输出不合法 / 验证不过）返回 None。"""
        subset = _select_financial_blocks(blocks)
        if not subset:
            return None
        result = self._completion.complete(
            role=LLMRole.WRITER,
            system_prompt=(
                "你是证券申报数据提取器。在给定的 10-K 文本片段中，找到"
                f"“{label_cn}”（{label_en}）这一行的数值。只从给定文本中提取，"
                "不得编造数字或位置。只输出一个 JSON 对象："
                '{"metric_name": "<指标名>", "value": <纯数字>, '
                '"locator": "<[offset:xxx] 标识>", "excerpt": "<含该数值的原文片段>"}'
            ),
            user_prompt=(
                f"# 目标指标\n{label_cn}（{label_en}）\n\n"
                "# 10-K 文本片段（每块前为 [offset:xxx] 标识）\n\n"
                + "\n\n".join(f"[{loc}]\n{text}" for loc, text in subset)
            ),
            max_tokens=500,
        )
        return self._parse_and_validate(
            result.markdown, blocks, metric_name=metric_name, label_en=label_en
        )

    def _parse_and_validate(
        self,
        raw: str,
        blocks: Iterable[Any],
        *,
        metric_name: str,
        label_en: str,
    ) -> ExtractedFact | None:
        """解析 LLM JSON 输出并强验证（value 数字 / locator 真实 / excerpt 含关键字）。"""
        match = _JSON_OBJECT_RE.search(raw or "")
        if match is None:
            return None
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        value = self._coerce_value(payload.get("value"))
        locator = str(payload.get("locator") or "").strip()
        excerpt = str(payload.get("excerpt") or "").strip()
        if value is None or not locator or not excerpt:
            return None
        known_locators = {block.locator for block in blocks}
        if locator not in known_locators:
            return None
        if not _label_matches(label_en, excerpt):
            return None
        return ExtractedFact(
            metric_name=str(payload.get("metric_name") or metric_name),
            value=value,
            locator=locator,
            excerpt=excerpt,
        )

    @staticmethod
    def _coerce_value(raw: object) -> Decimal | None:
        """把 LLM 输出的 value 解析为有限 Decimal；允许负值（符号语义由调用方判定）。

        只拒绝 None / bool / 非数字 / NaN / Inf；现金流量表净变动常为负，故不再
        按正数校验。调用方（``_supplement_statement_rows``）只更新 value 缺失的行。
        """
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = Decimal(str(raw).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return None
        if not value.is_finite():
            return None
        return value
