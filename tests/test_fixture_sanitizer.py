"""P05-12 SEC fixture 脱敏与离线回放测试。

基于仓库已有合法 fixture（不执行真实 SEC 网络录制）：
- 脱敏器递归移除 Authorization/Cookie/API Key；
- FixtureMeta 记录来源 URL/录制日期/schema 版本/checksum；
- replay 离线回放纯本地读取，绝不联网。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from invest_research.infrastructure.fixture import (
    FixtureMeta,
    build_meta,
    replay,
    sanitize_response,
)

FIXTURE_DIR = Path("tests/fixtures")


def _load(name: str) -> dict:
    path = FIXTURE_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


# ---- 脱敏器 ----


def test_sanitize_removes_authorization_and_cookie() -> None:
    raw = {
        "headers": {"Authorization": "Bearer secret-token-123", "Cookie": "session=abc"},
        "body": {"ok": True},
    }
    clean = sanitize_response(raw)
    assert clean["headers"]["Authorization"] == "***"
    assert clean["headers"]["Cookie"] == "***"
    assert clean["body"] == {"ok": True}


def test_sanitize_removes_api_key_variants() -> None:
    raw = {
        "request": {
            "X-Api-Key": "k-123456",
            "apiKey": "k-abc",
            "api_key": "k-def",
        },
        "data": [1, 2],
    }
    clean = sanitize_response(raw)
    assert clean["request"]["X-Api-Key"] == "***"
    assert clean["request"]["apiKey"] == "***"
    assert clean["request"]["api_key"] == "***"
    assert clean["data"] == [1, 2]


def test_sanitize_masks_bearer_in_plain_text() -> None:
    raw = {"message": "auth with Bearer super-secret-token now"}
    clean = sanitize_response(raw)
    assert "super-secret-token" not in clean["message"]
    assert "Bearer ***" in clean["message"]


def test_sanitize_masks_key_value_in_plain_text() -> None:
    raw = {"url": "https://x.example?api_key=hidden-value-xyz"}
    clean = sanitize_response(raw)
    assert "hidden-value-xyz" not in clean["url"]
    assert clean["url"].endswith("***")


def test_sanitize_preserves_nonsensitive_nested_data() -> None:
    raw = {"filings": {"recent": {"form": ["10-K", "10-Q"], "size": [100, 50]}}}
    clean = sanitize_response(raw)
    assert clean["filings"]["recent"]["form"] == ["10-K", "10-Q"]
    assert clean["filings"]["recent"]["size"] == [100, 50]


# ---- FixtureMeta ----


def test_build_meta_records_source_checksum() -> None:
    content = json.dumps({"cik": "0000789019"})
    meta = build_meta(
        source_url="https://data.sec.gov/submissions/CIK0000789019.json",
        schema_version="sec_submissions_v1",
        content=content,
    )
    assert meta.source_url.startswith("https://data.sec.gov")
    assert meta.schema_version == "sec_submissions_v1"
    assert meta.content_checksum == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_fixture_meta_frozen() -> None:
    meta = FixtureMeta(
        source_url="https://x",
        recorded_on="2025-01-01",
        schema_version="v1",
        content_checksum="abc",
    )
    try:
        meta.schema_version = "v2"  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except Exception:
        pass


# ---- 离线回放（不联网） ----


def test_replay_submissions_fixture_offline() -> None:
    data = replay(FIXTURE_DIR / "sec_submissions_msft.json")
    assert data["cik"] == "0000789019"
    assert data["tickers"] == ["MSFT"]
    assert data["filings"]["recent"]["form"][0] == "10-Q"


def test_replay_companyfacts_fixture_offline() -> None:
    data = replay(FIXTURE_DIR / "companyfacts_msft.json")
    assert data["cik"] == "0000789019"  # 保真事实的固定公司


def test_existing_fixtures_do_not_contain_secrets() -> None:
    """CI 普通测试不访问真实 SEC；已有 fixture 不得含敏感字段。"""
    import re

    secret_patterns = [
        re.compile(r"(?i)authorization"),
        re.compile(r"(?i)set-cookie"),
        re.compile(r"(?i)api[_-]?key"),
        re.compile(r"(?i)x-api-key"),
    ]
    for fixture in FIXTURE_DIR.glob("*.json"):
        text = fixture.read_text(encoding="utf-8")
        for pattern in secret_patterns:
            assert pattern.search(text) is None, f"{fixture.name} contains secret"
