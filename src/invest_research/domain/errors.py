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
    TIMEOUT = "TIMEOUT"
    DOCUMENT_UNSUPPORTED = "DOCUMENT_UNSUPPORTED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    # P06-11-fix：Agent 输出是工具调用过程/参数等非最终 Pack 结构（稳定分类，不归 INTERNAL_BUG）。
    NOT_A_PACK = "NOT_A_PACK"
    # P06-11-fix：Agent 迭代预算耗尽（CrewAI 会把最后一次工具输出当最终答案）。
    ITERATION_LIMIT = "ITERATION_LIMIT"
    DATA_AMBIGUOUS = "DATA_AMBIGUOUS"
    QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
    # P06-11B：供应商拒绝远程结构化输出（response_format/JSON Schema 不受支持）。
    # 例如 DeepSeek 普通 Chat Completion 返回 HTTP 400 "This response_format type
    # is unavailable now"。归类为不可重试：重试同样会失败，等待配置/适配修复。
    STRUCTURED_OUTPUT_UNSUPPORTED = "STRUCTURED_OUTPUT_UNSUPPORTED"
    # P06-11C：LLM 选中的 fact_ref 在预取事实集中找不到唯一匹配（非网络错误，禁止猜测）。
    FACT_REFERENCE_UNRESOLVED = "FACT_REFERENCE_UNRESOLVED"
    # P06-11C：fact_ref 在预取事实集中存在多重匹配（歧义，禁止猜测取其一）。
    FACT_REFERENCE_AMBIGUOUS = "FACT_REFERENCE_AMBIGUOUS"
    # P06-11C：LLM 试图改写事实来源/数值（如草稿携带事实内容与原始事实不一致）。
    FACT_PROVENANCE_MISMATCH = "FACT_PROVENANCE_MISMATCH"
    # P06-11D：Writer 只输出 Markdown 正文；空文本/JSON 包装/过短说明/缺必要章节等
    # 明显不是合法报告正文的输出（确定性拒绝，禁止把半成品当最终草稿）。
    REPORT_INVALID = "REPORT_INVALID"
    # P06-11D：Writer 输出明显截断（finish_reason=length 或正文末尾呈现截断痕迹），
    # 稳定失败（重试同样可能截断，等待配置/提示词修复）。
    REPORT_TRUNCATED = "REPORT_TRUNCATED"
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
        ErrorCode.TIMEOUT,  # P06-09：超时是显式可重试的上游失败（有上限重试）
        ErrorCode.SCHEMA_INVALID,  # 限定次数修复（guardrail 反馈后重试）
    }
)

_NON_RETRYABLE_ERRORS: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.INPUT_INVALID,
        ErrorCode.COMPANY_AMBIGUOUS,
        ErrorCode.AUTH_ERROR,
        ErrorCode.DOCUMENT_UNSUPPORTED,
        # 工具过程/参数被当最终 Pack、迭代预算耗尽：不是可重试的上游干扰，
        # 而是 Agent 未能产出最终答案（重试同样会失败，等待修复配置/提示词）。
        ErrorCode.NOT_A_PACK,
        ErrorCode.ITERATION_LIMIT,
        ErrorCode.DATA_AMBIGUOUS,
        ErrorCode.QUALITY_GATE_FAILED,
        # P06-11B：供应商不支持结构化输出 response_format → 立即终态失败（不可重试）。
        ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED,
        # P06-11C：fact_ref 未解决/歧义/来源不一致是确定性本地问题，重试同样失败。
        ErrorCode.FACT_REFERENCE_UNRESOLVED,
        ErrorCode.FACT_REFERENCE_AMBIGUOUS,
        ErrorCode.FACT_PROVENANCE_MISMATCH,
        # P06-11D：Writer 正文不合法/截断是确定性本地问题，重试同样失败。
        ErrorCode.REPORT_INVALID,
        ErrorCode.REPORT_TRUNCATED,
        ErrorCode.INTERNAL_BUG,
    }
)


def is_retryable(error_code: ErrorCode | str) -> bool:
    """该错误码是否允许自动重试。"""
    return ErrorCode(error_code) in _RETRYABLE_ERRORS


def is_terminal_failure(error_code: ErrorCode | str) -> bool:
    """该错误码是否属于不可恢复的终态失败（不自动重试）。"""
    return ErrorCode(error_code) in _NON_RETRYABLE_ERRORS
