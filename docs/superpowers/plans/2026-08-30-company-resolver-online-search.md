# 公司解析器在线 SEC 搜索兜底 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补全三层解析架构的第三层——本地快照（评分匹配）+ 覆盖表都解析不到时，实时用 SEC EDGAR 按名称搜索公司、拿 CIK 兜底。本地主路径保持确定性、离线；online 兜底 best-effort、明确失败。

**Architecture:** ① 新模块 `tools/sec_company_search.py`：`search_company_by_name(name, client, user_agent) -> list[dict]`——调 SEC browse-edgar getcompany（`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={名称}&output=atom`），解析候选（CIK/名称/ticker），含 SEC rate-limit 与 UA 合规；best-effort。② `company_resolver.CompanyResolverTool` 增加可选 `online_search: Callable[[str], list[CompanyIdentity]] | None`——`execute` 本地无候选时，若有 online_search 则调用它；有结果返回（resolved=True 唯一 或 candidates），无结果/失败 → 明确 ToolFailure（不静默）。③ worker 注入 online_search（用 `build_http_client` + SEC_USER_AGENT + `search_company_by_name`）。

**Tech Stack:** Python 3.12、httpx（注入的 client）、标准库 `xml.etree`（解析 atom）、Pydantic、pytest、ruff、mypy strict。

**Spec:** 用户确认的三层架构（对话约定）：
- 第 1 层本地快照（评分匹配）+ 第 2 层覆盖表：已落地（`company-resolver-flexible-match` 计划）。
- 第 3 层：本地解析不到（快照无、覆盖表无）时，实时上 SEC 官网按名称搜 CIK。
- 仅此层联网；本地主路径不联网、确定性。
- 失败语义：online 无结果/网络失败 → 明确 ToolFailure（INPUT_INVALID / 网络错误分类），不静默回退。

## Global Constraints

- Python 3.12；`ruff check src tests` 必须通过（项目用 `.venv/Scripts/python.exe`）。
- 依赖边界：`tools/` 层仅标准库 + Pydantic + domain + **注入的 httpx client**（不自己建 client；复用 `infrastructure/http/client.py` 的 `build_http_client` 由 worker 注入）。
- SEC 合规：请求必须带显式 User-Agent（SEC EDGAR 要求）；rate-limit 尊重（失败按错误码分类，不重试风暴）。
- 新代码注释用中文。
- 测试用 pytest，新逻辑必须有 failing test 先行；online 搜索用 mock client（不真联网）。
- repo 有既有全树 `ruff format` 漂移与 2 个既有全树 mypy 错误——改动文件门禁干净即可。

---

### Task 1: `sec_company_search` 模块（SEC 名称搜索 → 候选）

**Files:**
- Create: `src/invest_research/tools/sec_company_search.py`
- Test: `tests/test_sec_company_search.py`

**Interfaces:**
- Consumes: 注入的 `httpx.Client`、`user_agent`；SEC browse-edgar getcompany 端点。
- Produces:
  - `search_company_by_name(name: str, client: Any, user_agent: str) -> list[dict[str, str]]`：返回候选列表 `[{"cik": ..., "legal_name": ..., "ticker": ...}]`；无候选/失败返回 `[]`（best-effort）。
  - 内部 `_parse_company_search(atom_xml: str) -> list[dict[str, str]]`（解析 browse-edgar atom 响应，供测试）。

- [ ] **Step 1: 写失败测试**（mock client，不联网）

```python
# tests/test_sec_company_search.py
from invest_research.tools.sec_company_search import search_company_by_name, _parse_company_search

_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>EXXON MOBIL CORP</title>
    <category term="cik"/>
    <link rel="self" href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000034088&amp;type=10-K"/>
  </entry>
</feed>"""


def test_parse_company_search_extracts_candidates() -> None:
    parsed = _parse_company_search(_ATOM)
    assert parsed == [{"cik": "0000034088", "legal_name": "EXXON MOBIL CORP", "ticker": ""}]


def test_search_company_by_name_returns_candidates_with_mock_client() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("User-Agent")  # SEC 合规：必须带 UA
        return httpx.Response(200, text=_ATOM)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidates = search_company_by_name("Exxon Mobil", client, user_agent="test-agent/1.0")
    assert candidates and candidates[0]["cik"] == "0000034088"


def test_search_company_by_name_fails_gracefully() -> None:
    import httpx

    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(500)))
    assert search_company_by_name("X", client, "test-agent/1.0") == []
```

- [ ] **Step 2: 运行确认失败**（`ModuleNotFoundError`）
- [ ] **Step 3: 实现**

```python
"""src/invest_research/tools/sec_company_search.py
SEC EDGAR 在线公司搜索（第三层兜底）：本地快照解析不到时按名称实时搜索拿 CIK。
best-effort：无候选/网络失败返回 []（不阻塞调用方）。
SEC 合规：请求带显式 User-Agent；httpx client 由 infrastructure 注入（复用 build_http_client）。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

_EDGAR_COMPANY_SEARCH = "https://www.sec.gov/cgi-bin/browse-edgar"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_company_search(atom_xml: str) -> list[dict[str, str]]:
    """解析 browse-edgar getcompany atom 响应，提取 {cik, legal_name, ticker} 候选。"""
    try:
        root = ET.fromstring(atom_xml)
    except ET.ParseError:
        return []
    candidates: list[dict[str, str]] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        cik = ""
        for link in entry.findall(f"{_ATOM_NS}link"):
            href = link.get("href") or ""
            match = re.search(r"CIK=(\d{10})", href)
            if match:
                cik = match.group(1)
        if title and cik:
            candidates.append({"cik": cik, "legal_name": title, "ticker": ""})
    return candidates


def search_company_by_name(
    name: str, client: Any, user_agent: str
) -> list[dict[str, str]]:
    """按公司名调 SEC browse-edgar 搜索；best-effort，失败/无候选返回 []。"""
    params = f"?action=getcompany&company={quote(name)}&output=atom&count=10"
    try:
        response = client.get(
            _EDGAR_COMPANY_SEARCH + params,
            headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
            timeout=30.0,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - 在线兜底 best-effort
        return []
    return _parse_company_search(response.text)
```

