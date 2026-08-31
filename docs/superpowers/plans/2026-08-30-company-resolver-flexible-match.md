# 公司解析器弹性匹配（最相符评分 + 主实体覆盖）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 XOM 解析失败（根因：SEC company_tickers.json 把 XOM ticker 绑到子公司 0002115436，母公司 0000034088 不在数据里；且 resolver 只做精确匹配、精确失败即拒绝）。改为：① 主实体覆盖表在运行时补入正确实体；② resolver 用"最相符评分匹配"替代"精确命中否则拒绝"——精确命中最高分，返回最高分唯一命中，同分显式 ambiguity。

**Architecture:** ① `company_resolver.py` 新增 `MAIN_ENTITY_OVERRIDES`（ticker → {cik, legal_name}，补入 SEC 数据缺失/错绑的主实体），`load_sec_company_index()` 加载快照时并入（运行时生效，不依赖重新生成）；`update_sec_company_index.py` 生成时同样并入（未来重新生成保留覆盖）。② `CompanyIndex.lookup` 改为评分匹配：精确（ticker/CIK/归一化名称）3 分、归一化名称前缀 2 分、名称包含 1 分；返回最高分候选，多个同最高分 → ambiguity（resolved=False）；无任何 >0 分 → ToolFailure。

**Tech Stack:** Python 3.12、标准库、Pydantic、pytest、ruff、mypy strict。

**Spec:** 用户确认的方向：
- 不要"强硬靠 SEC 表约束"——数据错绑/缺失时，覆盖表补入正确实体；查询名称时按"最相符"（评分）匹配。
- 匹配弹性：精确命中给最高分，返回最高分精确命中；同分多候选显式 ambiguity（不猜）。
- 根因（实测）：SEC company_tickers.json 里 XOM → CIK 0002115436（ExxonMobil Holdings Corp 子公司）；母公司 EXXON MOBIL CORP（0000034088）不在该数据，但确实提交 10-K（SEC submissions 确认）。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- 依赖边界：`tools/` 层仅标准库 + Pydantic + domain。
- 新代码注释用中文。
- 测试用 pytest，新逻辑必须有 failing test 先行。
- resolver 保持确定性、可解释：评分规则明确，无 >0 分才失败，同分不猜。
- repo 有既有全树 `ruff format` 漂移与 2 个既有全树 mypy 错误——改动文件门禁干净即可，不 reformat 无关文件。

---

### Task 1: resolver 最相符评分匹配

**Files:**
- Modify: `src/invest_research/tools/company_resolver.py`（`CompanyIndex.lookup` + 新增评分函数）
- Test: `tests/test_company_resolver.py`（或既有 resolver 测试文件）

**Interfaces:**
- Consumes: `CompanyIndex`（现有索引结构）、`_normalize_lookup_key`、`CompanyIdentity`。
- Produces:
  - `_match_score(query: str, identity: CompanyIdentity) -> int`：精确 3、名称前缀 2、名称包含 1、无 0。
  - `CompanyIndex.lookup(query) -> list[CompanyIdentity]`：返回最高分候选（>0 分）；多个同最高分 → 全部返回（上层标 ambiguity）；无 >0 分 → 空。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_company_resolver.py 追加
def test_lookup_scores_highest_match_for_partial_name() -> None:
    from invest_research.tools.company_resolver import CompanyIndex
    from invest_research.domain.models import CompanyIdentity

    parent = CompanyIdentity(cik="0000034088", ticker="XOM", legal_name="EXXON MOBIL CORP")
    subsidiary = CompanyIdentity(cik="0002115436", ticker="XOM", legal_name="ExxonMobil Holdings Corp")
    index = CompanyIndex([("XOM", parent), ("XOM", subsidiary)])
    # 查询 "Exxon Mobil" → 归一化后与 parent 名称前缀匹配（高分），与 subsidiary 包含匹配（低分）
    result = index.lookup("Exxon Mobil")
    assert [c.cik for c in result] == ["0000034088"]  # 最高分命中 parent


