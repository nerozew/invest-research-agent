"""P05-12 SEC fixture 脱敏与离线回放（record/replay）。

授权边界（对齐用户指令）：不执行新的真实 SEC 网络录制；基于仓库已有
合法 fixture（tests/fixtures/sec_submissions_msft.json、companyfacts_msft.json）
完成离线部分。

提供：
- ``FixtureMeta``：来源 URL、录制日期、schema 版本、内容 checksum；
- ``sanitize_response``：递归移除 Authorization/Cookie/API Key 等敏感字段与值；
- ``build_meta``：从响应内容生成 FixtureMeta（含 sha256 checksum）；
- ``replay``：离线回放入口（纯本地文件读取，不联网）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from invest_research.infrastructure.observability.logging import mask_secrets

__all__ = ["FixtureMeta", "sanitize_response", "build_meta", "replay"]

# 响应中视为敏感、需整体移除的键名（大小写不敏感；先移除完整值再走脱敏兜底）
SENSITIVE_RESPONSE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "api-key",
        "x-api-key",
        "apikey",
        "api_key",
        "password",
        "client-secret",
    }
)


class FixtureMeta(BaseModel):
    """fixture 元数据：来源 URL、录制日期、schema 版本、内容 checksum。"""

    model_config = ConfigDict(frozen=True)

    source_url: str = Field(min_length=1)
    recorded_on: date
    schema_version: str = Field(min_length=1)
    content_checksum: str = Field(min_length=1)


def _is_sensitive_key(key: str) -> bool:
    k = key.lower().replace("_", "-")
    return k in SENSITIVE_RESPONSE_KEYS or k.replace("-", "_") in SENSITIVE_RESPONSE_KEYS


def _sanitize_str(value: str) -> str:
    if not value:
        return value
    masked = mask_secrets(event=value)["event"]
    return str(masked)


def sanitize_response(data: Any) -> Any:
    """递归脱敏响应：敏感键整体替换为 ***；普通字符串 bearer/token 值兜底打码。"""
    if isinstance(data, dict):
        return {
            key: ("***" if _is_sensitive_key(key) else sanitize_response(val))
            for key, val in data.items()
        }
    if isinstance(data, list):
        return [sanitize_response(item) for item in data]
    if isinstance(data, str):
        return _sanitize_str(data)
    return data


def build_meta(*, source_url: str, schema_version: str, content: str) -> FixtureMeta:
    """从响应内容生成 FixtureMeta（recorded_on 为今天，checksum = sha256）。"""
    return FixtureMeta(
        source_url=source_url,
        recorded_on=date.today(),
        schema_version=schema_version,
        content_checksum=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def replay(fixture_path: Path) -> Any:
    """离线回放：读取本地 fixture JSON，返回解析后的对象。

    纯本地文件读取，绝不发起网络请求（CI 普通测试可安全使用）。
    """
    raw = fixture_path.read_text(encoding="utf-8")
    return json.loads(raw)
