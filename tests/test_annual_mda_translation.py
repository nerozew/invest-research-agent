"""年度报告官方声明翻译 + MD&A 摘译（LLM best-effort 边界）。

覆盖：
- ``_parse_translated_statements``：按 【label】 标记 / 按顺序空行块 / 解析失败三种分支；
- ``AnnualSectionExecutor.translate_official_statements``：fake completion 注入翻译 →
  返回带 translation 的新集合；completion 抛错 → 原样保留英文（不阻塞）；
- ``AnnualSectionExecutor.summarize_mda``：注入中文摘译 → 返回中文；无 MD&A → 空串。
"""

from __future__ import annotations

from pathlib import Path

from invest_research.infrastructure.annual_document_pipeline import AnnualParsedTextBlock
from invest_research.infrastructure.annual_llm_writing import (
    AnnualLlmCallError,
    AnnualSectionExecutor,
    _parse_translated_statements,
)
from invest_research.reporting.annual_report_renderer import (
    AnnualOfficialStatements,
    OfficialStatement,
)


class _FakeCompletion:
    """返回固定 markdown；可选抛错模拟调用失败。"""

    def __init__(self, markdown: str | None = None, *, raise_error: bool = False) -> None:
        self._markdown = markdown or ""
        self._raise_error = raise_error
        self.calls = 0

    def complete(self, *, role, system_prompt, user_prompt, max_tokens=4000):
        self.calls += 1
        if self._raise_error:
            raise AnnualLlmCallError("FAKE_ERROR", "fake completion failure")
        return type("R", (), {"markdown": self._markdown})()


def _audit_statement() -> OfficialStatement:
    return OfficialStatement(
        text="In our opinion, the financial statements present fairly...",
        locator="offset:100",
    )


def _mda_blocks() -> tuple[AnnualParsedTextBlock, ...]:
    return (
        AnnualParsedTextBlock(
            text="Item 7. Management's Discussion and Analysis", locator="offset:5000"
        ),
        AnnualParsedTextBlock(
            text=(
                "Revenue increased 30% due to strong data center demand, reflecting continued "
                "adoption across customer verticals and our focus on operating leverage as we "
                "scale the fulfillment network and expand internationally. We expect this trend "
                "to continue as we invest in new capabilities and geographic markets."
            ),
            locator="offset:5100",
        ),
    )


# ---------------------------------------------------------------------------
# _parse_translated_statements
# ---------------------------------------------------------------------------


def test_parse_labeled_translations():
    output = "【封面页】封面翻译。\n\n【独立审计意见】审计翻译。"
    labels = ["封面页", "独立审计意见"]
    assert _parse_translated_statements(output, labels) == ["封面翻译。", "审计翻译。"]


def test_parse_falls_back_to_ordered_blocks_without_labels():
    output = "第一段翻译。\n\n第二段翻译。"
    assert _parse_translated_statements(output, ["封面页", "独立审计意见"]) == [
        "第一段翻译。",
        "第二段翻译。",
    ]


def test_parse_returns_empty_on_mismatch():
    assert _parse_translated_statements("只有一段。", ["封面页", "独立审计意见", "302 认证"]) == []


# ---------------------------------------------------------------------------
# translate_official_statements
# ---------------------------------------------------------------------------


def test_translate_official_statements_attaches_translation(tmp_path: Path):
    completion = _FakeCompletion(
        "【封面页】封面中文翻译。\n\n【独立审计意见】审计中文翻译。"
    )
    executor = AnnualSectionExecutor(tmp_path, completion)
    statements = AnnualOfficialStatements(
        cover_page=_audit_statement(),
        audit_opinion=OfficialStatement(text="audit en", locator="offset:2"),
    )
    out = executor.translate_official_statements(statements)
    assert out.cover_page is not None and out.cover_page.translation == "封面中文翻译。"
    assert out.audit_opinion is not None and out.audit_opinion.translation == "审计中文翻译。"
    assert completion.calls == 1


def test_translate_official_statements_keeps_english_on_error(tmp_path: Path):
    completion = _FakeCompletion(raise_error=True)
    executor = AnnualSectionExecutor(tmp_path, completion)
    statements = AnnualOfficialStatements(audit_opinion=_audit_statement())
    out = executor.translate_official_statements(statements)
    # 失败不阻塞：translation 保持 None，英文原文保留。
    assert out.audit_opinion is not None
    assert out.audit_opinion.translation is None


def test_translate_official_statements_empty_no_call(tmp_path: Path):
    completion = _FakeCompletion("不应被调用")
    executor = AnnualSectionExecutor(tmp_path, completion)
    out = executor.translate_official_statements(AnnualOfficialStatements())
    assert out.is_empty
    assert completion.calls == 0


# ---------------------------------------------------------------------------
# summarize_mda
# ---------------------------------------------------------------------------


def test_summarize_mda_returns_chinese_summary(tmp_path: Path):
    completion = _FakeCompletion(
        "管理层认为收入增长主要由云服务驱动，受益于企业客户持续上云与海外市场扩展。"
        "同时公司通过优化履约网络和运营杠杆改善经营利润率，并持续投入生成式人工智能"
        "基础设施以支撑长期增长。管理层预计收入增速将在未来几个季度保持稳健。"
    )
    executor = AnnualSectionExecutor(tmp_path, completion)
    summary = executor.summarize_mda(_mda_blocks())
    assert len(summary) > 100
    assert "云服务驱动" in summary
    assert completion.calls == 1


def test_summarize_mda_too_short_returns_empty(tmp_path: Path):
    """模型只给元说明/偷懒（<100 字）→ 返回空，由调用方回退原文直取。"""
    completion = _FakeCompletion("该节选自公司年报中管理层讨论与分析。")
    executor = AnnualSectionExecutor(tmp_path, completion)
    assert executor.summarize_mda(_mda_blocks()) == ""
    assert completion.calls == 1


def test_summarize_mda_missing_returns_empty_no_call(tmp_path: Path):
    completion = _FakeCompletion("不应被调用")
    executor = AnnualSectionExecutor(tmp_path, completion)
    blocks = (AnnualParsedTextBlock(text="普通正文没有管理层讨论", locator="offset:1"),)
    assert executor.summarize_mda(blocks) == ""
    assert completion.calls == 0
