# GOOGL 真实测评暴露的三个报告缺陷修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 GOOGL 真实测评暴露的三个缺陷：① 财务报表 colspan 导致的 N/A 噪音；② 利润表 canonical 选到每股收益表而非主表；③ 跨年 concept 不可比导致 revenue 缺失、5 个指标 not_computable。

**Architecture:** ① 提取器保留/展开 colspan 并识别表头年份行，渲染器按年份列对齐（`行名 | 2024 | 2025`），`$`/括号与相邻数字合并；② income_statement 的 canonical 选择加"主表特征优先"（含 Revenue/Operating income 特征优先于行数最多）；③ `_select_pair` 允许 target/comparator 从同一候选集合各自独立匹配不同 concept。

**Tech Stack:** Python 3.12、标准库 `html.parser`、Pydantic、pytest、ruff、mypy strict。

**Spec:** 三个缺陷的实测根因（已确认）：
- ① GOOGL 资产负债表/利润表 HTML 表格用 colspan：行名列 colspan=3，每个年份数据列实际为 `空x3 | $x1 | 数字x1 | 空x1`（有的行 `$` 省略、数字 colspan=2）。提取器按 `<tr>/<td>` 原样重组不展开 colspan → 每行列数不一 → 渲染按最长行定列、短行补 N/A。
- ② GOOGL 主利润表 table[79]（16 行，含 Revenues）存在，但每股收益细表行数更多，canonical"行数最多"规则选错。
- ③ GOOGL 2024 财年用 `RevenueFromContractWithCustomerExcludingAssessedTax`、2025 财年用 `Revenues`，两个 concept 都在 revenue 候选集合里，但 `_select_pair` 要求 target 与 comparator 用同一 concept → 无 concept 同时满足两年 → revenue 缺失 → 收入增长率/毛利率/营业利润率/净利率/经营现金流率全部 not_computable。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- 依赖边界：`financial/` 与 `reporting/` 层仅标准库 + Pydantic + domain；`infrastructure/` 按既有依赖方向。
- 新代码注释用中文。
- 测试用 pytest，命名 `tests/test_*.py`；新逻辑必须有 failing test 先行。
- 数字/表格内容**不经 LLM 改写**（原表数字原封不动）。
- repo 有既有全树 `ruff format` 漂移与 2 个既有全树 mypy 错误——改动文件门禁干净即可，不 reformat 无关文件。

---

### Task 1: 修复 colspan——原表按年份列对齐渲染

**Files:**
- Modify: `src/invest_research/financial/html_statement_extractor.py`（`HtmlStatement` 增加可选年份列信息）
- Modify: `src/invest_research/reporting/html_statements_renderer.py`（按年份列对齐渲染）
- Test: `tests/test_html_statement_extractor.py`、`tests/test_html_statements_renderer.py`

**Interfaces:**
- Consumes: 现有 `HtmlStatement`（`kind`、`rows: tuple[tuple[str, ...], ...]`）。
- Produces: `HtmlStatement` 增加 `year_columns: tuple[str, ...] | None = None`（识别出的年份，如 `("2024","2025")`；无法识别为 None）。`render_html_statements` 在 `year_columns` 存在时渲染 `行名 | 年份1 | 年份2…` 干净表格（`$`/括号/空列并入数值），否则回退现有逻辑。

- [ ] **Step 1: 写失败测试**（构造 GOOGL 风格 colspan 表格）

```python
# tests/test_html_statements_renderer.py 追加
def test_render_aligns_by_year_columns_without_na_noise():
    from invest_research.financial.annual_statements import FinancialStatementKind
    from invest_research.financial.html_statement_extractor import HtmlStatement
    from invest_research.reporting.html_statements_renderer import render_html_statements

    stmt = HtmlStatement(
        kind=FinancialStatementKind.BALANCE_SHEET,
        # 行名x3 | 2024 | 2025 展开后的原始行（含 $ 与空列）
        rows=(
            ("", "", "", "As of December 31,", "", "", "", "", "", "", ""),
            ("", "", "", "2024", "", "", "2025", "", "", "", ""),
            ("现金及现金等价物", "", "", "$", "23,466", "", "$", "30,708", "", "", ""),
        ),
        year_columns=("2024", "2025"),
        source_table_index=0,
    )
    md = render_html_statements((stmt,))
    assert "2024" in md and "2025" in md
    assert "N/A" not in md  # 干净对齐，无 N/A 噪音
    assert "$ 23,466" in md or "23,466" in md
```

