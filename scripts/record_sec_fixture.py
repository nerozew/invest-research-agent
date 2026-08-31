"""P05-12 真实 SEC fixture 录制工具（record/replay，联网但只访问 sec.gov）。

用法（示例，AAPL 固定 CIK=0000320193）：
    uv run python scripts/record_sec_fixture.py \
        --ticker AAPL --cik 0000320193 \
        --out tests/fixtures/sec_recorded_aapl.json

行为：
- 访问 SEC 官方数据端点：Company Tickers / Submissions / Company Facts；
- 从真实 submissions 响应里挑选一个"已存在的历史截止日"（默认最近一个 10-K 的
  filingDate，可被 --as-of 显式覆盖并要求存在），不使用动态 today；
- 立即用 infrastructure.fixture 的 sanitize_response 递归脱敏（Authorization/
  Cookie/API Key 等键 → ***），再编 FixtureMeta（含 sha256 checksum）；
- 落盘格式：{"meta": FixtureMeta, "payload": {"tickers", "submissions", "company_facts"}}；
- 绝不写入任何密钥/联系人邮箱（SEC 数据本身不含，脱敏再兜底）。

授权边界：本工具只访问 sec.gov/data.sec.gov 公开数据，不调用任何大模型。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from invest_research.infrastructure.fixture import build_meta, sanitize_response

# SEC EDGAR 合规：User-Agent 必须包含联系邮箱（格式合规占位，非真实邮箱）
SEC_UA = "invest-research-agent/0.1 (contact@example.com)"
SEC_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _get(client: httpx.Client, url: str) -> dict[str, Any]:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def _recent_filings(submissions: dict[str, Any]) -> dict[str, Any]:
    """submissions 的 filingDate 列表位于顶层 `filings.recent` 子对象中。"""
    filings = submissions.get("filings", {})
    return filings.get("recent", {})


def pick_as_of(submissions: dict[str, Any], prefer_form: str = "10-K") -> str:
    """从真实 submissions 中挑一个已存在的历史 filingDate（10-K/10-Q 均可）。"""
    recent = _recent_filings(submissions)
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    for form, filed in zip(forms, dates):
        if form == prefer_form and filed:
            return str(filed)
    # 退而求其次：任意真实 filingDate（仍为历史存在值）
    for form, filed in zip(forms, dates):
        if form in ("10-K", "10-Q") and filed:
            return str(filed)
    raise ValueError("submissions 响应中没有找到 10-K/10-Q filingDate")


def build_payload(
    ticker: str,
    cik: str,
    as_of: str | None,
) -> tuple[dict[str, Any], str]:
    """拉取真实 SEC 数据并返回 (payload, 使用的 as_of_date)。"""
    base = f"https://data.sec.gov/submissions/CIK{cik}.json"
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    tickers_url = "https://www.sec.gov/files/company_tickers.json"

    with httpx.Client(headers={"User-Agent": SEC_UA}, timeout=SEC_TIMEOUT) as client:
        submissions = _get(client, base)
        company_facts = _get(client, facts_url)
        tickers = _get(client, tickers_url)

    resolved = as_of or pick_as_of(submissions)

    # 校验显式 as_of 必须存在于 submissions（用户要求：使用真实历史截止日）
    recent = _recent_filings(submissions)
    existing = set(recent.get("filingDate", []))
    if as_of is not None and as_of not in existing:
        raise ValueError(
            f"提供的 as_of={as_of} 不在真实 submissions 的 filingDate 列表中；"
            f"请选用 {sorted(existing)[-5:]} 等已存在的日期"
        )

    cik_digits = cik.strip().lstrip("0") or "0"
    payload = {
        "ticker": ticker,
        "cik": cik,
        "cik_digits": cik_digits,
        "as_of_date": resolved,
        "tickers": tickers,
        "submissions": submissions,
        "company_facts": company_facts,
    }
    return payload, resolved


def main() -> None:
    parser = argparse.ArgumentParser(description="录制一家公司的 SEC 契约 fixture")
    parser.add_argument("--ticker", required=True, help="公司 ticker，如 AAPL")
    parser.add_argument("--cik", required=True, help="10 位 CIK，如 0000320193")
    parser.add_argument("--as-of", default=None, help="可选：必须存在于 submissions 的 filingDate")
    parser.add_argument("--out", required=True, help="输出 fixture JSON 路径")
    args = parser.parse_args()

    # 校验 CIK 为 10 位
    if len(args.cik) != 10 or not args.cik.isdigit():
        raise SystemExit(f"CIK 必须为 10 位数字，收到: {args.cik!r}")

    payload, resolved = build_payload(args.ticker, args.cik, args.as_of)
    sanitized = sanitize_response(payload)
    raw_content = json.dumps(sanitized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    meta = build_meta(
        source_url=f"https://data.sec.gov/submissions/CIK{args.cik}.json",
        schema_version="sec_fixture_v1",
        content=raw_content.decode("utf-8"),
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"meta": meta.model_dump(mode="json"), "payload": sanitized},
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"recorded: {out} (as_of={resolved}, checksum={meta.content_checksum[:16]}...)")


if __name__ == "__main__":
    main()
