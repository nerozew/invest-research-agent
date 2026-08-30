"""src/invest_research/reporting/html_statements_renderer.py
方案 B：HTML 原表渲染为报告"## 财务报表"章节（行名译中文，数字原样）。
数字保持原样字符串（含 $、括号、千分位），不转换、不计算，确保"原封不动"。

Ruling 1：真实表头即原表首行 rows[0]（首格为行名列→译中文，其余列保持原样），
数据行 = rows[1:]（首格翻译，其余列原样）；不使用"列 N"占位列头。
"""

from __future__ import annotations

from invest_research.financial.html_statement_extractor import HtmlStatement
from invest_research.financial.statement_cn_labels import translate_statement_row

_SECTION_TITLE = "## 财务报表"
_NOTE = "> 以下数据直接取自 SEC 申报 10-K 原始报表，行名译中文，数字未经改写。"

_STATEMENT_HEADING = {
    "income_statement": "利润表",
    "balance_sheet": "资产负债表",
    "cash_flow": "现金流量表",
}


def _md_cell(value: str) -> str:
    text = value.strip() or "N/A"
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _render_one(stmt: HtmlStatement) -> str:
    rows = stmt.rows
    if not rows:
        return ""
    # 列数 = 最长行（原表有 colspan 时列宽不一，用最长行定列）。
    ncols = max((len(r) for r in rows), default=1)
    # 表头 = 原表首行（真实表头，如 "Year Ended" + 各财年日期列），首格翻译，其余原样。
    header = [
        _md_cell(translate_statement_row(cell) if i == 0 else cell)
        for i, cell in enumerate(rows[0])
    ]
    # 表头补足到 ncols（补齐格留空，不用"N/A"——那是给数据格用的）。
    header += [""] * (ncols - len(header))
    lines = [f"### {_STATEMENT_HEADING.get(stmt.kind.value, stmt.kind.value)}"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * ncols)
    for row in rows[1:]:
        translated = [
            translate_statement_row(cell) if i == 0 else cell for i, cell in enumerate(row)
        ]
        padded = translated + [""] * (ncols - len(translated))
        lines.append("| " + " | ".join(_md_cell(c) for c in padded) + " |")
    return "\n".join(lines)


def render_html_statements(statements: tuple[HtmlStatement, ...]) -> str:
    """渲染三张原表为 markdown；无表返回空串（调用方据此省略章节）。"""
    if not statements:
        return ""
    parts = [_SECTION_TITLE, _NOTE]
    parts.extend(_render_one(stmt) for stmt in statements)
    return "\n\n".join(parts)
