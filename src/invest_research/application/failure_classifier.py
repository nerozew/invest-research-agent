"""P06-09 失败分类器：异常 → 稳定 error_code / error_message / failure_stage。

职责：
- 在 Worker 应用边界把 Flow/解析抛出的异常统一映射为稳定的错误三元组；
- schema 校验失败 / 外部网络失败 / 超时 / 质量门禁失败使用不同 error_code：
  - SCHEMA_INVALID（pack 解析失败，PackParseError）
  - RATE_LIMITED / NETWORK_TRANSIENT / UPSTREAM_5XX / TIMEOUT（外部调用）
  - QUALITY_GATE_FAILED（质量门禁拒绝）
  - INTERNAL_BUG（其它未分类异常，兜底不隐藏）
- 脱敏：错误消息截断且不含密钥/内部路径/完整 prompt；
- failure_stage 记录失败发生的工作流阶段（如 04_analysis / 05_writer）。

依赖方向：本层只依赖标准库 + domain（错误码）；纯函数可独立离线测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from invest_research.domain.errors import ErrorCode

# 错误消息最大长度（与 SqlProgressSink 的 _MAX_ERROR_MESSAGE 对齐，统一脱敏边界）
_MAX_ERROR_MESSAGE = 500

# 内部路径泄露防护：把绝对路径/密钥占位等缩略为 <redacted>
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"), r"\1=<redacted>"),
    (re.compile(r"(?i)(sk-[A-Za-z0-9_-]{6,})"), "<redacted>"),
    (re.compile(r"[A-Za-z]:\\\\?[^\s'\"]+"), "<path>"),
    (re.compile(r"/[A-Za-z0-9_\-./]{4,}/[A-Za-z0-9_\-./]{4,}"), "<path>"),
    (re.compile(r"(?i)(bearer|authorization)\s+\S+"), r"\1 <redacted>"),
)


@dataclass(frozen=True)
class FailureInfo:
    """一次失败的稳定分类结果（供 Worker 保存到 research_jobs）。"""

    error_code: str
    error_message: str
    failure_stage: str | None


def sanitize_message(message: str) -> str:
    """脱敏错误消息：截断 + 隐藏密钥/token/路径，返回可安全落库文本。"""
    text = (message or "").strip()
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:_MAX_ERROR_MESSAGE]


def classify_failure(exc: Exception, *, stage: str | None = None) -> FailureInfo:
    """按异常类型分类为稳定错误三元组。

    分类优先级（P06-09 稳定 error_code）：
    1. PackParseError → SCHEMA_INVALID（schema 失败，非重试外仍可 guardrail 重试）；
    2. 超时异常（TimeoutError / 消息含 timeout / timed out）→ TIMEOUT；
    3. 429 / rate limit → RATE_LIMITED；
    4. 连接错误（ConnectionError）→ NETWORK_TRANSIENT；
    5. 5xx / 服务端 → UPSTREAM_5XX；
    6. 认证 / 401 / 403 → AUTH_ERROR；
    7. 输入校验 / ValueError 且含"不能为空"/"必须" → INPUT_INVALID；
    8. 其它 → INTERNAL_BUG（兜底，绝不隐藏失败）。
    """
    # PackParseError / LiveFlowExecutionError 等通过鸭子类型识别（error_code 属性）。
    # P06-11-fix：稳定边界错误码（NOT_A_PACK / SCHEMA_INVALID / ITERATION_LIMIT 等）
    # 由抛错方给出，分类器直接透传，不落到笼统 INTERNAL_BUG。
    attached_code = getattr(exc, "error_code", None)
    if attached_code is not None:
        try:
            known = ErrorCode(attached_code)
        except ValueError:
            known = None
        if known is not None:
            return FailureInfo(
                error_code=known.value,
                error_message=sanitize_message(str(exc)),
                failure_stage=stage,
            )

    text = f"{type(exc).__name__}: {exc}".lower()

    # P06-11B：供应商拒绝远程结构化输出（response_format/JSON Schema 不受支持）。
    # 必须优先于迭代耗尽判断——请求阶段 400 是更接近失败根因的分类；
    # 即使同一次执行同时出现迭代耗尽，也保留 response_format 根因。
    if (
        "response_format" in text
        or "response format" in text
        or "json_schema" in text
        or "this response_format type is unavailable" in text
        or "structured output" in text and "not support" in text
    ):
        return FailureInfo(
            error_code=ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )

    # P06-11-fix：CrewAI 迭代预算耗尽（force_final_answer 提示后仍拿不到最终答案）。
    if "force_final_answer" in text or "maximum iterations reached" in text:
        return FailureInfo(
            error_code=ErrorCode.ITERATION_LIMIT.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )

    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return FailureInfo(
            error_code=ErrorCode.TIMEOUT.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )
    if isinstance(exc, ConnectionError) or "connection" in text:
        return FailureInfo(
            error_code=ErrorCode.NETWORK_TRANSIENT.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )
    if "429" in text or "rate limit" in text or "too many requests" in text:
        if "429" in text or "rate" in text:
            return FailureInfo(
                error_code=ErrorCode.RATE_LIMITED.value,
                error_message=sanitize_message(str(exc)),
                failure_stage=stage,
            )
    if "5" in text and ("server error" in text or "500" in text or "502" in text or "503" in text):
        return FailureInfo(
            error_code=ErrorCode.UPSTREAM_5XX.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )
    if "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return FailureInfo(
            error_code=ErrorCode.AUTH_ERROR.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )
    if isinstance(exc, ValueError):
        return FailureInfo(
            error_code=ErrorCode.INPUT_INVALID.value,
            error_message=sanitize_message(str(exc)),
            failure_stage=stage,
        )

    return FailureInfo(
        error_code=ErrorCode.INTERNAL_BUG.value,
        error_message=sanitize_message(str(exc)),
        failure_stage=stage,
    )
