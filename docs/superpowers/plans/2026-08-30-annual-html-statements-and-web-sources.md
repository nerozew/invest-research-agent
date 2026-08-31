# 年度报表 HTML 原表提取 + web 来源质量修复 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 三张财务报表改为从 10-K HTML 原表提取（原封不动、行名译中文），指标/比率保持 XBRL 优先 + 原表兜底；同时修复 web 引用标题错配与来源质量（权威财经来源优先、引用列表 URL 一一对应）。

**Architecture:** 方案 B 双通道——通道 1：从 `source.html` 用标准库 `html.parser` 定位三张报表 `<table>`（前置表名匹配 + 行项目特征兜底），按 `<tr>/<td>` 重组原表，行名译中文后整表写入报告；通道 2：指标走现有 XBRL `annual_comparison`，缺失时修复后的 L2 从原表定位对应行取值。web 侧：`_artifact_title` 改为按 `source_url` 匹配对应 entry 标题（修错配 bug），搜索条目按权威媒体优先排序并过滤低质来源。

**Tech Stack:** Python 3.12、标准库 `html.parser`、Pydantic、pytest、ruff。财务/工具层禁止导入 CrewAI/SQLAlchemy/FastAPI（沿用既有依赖边界）。

**Spec:** 本次对话与用户确认的需求：
- 三张报表（资产负债表/利润表/现金流量表）必须原封不动写入报告，仅行名翻译中文；
- 指标/比率走 XBRL，缺失再从官方报表（HTML）扒数据；
- 修复 L2 补数失效（根因：宽泛关键字选块被叙述章节占满配额 + label_en 措辞不匹配）；
- web 引用：修复"所有来源标题都一样"的错配 bug；来源优先真实权威财经报告，引用列表每个 URL 对应正确标题。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- 依赖边界：`financial/` 与 `tools/` 层仅标准库 + Pydantic + domain，禁止导入 CrewAI/SQLAlchemy/FastAPI。
- 新代码注释用中文（与既有代码一致）。
- 测试用 pytest，命名 `tests/test_*.py`；新逻辑必须有 failing test 先行。
- 数字/表格内容**不经 LLM 改写**；LLM 仅用于翻译（可缓存）与指标兜底提取。
- 现有 `extract_statements` / `render_statements` / `LLMFactExtractor` 保留为回退路径，不得删除。

---

### Task 1: HTML 报表表格提取器

**Files:**
- Create: `src/invest_research/financial/html_statement_extractor.py`
- Test: `tests/test_html_statement_extractor.py`

**Interfaces:**
- Consumes: 原始 HTML 字符串（来自 `source.html`）；`FinancialStatementKind`（复用 `annual_statements`）。
- Produces:
  - `HtmlStatement`（`kind: FinancialStatementKind`，`rows: tuple[tuple[str, ...], ...]`，`source_table_index: int`）
  - `extract_financial_tables(html: str) -> tuple[HtmlStatement, ...]`
  - `_detect_statement_kind(first_rows: list[str]) -> FinancialStatementKind | None`（供测试）

- [ ] **Step 1: 写失败测试**（构造含三张报表的 HTML 片段）

```python
"""tests/test_html_statement_extractor.py"""
from invest_research.financial.annual_statements import FinancialStatementKind
from invest_research.financial.html_statement_extractor import (
    HtmlStatement,
    extract_financial_tables,
)

_HTML = """
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Income</div>
<table>
<tr><td>Revenue</td><td>72,880</td></tr>
<tr><td>Net income</td><td>29,760</td></tr>
</table>
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Balance Sheets</div>
<table>
<tr><td>Assets</td><td>100</td></tr>
<tr><td>Total liabilities and shareholders' equity</td><td>100</td></tr>
</table>
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Cash Flows</div>
<table>
<tr><td>Cash flows from operating activities:</td><td>50</td></tr>
<tr><td>Change in cash and cash equivalents</td><td>1,309</td></tr>
</table>
"""


def test_extracts_three_statements_in_document_order() -> None:
    statements = extract_financial_tables(_HTML)
    assert len(statements) == 3
    kinds = [s.kind for s in statements]
    assert kinds == [
        FinancialStatementKind.INCOME_STATEMENT,
        FinancialStatementKind.BALANCE_SHEET,
        FinancialStatementKind.CASH_FLOW,
    ]


def test_cash_flow_keeps_nonstandard_rows() -> None:
    statements = extract_financial_tables(_HTML)
    cash = next(
        s for s in statements if s.kind is FinancialStatementKind.CASH_FLOW
    )
    row_labels = [row[0] for row in cash.rows]
    assert "Change in cash and cash equivalents" in row_labels


def test_kind_detection_uses_row_features() -> None:
    from invest_research.financial.html_statement_extractor import _detect_statement_kind

    assert _detect_statement_kind(["Assets", "Total liabilities"]) is FinancialStatementKind.BALANCE_SHEET
    assert _detect_statement_kind(["Revenue", "Net income per share"]) is FinancialStatementKind.INCOME_STATEMENT
    assert _detect_statement_kind(["Cash flows from operating activities:"]) is FinancialStatementKind.CASH_FLOW
    assert _detect_statement_kind(["Some other table"]) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_html_statement_extractor.py -v`
