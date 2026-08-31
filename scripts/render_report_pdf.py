"""P06-02 示例文件生成：用真实 AAPL live 工件（或 golden 回退）渲染报告样例。

输出（写入 docs/p06-02-samples/，提交进仓库供人工视觉确认）：
- sample_report.md      ：Jinja2 模板渲染的 Markdown 报告
- sample_report.pdf     ：PyMuPDF 渲染的 PDF（内置中文字体、链接、分页）
- sample_report_pageN.png：全部页面 PNG 截图

用法：uv run python scripts/render_report_pdf.py

安全：只读取工件 JSON（无密钥）；渲染过程不联网、不调用模型。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = PROJECT_ROOT / "artifacts" / "AAPL_2025-10-31"
OUT_DIR = PROJECT_ROOT / "docs" / "p06-02-samples"


def _fallback_state():
    """没有真实 live 工件时的回退样例（与 golden 测试同源，纯离线）。"""
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

    request = ResearchRequest(input_company="AAPL", as_of_date=date(2025, 10, 31))
    identity = CompanyIdentity(cik="0000320193", ticker="AAPL", legal_name="Apple Inc.")
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
        title="Apple Inc. (AAPL) 投资研究报告（示例）",
        markdown=(
            "## 执行摘要\n\n"
            "本报告基于截至 2025-10-31 可获得的公开信息自动生成。\n\n"
            "## 公司与业务概览\n\n"
            "Apple Inc. 是一家美国上市公司，股票代码 AAPL。\n\n"
            "## 关键指标表\n\n"
            "| 指标 | 数值 | 单位 |\n"
            "| :--- | :--- | :--- |\n"
            "| 营收 | 391,035 | 百万美元 |\n\n"
            "## 数据限制\n\n"
            "部分指标因数据不足无法计算。\n\n"
            "## 来源清单与非投资建议声明\n\n"
            "详细来源见下方自动生成清单；本报告不构成投资建议。"
        ),
        citation_keys=["src_sec_2025_10k"],
    )
    return ResearchFlowState(
        request=request,
        company_identity=identity,
        research_pack=research,
        analysis_pack=analysis,
        report_draft=draft,
    )


def _load_state_from_artifacts():
    """从真实 live 工件恢复 Flow state（P05-13 AAPL 运行产物，无密钥）。"""
    from invest_research.domain.models import (
        FinancialAnalysisPack,
        ReportDraft,
        ResearchPack,
        ResearchRequest,
    )
    from invest_research.flows.state import ResearchFlowState

    request = ResearchRequest.model_validate_json(
        (ARTIFACT_DIR / "00_request.json").read_text(encoding="utf-8")
    )
    research = ResearchPack.model_validate_json(
        (ARTIFACT_DIR / "02_research_pack.json").read_text(encoding="utf-8")
    )
    analysis = FinancialAnalysisPack.model_validate_json(
        (ARTIFACT_DIR / "04_financial_analysis_pack.json").read_text(encoding="utf-8")
    )
    draft = ReportDraft.model_validate_json(
        (ARTIFACT_DIR / "05_report_draft.json").read_text(encoding="utf-8")
    )
    return ResearchFlowState(
        request=request,
        company_identity=research.company_identity,
        research_pack=research,
        analysis_pack=analysis,
        report_draft=draft,
    )


def main() -> int:
    from invest_research.reporting.pdf import MarkdownPdfRenderer, render_page_png
    from invest_research.reporting.renderer import ReportRenderer, build_render_input

    if ARTIFACT_DIR.exists() and (ARTIFACT_DIR / "05_report_draft.json").exists():
        state = _load_state_from_artifacts()
        source = "真实 live 工件（artifacts/AAPL_2025-10-31）"
    else:
        state = _fallback_state()
        source = "离线回退样例（无真实 live 工件）"

    render_input = build_render_input(state, generated_at=datetime.now())
    if render_input is None:
        print("[FAILED] 无法构建渲染输入（缺少 report_draft）")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = ReportRenderer().render(render_input)
    (OUT_DIR / "sample_report.md").write_text(md, encoding="utf-8")

    pdf_path = MarkdownPdfRenderer().render(md, OUT_DIR / "sample_report.pdf")
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        pages = len(doc)
    for page_index in range(pages):
        render_page_png(
            pdf_path,
            page_index,
            OUT_DIR / f"sample_report_page{page_index + 1}.png",
            dpi=120,
        )

    print(f"[OK] 样例已生成（来源：{source}）")
    print(f"  Markdown : {OUT_DIR / 'sample_report.md'}")
    print(f"  PDF      : {pdf_path}（{pages} 页）")
    print(f"  页面截图  : {OUT_DIR / 'sample_report_pageN.png'}（共 {pages} 张）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
