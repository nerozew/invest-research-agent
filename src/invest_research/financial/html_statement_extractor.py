"""src/invest_research/financial/html_statement_extractor.py
方案 B 通道 1：从 10-K HTML 原表提取三张报表（原封不动，不经 LLM）。
识别策略：表格前的文本段含表名（Consolidated Statements of ...）→ 标题命中；否则回退用
表格行项目特征（_ROW_FEATURES）识别。表名在整份 10-K 里会多次出现（目录/正文/附注），
故每个 kind 只保留行数最多的表作为 canonical，避免 MD&A 里的小表被误判进报表。
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from invest_research.financial.annual_statements import FinancialStatementKind

_TITLE_KEYWORDS: tuple[tuple[FinancialStatementKind, tuple[str, ...]], ...] = (
    (
        FinancialStatementKind.INCOME_STATEMENT,
        (
            "statements of income",
            "statement of income",
            "statements of operations",
            "statements of earnings",
        ),
    ),
    (FinancialStatementKind.BALANCE_SHEET, ("balance sheets", "balance sheet")),
    (
        FinancialStatementKind.CASH_FLOW,
        ("statements of cash flows", "statement of cash flows"),
    ),
)

_ROW_FEATURES: dict[FinancialStatementKind, tuple[str, ...]] = {
    FinancialStatementKind.INCOME_STATEMENT: (
        "net income per share",
        "gross profit",
        "operating income",
        "income before income tax",
    ),
    FinancialStatementKind.BALANCE_SHEET: (
        "total liabilities",
        "total assets",
        "shareholders' equity",
        "current assets",
    ),
    FinancialStatementKind.CASH_FLOW: (
        "cash flows from operating activities",
        "net cash provided by operating activities",
        "cash flows from financing activities",
    ),
}


def _looks_like_year(text: str) -> bool:
    """单元格是否就是纯 4 位年份（如 "2024"），排除带千分位/文字的数字。"""
    return len(text) == 4 and text.isdigit()


def _detect_year_columns(rows: tuple[tuple[str, ...], ...]) -> tuple[str, ...] | None:
    """从表头前两行识别年份列（含 2-3 个纯 4 位年份的短行）。

    真实 10-K 的年份表头行形如 ``('', '2024', '', '2025')`` 或
    ``('', '2023', '', '2024', '', '2025')``；识别不到返回 None（渲染回退现有逻辑）。
    """
    for row in rows[:2]:
        years = tuple(cell for cell in row if _looks_like_year(cell))
        if 2 <= len(years) <= 3:
            return years
    return None


@dataclass(frozen=True)
class HtmlStatement:
    """从 HTML 提取的一张原始报表（保留全部行，含非标准行）。"""

    kind: FinancialStatementKind
    rows: tuple[tuple[str, ...], ...]
    source_table_index: int
    year_columns: tuple[str, ...] | None = None


def _detect_statement_kind(first_rows: list[str]) -> FinancialStatementKind | None:
    """按行项目特征识别报表类型（_TITLE_KEYWORDS 未命中时的兜底）。"""
    blob = " ".join(first_rows).lower()
    for kind, features in _ROW_FEATURES.items():
        if any(feature in blob for feature in features):
            return kind
    return None


def _normalize_cell(text: str) -> str:
    return " ".join(text.split())


class _TableGrabber(HTMLParser):
    """遍历 HTML：记录每个 <table> 前的文本段 + 表格行列，供上层识别。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._in_cell = False
        self._cell: list[str] = []
        self._row: list[str] = []
        self._table: list[list[str]] | None = None
        self._pending_text: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.before_texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        if self._skip:
            return
        if tag == "table":
            self._table = []
            self.before_texts.append(" ".join(self._pending_text))
            self._pending_text = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
        if self._skip:
            return
        if tag in {"td", "th"} and self._in_cell:
            self._row.append(_normalize_cell("".join(self._cell)))
            self._in_cell = False
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = []
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_cell:
            self._cell.append(data)
        else:
            self._pending_text.append(data)


def _kind_by_title(before_text: str) -> FinancialStatementKind | None:
    """按表前文本段中的表名关键词识别报表类型（_TITLE_KEYWORDS）。

    真实 10-K 里同一表名常出现多次（目录、正文散文提及、财务附注），标题命中只是
    候选信号；最终 canonical 由 ``extract_financial_tables`` 按行数最多决定。
    """
    before_lower = before_text.lower()
    return next(
        (
            kind
            for kind, keywords in _TITLE_KEYWORDS
            if any(keyword in before_lower for keyword in keywords)
        ),
        None,
    )


def extract_financial_tables(html: str) -> tuple[HtmlStatement, ...]:
    """定位三张报表表格并原样提取行列；每个 kind 只保留行数最多的 canonical 表。

    先收集所有匹配表（表名关键词 ``_kind_by_title``，未命中回退行特征
    ``_detect_statement_kind``），再按 ``len(rows)`` 每 kind 取最大一张，最后按
    ``source_table_index`` 升序返回（最多 3 张）。找不到任何报表返回空 tuple
    （调用方回退 XBRL）。
    """
    grabber = _TableGrabber()
    grabber.feed(html)
    best_by_kind: dict[FinancialStatementKind, HtmlStatement] = {}
    for index, (table, before) in enumerate(
        zip(grabber.tables, grabber.before_texts, strict=False)
    ):
        kind = _kind_by_title(before) or _detect_statement_kind(
            [row[0] for row in table[:3] if row]
        )
        if kind is None:
            continue
        rows = tuple(tuple(row) for row in table)
        statement = HtmlStatement(
            kind=kind,
            rows=rows,
            source_table_index=index,
            year_columns=_detect_year_columns(rows),
        )
        current = best_by_kind.get(kind)
        if current is None or len(statement.rows) > len(current.rows):
            best_by_kind[kind] = statement
    return tuple(sorted(best_by_kind.values(), key=lambda s: s.source_table_index))