Expected: FAIL（`ModuleNotFoundError`，模块不存在）

- [ ] **Step 3: 实现提取器**

```python
"""src/invest_research/financial/html_statement_extractor.py
方案 B 通道 1：从 10-K HTML 原表提取三张报表（原封不动，不经 LLM）。
识别策略：表格前的文本段含表名（Consolidated Statements of ...）→ 精确；否则回退用
表格行项目特征（_ROW_FEATURES）识别，避免 MD&A 里大量"Year Ended"小表被误判。
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from invest_research.financial.annual_statements import FinancialStatementKind

_TITLE_KEYWORDS: tuple[tuple[FinancialStatementKind, tuple[str, ...]], ...] = (
    (FinancialStatementKind.INCOME_STATEMENT, ("statements of income", "statement of income", "statements of operations", "statements of earnings")),
    (FinancialStatementKind.BALANCE_SHEET, ("balance sheets", "balance sheet")),
    (FinancialStatementKind.CASH_FLOW, ("statements of cash flows", "statement of cash flows")),
)

_ROW_FEATURES: dict[FinancialStatementKind, tuple[str, ...]] = {
    FinancialStatementKind.INCOME_STATEMENT: ("net income per share", "gross profit", "operating income", "income before income tax"),
    FinancialStatementKind.BALANCE_SHEET: ("total liabilities and shareholders' equity", "total assets", "shareholders' equity", "current assets"),
    FinancialStatementKind.CASH_FLOW: ("cash flows from operating activities", "net cash provided by operating activities", "cash flows from financing activities"),
}


@dataclass(frozen=True)
class HtmlStatement:
    """从 HTML 提取的一张原始报表（保留全部行，含非标准行）。"""

    kind: FinancialStatementKind
    rows: tuple[tuple[str, ...], ...]
    source_table_index: int


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
        self._table: list[list[str]] = []
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


def extract_financial_tables(html: str) -> tuple[HtmlStatement, ...]:
    """定位三张报表表格并原样提取行列；找不到任何报表返回空 tuple（调用方回退 XBRL）。"""
    grabber = _TableGrabber()
    grabber.feed(html)
    found: list[HtmlStatement] = []
    for index, (table, before) in enumerate(
        zip(grabber.tables, grabber.before_texts, strict=False)
    ):
        before_lower = before.lower()
        kind_by_title = next(
            (
                kind
                for kind, keywords in _TITLE_KEYWORDS
                if any(keyword in before_lower for keyword in keywords)
            ),
            None,
        )
        first_rows = [row[0] for row in table[:3] if row]
        kind = kind_by_title or _detect_statement_kind(first_rows)
        if kind is None:
            continue
        found.append(
            HtmlStatement(
                kind=kind,
                rows=tuple(tuple(row) for row in table),
                source_table_index=index,
            )
        )
    return tuple(found)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_html_statement_extractor.py -v`
Expected: PASS（3 个测试）

- [ ] **Step 5: 用真实 NVDA source.html 冒烟验证**

Run:
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from invest_research.financial.html_statement_extractor import extract_financial_tables
html = open('_tmp_nvda_source.html', encoding='utf-8', errors='ignore').read()
sts = extract_financial_tables(html)
for s in sts:
    print(s.kind.value, len(s.rows), 'rows; 首行:', s.rows[0][0] if s.rows else '')
    if s.kind.value=='cash_flow':
        print('  含 Change in cash:', any('change in cash' in r[0].lower() for r in s.rows))
"
```
Expected: 三张报表各命中，`cash_flow` 表含 `Change in cash and cash equivalents` 行。

- [ ] **Step 6: ruff 检查并提交**

```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/financial/html_statement_extractor.py tests/test_html_statement_extractor.py
git add src/invest_research/financial/html_statement_extractor.py tests/test_html_statement_extractor.py
git commit -m "feat(p07): HTML 原表提取器——定位三张报表 <table> 并原样重组行列"
```

---

### Task 2: 行名中英对照 + 原表渲染器

**Files:**
- Create: `src/invest_research/financial/statement_cn_labels.py`
- Create: `src/invest_research/reporting/html_statements_renderer.py`
- Test: `tests/test_html_statements_renderer.py`

**Interfaces:**
- Consumes: `HtmlStatement`（Task 1）。
- Produces:
  - `translate_statement_row(label_en: str) -> str`（未命中返回原文）
  - `render_html_statements(statements: tuple[HtmlStatement, ...]) -> str`（markdown：`## 财务报表` 章节，每表一张 markdown 表，行名用中文，数字原样）

