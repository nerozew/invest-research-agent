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


def _md_cell(value: str, *, missing: str = "N/A") -> str:
    text = value.strip() or missing
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _is_year_cell(text: str) -> bool:
    """单元格是否为纯 4 位年份（如 "2024"），用于定位表头年份行。"""
    return len(text) == 4 and text.isdigit()


def _find_data_start(rows: tuple[tuple[str, ...], ...], years: tuple[str, ...]) -> int:
    """定位年份表头行之后的首个数据行下标（年份行通常在第 0/1 行）。"""
    year_set = set(years)
    for i in range(min(2, len(rows))):
        if {cell for cell in rows[i] if _is_year_cell(cell)} == year_set:
            return i + 1
    return 2  # 兜底：跳过表头行（第 0 行）与年份行（第 1 行）


def _looks_like_number(text: str) -> bool:
    """数值格判定：含数字即为数值（$、%、空列不计）。"""
    return any(ch.isdigit() for ch in text)


def _extract_year_values(value_cells: tuple[str, ...], n_years: int) -> list[str]:
    """从数据行数值格按顺序提取每个财年的值。

    ``$`` 与相邻数字合并（``$ 23,466`` → ``$23,466``），括号负数原样保留，
    空列跳过；数值不足 n_years 时用 ``·`` 补齐（不产生 N/A 噪音）。
    """
    tokens: list[str] = []
    i = 0
    n = len(value_cells)
    while i < n:
        cell = value_cells[i].strip()
        if not cell:
            i += 1
            continue
        if cell == "$":
            # 合并紧跟的数值格（中间空列可跳过），如 ["$", "", "23,466"]。
            j = i + 1
            while j < n and not value_cells[j].strip():
                j += 1
            if j < n and _looks_like_number(value_cells[j]):
                tokens.append("$" + value_cells[j].strip())
                i = j + 1
                continue
            i += 1
            continue
        if _looks_like_number(cell):
            tokens.append(cell)
        i += 1
    # 对齐到 n_years：不足补 ·，超出截断。
    return (tokens + ["·"] * n_years)[:n_years]


def _render_year_aligned(stmt: HtmlStatement) -> str:
    """按表头年份列对齐渲染（行名 | 年份1 | 年份2…），消除 colspan N/A 噪音。"""
    years = stmt.year_columns or ()
    ncols = len(years) + 1
    lines = [f"### {_STATEMENT_HEADING.get(stmt.kind.value, stmt.kind.value)}"]
    lines.append("| " + " | ".join(_md_cell(c) for c in ("项目", *years)) + " |")
    lines.append("|" + "---|" * ncols)
    for row in stmt.rows[_find_data_start(stmt.rows, years) :]:
        name = translate_statement_row(row[0].strip())
        values = _extract_year_values(row[1:], len(years))
        lines.append("| " + " | ".join(_md_cell(c, missing="·") for c in (name, *values)) + " |")
    return "\n".join(lines)


def _render_one(stmt: HtmlStatement) -> str:
    if stmt.year_columns:
        return _render_year_aligned(stmt)
    rows = stmt.rows
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
