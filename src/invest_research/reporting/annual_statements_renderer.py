"""三张财务报表的 Markdown 渲染（数字原封不动，不经 LLM）。

把 ``FinancialStatementSet`` 渲染为报告独立章节 ``## 财务报表``：
- 每张表一张 Markdown 表格（项目 | 本期 | 上期可选 | 单位）；
- 数字直接输出 ``FinancialFact.value``（Decimal 十进制串），缺失行 ``N/A``；
- 单元格转义（``|`` / 换行）防注入，复用 ``reporting/renderer.py`` 的 ``_md_cell`` 思想。

依赖边界：仅标准库 + financial 层模型；不依赖 LLM / 报告模板。
"""

from __future__ import annotations

from decimal import Decimal

from invest_research.financial.annual_statements import FinancialStatementSet

_SECTION_TITLE = "## 财务报表"
_NOTE = "> 以下数据直接取自 SEC 申报的 XBRL 事实，未经改写。"


def _md_cell(value: object) -> str:
    """Markdown 表格单元格转义：竖线→\\|、换行→空格、空值→N/A。"""
    text = str(value).strip() if value is not None else ""
    if not text:
        return "N/A"
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _decimal_text(value: Decimal | None) -> str:
    """Decimal 转十进制字符串（保持原封不动的数值），None → N/A。"""
    if value is None:
        return "N/A"
    return f"{value:f}"


def _render_set(
    stmt: FinancialStatementSet, include_comparator: bool
) -> str:
    """渲染单张报表为 Markdown 子表。"""
    has_comparator = (
        include_comparator
        and stmt.comparator_year is not None
        and any(row.comparator_value is not None for row in stmt.rows)
    )
    header = ["项目"]
    if has_comparator:
        header.append(f"本期（FY {stmt.target_year}）")
        header.append(f"上期（FY {stmt.comparator_year}）")
    else:
        header.append(f"FY {stmt.target_year}")
    header.append("单位")

    lines = [f"### {stmt.label}"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for row in stmt.rows:
        cells = [_md_cell(row.label)]
        if has_comparator:
            cells.append(_md_cell(_decimal_text(row.value)))
            cells.append(_md_cell(_decimal_text(row.comparator_value)))
        else:
            cells.append(_md_cell(_decimal_text(row.value)))
        cells.append(_md_cell(row.unit or "N/A"))
        lines.append("| " + " | ".join(cells) + " |")
    if stmt.balance_check is not None:
        lines.append("")
        message = stmt.balance_check.message or (
            "成立" if stmt.balance_check.ok else "不成立"
        )
        lines.append(f"勾稽校验：{message}")
    if stmt.limitations:
        lines.append("")
        lines.extend(f"- {_md_cell(lim)}" for lim in stmt.limitations)
    return "\n".join(lines)


def render_statements(
    sets: tuple[FinancialStatementSet, ...], *, include_comparator: bool = True
) -> str:
    """渲染财务报表章节 markdown；无任何报表行数据时返回空字符串。"""
    non_empty = [stmt for stmt in sets if stmt.rows]
    if not non_empty:
        return ""
    parts = [_SECTION_TITLE, _NOTE]
    parts.extend(_render_set(stmt, include_comparator) for stmt in non_empty)
    return "\n\n".join(parts)


__all__ = ["render_statements"]
