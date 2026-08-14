"""结构化日志与敏感字段脱敏（P05-05）。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

import structlog

__all__ = ["sanitize_value", "mask_secrets", "setup_structlog"]

SENSITIVE_KEYS = frozenset(
    {
        "key",
        "api_key",
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "token",
        "secret",
        "access_token",
        "refresh_token",
        "client_secret",
        "idempotency_key",
    }
)

_AUTH_PATTERN = re.compile(r"(?i)(bearer|basic|token)\s+[a-z0-9._~+/=-]+")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)((?:api[_-]?)?key|authorization|password|token|secret)"
    r"(\s*[=:]\s*)([^\s,;'\"]+)"
)

MASK = "***"


def sanitize_value(value: str) -> str:
    # 先处理 Bearer/Token 等完整认证值，再处理 key=val（避免前者残留）。
    value = _AUTH_PATTERN.sub(lambda m: f"{m.group(1)} {MASK}", value)
    value = _KEY_VALUE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", value)
    return value


def mask_secrets(*, event: str | None = None, **kw: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, val in kw.items():
        if isinstance(val, str):
            k = key.lower()
            is_sensitive = (
                k in SENSITIVE_KEYS
                or k.replace("_", "-") in SENSITIVE_KEYS
                or val.lower().startswith("bearer ")
            )
            if is_sensitive:
                result[key] = MASK
            else:
                result[key] = sanitize_value(val)
        elif isinstance(val, dict):
            result[key] = mask_secrets(**val)
        else:
            result[key] = val
    if event is not None:
        result["event"] = sanitize_value(event) if event else event
    return result


def _mask_processor(_: Any, __: str, event_dict: Mapping[str, Any]) -> dict[str, Any]:
    return mask_secrets(**dict(event_dict))


def setup_structlog(*, json_logs: bool = True) -> None:
    processors: list[Callable[..., Any]] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _mask_processor,
    ]
    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(20),
        cache_logger_on_first_use=True,
    )
