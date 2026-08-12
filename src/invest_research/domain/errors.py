"""错误码定义与错误分类规则（P01-01）。

依据 `docs/04-WORKFLOW-RELIABILITY.md` §4「错误分类与策略」：
定义了 11 个错误码、各自的示例、是否可重试、最终行为。

本模块为纯 Python，禁止导入任何外部库，保证可独立测试。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """统一错误码（workflow_steps.error_json / research_jobs.error_code）。"""

    INPUT_INVALID = "INPUT_INVALID"
    COMPANY_AMBIGUOUS = "COMPANY_AMBIGUOUS"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_TRANSIENT = "NETWORK_TRANSIENT"
    UPSTREAM_5XX = "UPSTREAM_5XX"
    DOCUMENT_UNSUPPORTED = "DOCUMENT_UNSUPPORTED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    DATA_AMBIGUOUS = "DATA_AMBIGUOUS"
    QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
    INTERNAL_BUG = "INTERNAL_BUG"


# ---------------------------------------------------------------------------
# 错误分类规则（docs/04-WORKFLOW-RELIABILITY.md §4 表格）
# ---------------------------------------------------------------------------
# _RETRYABLE：达到重试上限后才进入最终失败；
# _NON_RETRYABLE：立即为终态失败，不重试。
# 注意：DOCUMENT_UNSUPPORTED 的"重试"仅指切换到备用解析器，
#       不视为同一动作的自动重试，因此归为非自动重试。

_RETRYABLE_ERRORS: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.NETWORK_TRANSIENT,
        ErrorCode.UPSTREAM_5XX,
        ErrorCode.SCHEMA_INVALID,  # 限定次数修复（guardrail 反馈后重试）
    }
)

_NON_RETRYABLE_ERRORS: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.INPUT_INVALID,
        ErrorCode.COMPANY_AMBIGUOUS,
        ErrorCode.AUTH_ERROR,
        ErrorCode.DOCUMENT_UNSUPPORTED,
        ErrorCode.DATA_AMBIGUOUS,
        ErrorCode.QUALITY_GATE_FAILED,
        ErrorCode.INTERNAL_BUG,
    }
)


def is_retryable(error_code: ErrorCode | str) -> bool:
    """该错误码是否允许自动重试。"""
    return ErrorCode(error_code) in _RETRYABLE_ERRORS


def is_terminal_failure(error_code: ErrorCode | str) -> bool:
    """该错误码是否属于不可恢复的终态失败（不自动重试）。"""
    return ErrorCode(error_code) in _NON_RETRYABLE_ERRORS