- [ ] **Step 4: 运行确认通过**
Run: `.venv/Scripts/python.exe -m pytest tests/test_sec_company_search.py -v`
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/tools/sec_company_search.py tests/test_sec_company_search.py
git add src/invest_research/tools/sec_company_search.py tests/test_sec_company_search.py
git commit -m "feat(p07): SEC 在线公司名称搜索模块（第三层兜底）"
```

---

### Task 2: resolver 接入 `online_search`

**Files:**
- Modify: `src/invest_research/tools/company_resolver.py`（`CompanyResolverTool` 增加 `online_search` 注入；`execute` 本地失败时兜底）
- Test: `tests/test_company_resolver.py`

**Interfaces:**
- Consumes: `CompanyIndex.lookup`、注入的 `online_search: Callable[[str], list[CompanyIdentity]] | None`。
- Produces: `CompanyResolverTool.__init__(index=None, online_search=None)`；`execute` 本地 `lookup` 空时，若 `online_search` 存在则调用（输入 `input_company`），有候选返回 `ResolveCompanyResponse`（唯一 → resolved=True；多个 → resolved=False + candidates），无候选/online 失败 → 原 ToolFailure（文案注明"本地未命中，在线搜索亦无结果"）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_company_resolver.py 追加
def test_execute_falls_back_to_online_search_when_local_miss() -> None:
    from invest_research.tools.company_resolver import CompanyIndex, CompanyResolverTool
    from invest_research.domain.models import CompanyIdentity

    index = CompanyIndex([])  # 本地空
    found = [CompanyIdentity(cik="9999999999", ticker="", legal_name="New Company Corp")]

    def online(query: str) -> list[CompanyIdentity]:
        assert query == "New Company"
        return found

    tool = CompanyResolverTool(index, online_search=online)
    result = tool.execute(ResolveCompanyRequest(input_company="New Company"))
    assert result.is_success and result.value.resolved is True
    assert result.value.candidates[0].cik == "9999999999"


def test_execute_online_search_no_result_returns_failure() -> None:
    tool = CompanyResolverTool(CompanyIndex([]), online_search=lambda q: [])
    result = tool.execute(ResolveCompanyRequest(input_company="Unknown"))
    assert not result.is_success
```

- [ ] **Step 2: 运行确认失败**（当前 execute 本地空 → 直接 ToolFailure，不调 online）
- [ ] **Step 3: 实现**：`CompanyResolverTool` 加 `online_search` 字段；`execute` 本地候选空且 `online_search` 非 None 时调用；结果转换为 `CompanyIdentity` 列表返回；无结果/异常 → ToolFailure（异常分类复用现有 `ToolError`）。
- [ ] **Step 4: 运行确认通过 + 回归**（`tests/test_company_resolver.py` 全量）
- [ ] **Step 5: ruff 检查并提交**
```bash
.venv/Scripts/python.exe -m ruff check src/invest_research/tools/company_resolver.py tests/test_company_resolver.py
git add src/invest_research/tools/company_resolver.py tests/test_company_resolver.py
git commit -m "feat(p07): resolver 本地未命中时在线 SEC 搜索兜底"
```

---

### Task 3: worker 注入 + 验证

**Files:**
- Modify: `src/invest_research/infrastructure/queue/worker.py`（注入 `online_search` 到 resolver）
- Modify: `tests/test_annual_filing_selector.py` 或 resolver 相关（按需）

**Interfaces:**
- Consumes: `search_company_by_name`、`build_http_client`、`SEC_USER_AGENT`（settings）。
- Produces: worker 创建 `CompanyResolverTool(online_search=<闭包：name → search_company_by_name 结果转 CompanyIdentity>)` 注入 flow。

- [ ] **Step 1**: worker 注入 `online_search`（闭包：调 `search_company_by_name`，把 `[{"cik","legal_name","ticker"}]` 转 `CompanyIdentity` 列表；`user_agent` 用 settings 的 `sec_user_agent` 或 `.env` 的 SEC_USER_AGENT）。
- [ ] **Step 2**: 本地冒烟：`.venv/Scripts/python.exe -c "from invest_research.tools.company_resolver import CompanyResolverTool; ..."` 用 mock/真实名称验证本地未命中触发 online（构造一个快照外名称，如 "Some Obscure New Company"）。
- [ ] **Step 3**: 全量 `.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -3`（无新失败）。
- [ ] **Step 4**: commit（若注入代码存在）。

## Self-Review

- **Spec 覆盖**：在线搜索模块（Task 1）✓；resolver 兜底（Task 2）✓；worker 注入（Task 3）✓。
- **三层架构完整**：本地快照（评分）→ 覆盖表（数据修正）→ 在线搜索（快照外公司）。仅第三层联网。
- **确定性/失败语义**：本地主路径不联网；online 仅本地空时触发；无结果/失败 → 明确 ToolFailure。
- **依赖边界**：`tools/` 层不自己建 httpx client（由 worker 用 `build_http_client` 注入）；SEC 合规（UA）。
