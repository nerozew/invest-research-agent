"""年度报告官方声明翻译 + MD&A 摘译（LLM best-effort 边界）。

覆盖：
- ``_parse_translated_statements``：按 【label】 标记 / 按顺序空行块 / 解析失败三种分支；
- ``AnnualSectionExecutor.translate_official_statements``：fake completion 注入翻译 →
  返回带 translation 的新集合；completion 抛错 → 原样保留英文（不阻塞）；
- ``AnnualSectionExecutor.summarize_mda``：注入中文深度要点 → 返回中文；无 MD&A → 空串。
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


# 深度化后的 fake 输出：七维组织、>400 字，模拟资深分析师多维要点梳理。
_DEEP_MDA_SUMMARY = (
    "① 经营业绩与收入/利润驱动：管理层认为收入增长主要由云服务驱动，受益于企业客户"
    "持续上云与海外市场扩展，全年营收同比增长约 30%，毛利率保持稳定并略有提升。"
    "② 分部或产品线表现：云基础设施部门收入增长最为强劲，生成式人工智能相关服务贡献"
    "主要增量；企业软件部门增速相对放缓，但客户续约率维持高位。③ 成本与费用结构变化："
    "公司通过优化履约网络和提升运营杠杆改善经营利润率，履约成本占收入比重下降，研发投入"
    "与销售费用持续加大。④ 资本配置、分红回购与流动性：资本开支主要用于生成式人工智能"
    "基础设施与数据中心扩建，经营性现金流保持强劲，资产负债表结构稳健，拥有充足流动性。"
    "⑤ 资产负债与现金流：应收账款周转天数小幅上升但仍属健康区间，存货周转改善，自由"
    "现金流为正。⑥ 风险与不确定性：管理层提示宏观经济波动、海外监管政策变化、人工智能"
    "基础设施投入回报周期偏长以及行业竞争加剧等风险。⑦ 前瞻性展望：管理层预计收入增速"
    "将在未来几个季度保持稳健，将继续加大在生成式人工智能基础设施上的投入，并希望通过"
    "规模效应与运营杠杆进一步提升盈利能力，以支撑长期可持续增长。"
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
    completion = _FakeCompletion("【封面页】封面中文翻译。\n\n【独立审计意见】审计中文翻译。")
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
    completion = _FakeCompletion(_DEEP_MDA_SUMMARY)
    executor = AnnualSectionExecutor(tmp_path, completion)
    summary = executor.summarize_mda(_mda_blocks())
    assert len(summary) > 400
    assert "云服务驱动" in summary
    assert completion.calls == 1


def test_summarize_mda_too_short_returns_empty(tmp_path: Path):
    """模型只给元说明/偷懒（<400 字）→ 返回空，由调用方回退原文直取。"""
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


def test_summarize_mda_uses_deep_analysis_prompt_and_big_budget(tmp_path: Path) -> None:
    """资深分析师多维深度梳理：system_prompt 含多维维度关键词，max_tokens 提至 6000。"""

    class _RecordingCompletion:
        def __init__(self, markdown: str) -> None:
            self._markdown = markdown
            self.calls: list[tuple[str, int]] = []

        def complete(
            self,
            *,
            role: object,
            system_prompt: str,
            user_prompt: str,
            max_tokens: int = 500,
        ) -> object:
            self.calls.append((system_prompt, max_tokens))
            return type("R", (), {"markdown": self._markdown})()

    completion = _RecordingCompletion(_DEEP_MDA_SUMMARY)
    executor = AnnualSectionExecutor(tmp_path, completion)
    summary = executor.summarize_mda(_mda_blocks())
    assert summary == _DEEP_MDA_SUMMARY
    assert completion.calls
    system_prompt, max_tokens = completion.calls[0]
    assert "资深" in system_prompt
    assert "分部" in system_prompt
    assert "前瞻" in system_prompt
    assert "深度" in system_prompt
    assert max_tokens >= 6000


def test_summarize_mda_feeds_extract_mda_with_larger_cap(tmp_path: Path) -> None:
    """extract_mda 以 12000 上限取原文：LLM 实际收到超过 3000 字的 MD&A 原文节选。

    旧行为 ``extract_mda`` 默认 ``max_chars=3000`` 会把原文先截到 ~3000 字，
    ``_MDA_SUMMARIZE_MAX_CHARS=12000`` 切片是 no-op；修复后应把更大原文传入 LLM。
    """

    class _RecordingCompletion:
        def __init__(self) -> None:
            self.user_prompt = ""

        def complete(
            self,
            *,
            role: object,
            system_prompt: str,
            user_prompt: str,
            max_tokens: int = 500,
        ) -> object:
            self.user_prompt = user_prompt
            return type("R", (), {"markdown": _DEEP_MDA_SUMMARY})()

    long_body = (
        "Revenue increased 30% due to strong data center demand and continued adoption. " * 80
    )
    assert len(long_body) > 5000
    blocks = (
        AnnualParsedTextBlock(
            text="Item 7. Management's Discussion and Analysis", locator="offset:5000"
        ),
        AnnualParsedTextBlock(text=long_body, locator="offset:5100"),
    )
    completion = _RecordingCompletion()
    executor = AnnualSectionExecutor(tmp_path, completion)
    summary = executor.summarize_mda(blocks)
    assert summary == _DEEP_MDA_SUMMARY
    # 修复前原文被截到 ~3000 字；修复后应把 >5000 字的原文节选传给 LLM。
    assert len(completion.user_prompt) > 5000
