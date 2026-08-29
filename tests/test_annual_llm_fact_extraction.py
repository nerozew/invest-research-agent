"""L2 兜底：LLM 从 10-K 原文提取缺失数值（有界 + 强验证）。

覆盖：
- 合法 LLM JSON（value/locator/excerpt）→ 提取成功且 locator 在 blocks；
- locator 不在 blocks / excerpt 不含关键字 / value 非正数 → 返回 None（LLM_REJECTED）；
- 无财务报表区域块 → 不调用 LLM、返回 None。
"""

from __future__ import annotations

from invest_research.infrastructure.annual_document_pipeline import AnnualParsedTextBlock
from invest_research.infrastructure.annual_llm_fact_extraction import (
    ExtractedFact,
    LLMFactExtractor,
    _select_financial_blocks,
)


class _FakeCompletion:
    def __init__(self, markdown: str, *, calls: list[int] | None = None) -> None:
        self._markdown = markdown
        self.calls = calls if calls is not None else []

    def complete(self, *, role, system_prompt, user_prompt, max_tokens=500):
        self.calls.append(len(user_prompt))
        return type("R", (), {"markdown": self._markdown})()


def _blocks() -> tuple[AnnualParsedTextBlock, ...]:
    return (
        AnnualParsedTextBlock(text="Consolidated Statements of Operations", locator="offset:1000"),
        AnnualParsedTextBlock(
            text="Gross profit                      311,671 million", locator="offset:1020"
        ),
        AnnualParsedTextBlock(
            text="Net income                       52,000 million", locator="offset:1040"
        ),
    )


def _ok_json() -> str:
    return (
        '{"metric_name": "gross_margin", "value": 311671, '
        '"locator": "offset:1020", "excerpt": "Gross profit 311,671 million"}'
    )


def test_extract_success_validates_locator_and_keyword() -> None:
    completion = _FakeCompletion(_ok_json())
    result = LLMFactExtractor(completion).extract(
        blocks=_blocks(),
        metric_name="gross_margin",
        label_cn="毛利率",
        label_en="gross profit",
    )
    assert isinstance(result, ExtractedFact)
    assert result.value == 311671
    assert result.locator == "offset:1020"
    assert completion.calls, "应调用 LLM"


def test_extract_rejects_locator_not_in_blocks() -> None:
    bad = _ok_json().replace("offset:1020", "offset:999999")
    result = LLMFactExtractor(_FakeCompletion(bad)).extract(
        blocks=_blocks(), metric_name="gross_margin", label_cn="毛利率", label_en="gross profit"
    )
    assert result is None  # LLM 编造位置 → 拒绝


def test_extract_rejects_excerpt_without_keyword() -> None:
    bad = _ok_json().replace("Gross profit", "Some other line")
    result = LLMFactExtractor(_FakeCompletion(bad)).extract(
        blocks=_blocks(), metric_name="gross_margin", label_cn="毛利率", label_en="gross profit"
    )
    assert result is None  # excerpt 不含指标关键字 → 拒绝（防造数）


def test_extract_rejects_non_positive_value() -> None:
    bad = _ok_json().replace("311671", "-5")
    result = LLMFactExtractor(_FakeCompletion(bad)).extract(
        blocks=_blocks(), metric_name="gross_margin", label_cn="毛利率", label_en="gross profit"
    )
    assert result is None


def test_extract_skips_llm_when_no_financial_blocks() -> None:
    completion = _FakeCompletion(_ok_json())
    blocks = (AnnualParsedTextBlock(text="普通正文没有财务报表", locator="offset:1"),)
    result = LLMFactExtractor(completion).extract(
        blocks=blocks, metric_name="gross_margin", label_cn="毛利率", label_en="gross profit"
    )
    assert result is None
    assert not completion.calls, "无财务区域块时不应调用 LLM"


def test_select_financial_blocks_is_bounded() -> None:
    blocks = tuple(
        AnnualParsedTextBlock(
            text=("Consolidated Statements" if i == 100 else f"row {i} " + "x" * 400),
            locator=f"offset:{i}",
        )
        for i in range(200)
    )
    chosen = _select_financial_blocks(blocks)
    assert len(chosen) <= 40
    assert all(len(text) <= 300 for _loc, text in chosen)
    assert any(loc == "offset:100" for loc, _t in chosen)