def test_lookup_exact_ticker_wins_over_name_substring() -> None:
    index = CompanyIndex([
        ("XOM", CompanyIdentity(cik="0000034088", ticker="XOM", legal_name="EXXON MOBIL CORP")),
        ("XOM", CompanyIdentity(cik="0002115436", ticker="XOM", legal_name="ExxonMobil Holdings Corp")),
    ])
    # 精确 ticker XOM → 两个实体同分（都是精确 ticker）→ ambiguity，都返回
    result = index.lookup("XOM")
    assert len(result) == 2
    assert {c.cik for c in result} == {"0000034088", "0002115436"}


def test_lookup_no_match_returns_empty() -> None:
    index = CompanyIndex([])
    assert index.lookup("TotallyUnknown Corp") == []
```

- [ ] **Step 2: 运行确认失败**（当前 lookup 只精确匹配，部分名称 → 空）
- [ ] **Step 3: 实现**

```python
def _match_score(query: str, identity: CompanyIdentity) -> int:
    """查询与实体的匹配分：精确 3、归一化名称前缀 2、名称包含 1、无 0。

    精确覆盖 ticker/CIK/归一化名称；前缀/包含只看归一化 legal_name（避免过泛）。
    """
    q = _normalize_lookup_key(query)
    if not q:
        return 0
    name = _normalize_lookup_key(identity.legal_name)
    # 精确：归一化名称全等，或查询本身就是 ticker/CIK 精确命中（由 _key 归一化保证）
    if name == q:
        return 3
    if _key(query) == identity.cik or (
        identity.ticker and _key(query) in _ticker_keys(identity.ticker)
    ):
        return 3
    if name.startswith(q):
        return 2
    if q in name:
        return 1
    return 0
```

`lookup` 改为：
```python
def lookup(self, query: str) -> list[CompanyIdentity]:
    """返回最高分候选；多个同最高分全部返回（上层判 ambiguity）；无 >0 分返回空。"""
    scored: dict[int, list[CompanyIdentity]] = {}
    # 精确索引命中（现有 _key 路径）也参与，但用评分统一
    exact = self._index.get(self._key(query), [])
    # 全表扫描评分（快照 ~10k 条，单次可接受；或用名称前缀桶优化——先简单扫描）
    for identity in self._all_identities:
        score = _match_score(query, identity)
        if score > 0:
            scored.setdefault(score, []).append(identity)
    if not scored:
        return []
    best = max(scored)
    # 精确索引命中优先（同 3 分时不丢）
    result = scored[best]
    by_cik: dict[str, CompanyIdentity] = {}
    for identity in result:
        by_cik.setdefault(identity.cik, identity)
    return list(by_cik.values())
```

> 实现说明：为保持确定性 + 可控，`CompanyIndex` 增加 `self._all_identities`（构造时收集全部 identity）；`lookup` 用评分而非纯精确。评分时精确索引命中（`_index.get`）与名称评分合并——若精确命中存在且分数 >= 名称匹配，优先精确（最高分语义）。具体合并顺序实现时以测试为准（"精确 ticker 两个实体同分 → 都返回"、"名称前缀命中 → parent"）。

- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_company_resolver.py` 全量；既有 resolver 测试不回归）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/tools/company_resolver.py tests/test_company_resolver.py
git add src/invest_research/tools/company_resolver.py tests/test_company_resolver.py
git commit -m "feat(p07): 公司解析器最相符评分匹配（精确最高分，前缀/包含兜底）"
```

---

### Task 2: 主实体覆盖表（运行时 + 生成脚本）

**Files:**
- Modify: `src/invest_research/tools/company_resolver.py`（新增 `MAIN_ENTITY_OVERRIDES`；`load_sec_company_index` 并入）
- Modify: `scripts/update_sec_company_index.py`（生成时并入覆盖实体）
- Test: `tests/test_company_resolver.py`

**Interfaces:**
- Consumes: 快照 entries、`CompanyIdentity`。
- Produces: `MAIN_ENTITY_OVERRIDES: dict[str, dict[str, str]]`（ticker → {"cik", "legal_name"}）；`load_sec_company_index()` 加载快照后并入覆盖实体（dedup by cik）；`update_sec_company_index.py` `_normalize` 后并入。

- [ ] **Step 1: 写失败测试**

```python
def test_override_entity_added_when_snapshot_missing() -> None:
    from invest_research.tools.company_resolver import MAIN_ENTITY_OVERRIDES, load_sec_company_index

    # 真实快照里 XOM 绑到子公司 0002115436，母公司 0000034088 不在 → 覆盖表应补入
    index = load_sec_company_index()
    parent = next((c for c in index.lookup("EXXON MOBIL CORP")), None)
    assert parent is not None and parent.cik == "0000034088"
    # XOM 查询应能匹配到母公司（精确 ticker 命中两个实体 → 含母公司）
    xom = index.lookup("XOM")
    assert any(c.cik == "0000034088" for c in xom)
