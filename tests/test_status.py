"""P01-01 状态枚举、状态转换与错误码分类测试。"""

import pytest

from invest_research.domain.errors import ErrorCode, is_retryable, is_terminal_failure
from invest_research.domain.status import (
    JobStatus,
    StepStatus,
    can_transition_job,
    can_transition_step,
)

# ---------------------------------------------------------------------------
# 枚举完整性
# ---------------------------------------------------------------------------


def test_job_status_enum_values() -> None:
    """JobStatus 枚举必须与数据库设计一致（6 个合法值）。"""
    assert {s.value for s in JobStatus} == {
        "pending",
        "running",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
    }


def test_step_status_enum_values() -> None:
    """StepStatus 枚举必须与数据库设计一致（6 个合法值）。"""
    assert {s.value for s in StepStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed_retryable",
        "failed_terminal",
        "skipped",
    }


# ---------------------------------------------------------------------------
# Job 状态转换（docs/04-WORKFLOW-RELIABILITY.md §3）
# ---------------------------------------------------------------------------


def test_job_legal_transitions() -> None:
    """合法迁移必须被接受。"""
    assert can_transition_job(JobStatus.PENDING, JobStatus.RUNNING)
    assert can_transition_job(JobStatus.PENDING, JobStatus.CANCELLED)
    assert can_transition_job(JobStatus.RUNNING, JobStatus.SUCCEEDED)
    assert can_transition_job(JobStatus.RUNNING, JobStatus.PARTIAL)
    assert can_transition_job(JobStatus.RUNNING, JobStatus.FAILED)
    assert can_transition_job(JobStatus.RUNNING, JobStatus.CANCELLED)


def test_job_illegal_transitions() -> None:
    """非法迁移必须被拒绝。"""
    # 从终态不能继续迁移
    for terminal in (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED):
        assert not can_transition_job(terminal, JobStatus.RUNNING)
    # 不能跳过 pending 直接 succeeded
    assert not can_transition_job(JobStatus.PENDING, JobStatus.SUCCEEDED)
    # 不能 pending 直接 partial
    assert not can_transition_job(JobStatus.PENDING, JobStatus.PARTIAL)


def test_job_is_terminal() -> None:
    """只有 4 个终态。"""
    for status in (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED):
        assert status.is_terminal
    assert not JobStatus.PENDING.is_terminal
    assert not JobStatus.RUNNING.is_terminal


# ---------------------------------------------------------------------------
# Step 状态转换（04 §3 状态机）
# ---------------------------------------------------------------------------


def test_step_legal_transitions() -> None:
    """合法 Step 迁移必须被接受。"""
    assert can_transition_step(StepStatus.PENDING, StepStatus.RUNNING)
    assert can_transition_step(StepStatus.PENDING, StepStatus.SKIPPED)
    assert can_transition_step(StepStatus.RUNNING, StepStatus.SUCCEEDED)
    assert can_transition_step(StepStatus.RUNNING, StepStatus.FAILED_RETRYABLE)
    assert can_transition_step(StepStatus.RUNNING, StepStatus.FAILED_TERMINAL)
    assert can_transition_step(StepStatus.RUNNING, StepStatus.SKIPPED)
    assert can_transition_step(StepStatus.FAILED_RETRYABLE, StepStatus.RUNNING)
    assert can_transition_step(StepStatus.FAILED_RETRYABLE, StepStatus.FAILED_TERMINAL)


def test_step_illegal_transitions() -> None:
    """非法 Step 迁移必须被拒绝。"""
    # 终态不可迁移
    for terminal in (StepStatus.SUCCEEDED, StepStatus.FAILED_TERMINAL, StepStatus.SKIPPED):
        assert not can_transition_step(terminal, StepStatus.RUNNING)
    # failed_retryable 不能直接 succeeded / skipped
    assert not can_transition_step(StepStatus.FAILED_RETRYABLE, StepStatus.SUCCEEDED)
    # pending 不能直接 failed
    assert not can_transition_step(StepStatus.PENDING, StepStatus.FAILED_TERMINAL)


def test_step_is_terminal() -> None:
    """Step 终态只有 3 个。"""
    for status in (StepStatus.SUCCEEDED, StepStatus.FAILED_TERMINAL, StepStatus.SKIPPED):
        assert status.is_terminal
    assert not StepStatus.RUNNING.is_terminal
    assert not StepStatus.FAILED_RETRYABLE.is_terminal


# ---------------------------------------------------------------------------
# 错误码分类（04 §4）
# ---------------------------------------------------------------------------


def test_error_code_count() -> None:
    """必须包含 P06-11G 后用于稳定归因的全部 23 个错误码。"""
    assert len(ErrorCode) == 23


def test_retryable_error_codes() -> None:
    """可重试错误码：RATE_LIMITED/NETWORK_TRANSIENT/UPSTREAM_5XX/TIMEOUT/SCHEMA_INVALID。"""
    retryable = {
        ErrorCode.RATE_LIMITED,
        ErrorCode.NETWORK_TRANSIENT,
        ErrorCode.UPSTREAM_5XX,
        ErrorCode.TIMEOUT,
        ErrorCode.SCHEMA_INVALID,
    }
    for code in retryable:
        assert is_retryable(code)
        assert not is_terminal_failure(code)


def test_non_retryable_error_codes() -> None:
    """不可重试错误码（终态失败）。"""
    non_retryable = {
        ErrorCode.INPUT_INVALID,
        ErrorCode.COMPANY_AMBIGUOUS,
        ErrorCode.AUTH_ERROR,
        ErrorCode.DOCUMENT_UNSUPPORTED,
        ErrorCode.DATA_AMBIGUOUS,
        ErrorCode.QUALITY_GATE_FAILED,
        ErrorCode.INTERNAL_BUG,
    }
    for code in non_retryable:
        assert not is_retryable(code)
        assert is_terminal_failure(code)


def test_is_retryable_accepts_string() -> None:
    """is_retryable 必须同时接受枚举与字符串。"""
    assert is_retryable("RATE_LIMITED")
    assert not is_retryable("AUTH_ERROR")


def test_unknown_error_code_raises() -> None:
    """未知错误码必须抛出 ValueError，而不是静默返回 False。"""
    with pytest.raises(ValueError):
        is_retryable("NOT_A_REAL_CODE")
