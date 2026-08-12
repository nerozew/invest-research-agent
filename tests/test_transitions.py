"""P01-06 Job/Step 状态转换器测试：合法返回目标、非法抛异常、终态与相同状态被拒。"""

import pytest

from invest_research.domain.status import JobStatus, StepStatus
from invest_research.domain.transitions import (
    InvalidStateTransitionError,
    transition_job,
    transition_step,
)

# ---------------------------------------------------------------------------
# Job 转换
# ---------------------------------------------------------------------------


def test_job_legal_transition_returns_target() -> None:
    """合法 Job 转换返回目标状态。"""
    assert transition_job(JobStatus.PENDING, JobStatus.RUNNING) == JobStatus.RUNNING
    assert transition_job(JobStatus.PENDING, JobStatus.CANCELLED) == JobStatus.CANCELLED
    assert transition_job(JobStatus.RUNNING, JobStatus.SUCCEEDED) == JobStatus.SUCCEEDED
    assert transition_job(JobStatus.RUNNING, JobStatus.PARTIAL) == JobStatus.PARTIAL
    assert transition_job(JobStatus.RUNNING, JobStatus.FAILED) == JobStatus.FAILED


def test_job_illegal_transition_raises() -> None:
    """非法 Job 转换抛 InvalidStateTransitionError。"""
    with pytest.raises(InvalidStateTransitionError):
        transition_job(JobStatus.PENDING, JobStatus.SUCCEEDED)


def test_job_terminal_cannot_transition() -> None:
    """Job 终态不能继续转换。"""
    for terminal in (
        JobStatus.SUCCEEDED,
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    ):
        with pytest.raises(InvalidStateTransitionError):
            transition_job(terminal, JobStatus.RUNNING)


def test_job_same_state_rejected() -> None:
    """Job 相同状态转换被拒绝（状态机不允许原地自转）。"""
    with pytest.raises(InvalidStateTransitionError):
        transition_job(JobStatus.RUNNING, JobStatus.RUNNING)
    with pytest.raises(InvalidStateTransitionError):
        transition_job(JobStatus.PENDING, JobStatus.PENDING)


def test_job_error_records_object_type_and_states() -> None:
    """异常记录对象类型、当前与目标状态。"""
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        transition_job(JobStatus.PENDING, JobStatus.SUCCEEDED)
    assert exc_info.value.object_type == "job"
    assert exc_info.value.current == JobStatus.PENDING
    assert exc_info.value.target == JobStatus.SUCCEEDED
    assert "非法job状态转换: pending -> succeeded" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Step 转换
# ---------------------------------------------------------------------------


def test_step_legal_transition_returns_target() -> None:
    """合法 Step 转换返回目标状态。"""
    assert transition_step(StepStatus.PENDING, StepStatus.RUNNING) == StepStatus.RUNNING
    assert (
        transition_step(StepStatus.RUNNING, StepStatus.FAILED_RETRYABLE)
        == StepStatus.FAILED_RETRYABLE
    )
    assert transition_step(StepStatus.FAILED_RETRYABLE, StepStatus.RUNNING) == StepStatus.RUNNING
    assert (
        transition_step(StepStatus.FAILED_RETRYABLE, StepStatus.FAILED_TERMINAL)
        == StepStatus.FAILED_TERMINAL
    )


def test_step_illegal_transition_raises() -> None:
    """非法 Step 转换抛 InvalidStateTransitionError。"""
    with pytest.raises(InvalidStateTransitionError):
        transition_step(StepStatus.PENDING, StepStatus.FAILED_TERMINAL)
    with pytest.raises(InvalidStateTransitionError):
        # failed_retryable 不能直接 succeeded，需经 running
        transition_step(StepStatus.FAILED_RETRYABLE, StepStatus.SUCCEEDED)


def test_step_terminal_cannot_transition() -> None:
    """Step 终态不能继续转换。"""
    for terminal in (
        StepStatus.SUCCEEDED,
        StepStatus.FAILED_TERMINAL,
        StepStatus.SKIPPED,
    ):
        with pytest.raises(InvalidStateTransitionError):
            transition_step(terminal, StepStatus.RUNNING)


def test_step_same_state_rejected() -> None:
    """Step 相同状态转换被拒绝。"""
    with pytest.raises(InvalidStateTransitionError):
        transition_step(StepStatus.RUNNING, StepStatus.RUNNING)
    with pytest.raises(InvalidStateTransitionError):
        transition_step(StepStatus.PENDING, StepStatus.PENDING)


def test_step_error_records_object_type_and_states() -> None:
    """异常记录对象类型、当前与目标状态。"""
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        transition_step(StepStatus.PENDING, StepStatus.FAILED_TERMINAL)
    assert exc_info.value.object_type == "step"
    assert exc_info.value.current == StepStatus.PENDING
    assert exc_info.value.target == StepStatus.FAILED_TERMINAL
    assert "非法step状态转换: pending -> failed_terminal" in str(exc_info.value)
