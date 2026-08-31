"""src/invest_research/reporting/html_statements_renderer.py
方案 B：HTML 原表渲染为报告"## 财务报表"章节（行名译中文，数字原样）。
数字保持原样字符串（含 $、括号、千分位），不转换、不计算，确保"原封不动"。

Ruling 1：真实表头即原表首行 rows[0]（首格为行名列→译中文，其余列保持原样），
数据行 = rows[1:]（首格翻译，其余列原样）；不使用"列 N"占位列头。
"""

from __future__ import annotations

import warnings

from invest_research.financial.html_statement_extractor import HtmlStatement, _looks_like_year
from invest_research.financial.statement_cn_labels import translate_statement_row

_SECTION_TITLE = "## 财务报表"
_NOTE = "> 以下数据直接取自 SEC 申报 10-K 原始报表，行名译中文，数字未经改写。"

_STATEMENT_HEADING = {
    "income_statement": "利润表",
    "balance_sheet": "资产负债表",
    "cash_flow": "现金流量表",
}


def _translate_row_name(label: str, extra_labels: dict[str, str] | None) -> str:
    """行名翻译：extra_labels（LLM 补翻译）优先，否则走通用对照表。"""
    stripped = label.strip()
    return (extra_labels or {}).get(stripped) or translate_statement_row(stripped)


def _md_cell(value: str, *, missing: str = "N/A") -> str:
    text = value.strip() or missing
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _find_data_start(rows: tuple[tuple[str, ...], ...], years: tuple[str, ...]) -> int:
    """定位年份表头行之后的首个数据行下标（年份行通常在第 0/1 行）。"""
    year_set = set(years)
    for i in range(min(2, len(rows))):
        if {cell for cell in rows[i] if _looks_like_year(cell)} == year_set:
            return i + 1
    return 2  # 兜底：跳过表头行（第 0 行）与年份行（第 1 行）


def _looks_like_number(text: str) -> bool:
    """数值格判定：含数字即为数值（$、%、空列不计）。"""
    return any(ch.isdigit() for ch in text)


def _is_well_formed_number(text: str) -> bool:
    """数值文本是否为合规千分位数字（限制数值格合并）。

    仅当相邻数值格拼接后仍是规范数字时才允许合并——否则会把相邻两个财年的值
    无依据地拼成一个错值。例：``"1,23"``+``"4,567"`` = ``"1,234,567"`` 合法（GOOGL
    ``数字[x2]`` 拆格），再拼 ``"30,708"`` 成 ``"1,234,56730,708"`` 出现 5 位千分位组
    即非法，说明跨到了下一个财年，不得继续合并。
    """
    s = text.strip()
    if not s or not _looks_like_number(s):
        return False
    body = s.lstrip("$").replace(",", "").replace("%", "")
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    if not body.replace(".", "").replace("-", "").replace("+", "").isdigit():
        return False
    # 千分位逗号组校验：整数部分每个逗号组 ≤3 位数字。
    int_part = s.lstrip("$").replace("%", "").split(".", 1)[0]
    int_part = (
        int_part.replace("(", "").replace(")", "").replace("-", "").replace("+", "").strip()
    )
    return all(
        len("".join(ch for ch in group if ch.isdigit())) <= 3
        for group in int_part.split(",")
    )