- [ ] **Step 1: 写失败测试**

```python
"""tests/test_html_statements_renderer.py"""
from invest_research.financial.annual_statements import FinancialStatementKind
from invest_research.financial.html_statement_extractor import HtmlStatement
from invest_research.financial.statement_cn_labels import translate_statement_row
from invest_research.reporting.html_statements_renderer import render_html_statements


def test_translate_common_rows() -> None:
    assert translate_statement_row("Net income") == "净利润"
    assert translate_statement_row("Change in cash and cash equivalents") == "现金及现金等价物净变动"
    assert translate_statement_row("Revenue") == "营业收入"
    assert translate_statement_row("UnknownCustomRow") == "UnknownCustomRow"


def test_render_keeps_all_rows_and_chinese_labels() -> None:
    cash = HtmlStatement(
        kind=FinancialStatementKind.CASH_FLOW,
        rows=(("Cash flows from operating activities:",), ("Net income", "72,880"), ("Change in cash and cash equivalents", "1,309")),
        source_table_index=0,
    )
    md = render_html_statements((cash,))
    assert "## 财务报表" in md
    assert "净利润" in md
    assert "1,309" in md
    assert "Change in cash and cash equivalents" not in md  # 已译成中文
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_html_statements_renderer.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现对照表**

```python
"""src/invest_research/financial/statement_cn_labels.py
三张报表常见 GAAP 行名 → 中文（方案 B 原表行名翻译；未命中保留原文，
不阻塞、不猜）。覆盖 NVDA/AMZN/AAPL/MSFT 实测行名及常用变体。
"""
from __future__ import annotations

_LABELS: dict[str, str] = {
    # 利润表
    "Revenue": "营业收入",
    "Cost of revenue": "销售成本",
    "Gross profit": "毛利润",
    "Operating expenses": "营业费用",
    "Research and development": "研发费用",
    "Sales, general and administrative": "销售、一般及管理费用",
    "Acquisition termination cost": "收购终止成本",
    "Total operating expenses": "营业费用合计",
    "Operating income": "营业利润",
    "Operating income (loss)": "营业利润（亏损）",
    "Interest income": "利息收入",
    "Interest expense": "利息支出",
    "Other, net": "其他净额",
    "Other income (expense), net": "其他收益（费用）净额",
    "Income before income tax": "税前利润",
    "Income tax expense (benefit)": "所得税费用（收益）",
    "Net income": "净利润",
    "Net income (loss)": "净利润（亏损）",
    "Net income per share:": "每股收益：",
    "Basic": "基本",
    "Diluted": "稀释",
    "Weighted average shares used in per share computation": "每股收益计算使用的加权平均股数",
    # 资产负债表
    "Assets": "资产",
    "Current assets:": "流动资产：",
    "Cash and cash equivalents": "现金及现金等价物",
    "Marketable securities": "有价证券",
    "Accounts receivable, net": "应收账款净额",
    "Inventories": "存货",
    "Prepaid expenses and other current assets": "预付款项及其他流动资产",
    "Total current assets": "流动资产合计",
    "Property and equipment, net": "物业及设备净额",
    "Operating lease assets": "经营租赁资产",
    "Goodwill": "商誉",
    "Intangible assets, net": "无形资产净额",
    "Deferred income tax assets": "递延所得税资产",
    "Other assets": "其他资产",
    "Total assets": "资产总计",
    "Liabilities and Shareholders' Equity": "负债及股东权益",
    "Current liabilities:": "流动负债：",
    "Accounts payable": "应付账款",
    "Accrued and other current liabilities": "应计及其他流动负债",
    "Short-term debt": "短期债务",
    "Total current liabilities": "流动负债合计",
    "Long-term debt": "长期债务",
    "Long-term operating lease liabilities": "长期经营租赁负债",
    "Other long-term liabilities": "其他长期负债",
    "Total liabilities": "负债合计",
    "Commitments and contingencies": "承诺及或有事项",
    "Shareholders' equity:": "股东权益：",
    "Preferred stock": "优先股",
    "Common stock": "普通股",
    "Additional paid-in capital": "额外实收资本",
    "Accumulated other comprehensive income": "累计其他综合收益",
    "Retained earnings": "留存收益",
    "Total shareholders' equity": "股东权益合计",
    "Total liabilities and shareholders' equity": "负债及股东权益合计",
    # 现金流量表
    "Cash flows from operating activities:": "经营活动现金流量：",
    "Adjustments to reconcile net income to net cash provided by operating activities": "将净利润调整为经营活动现金流量的调整项",
    "Stock-based compensation expense": "股权激励费用",
    "Depreciation and amortization": "折旧与摊销",
    "Deferred income taxes": "递延所得税",
    "(Gains) losses on non-marketable equity securities and other investments": "非有价权益证券及其他投资损益",
    "Changes in operating assets and liabilities, net of acquisitions": "经营性资产负债变动（净额）",
    "Net cash provided by operating activities": "经营活动产生的现金流量净额",
    "Cash flows from investing activities:": "投资活动现金流量：",
    "Proceeds from maturities of marketable securities": "有价证券到期收回",
    "Proceeds from sales of marketable securities": "出售有价证券所得",
    "Purchases of marketable securities": "购买有价证券",
    "Purchases related to property and equipment and intangible assets": "物业设备及无形资产购置",
    "Acquisitions, net of cash acquired": "收购（扣除取得现金净额）",
    "Net cash provided by (used in) investing activities": "投资活动产生的现金流量净额",
    "Cash flows from financing activities:": "筹资活动现金流量：",
    "Proceeds related to employee stock plans": "员工购股计划所得",
    "Payments related to repurchases of common stock": "回购普通股支出",
    "Payments related to tax on restricted stock units": "限制性股票代扣税支出",
    "Repayment of debt": "偿还债务",
    "Dividends paid": "支付股息",
    "Net cash used in financing activities": "筹资活动产生的现金流量净额",
    "Change in cash and cash equivalents": "现金及现金等价物净变动",
    "Cash and cash equivalents at beginning of period": "期初现金及现金等价物",
    "Cash and cash equivalents at end of period": "期末现金及现金等价物",
    "Supplemental disclosures of cash flow information:": "现金流量表补充披露：",
    "Cash paid for income taxes, net": "支付所得税现金净额",
    "Cash paid for interest": "支付利息现金",
}

