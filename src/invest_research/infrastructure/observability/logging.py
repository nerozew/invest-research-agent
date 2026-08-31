"""结构化日志与敏感字段脱敏（P05-05 + P06-09C）。

P05-05 提供：
- ``sanitize_value`` / ``mask_secrets``：递归脱敏 Authorization/Cookie/API Key
  等敏感字段（供 fixture 回放与 structlog 处理器使用）；
- ``setup_structlog``：配置 structlog（JSON 或控制台）＋脱敏处理器。

P06-09C 提供：
- ``structured_extra``：把 Job 级低基数/非敏感字段收敛为 ``logging`` 的
  ``extra=`` dict；值为 None 的键被剔除，避免 ``key=None`` 噪音；
- ``span_id_from_context``：读取当前 OTel span 的 span_id（十六进制），与
  ``tracing.trace_id_from_context`` 对称，供日志与 Jaeger 链路关联。

``extra`` 的字段都是低基数/非敏感；不得放入 job 内容、提示词、API key 或
任何个人/机密数据。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

import structlog

__all__ = [
    "sanitize_value",
    "mask_secrets",
    "setup_structlog",
    "structured_extra",
    "span_id_from_context",
]

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


def span_id_from_context() -> str | None:
    """返回当前 span context 的 span_id（十六进制），无则 None。"""
    from opentelemetry import trace

    current_span = trace.get_current_span()
    ctx = current_span.get_span_context()
    if ctx.span_id == 0:
        return None
    # span_id 是 64 位，按 16 位十六进制对齐（与 OTel 控制台导出格式一致）
    return format(ctx.span_id, "016x")


def structured_extra(
    *,
    job_id: str | None = None,
    stage: str | None = None,
    error_code: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> dict[str, str]:
    """构建结构化日志 extra；值为 None 的键不进入返回 dict。

    - job_id / stage / error_code / trace_id / span_id 均为低基数/非敏感字段；
    - 只返回非 None 键，日志处理器不会看到 ``key=None`` 的占位噪音。
    """
    extra: dict[str, str] = {}
    if job_id is not None:
        extra["job_id"] = str(job_id)
    if stage is not None:
        extra["stage"] = stage
    if error_code is not None:
        extra["error_code"] = error_code
    if trace_id is not None:
        extra["trace_id"] = trace_id
    if span_id is not None:
        extra["span_id"] = span_id
    return extra