（同理为现金流量表三年份构造一例。）

- [ ] **Step 2: 运行确认失败**
Run: `.venv/Scripts/python.exe -m pytest tests/test_html_statements_renderer.py -v`（当前渲染含 N/A，断言失败）

- [ ] **Step 3: 实现**

`html_statement_extractor.py`：
```python
# HtmlStatement 增加字段
year_columns: tuple[str, ...] | None = None
```
提取时从表格前两行识别年份：遍历表格行，找"含 2-3 个 4 位年份文本的短行"（如 `2024`/`2025`/`2023`），取其年份文本序列作为 `year_columns`。识别不到则 None。

`html_statements_renderer.py`：`year_columns` 存在时——表头 = `("项目", *year_columns)`；每个数据行：首列为行名（去掉空列），按年份列锚点取数值（识别 `$`/括号并合并到相邻数字；空值显示 `·` 而非 N/A）。锚点定位方式以实测 GOOGL 结构为准（行名列 colspan=3、每年份数据列 `空x3|$|数字|空` 或 `空x3|数字x2|空`），用 TDD 逐步适配。

- [ ] **Step 4: 运行确认通过（含既有测试回归）**
Run: `.venv/Scripts/python.exe -m pytest tests/test_html_statements_renderer.py tests/test_html_statement_extractor.py -v`
Expected: 新用例 PASS + 既有用例无回归

- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/financial/html_statement_extractor.py src/invest_research/reporting/html_statements_renderer.py tests/
git add src/invest_research/financial/html_statement_extractor.py src/invest_research/reporting/html_statements_renderer.py tests/
git commit -m "fix(p07): 原表按表头年份列对齐渲染，消除 colspan N/A 噪音"
```

---

### Task 2: 修复利润表 canonical 选到每股收益表

**Files:**
- Modify: `src/invest_research/financial/html_statement_extractor.py`（canonical 选择）
- Test: `tests/test_html_statement_extractor.py`

**Interfaces:**
- Consumes: `extract_financial_tables`（现有 canonical 选择：每 kind 行数最多）。
- Produces: income_statement 的 canonical 优先"含 Revenue/Operating income 特征的主表"，再按行数；其他 kind 保持行数最多。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_html_statement_extractor.py 追加
def test_income_canonical_prefers_main_statement_over_per_share():
    from invest_research.financial.annual_statements import FinancialStatementKind
    from invest_research.financial.html_statement_extractor import extract_financial_tables

    html = """
    <div>NVIDIA Corporation and Subsidiaries<br>Consolidated Statements of Income</div>
    <table>
    <tr><td>Revenues</td><td>100</td></tr>
    <tr><td>Operating income</td><td>40</td></tr>
    <tr><td>Net income</td><td>30</td></tr>
    </table>
    <div>per share data</div>
    <table>
    <tr><td>Basic net income per share:</td><td>1</td></tr>
    <tr><td>Numerator</td><td>2</td></tr>
    <tr><td>Denominator</td><td>3</td></tr>
    <tr><td>Basic net income per share</td><td>4</td></tr>
    <tr><td>Diluted net income per share</td><td>5</td></tr>
    <tr><td>Numerator</td><td>6</td></tr>
    </table>
    """
    statements = extract_financial_tables(html)
    income = next(s for s in statements if s.kind is FinancialStatementKind.INCOME_STATEMENT)
    assert any("Revenues" in row[0] for row in income.rows)  # 主表（含 Revenues）
```

- [ ] **Step 2: 运行确认失败**（当前 canonical 行数最多选到每股收益表）
- [ ] **Step 3: 实现**：income_statement 候选表评分——含 `Revenue`/`Operating income` 特征的行名加分，评分最高者获胜（平手按行数）；其余 kind 保持行数最多。
- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_html_statement_extractor.py` 全量）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/financial/html_statement_extractor.py tests/test_html_statement_extractor.py
git commit -m "fix(p07): 利润表 canonical 优先主表（含 Revenue/Operating income），不再选到每股收益细表"
```

