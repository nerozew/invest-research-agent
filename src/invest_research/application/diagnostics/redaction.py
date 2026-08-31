"""P06-11K：诊断 Payload 递归脱敏器。

安全规则（任务文档“五、Payload 安全规则”）：
- 递归脱敏字段或模式：``api_key`` / ``authorization`` / ``bearer`` / ``token`` /
  ``password`` / ``secret`` / ``cookie`` / ``session`` / ``sk-*`` 以及密钥值；
- **永远不保存**：``reasoning_content``、Chain-of-Thought（``thinking`` /
  ``chain_of_thought`` / ``reasoning`` 内容）、Authorization header、Cookie、
  API Key、完整二进制内容、完整数据库连接串；
- URL query 中的密钥必须删除（保留其余 query）；
- 工具参数允许保存脱敏后的结构。

脱敏结果：
- ``data``：脱敏后的可序列化副本（原始输入不被修改）；
- ``redacted_fields``：被脱敏的字段路径（如 ``headers.authorization``）；
- ``dropped_fields``：被永久丢弃的字段路径（reasoning/CoT 等）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SENSITIVE_KEYS",
    "FORBIDDEN_KEYS",
    "MASK",
    "RedactionResult",
    "RedactionContext",
    "RecursiveRedactor",
    "redact_payload",
    "scrub_url_query_secrets",
]

MASK = "***"

# 递归脱敏的关键字（大小写不敏感；同时匹配连字符/下划线变体）。
# 与 logging.SENSITIVE_KEYS 一致并扩展 session/bearer/sk 前缀处理。
SENSITIVE_KEYS = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "set-cookie",
        "set_cookie",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "session",
        "idempotency_key",
        "private_key",
        "x-api-key",
    }
)

# 永远不保存的键（Chain-of-Thought / 推理内容）。命中即整个字段丢弃。
FORBIDDEN_KEYS = frozenset(
    {
        "reasoning_content",
        "chain_of_thought",
        "chainofthought",
        "thinking",
        "cot",
    }
)

# URL query 中的密钥参数名（删除键与值，保留其余参数）。
_URL_SECRET_QUERY_KEYS = frozenset(
    {"api_key", "apikey", "key", "token", "access_token", "auth", "signature", "sig"}
)

_SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
_AUTH_PATTERN = re.compile(r"(?i)\b(bearer|basic|token)\s+[a-z0-9._~+/=-]+")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)((?:api[_-]?)?key|authorization|password|token|secret)"
    r"(\s*[=:]\s*)([^\s,;'\"]+)"
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower().replace("-", "_")
    return lowered in SENSITIVE_KEYS or lowered.replace("_", "-") in SENSITIVE_KEYS


# FORBIDDEN_KEYS 的归一化集合（去掉 _/- 后的等价键集合），
# 与 _is_forbidden_key 的归一化比较口径一致，避免 "reasoningcontent" 匹配不到
# "reasoning_content"。
_FORBIDDEN_KEYS_NORMALIZED: frozenset[str] = frozenset(
    key.replace("_", "").replace("-", "") for key in FORBIDDEN_KEYS
)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.strip().lower().replace("_", "").replace("-", "")
    return lowered in _FORBIDDEN_KEYS_NORMALIZED


def _is_sensitive_string(value: str) -> bool:
    """判断字符串内容是否包含需要脱敏的模式。

    - ``sk-*`` 前缀（至少 8 位）；
    - Bearer/Basic/Token 认证值；
    - ``key=value`` / ``key: value`` 形式的密钥对。
    """
    if _SK_PATTERN.search(value):
        return True
    if _AUTH_PATTERN.search(value):
        return True
    return bool(_KEY_VALUE_PATTERN.search(value))


def _mask_string(value: str) -> str:
    """对字符串做脱敏替换（sk-*、Bearer、key=value）。"""
    value = _SK_PATTERN.sub("sk-" + MASK, value)
    value = _AUTH_PATTERN.sub(lambda m: f"{m.group(1)} {MASK}", value)
    value = _KEY_VALUE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", value)
    return value


def scrub_url_query_secrets(url: str) -> str:
    """删除 URL query 中的密钥参数（保留其余 query）。

    例如 ``https://host/path?a=1&api_key=sk-abc&token=x&b=2`` →
    ``https://host/path?a=1&b=2``（密钥键与值一并删除）。
    没有 query 或没有密钥参数时原样返回。
    """
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    if not query:
        return url
    kept: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        name = pair.split("=", 1)[0].strip().lower()
        if name in _URL_SECRET_QUERY_KEYS:
            continue
        kept.append(pair)
    if not kept:
        return base
    return f"{base}?{'&'.join(kept)}"


@dataclass(frozen=True)
class RedactionContext:
    """脱敏上下文：携带已知的真实密钥值（K-3 由 Settings 的 SecretStr 构建）。

    ``known_secrets``：字符串集合。命中集合的字符串按“完整值替换”脱敏
    （避免长度/前缀推测）；K-1 只提供机制，K-3 接线时传入真实 SecretStr 值。
    """

    known_secrets: frozenset[str] = frozenset()


@dataclass
class RedactionResult:
    """脱敏结果：脱敏后副本 + 被脱敏字段路径 + 被丢弃字段路径。"""

    data: Any
    redacted_fields: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)


class RecursiveRedactor:
    """递归脱敏器（Job 内可复用；线程安全——不持有共享可变状态）。

    规则：
    1. 命中 ``FORBIDDEN_KEYS`` 的字段整体丢弃（reasoning_content/CoT）；
    2. 命中 ``SENSITIVE_KEYS`` 的字段值替换为 ``MASK``；
    3. 字符串值命中 ``sk-*`` / Bearer / key=value / 已知密钥 → 模式替换；
    4. 嵌套 dict/list 递归处理；
    5. URL 字符串中 query 密钥参数被删除。
    """

    def __init__(self, context: RedactionContext | None = None) -> None:
        self._context = context or RedactionContext()

    def redact(self, data: Any, path: str = "") -> RedactionResult:
        result = RedactionResult(data=None)
        result.data = self._redact_into(data, path, result)
        return result

    def _redact_into(self, data: Any, path: str, result: RedactionResult) -> Any:
        if isinstance(data, dict):
            out: dict[str, Any] = {}
            for key, value in data.items():
                key_path = f"{path}.{key}" if path else str(key)
                if _is_forbidden_key(str(key)):
                    result.dropped_fields.append(key_path)
                    continue
                if _is_sensitive_key(str(key)):
                    out[str(key)] = MASK
                    result.redacted_fields.append(key_path)
                    continue
                out[str(key)] = self._redact_into(value, key_path, result)
            return out
        if isinstance(data, list):
            return [
                self._redact_into(item, f"{path}[{i}]", result)
                for i, item in enumerate(data)
            ]
        if isinstance(data, str):
            return self._redact_string(data, path, result)
        return data

    def _redact_string(self, value: str, path: str, result: RedactionResult) -> str:
        # URL 优先：query 中的密钥参数必须删除（保留其余 query 与 path），
        # 而不是被 _mask_string 原地改写（会保留 api_key=***）。
        if value.startswith(("http://", "https://")):
            scrubbed = scrub_url_query_secrets(value)
            if scrubbed != value:
                result.redacted_fields.append(path)
            return scrubbed
        if value in self._context.known_secrets:
            result.redacted_fields.append(path)
            return MASK
        if _is_sensitive_string(value):
            result.redacted_fields.append(path)
            return _mask_string(value)
        return value


def redact_payload(
    data: Any, context: RedactionContext | None = None
) -> RedactionResult:
    """便捷函数：构造 RecursiveRedactor 并执行脱敏。"""
    return RecursiveRedactor(context).redact(data)
