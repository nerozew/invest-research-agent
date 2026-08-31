# SEC 在线搜索多命中名称补全 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SEC browse-edgar 多命中时名称渲染成 `ARRAY(...)`（SEC 自身 bug）导致在线搜索返回空的问题：多命中时 `<cik>` 是真实的，用 SEC submissions API（`CIK{...}.json`）按 CIK 补回真实名称，让多实体知名公司（Nintendo/Nestle 等）也能在线解析到 CIK + 名称。

**Architecture:** `tools/sec_company_search.py` 增强：① `_parse_company_search` 对多命中 ARRAY 候选**保留 cik**（legal_name 标记待补）；② 新增 `_fetch_name_by_cik(cik, client, user_agent)`——调 `https://data.sec.gov/submissions/CIK{cik}.json` 拿 `{"name": ...}`；③ `search_company_by_name` 对 ARRAY/空名称候选逐个补名，补名成功 → 保留（真实名称），失败 → 跳过（best-effort，不引入垃圾名）。单命中（名称正常）不变。

**Tech Stack:** Python 3.12、httpx（注入 client）、标准库、pytest、ruff、mypy strict。

**Spec:** 用户确认的改进方向（对话约定）：
- 多命中时 SEC 把 `conformed-name` 渲染成 `ARRAY(...)`（实测：Nintendo/Nestle/Adidas/Berkshire 等），但 `<cik>` 真实。
- 改进：保留 cik，用 submissions API 补真实名称。
- best-effort：补名失败跳过该候选（保持 ARRAY 防御，不引入垃圾名）；尊重 SEC rate limit（多命中候选 ≤10，逐 cik 补名可接受）。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- 依赖边界：`tools/` 层仅标准库 + Pydantic + domain + 注入 httpx client（不自己建）。
- SEC 合规：所有请求带显式 UA；best-effort。
- 新代码注释用中文。
- 测试用 pytest，failing test 先行；mock client（不真联网）。
- repo 有既有全树 `ruff format` 漂移与 2 个既有全树 mypy 错误——改动文件门禁干净即可。

---

### Task 1: 多命中名称补全（submissions API）

**Files:**
- Modify: `src/invest_research/tools/sec_company_search.py`
- Test: `tests/test_sec_company_search.py`

**Interfaces:**
- Consumes: `search_company_by_name`、`_parse_company_search`、注入 client。
- Produces:
  - `_looks_like_array_residue(name: str) -> bool`（`ARRAY(` 前缀判断）。
  - `_fetch_name_by_cik(cik: str, client: Any, user_agent: str) -> str`（submissions API 补名称；失败返回 `""`）。
  - `search_company_by_name`：解析候选后，对 ARRAY/空名称候选逐个 `_fetch_name_by_cik` 补名；成功 → `{"cik","legal_name(真实)","ticker"}`，失败 → 跳过。

- [ ] **Step 1: 写失败测试**（mock client）

```python
# tests/test_sec_company_search.py 追加
def test_fetch_name_by_cik_uses_submissions_api() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert "data.sec.gov/submissions/CIK0001472373.json" in request.url
        return httpx.Response(200, json={"name": "NESTLE SA"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert _fetch_name_by_cik("0001472373", client, "test-agent/1.0") == "NESTLE SA"


def test_search_multi_hit_enriches_name_from_submissions() -> None:
    import httpx

    # browse-edgar 多命中：conformed-name 为 ARRAY 残留，cik 真实
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <company-info name="ARRAY(0x1)">
          <cik>0001472373</cik>
          <conformed-name>ARRAY(0x1)</conformed-name>
        </company-info>
      </entry>
    </feed>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-edgar" in str(request.url):
            return httpx.Response(200, text=atom)
        assert "submissions/CIK0001472373.json" in str(request.url)
        return httpx.Response(200, json={"name": "NESTLE SA"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidates = search_company_by_name("Nestle", client, "test-agent/1.0")
    assert candidates == [{"cik": "0001472373", "legal_name": "NESTLE SA", "ticker": ""}]


def test_search_multi_hit_skips_when_enrich_fails() -> None:
    import httpx

    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><company-info name="ARRAY(0x1)"><cik>0001472373</cik><conformed-name>ARRAY(0x1)</conformed-name></company-info></entry>
    </feed>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-edgar" in str(request.url):
            return httpx.Response(200, text=atom)
        return httpx.Response(500)  # submissions 失败

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert search_company_by_name("Nestle", client, "test-agent/1.0") == []
```

- [ ] **Step 2: 运行确认失败**（当前多命中 ARRAY 全跳 → `[]`，不补名）
- [ ] **Step 3: 实现**

```python
def _looks_like_array_residue(name: str) -> bool:
    return name.startswith("ARRAY(")


def _fetch_name_by_cik(cik: str, client: Any, user_agent: str) -> str:
    """按 CIK 调 SEC submissions API 拿真实公司名；失败返回空串。"""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        response = client.get(url, headers={"User-Agent": user_agent}, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        return str(data.get("name", "")).strip()
    except Exception:  # noqa: BLE001 - best-effort
        return ""
```

`search_company_by_name` 解析后：
```python
    candidates = _parse_company_search(response.text)
    enriched: list[dict[str, str]] = []
    for cand in candidates:
        legal_name = cand["legal_name"]
        if _looks_like_array_residue(legal_name):
            real = _fetch_name_by_cik(cand["cik"], client, user_agent)
            if not real:
                continue  # 补名失败 → 跳过（不引入垃圾名）
            legal_name = real
        enriched.append({"cik": cand["cik"], "legal_name": legal_name, "ticker": cand.get("ticker", "")})
    return enriched
```

- [ ] **Step 4: 运行确认通过**
Run: `.venv/Scripts/python.exe -m pytest tests/test_sec_company_search.py -v`
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/tools/sec_company_search.py tests/test_sec_company_search.py
git add src/invest_research/tools/sec_company_search.py tests/test_sec_company_search.py
git commit -m "feat(p07): SEC 在线搜索多命中时用 submissions API 补真实名称"
```

---

### Task 2: 真实验证 + 回归

- [ ] **Step 1**: 真实调用 `search_company_by_name("Nestle" / "Nintendo", client, UA)`（SEC_USER_AGENT），验证多命中补名后返回真实 CIK + 名称（如 Nestle → 真实实体 CIK + "NESTLE S.A." 或类似）。
- [ ] **Step 2**: 全量 `.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -3`（无新失败）。
- [ ] **Step 3**: 若验证通过，rebuild 镜像 → up → 端到端重跑一家之前多命中失败的知名公司（本地 MISS 的，如 "Nintendo"——但需注意：即使在线拿到 CIK，后续 filings 若该实体无 10-K 仍可能失败；若如此则如实记录边界）。

## Self-Review

- **Spec 覆盖**：多命中补名（Task 1）✓；真实验证（Task 2）✓。
- **best-effort 保持**：补名失败跳过，不引入垃圾名；单命中路径不变。
- **依赖边界**：仍只用注入 client + stdlib。
- **rate limit**：多命中候选 ≤10，逐 cik submissions 一次，符合 SEC 10 req/s 限制。