---

### Task 3: 修复跨年 concept 不可比（`_select_pair` 放宽）

**Files:**
- Modify: `src/invest_research/financial/annual_comparison.py`（`_select_pair`）
- Test: `tests/test_annual_comparison.py`（或 `tests/test_annual_statements.py`）

**Interfaces:**
- Consumes: `_select_pair`（现有：遍历候选 concept，要求 target/comparator 同 concept）。
- Produces: `_select_pair` 允许 target/comparator 从候选集合各自独立匹配不同 concept；找不到才返回 limitation。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_annual_comparison.py 追加（构造 GOOGL 场景：两年不同 concept）
def test_select_pair_allows_different_concepts_across_years():
    from datetime import date
    from invest_research.domain.models import FinancialFact
    from invest_research.financial.annual_comparison import _select_pair
    from invest_research.financial.concept_mapping import ConceptMapping

    mapping = ConceptMapping(entries=[{"metric_name": "revenue", "candidates": (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "Revenue")}])
    def fact(concept, fy, end, val):
        return FinancialFact(concept=concept, fiscal_year=fy, fiscal_period="FY",
                             form_type="10-K", accession_number="accn", value=val,
                             unit="USD", period_start=date(end.year-1, 1, 1), period_end=end)
    facts = [
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", 2024, date(2024,12,31), 350018),
        fact("Revenues", 2025, date(2025,12,31), 402836),
    ]
    target, comparator = _select_pair(
        facts, mapping=mapping, logical_name="revenue",
        target_year=2025, target_accession="accn", target_report_date=date(2025,12,31),
        comparator_year=2024, comparator_accession="accn", comparator_report_date=date(2024,12,31),
        instant=False,
    )
    assert target.fact is not None and target.fact.value == 402836
    assert comparator.fact is not None and comparator.fact.value == 350018
```

（构造 `FinancialFact` 所需必填字段以实际 model 为准；测试目标：target 用 `Revenues`、comparator 用 `RevenueFromContract…` 都能匹配。）

- [ ] **Step 2: 运行确认失败**（当前要求同 concept → limitation）
- [ ] **Step 3: 实现**：`_select_pair` 先尝试"同 concept"（保持原行为）；若失败，再遍历候选，允许 target/comparator 各自选不同 concept（都是合法候选）。仅当两者都无匹配才返回 limitation。保留"同 concept 优先"避免行为退化。
- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_annual_comparison.py`、`tests/test_annual_statements.py` 全量）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/financial/annual_comparison.py tests/test_annual_comparison.py
git commit -m "fix(p07): _select_pair 允许跨年使用不同 concept，修复 revenue 缺失致指标 not_computable"
```

---

### Task 4: 全链路验证（GOOGL 数据冒烟 + 全量测试）

- [ ] **Step 1**: 用 `_tmp_googl_source.html`（项目根目录，临时从容器提取）跑 `extract_financial_tables` + `render_html_statements`，验证：三张表干净渲染（资产负债表 `行名|2024|2025`、现金流量表/利润表 `行名|2023|2024|2025`），利润表为主表（含 Revenues/Operating income），无 N/A 噪音。
- [ ] **Step 2**: 全量 `.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -3`（无新失败；2 个 psycopg2 既有失败除外）。
- [ ] **Step 3**: commit（若修复 1-3 有遗漏的测试/文档）或确认干净。

## Self-Review

- **Spec 覆盖**：① colspan 年份列对齐（Task 1）✓；② 利润表主表优先（Task 2）✓；③ 跨年 concept 可比（Task 3）✓；GOOGL 冒烟 + 全量（Task 4）✓。
- **占位符**：Task 1/3 的 `FinancialFact`/colspan 锚点细节标"以实际 model/实测为准"——需 implementer 按既有 fixture 模式与 GOOGL 结构 TDD 适配，不是跳过测试。
- **接口一致**：`HtmlStatement.year_columns` 新增字段带默认 None，向后兼容；`render_html_statements` 无年份列时回退既有逻辑。
