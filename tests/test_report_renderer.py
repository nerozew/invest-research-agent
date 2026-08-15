"""P06-01 报告渲染器测试：golden、确定性、章节/引用稳定、转义与边界。

验证目标（docs/05 P06-01）：
- golden test：固定输入 → 渲染结果逐字节等于 `tests/fixtures/golden_report.md`；
- 章节与引用稳定：模板固定章节 + 正文必需章节透传 + 引用键/来源清单确定性；
- 模板健壮性：空值、转义、语言标签、缺失 pack。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.flows.state import ResearchFlowState
from invest_research.reporting.renderer import (
    DEFAULT_DISCLAIMER,
    ReportRenderer,
    ReportRenderInput,
    ReportSource,
    build_render_input,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "golden_report.md"


def _fixed_input(**overrides: object) -> ReportRenderInput:
    """固定的渲染输入（golden 基准；任何覆盖只用于单项测试）。"""
    defaults: dict[str, object] = dict(
        title="Apple Inc. (AAPL) 投资研究报告",
        legal_name="Apple Inc.",
        ticker="AAPL",
        cik="0000320193",
        as_of_date="2025-10-31",
        generated_at="2025-11-01T00:00:00",
        language="zh-CN",
        body_markdown=(
            "## 执行摘要\n执行摘要内容\n"
            "## 公司与业务概览\n公司与业务概览内容\n"
            "## 近期重要事件与行业背景\n近期重要事件与行业背景内容\n"
            "## 财务表现\n财务表现内容\n"
            "## 关键指标表\n关键指标表内容\n"
            "## 风险因素与催化因素\n风险因素与催化因素内容\n"
            "## 数据限制\n数据限制内容\n"
            "## 来源清单与非投资建议声明\n来源清单内容"
        ),
        citation_keys=["src_sec_2025_10k", "metric_revenue_growth"],
        sources=[
            ReportSource(
                title="Apple Inc. Annual Report (Form 10-K)",
                url=(
                    "https://www.sec.gov/Archives/edgar/data/320193/"
                    "000032019325000123/aapl-20250927.htm"
                ),
                locator="10-K",
                publisher="SEC EDGAR",
            ),
            ReportSource(
                title="Apple Inc. News Article",
                url="https://news.example.com/apple/2025",
            ),
        ],
        limitations=["指标因数据不足无法计算", "营收为占位值"],
    )
    merged = {**defaults, **overrides}
    return ReportRenderInput(**merged)


def test_render_matches_golden() -> None:
    """固定输入渲染结果与已入库 golden 文件逐字节一致（章节/引用稳定）。"""
    rendered = ReportRenderer().render(_fixed_input())
    golden = GOLDEN_PATH.read_text(encoding="utf-8")
    assert rendered == golden


def test_render_is_deterministic() -> None:
    """同一输入渲染两次，输出完全一致（确定性模板化生成）。"""
    renderer = ReportRenderer()
    assert renderer.render(_fixed_input()) == renderer.render(_fixed_input())


def test_required_template_sections_stable() -> None:
    """模板固定章节与正文必需章节全部出现在输出中。"""
    out = ReportRenderer().render(_fixed_input())
    for heading in (
        "# Apple Inc. (AAPL) 投资研究报告",
        "| 公司 |",
        "| Ticker |",
        "| CIK |",
        "| 数据截止日 |",
        "| 生成时间 |",
        "| 报告语言 |",
        "## 执行摘要",
        "## 财务表现",
        "## 关键指标表",
        "## 数据限制（结构化）",
        "## 引用与来源（模板自动生成）",
        "## 非投资建议声明（固定文本）",
    ):
        assert heading in out


def test_sources_render_in_order_with_locator_and_publisher() -> None:
    out = ReportRenderer().render(_fixed_input())
    assert (
        "1. [Apple Inc. Annual Report (Form 10-K)（SEC EDGAR）]"
        "(https://www.sec.gov/Archives/edgar/data/320193/000032019325000123/aapl-20250927.htm)"
        "（定位：10-K）"
    ) in out
    assert "2. [Apple Inc. News Article](https://news.example.com/apple/2025)" in out
    assert "引用键：src_sec_2025_10k、metric_revenue_growth" in out


def test_citation_keys_empty_omits_line() -> None:
    out = ReportRenderer().render(_fixed_input(citation_keys=[]))
    assert "引用键：" not in out


def test_limitations_render_and_absent_when_empty() -> None:
    out = ReportRenderer().render(_fixed_input())
    assert "- 指标因数据不足无法计算" in out
    assert "- 营收为占位值" in out
    out_empty = ReportRenderer().render(_fixed_input(limitations=[]))
    assert "数据限制（结构化）" not in out_empty


def test_table_cell_escaping() -> None:
    """竖线与换行不破坏表格结构（md_cell 转义）。"""
    out = ReportRenderer().render(
        _fixed_input(legal_name="ACME | Corp\nLtd", ticker="|", cik=None)
    )
    assert "| ACME \\| Corp Ltd |" in out
    assert "| \\| |" in out
    # 原始换行不应出现在表格单元格内
    assert "| ACME | Corp" not in out


def test_missing_optional_fields_show_na() -> None:
    out = ReportRenderer().render(_fixed_input(ticker=None, cik=None))
    assert "| N/A |" in out


def test_empty_sources_shows_none() -> None:
    out = ReportRenderer().render(_fixed_input(sources=[]))
    assert "- 无可用来源" in out


def test_language_label_en() -> None:
    out = ReportRenderer().render(_fixed_input(language="en"))
    assert "| English |" in out


def test_disclaimer_always_present() -> None:
    """固定免责声明总是输出（即使正文未包含）。"""
    out = ReportRenderer().render(_fixed_input())
    assert DEFAULT_DISCLAIMER in out
    assert "非投资建议声明（固定文本）" in out


def test_generated_at_and_as_of_in_cover() -> None:
    out = ReportRenderer().render(_fixed_input())
    assert "2025-11-01T00:00:00" in out
    assert "2025-10-31" in out


def _sample_state() -> ResearchFlowState:
    request = ResearchRequest(
        input_company="AAPL",
        as_of_date=date(2025, 10, 31),
        language="zh-CN",
    )
    identity = CompanyIdentity(
        cik="0000320193",
        ticker="AAPL",
        legal_name="Apple Inc.",
        exchange="NASDAQ",
    )
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=date(2025, 10, 31),
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000123/aapl-20250927.htm",
                title="Apple Inc. Annual Report (Form 10-K)",
                accessed_at=date(2025, 10, 31),
                locator="10-K",
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2025, 9, 27),
        facts=[
            FinancialFact(
                company_id=identity.cik,
                source_id="src-1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=0,
                unit="USD",
                period_start=date(2024, 9, 29),
                period_end=date(2025, 9, 27),
            )
        ],
        limitations=["指标因数据不足无法计算"],
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Apple Inc. (AAPL) 投资研究报告",
        markdown="## 执行摘要\n内容",
        citation_keys=["src_sec_2025_10k"],
    )
    return ResearchFlowState(
        request=request,
        company_identity=identity,
        research_pack=research,
        analysis_pack=analysis,
        report_draft=draft,
    )


def test_build_render_input_from_state() -> None:
    """从 Flow state 构建渲染输入：字段映射正确、含全部来源与限制。"""
    data = build_render_input(_sample_state(), generated_at=datetime(2025, 11, 1, 0, 0, 0))
    assert data is not None
    assert data.title == "Apple Inc. (AAPL) 投资研究报告"
    assert data.legal_name == "Apple Inc."
    assert data.ticker == "AAPL"
    assert data.cik == "0000320193"
    assert data.as_of_date == "2025-10-31"
    assert data.generated_at == "2025-11-01T00:00:00"
    assert data.language == "zh-CN"
    assert len(data.sources) == 1
    assert data.sources[0].locator == "10-K"
    assert data.limitations == ["指标因数据不足无法计算"]
    assert data.citation_keys == ["src_sec_2025_10k"]
    # 渲染出来的报告也包含正文与封面
    out = ReportRenderer().render(data)
    assert "Apple Inc. (AAPL) 投资研究报告" in out
    assert "## 执行摘要" in out


def test_build_render_input_requires_draft() -> None:
    """缺少 report_draft 时返回 None（不渲染半成品）。"""
    state = _sample_state()
    state.report_draft = None
    assert build_render_input(state) is None


def test_unknown_language_label_falls_back() -> None:
    out = ReportRenderer().render(_fixed_input(language="fr"))
    assert "| fr |" in out