```

- [ ] **Step 2: 运行确认失败**（当前 XOM → 只有子公司）
- [ ] **Step 3: 实现**

```python
# company_resolver.py
# SEC company_tickers 数据错绑/缺失主实体（ticker → 正确主实体）。运行时补入，
# 保证 resolver 能解析到真正提交 10-K 的母公司。
MAIN_ENTITY_OVERRIDES: dict[str, dict[str, str]] = {
    "XOM": {"cik": "0000034088", "legal_name": "EXXON MOBIL CORP"},
}


def _apply_main_entity_overrides(entries: list[tuple[str, CompanyIdentity]]) -> None:
    """把主实体覆盖并入 entries（按 cik 去重；覆盖 ticker 的实体保留，补入缺失的）。"""
    existing_ciks = {identity.cik for _, identity in entries}
    for ticker, override in MAIN_ENTITY_OVERRIDES.items():
        if override["cik"] not in existing_ciks:
            entries.append(
                (ticker, CompanyIdentity(cik=override["cik"], ticker=ticker, legal_name=override["legal_name"]))
            )
            existing_ciks.add(override["cik"])
```

`load_sec_company_index` 构造 entries 后调用 `_apply_main_entity_overrides(entries)`。

`update_sec_company_index.py` `_normalize` 的 companies 列表构建后同样并入（保持生成结果与运行时一致）。

- [ ] **Step 4: 运行确认通过 + 回归**
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/tools/company_resolver.py scripts/update_sec_company_index.py tests/test_company_resolver.py
git add src/invest_research/tools/company_resolver.py scripts/update_sec_company_index.py tests/test_company_resolver.py
git commit -m "feat(p07): 公司解析主实体覆盖表（XOM→母公司，运行时补入缺失实体）"
```

---

### Task 3: XOM 全链路验证

- [ ] **Step 1**: rebuild 镜像 → up api worker → 重跑 XOM research-job。
- [ ] **Step 2**: 验证 XOM 报告生成成功：10-K 获取正常（不再 `AnnualRuntimeBlocked`）、财务报表段行名翻译覆盖率 100%、数字原样。
- [ ] **Step 3**: 下载报告到 `real-test-xom-20260830/`。
- [ ] **Step 4**: 全量 `.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -3`（无新失败）。

## Self-Review

- **Spec 覆盖**：最相符评分匹配（Task 1）✓；主实体覆盖表运行时+生成（Task 2）✓；XOM 全链路（Task 3）✓。
- **确定性/可解释**：评分规则明确（精确 3 / 前缀 2 / 包含 1），无 >0 分才失败，同分显式 ambiguity——不猜。
- **性能**：`lookup` 全表扫描 ~10k 条 × 评分，单次可接受（resolver 是低频、确定性本地查询）；如有需要可用前缀桶优化（非本计划范围）。
- **向后兼容**：精确 ticker/CIK 仍最高分命中；`lookup` 空返回语义不变（上层仍 ToolFailure）；覆盖表补实体不影响既有实体。
