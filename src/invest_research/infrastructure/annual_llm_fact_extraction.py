"""L2 兜底：LLM 从 10-K 原文提取缺失数值（有界、可追溯、强验证）。

触发：L1 确定性推导（annual_statements / annual_comparison 的候选概念 + 勾稽推导）
之后仍缺失的指标。LLM 在 10-K 解析块（``AnnualParsedTextBlock``）的财务报表区域内
定位指标所在行并读取数值；代码强验证后才采纳，避免 LLM 凭空造数：

- ``value`` 必须是可解析的正数 Decimal；
- ``locator`` 必须真实存在于输入块（LLM 不能编造位置）；
- ``excerpt`` 必须包含指标的英文关键字（证明数值来自文件中对应行）。

输入脱敏：只给块文本 + 指标描述，不含 company/accession 等高基数。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from invest_research.agents.llm_factory import LLMRole

# 财务报表区域关键字（用于选取有界块子集，避免把整份 10-K 喂给 LLM）。
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
# 每个财务报表关键字块前后纳入的邻居块数（补全表格行上下文）。
_NEIGHBOR_BLOCKS = 5
# 单块截断字符上限（控制 LLM 输入体积）。
_BLOCK_CHARS = 300
# 有界块子集上限（远小于 10-K 数千块）。
_MAX_BLOCKS = 40

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ExtractedFact:
    """LLM 从 10-K 原文提取的单个数值（已验证可追溯）。"""

    metric_name: str
    value: Decimal
    locator: str
    excerpt: str


def _select_financial_blocks(blocks: Iterable[Any]) -> list[tuple[str, str]]:
    """选取含财务报表关键字的块及其邻居，每块截断，返回 ``(locator, text)`` 列表。"""
    items = list(blocks)
    chosen: dict[str, str] = {}
    for index, block in enumerate(items):
        text = (block.text or "").strip()
        lowered = text.lower()
        if any(kw in lowered for kw in _FINANCIAL_KEYWORDS):
            start = max(0, index - _NEIGHBOR_BLOCKS)
            end = min(len(items), index + _NEIGHBOR_BLOCKS + 1)
            for neighbor in range(start, end):
                ntext = (items[neighbor].text or "").strip()
                nloc = items[neighbor].locator
                if ntext and nloc not in chosen:
                    chosen[nloc] = ntext[:_BLOCK_CHARS]
    # 有界：按出现顺序截断。
    ordered = [(loc, chosen[loc]) for loc in chosen]
    return ordered[:_MAX_BLOCKS]


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
        if label_en.lower() not in excerpt.lower():
            return None
        return ExtractedFact(
            metric_name=str(payload.get("metric_name") or metric_name),
            value=value,
            locator=locator,
            excerpt=excerpt,
        )

    @staticmethod
    def _coerce_value(raw: object) -> Decimal | None:
        if raw is None:
            return None
        if isinstance(raw, bool):
            return None
        try:
            value = Decimal(str(raw).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return None
        if value <= 0:
            return None
        return value
