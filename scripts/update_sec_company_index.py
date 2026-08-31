"""Refresh the bundled SEC company-ticker snapshot from the official SEC endpoint.

This maintenance command is intentionally separate from request execution. Production
company resolution reads only the bundled snapshot and never performs a network request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from invest_research.tools.company_resolver import MAIN_ENTITY_OVERRIDES

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT / "src" / "invest_research" / "resources" / "sec_company_tickers_snapshot.json"
)
SOURCE_URL = "https://www.sec.gov/files/company_tickers.json"
SCHEMA_VERSION = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="更新本地 SEC 公司 ticker 确定性索引")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", ""),
        help="SEC 要求的可识别 User-Agent；也可设置 SEC_USER_AGENT",
    )
    return parser


def _download(url: str, user_agent: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed HTTPS default
        payload = response.read()
    if not isinstance(payload, bytes):
        raise TypeError("SEC company ticker 响应必须是 bytes")
    return payload


def _normalize(raw: bytes, *, source_url: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("SEC company_tickers.json 顶层必须是 object")

    companies: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in payload.values():
        if not isinstance(value, dict):
            raise ValueError("SEC company_tickers.json 含非 object 条目")
        cik = str(value.get("cik_str", "")).strip().zfill(10)
        ticker = str(value.get("ticker", "")).strip().upper()
        legal_name = str(value.get("title", "")).strip()
        if len(cik) != 10 or not cik.isdigit() or not ticker or not legal_name:
            raise ValueError(f"SEC company ticker 条目不完整: {value!r}")
        identity_key = (cik, ticker)
        if identity_key in seen:
            continue
        seen.add(identity_key)
        companies.append({"cik": cik, "ticker": ticker, "legal_name": legal_name})

    if len(companies) < 5_000:
        raise ValueError(f"SEC 公司索引异常偏小，仅 {len(companies)} 条")
    # 主实体覆盖：SEC 数据错绑/缺失时补入正确主实体（与运行时 load_sec_company_index 一致）
    existing_ciks = {item["cik"] for item in companies}
    for ticker, override in MAIN_ENTITY_OVERRIDES.items():
        if override["cik"] not in existing_ciks:
            companies.append(
                {"cik": override["cik"], "ticker": ticker, "legal_name": override["legal_name"]}
            )
            existing_ciks.add(override["cik"])
    companies.sort(key=lambda item: (item["ticker"], item["cik"], item["legal_name"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "source_url": source_url,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "company_count": len(companies),
        "companies": companies,
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(serialized)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    args = _parser().parse_args()
    if not args.user_agent.strip():
        print("拒绝下载：--user-agent/SEC_USER_AGENT 不能为空")
        return 2
    raw = _download(args.source_url, args.user_agent)
    payload = _normalize(raw, source_url=args.source_url)
    _atomic_write(args.output.resolve(), payload)
    print(
        f"已写入 {args.output.resolve()}：{payload['company_count']} 家公司，"
        f"sha256={payload['source_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