def _extract_year_values(value_cells: tuple[str, ...], n_years: int) -> list[str]:
    """从数据行数值格按顺序提取每个财年的值。

    ``$`` 与相邻数字合并（``$ 23,466`` → ``$23,466``），括号负数原样保留，
    空列跳过；同一财年列（锚点区间）内连续数值格合并为一个值（GOOGL 原表
    ``数字[x2]`` colspan 会把 $+数字拆两格），避免被当作两个财年造成静默错位；
    数值不足 n_years 时用 ``·`` 补齐（不产生 N/A 噪音）。

    合并限制：连续数值格只在拼接结果仍是规范数字（``_is_well_formed_number``）
    且合并后剩余格数仍足以填满其余年份时才会继续——否则会把相邻两个财年的值
    无依据地拼成一个错值（Finding 1）。数值格数超出 n_years 时发出告警而不是
    无声丢弃有效数字（Finding 3）。
    """
    tokens: list[str] = []
    i = 0
    n = len(value_cells)
    # 可作为独立值的格（非空、非 $）：用于约束合并不得"饿死"后续年份。
    value_flags = [bool(cell.strip()) and cell.strip() != "$" for cell in value_cells]
    suffix_value_count = [0] * (n + 1)
    for k in range(n - 1, -1, -1):
        suffix_value_count[k] = suffix_value_count[k + 1] + (1 if value_flags[k] else 0)

    def _can_merge(j: int, candidate: str) -> bool:
        """合并到下标 j 是否可接受：结果须为合法数字，且剩余格数够填剩余年份。"""
        if not _is_well_formed_number(candidate):
            return False
        remaining_needed = n_years - len(tokens) - 1
        return suffix_value_count[j + 1] >= remaining_needed

    while i < n:
        cell = value_cells[i].strip()
        if not cell:
            i += 1
            continue
        if cell == "$":
            # $ 并入同一财年列数值格（中间空列可跳过），并吸收后续连续数值格。
            j = i + 1
            while j < n and not value_cells[j].strip():
                j += 1
            if j < n and _looks_like_number(value_cells[j]):
                value = "$" + value_cells[j].strip()
                j += 1
                while j < n and _looks_like_number(value_cells[j]):
                    candidate = value + value_cells[j].strip()
                    if not _can_merge(j, candidate):
                        break
                    value = candidate
                    j += 1
                tokens.append(value)
                i = j
                continue
            i += 1
            continue
        if _looks_like_number(cell):
            value = cell
            j = i + 1
            # 连续数值格合并为一个值（数字[x2] 结构），如 ["1,23", "4,567"] → "1,234,567"；
            # 但仅当拼接结果仍是规范数字且不挤占后续年份的格数时继续。
            while j < n and _looks_like_number(value_cells[j]):
                candidate = value + value_cells[j].strip()
                if not _can_merge(j, candidate):
                    break
                value = candidate
                j += 1
            tokens.append(value)
            i = j
            continue
        i += 1
    if len(tokens) > n_years:
        warnings.warn(
            "数据行数值格超过年份数："
            f"提取到 {len(tokens)} 个值但只有 {n_years} 个年份列，"
            f"尾部 {tokens[n_years:]} 被截断",
            UserWarning,
            stacklevel=2,
        )
    # 对齐到 n_years：不足补 ·，超出截断（截断前已告警）。
    return (tokens + ["·"] * n_years)[:n_years]


def _render_year_aligned(stmt: HtmlStatement, extra_labels: dict[str, str] | None) -> str:
    """按表头年份列对齐渲染（行名 | 年份1 | 年份2…），消除 colspan N/A 噪音。"""
    years = stmt.year_columns or ()
    ncols = len(years) + 1
    lines = [f"### {_STATEMENT_HEADING.get(stmt.kind.value, stmt.kind.value)}"]
    lines.append("| " + " | ".join(_md_cell(c) for c in ("项目", *years)) + " |")
    lines.append("|" + "---|" * ncols)
    for row in stmt.rows[_find_data_start(stmt.rows, years) :]:
        name = _translate_row_name(row[0], extra_labels)
        values = _extract_year_values(row[1:], len(years))
        lines.append("| " + " | ".join(_md_cell(c, missing="·") for c in (name, *values)) + " |")
    return "\n".join(lines)


def _render_one(stmt: HtmlStatement, extra_labels: dict[str, str] | None) -> str:
    if stmt.year_columns:
        return _render_year_aligned(stmt, extra_labels)
    rows = stmt.rows
    if not rows:
        return ""
    # 列数 = 最长行（原表有 colspan 时列宽不一，用最长行定列）。
    ncols = max((len(r) for r in rows), default=1)
    # 表头 = 原表首行（真实表头，如 "Year Ended" + 各财年日期列），首格翻译，其余原样。
    header = [
        _md_cell(_translate_row_name(cell, extra_labels) if i == 0 else cell)
        for i, cell in enumerate(rows[0])
    ]
    # 表头补足到 ncols（补齐格留空，不用"N/A"——那是给数据格用的）。
    header += [""] * (ncols - len(header))
    lines = [f"### {_STATEMENT_HEADING.get(stmt.kind.value, stmt.kind.value)}"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * ncols)
    for row in rows[1:]:
        translated = [
            _translate_row_name(cell, extra_labels) if i == 0 else cell
            for i, cell in enumerate(row)
        ]
        padded = translated + [""] * (ncols - len(translated))
        lines.append("| " + " | ".join(_md_cell(c) for c in padded) + " |")
    return "\n".join(lines)


def render_html_statements(
    statements: tuple[HtmlStatement, ...],
    *,
    extra_labels: dict[str, str] | None = None,
) -> str:
    """渲染三张原表为 markdown；无表返回空串（调用方据此省略章节）。

    ``extra_labels`` 为 LLM 补翻译的行名映射（{英文行名: 中文}），优先于通用对照表；
    缺省 None 保持现有行为（未命中行名回退对照表/原文）。
    """
    if not statements:
        return ""
    parts = [_SECTION_TITLE, _NOTE]
    parts.extend(_render_one(stmt, extra_labels) for stmt in statements)
    return "\n\n".join(parts)