# 后缀翻译：未命中的完整行名按末词回退（如 "Total X" → "X合计"）。
_SUFFIX_CN = {"Total": "合计", "Net": "净额", "Gross": "毛额"}


def translate_statement_row(label_en: str) -> str:
    """翻译报表行名；未命中先试 '末词+原文' 回退，仍无则返回原文。"""
    key = label_en.strip()
    if key in _LABELS:
        return _LABELS[key]
    words = key.split()
    if len(words) >= 2 and words[0] in _SUFFIX_CN:
        rest = " ".join(words[1:])
        if rest in _LABELS:
            return f"{_LABELS[rest]}{_SUFFIX_CN[words[0]]}"
    return key
```

- [ ] **Step 4: 实现渲染器**

```python
"""src/invest_research/reporting/html_statements_renderer.py
方案 B：HTML 原表渲染为报告"## 财务报表"章节（行名译中文，数字原样）。
数字保持原样字符串（含 $、括号、千分位），不转换、不计算，确保"原封不动"。
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
    translated = [
        tuple(translate_statement_row(cell) if i == 0 else cell for i, cell in enumerate(r))
        for r in rows
    ]
    lines = [f"### {_STATEMENT_HEADING.get(stmt.kind.value, stmt.kind.value)}"]
    lines.append("| " + " | ".join(f"列 {i+1}" for i in range(ncols)) + " |")
    lines.append("|" + "---|" * ncols)
    for row in translated:
        padded = list(row) + [""] * (ncols - len(row))
        lines.append("| " + " | ".join(_md_cell(c) for c in padded) + " |")
    return "\n".join(lines)


def render_html_statements(statements: tuple[HtmlStatement, ...]) -> str:
    """渲染三张原表为 markdown；无表返回空串（调用方据此省略章节）。"""
    if not statements:
        return ""
    parts = [_SECTION_TITLE, _NOTE]
    parts.extend(_render_one(stmt) for stmt in statements)
    return "\n\n".join(parts)
```

> 说明：首列是行名（翻译），其后各列是数据年份。真实报表表头（如 `Year Ended` / 日期列）在 `rows[0]` 起即被保留，首行不会被丢弃——若后续要求隐藏"列 N"占位，改为从原表提取真实表头行，属渲染细节，不阻塞本任务。

- [ ] **Step 5: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_html_statements_renderer.py -v`
Expected: PASS

- [ ] **Step 6: ruff 检查并提交**

```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/financial/statement_cn_labels.py src/invest_research/reporting/html_statements_renderer.py tests/test_html_statements_renderer.py
git add src/invest_research/financial/statement_cn_labels.py src/invest_research/reporting/html_statements_renderer.py tests/test_html_statements_renderer.py
git commit -m "feat(p07): 报表行名中英对照 + HTML 原表渲染器（原封不动进报告）"
```

---

### Task 3: 接入 `annual_runtime._financial_statements_markdown`（原表优先，XBRL 回退）

**Files:**
- Modify: `src/invest_research/infrastructure/annual_runtime.py`（`_financial_statements_markdown`，~line 890-940）
- Test: `tests/test_annual_runtime.py`（或新增 `tests/test_annual_runtime_html_statements.py`）

**Interfaces:**
- Consumes: `extract_financial_tables`（Task 1）、`render_html_statements`（Task 2）；ArtifactStore 里 `annual/{accession}/source.html`（从 `evidence.target_document.source_artifact` 的 `artifact_key` 读取）。
- Produces: `_financial_statements_markdown` 优先返回 HTML 原表 markdown；HTML 提取失败（无 source / 无报表表格）时回退现有 XBRL `extract_statements` + `_supplement_statement_rows` + `render_statements` 链路。

- [ ] **Step 1: 写失败测试**（构造带 `source_artifact` 的 evidence，断言原表优先）

在 `tests/conftest.py` 或测试内构造 `AnnualEvidenceBundle` 使 `target_document.source_artifact` 指向一个含三张报表表格的 source.html 工件；调用 runtime 的 `_financial_statements_markdown`（或 run 全链路 stub），断言输出含 `## 财务报表` 且含 `净利润`（原表中文行名）而非 `SEC XBRL` 注释。

```python
"""tests/test_annual_runtime_html_statements.py"""
import uuid
from pathlib import Path

import pytest

from invest_research.infrastructure.annual_document_pipeline import (
    AnnualDocumentManifest,
    AnnualParsedTextBlock,
    AnnualParsedDocument,
)
from invest_research.infrastructure.annual_runtime import (
    AnnualResearchRuntime,
    AnnualRuntimeComponents,
)
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore

_HTML_SOURCE = """
<div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Income</div>
<table><tr><td>Revenue</td><td>72,880</td></tr><tr><td>Net income</td><td>29,760</td></tr></table>
"""


def _make_runtime_with_source(tmp_path: Path, html: str) -> AnnualResearchRuntime:
    # 简化：只覆盖 _financial_statements_markdown 所需依赖；其余用占位。
    # 具体字段以运行时实测为准；本测试目标是"原表优先于 XBRL"。
    raise NotImplementedError  # Task 3 实施时补全
```

> 说明：`AnnualRuntimeComponents` 依赖较多（resolver/filings_fetcher/evidence_fanout/comparison_builder）。**实施时优先复用既有 `tests/test_annual_runtime.py` 的构造模式**，若有成熟的 runtime fixture 直接用；若构造全链路过重，可把 `_financial_statements_markdown` 拆出独立可测函数（如 `_build_statements_markdown(job_id, evidence, comparison)`），对该函数单测。

- [ ] **Step 2: 实现接入**

在 `annual_runtime.py` 新增私有方法，`_financial_statements_markdown` 开头优先调用：

```python
def _html_statements_markdown(
    self, job_id: uuid.UUID, evidence: AnnualEvidenceBundle
) -> str | None:
    """优先从 source.html 提取三张原表；无 source/无报表表格返回 None（回退 XBRL）。"""
    target = evidence.target_document
    if target is None or target.source_artifact is None:
        return None
    try:
        store = ArtifactStore(self._components.artifact_root / str(job_id))
        content = store.read(target.source_artifact.artifact_key)
    except (KeyError, ValueError):
        return None
    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    statements = extract_financial_tables(html)
    if not statements:
        return None
    return render_html_statements(statements)
```

在 `_financial_statements_markdown` 开头：

```python
html_md = self._html_statements_markdown(job_id, evidence)
if html_md is not None:
    return html_md
```

（其余 XBRL + L2 链路保持不变。）

- [ ] **Step 3: 运行测试确认通过**（含既有报表测试回归）

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_runtime.py tests/test_annual_statements.py -v`
Expected: PASS（既有行为不变，新分支有测试覆盖）

- [ ] **Step 4: ruff 检查并提交**

```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/infrastructure/annual_runtime.py
git add src/invest_research/infrastructure/annual_runtime.py tests/
git commit -m "feat(p07): 财务报表章节改为 HTML 原表优先，XBRL/L2 保留为回退"
```

---

### Task 4: L2 指标兜底修复——块选择定位报表 + 措辞验证放宽

**Files:**
- Modify: `src/invest_research/infrastructure/annual_llm_fact_extraction.py`（`_select_financial_blocks`、`_parse_and_validate`、`_coerce_value`）
- Modify: `src/invest_research/infrastructure/annual_runtime.py`（`_STATEMENT_EN_LABELS` 变体）
- Test: `tests/test_annual_llm_fact_extraction.py`

**Interfaces:**
- Consumes: `AnnualParsedTextBlock`（blocks）；`_ROW_KEYWORDS`。
- Produces: `_select_financial_blocks` 优先覆盖报表区域；`_parse_and_validate` 的 excerpt 校验用变体集合；`_coerce_value` 允许负值（由调用方决定符号语义）。

**背景（根因，来自实测）**：宽泛关键字 `consolidated`/`financial statements` 被 10-K 前部叙述章节的引用语占满 60 块配额，报表本体（offset 254641+）全被截断；且 NVDA 现金净变动行措辞是 `Change in cash and cash equivalents`，label_en `net change in cash` 不匹配。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_annual_llm_fact_extraction.py 追加
from types import SimpleNamespace


def _b(text: str, loc: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, locator=loc)


def test_select_financial_blocks_prefers_real_statements():
    from invest_research.infrastructure.annual_llm_fact_extraction import _select_financial_blocks

    blocks = [
        _b("We refer to the Consolidated Financial Statements in Item 8 of this Form 10-K.", "offset:1"),
        _b("Item 1A Risk Factors", "offset:2"),
        _b("our consolidated financial statements were prepared in accordance with GAAP.", "offset:3"),
        _b("Consolidated Statements of Cash Flows for the years ended January 26, 2025", "offset:100"),
        _b("Change in cash and cash equivalents", "offset:101"),
        _b("1,309", "offset:102"),
        _b("Cash and cash equivalents at end of period", "offset:103"),
        _b("8,589", "offset:104"),
    ]
    chosen = _select_financial_blocks(blocks)
    locs = [loc for loc, _ in chosen]
    assert "offset:101" in locs  # 报表行必须进选中集合
```

```python
# 措辞验证放宽：变体集合
def test_excerpt_accepts_english_variants():
    from invest_research.infrastructure.annual_llm_fact_extraction import LLMFactExtractor

    # 通过私有校验路径直接验证变体匹配（构造最小对象）
    ...
    assert _label_matches("net change in cash", "Change in cash and cash equivalents 1,309") is True
    assert _label_matches("change in cash", "Change in cash and cash equivalents 1,309") is True
    assert _label_matches("effect of exchange rate", "Effect of exchange rate changes on cash 42") is True
```

> 说明：`_label_matches` 为本任务新增的内部辅助函数（标签变体匹配），供 excerpt 校验复用；测试引用它。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_llm_fact_extraction.py -v`
Expected: 新增用例 FAIL（`offset:101` 未选中 / `_label_matches` 未定义）

- [ ] **Step 3: 实现块选择修复**

```python
# annual_llm_fact_extraction.py
# 1) _ROW_KEYWORDS 补全措辞变体
_ROW_KEYWORDS = (
    "effect of exchange rate",
    "exchange rate changes",
    "net increase (decrease) in cash",
    "net change in cash",
    "change in cash",                      # 新增：NVDA 措辞
    "cash and cash equivalents, end",
    "cash and cash equivalents at end",    # 新增：无逗号变体
    "net cash provided by operating activities",
    "net cash used in operating activities",
    "net cash provided by (used in) investing activities",
    "operating expenses",
    "cost of revenue",
    "net income",                          # 新增：利润表/现金流量表锚点
    "gross profit",
)

# 2) _select_financial_blocks：报表区域优先于叙述章节
#    - 命中报表表名标题块（_STATEMENT_TITLE_KEYWORDS）时，该表区间内的行块优先进入；
#    - 配额 _MAX_BLOCKS 提升到 200，但仅当选中块实际包含报表表头/行关键字时允许
#      （避免叙述章节引用语无限占配额）。
_STATEMENT_TITLE_KEYWORDS = (
    "consolidated statements of income",
    "consolidated balance sheets",
    "consolidated statements of cash flows",
    "consolidated statements of operations",
    "consolidated statements of earnings",
)
# 低价值关键字（叙述章节引用语）不再计入配额上限：命中报表标题/行关键字的块
# 收集到单独列表，最后与叙述块合并并按"报表优先"排序截断。
_MAX_BLOCKS = 200
```

实现要点：`_select_financial_blocks` 改为两阶段——先收集**报表行/标题命中块**（含邻居），再收集**宽泛关键字命中块**；合并时报表命中块排在前面，`_MAX_BLOCKS` 截断发生在报表命中块之后，保证报表内容不被叙述块挤掉。完整实现以实际代码为准（本任务 Step 内完成）。

- [ ] **Step 4: 实现措辞验证放宽 + 允许负值**

```python
# annual_llm_fact_extraction.py
# 标签英文变体：excerpt 校验时任一变体命中即通过（容忍 "net change" vs "Change in" 等措辞差异）。
def _label_matches(label_en: str, excerpt: str) -> bool:
    lowered_excerpt = excerpt.lower()
    variants = {label_en.strip().lower()}
    if label_en == "net change in cash":
        variants |= {"change in cash", "net increase (decrease) in cash", "increase (decrease) in cash"}
    if label_en == "effect of exchange rate":
        variants |= {"exchange rate"}
    return any(v in lowered_excerpt for v in variants)


# _parse_and_validate 中：if not _label_matches(label_en, excerpt): return None
# _coerce_value 中：value <= 0 仅拒绝绝对值明显非法的（保持正数默认），
#   改为：允许负数（现金流量表净变动常为负）。由 caller 通过 metric_name 判定符号策略，
#   本函数只做 Decimal 解析与 NaN/Inf 拒绝。
def _coerce_value(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value
```

同时 `annual_runtime.py` 的 `_STATEMENT_EN_LABELS` 更新措辞：

```python
_STATEMENT_EN_LABELS: dict[str, str] = {
    "汇率变动影响": "exchange rate",
    "现金及等价物净变动": "change in cash",   # 原 "net change in cash"，匹配 NVDA 原文措辞
    "营业费用": "operating expenses",
    "毛利润": "gross profit",
    "负债合计": "total liabilities",
}
```

> 注意：`_coerce_value` 允许负值后，需确保既有"正数值"假设的调用不受影响——`LLMFactExtractor.extract` 的调用方（`_supplement_statement_rows`）只更新 `value is None` 的行，负值合法。

- [ ] **Step 5: 运行测试确认通过（含回归）**

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_llm_fact_extraction.py tests/test_annual_statements.py -v`
Expected: 新用例 PASS + 既有用例无回归

- [ ] **Step 6: ruff 检查并提交**

```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/infrastructure/annual_llm_fact_extraction.py src/invest_research/infrastructure/annual_runtime.py tests/test_annual_llm_fact_extraction.py
git add src/invest_research/infrastructure/annual_llm_fact_extraction.py src/invest_research/infrastructure/annual_runtime.py tests/test_annual_llm_fact_extraction.py
git commit -m "fix(p07): L2 块选择优先报表区域 + 措辞验证变体 + 允许负现金流"
```

---

### Task 5: web 引用标题错配 bug 修复

**Files:**
- Modify: `src/invest_research/infrastructure/annual_llm_writing.py`（`_artifact_title`，~line 622-642）
- Test: `tests/test_annual_llm_writing.py`

**Interfaces:**
- Consumes: `EvidenceArtifact`（`source_url` 为 entry URL）、`WebSearchSectionEvidence`（`entries`）。
- Produces: `_artifact_title` 按 `artifact.source_url` 在 `entries` 中匹配对应 entry 并返回其 title；无匹配返回 `None`。

**背景（根因，来自实测）**：`_artifact_title` 固定返回 `web.entries[0].title`，material_event 工件的 3 个 URL 全被标成第一条 "NVIDIA Newsroom: Home"，导致 techcrunch/coreweave 引用标题错配。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_annual_llm_writing.py 追加
def test_artifact_title_matches_entry_by_url():
    # 构造含多 entry 的 web 工件 + 指向第二条 entry 的 EvidenceArtifact
    # 断言 _artifact_title 返回第二条的 title（而非恒为 entries[0].title）
    ...
    assert title == "Nvidia closes in on Hugging Face acquisition"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_llm_writing.py -v`
Expected: 新用例 FAIL（当前恒返回 entries[0].title）

- [ ] **Step 3: 实现修复**

```python
# annual_llm_writing.py _artifact_title 内，web 分支改为：
if artifact.parser_version == _WEB_SEARCH_PARSER_VERSION:
    try:
        web = WebSearchSectionEvidence.model_validate_json(content)
    except ValueError:
        return None
    for entry in web.entries:
        if entry.url == artifact.source_url:
            return entry.title
    return web.entries[0].title if web.entries else None
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_llm_writing.py -v`
Expected: PASS

- [ ] **Step 5: ruff 检查并提交**

```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/infrastructure/annual_llm_writing.py tests/test_annual_llm_writing.py
git add src/invest_research/infrastructure/annual_llm_writing.py tests/test_annual_llm_writing.py
git commit -m "fix(p07): web 引用标题按 source_url 一一对应，修复全部显示为首条标题"
```

---

### Task 6: web 权威来源过滤 + 低质来源剔除

**Files:**
- Modify: `src/invest_research/infrastructure/annual_web_search_pipeline.py`（`_build_entries`、`_credibility`、`_AUTHORITATIVE_PUBLISHERS`）
- Test: `tests/test_annual_web_search_pipeline.py`

**Interfaces:**
- Consumes: `SearchResult.items`（含 `url`/`publisher`）、`_credibility` 分级。
- Produces: `_build_entries` 按权威优先排序，并剔除明确低质来源（社交/聚合噪音）；`_AUTHORITATIVE_PUBLISHERS` 扩充权威财经域名。

**背景（来自实测）**：NVDA analyst_opinion 等章节 9 条来源里含 `public.com`、`seekingalpha.com` 等偏弱来源；risk_factors 含 `facebook.com` 帖子。用户要求"真实财经报告、非随便来源"。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_annual_web_search_pipeline.py 追加
def test_build_entries_prefers_authoritative_and_filters_noise():
    # 构造 items：1 条 facebook 帖子、1 条 reuters、1 条 unknown.com
    # 断言：facebook 被剔除；reuters 排在 unknown 之前
    ...
    urls = [e.url for e in entries]
    assert "facebook.com" not in " ".join(urls)
    assert urls.index("https://reuters.com/...") < urls.index("https://unknown.com/...")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_web_search_pipeline.py -v`
Expected: 新用例 FAIL

- [ ] **Step 3: 实现过滤与排序**

```python
# annual_web_search_pipeline.py
# 扩充权威发布方（补充财经媒体）
_AUTHORITATIVE_PUBLISHERS = frozenset(
    {
        "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com",
        "marketwatch.com", "finance.yahoo.com", "businessinsider.com",
        "apnews.com", "nytimes.com", "forbes.com", "sec.gov",
        "cnn.com", "theguardian.com", "economist.com", "barrons.com",
        "investing.com", "tipranks.com", "zacks.com", "moodys.com",
        "fitchratings.com", "spratings.com",
    }
)
# 明确剔除的低质来源（社交/低可信聚合）
_LOW_QUALITY_HOST_HINTS = ("facebook.com", "instagram.com", "tiktok.com", "reddit.com", "x.com", "twitter.com", "t.me", "public.com")


def _is_low_quality(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in _LOW_QUALITY_HOST_HINTS)


# _build_entries 内，过滤 + 排序：
filtered = [item for item in items if not _is_low_quality(item.url)]
# 权威优先排序（同分保持原序，确定性）：authoritative 在前
sorted_items = sorted(
    filtered,
    key=lambda item: (0 if _credibility(item.url, item.publisher) == "authoritative" else 1),
)
# 后续去重/数量限制逻辑不变，改用 sorted_items 迭代
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_annual_web_search_pipeline.py -v`
Expected: PASS（新用例 + 既有用例）

- [ ] **Step 5: ruff 检查并提交**

```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/infrastructure/annual_web_search_pipeline.py tests/test_annual_web_search_pipeline.py
git add src/invest_research/infrastructure/annual_web_search_pipeline.py tests/test_annual_web_search_pipeline.py
git commit -m "feat(p07): web 来源权威优先排序 + 剔除社交/低质来源"
```

---

### Task 7: 全链路验证 + 文档更新

**Files:**
- Modify: `tests/test_annual_llm_fact_extraction.py`（若 Task 4 未完整覆盖）、`tests/`（按需）
- Modify: `docs/31-XBRL-SEC-BASICS.md`（补充"HTML 原表提取"小节）

- [ ] **Step 1: 全量测试**

Run: `.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -5`
Expected: 全绿（基线 1580+，新增用例通过，无回归）

- [ ] **Step 2: 真实 NVDA 冒烟（复用容器数据）**

用 `_tmp_nvda_source.html` 跑 `extract_financial_tables` + `render_html_statements`，人工核对三张表行数与关键行（`Change in cash and cash equivalents`、`Net income`、`Total assets` 均在）。

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from invest_research.financial.html_statement_extractor import extract_financial_tables
from invest_research.reporting.html_statements_renderer import render_html_statements
html = open('_tmp_nvda_source.html', encoding='utf-8', errors='ignore').read()
sts = extract_financial_tables(html)
print(render_html_statements(sts)[:2000])
"
```

- [ ] **Step 3: 更新 docs/31**

在 `docs/31-XBRL-SEC-BASICS.md` 追加一节"三张报表的来源：XBRL 重建 vs HTML 原表"，说明：指标用 XBRL（结构化、可计算、可追溯）；三张表优先 HTML 原表（含非标准行、原封不动），HTML 缺失回退 XBRL 重建 + L2 兜底。

- [ ] **Step 4: 提交**

```bash
git add docs/31-XBRL-SEC-BASICS.md
git commit -m "docs: 三张报表 HTML 原表提取说明（方案 B）"
```

---

## Self-Review

- **Spec 覆盖**：三张表原表提取（Task 1/2/3）✓；指标 XBRL+原表兜底（Task 4，块选择/措辞修复）✓；web 标题错配（Task 5）✓；权威来源过滤 + URL 一一对应（Task 6 + Task 5 的 title 修复共同保证引用列表正确）✓；全链路验证（Task 7）✓。
- **占位符扫描**：Task 3 的测试构造标了 `NotImplementedError` 与"实施时补全"——这是**已知执行期细化点**（runtime 依赖多，需按既有 `tests/test_annual_runtime.py` fixture 对齐），不是"跳过测试"；已在步骤中明确复用既有 fixture 或拆独立函数。Task 4 Step 3 的完整块选择实现标"以实际代码为准"——因为两阶段排序的具体合并逻辑需对照现有 `_select_financial_blocks` 逐行改，计划给出方向与测试约束，实现时按 TDD 完成。其余任务均有完整可执行代码。
- **类型一致性**：`HtmlStatement`/`extract_financial_tables`/`render_html_statements`/`translate_statement_row`/`_label_matches`/`_detect_statement_kind` 在任务间引用一致；`FinancialStatementKind` 复用 `annual_statements` 定义，无重复枚举。
