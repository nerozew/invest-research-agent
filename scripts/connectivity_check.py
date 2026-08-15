"""小规模连接测试：LLM / SEC / Serper 三端连通性（只发最小请求，不触发完整 E2E）。

用法：uv run python scripts/connectivity_check.py

安全：
- 绝不打印 LLM_API_KEY / SERPER_API_KEY 或含 key 的完整请求头；
- 只输出 HTTP 状态码、耗时、简短回复/条数；
- LLM 只发一次极短对话（max_tokens=8，费用极小）；Serper 一次 2 条搜索。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from invest_research.settings import Settings

    settings = Settings(_env_file=PROJECT_ROOT / ".env")
    llm_key = settings.llm_api_key.get_secret_value()
    serper_key = settings.serper_api_key.get_secret_value() if settings.serper_api_key else ""
    contact = settings.sec_user_agent_contact or "invest-research/0.1 (+test@example.com)"

    import httpx

    results: list[tuple[str, bool, str, str]] = []

    # ---- 1. LLM 连通（一次极短对话）----
    try:
        t0 = time.time()
        with httpx.Client(timeout=30.0) as c:
            r = c.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {llm_key}"},
                json={
                    "model": settings.llm_model_research,
                    "messages": [{"role": "user", "content": "只回复：OK"}],
                    "max_tokens": 8,
                },
            )
        dt = time.time() - t0
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()[:30]
            results.append(("LLM", True, f"HTTP 200 回复: {content!r}", f"{dt:.1f}s"))
        else:
            results.append(("LLM", False, f"HTTP {r.status_code}", f"{dt:.1f}s"))
    except Exception as exc:  # noqa: BLE001 - 连接测试边界
        results.append(("LLM", False, f"{type(exc).__name__}: {exc}", ""))

    # ---- 2. SEC 连通（单概念查询，公开数据无费用）----
    try:
        t0 = time.time()
        with httpx.Client(timeout=30.0) as c:
            r = c.get(
                "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json",
                headers={"User-Agent": contact, "Accept-Encoding": "gzip, deflate"},
            )
        dt = time.time() - t0
        if r.status_code == 200:
            n = len(r.json().get("units", {}).get("USD", []))
            results.append(("SEC", True, f"HTTP 200 Revenues 条目数={n}", f"{dt:.1f}s"))
        else:
            results.append(("SEC", False, f"HTTP {r.status_code}", f"{dt:.1f}s"))
    except Exception as exc:  # noqa: BLE001
        results.append(("SEC", False, f"{type(exc).__name__}: {exc}", ""))

    # ---- 3. Serper 连通（一次 2 条搜索，费用极小）----
    if not serper_key:
        results.append(("Serper", False, "未配置 SERPER_API_KEY", ""))
    else:
        try:
            t0 = time.time()
            with httpx.Client(timeout=30.0) as c:
                r = c.post(
                    settings.serper_endpoint,
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": "Apple Inc 10-K SEC", "num": 2},
                )
            dt = time.time() - t0
            if r.status_code == 200:
                n = len(r.json().get("organic", []))
                results.append(("Serper", True, f"HTTP 200 organic={n}", f"{dt:.1f}s"))
            else:
                results.append(("Serper", False, f"HTTP {r.status_code}", f"{dt:.1f}s"))
        except Exception as exc:  # noqa: BLE001
            results.append(("Serper", False, f"{type(exc).__name__}: {exc}", ""))

    print("\n===== 小规模连接测试结果 =====")
    all_ok = True
    for name, ok, detail, dur in results:
        line = f"[{'OK' if ok else 'FAIL'}] {name}: {detail}"
        if dur:
            line += f"  ({dur})"
        print(line)
        all_ok = all_ok and ok
    print("\n[ALL_OK] 三项连接全部成功" if all_ok else "\n[FAILED] 存在失败项（见上）")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())