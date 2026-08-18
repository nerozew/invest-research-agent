"""P06-11A：LLM 调用观测器离线测试（不联网）。

覆盖验收：
- 一次真实模型调用只计数一次（不三倍）；
- research/analysis/writer 三角色数据不会重复三倍；
- 成功/失败均记录调用次数与耗时；
- 有真实 usage 时正确记录 input/output/cached_input token；
- 无 usage 时只增加 llm_usage_missing_total（绝不伪造 0）；
- 事件总线 subscribe/emit 真实触发 handler；
- label 不包含高基数或敏感信息。
"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import REGISTRY

from invest_research.agents.llm_factory import LLMConfig
from invest_research.infrastructure.observability.llm_call_observer import (
    LlmCallObserver,
    extract_usage_tokens,
    has_usage,
    role_name_to_role,
)


def _make_config() -> LLMConfig:
    """构造最小 LLMConfig（api_key 为占位 SecretStr，不联网）。"""
    from pydantic import SecretStr

    return LLMConfig(
        provider="openai_compatible",
        vendor="qwen",
        base_url="https://dashscope.example.invalid",
        api_key=SecretStr("test-placeholder-key"),
        model_research="qwen-test",
        model_analysis="qwen-test",
        model_writer="qwen-test",
    )


@pytest.fixture()
def llm_config() -> LLMConfig:
    return _make_config()


def _samples() -> list[tuple[str, dict[str, str], float]]:
    """收集当前 REGISTRY 中 llm_ 指标样本。"""
    result: list[tuple[str, dict[str, str], float]] = []
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name.startswith("llm_"):
                result.append((sample.name, dict(sample.labels), float(sample.value)))
    return result


def _sample_total(name: str, **labels: str) -> float:
    """按 label 子集汇总当前值；测试比较前后增量，不注销全局 collector。"""
    return sum(
        value
        for sample_name, sample_labels, value in _samples()
        if sample_name == name
        and all(sample_labels.get(key) == expected for key, expected in labels.items())
    )


class _FakeAgent:
    """伪造 Agent：LLMEventBase.__init__ 读取 id/role 转 agent_role。"""

    def __init__(self, role: str) -> None:
        self.id = f"agent-{role}"
        self.role = role


def _make_started_event(role: str, model: str) -> Any:
    """构造 LLMCallStartedEvent（与 CrewAI 1.6.1 字段一致）。"""
    from crewai.events.types.llm_events import LLMCallStartedEvent

    return LLMCallStartedEvent(
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        from_task=None,
        from_agent=_FakeAgent(role),
        timestamp=0.0,
    )


def _make_completed_event(
    role: str,
    model: str,
    *,
    usage: dict[str, Any] | None = None,
    duration: float = 1.5,
) -> Any:
    """构造 LLMCallCompletedEvent（CrewAI 1.6.1 的 usage 位于 response）。"""
    from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallType

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "response": {"content": "ok", "usage": usage} if usage is not None else "ok",
        "call_type": LLMCallType.LLM_CALL,
        "from_task": None,
        "from_agent": _FakeAgent(role),
    }
    # 事件没有 duration 字段；参数仅保留为测试语义说明，
    # 真实耗时由 Started→Completed 的 monotonic 时钟计算。
    _ = duration
    return LLMCallCompletedEvent(**kwargs)


def _make_failed_event(role: str) -> Any:
    """构造 LLMCallFailedEvent（CrewAI 1.6.1 无 model/duration 字段）。"""
    from crewai.events.types.llm_events import LLMCallFailedEvent

    return LLMCallFailedEvent(
        error="boom",
        from_task=None,
        from_agent=_FakeAgent(role),
    )


def _drive(observer: LlmCallObserver, *events: Any) -> None:
    """按顺序驱动 handler（模拟事件总线 emit 的同步调用）。"""
    for event in events:
        if type(event).__name__ == "LLMCallStartedEvent":
            observer._on_started(None, event)
        elif type(event).__name__ == "LLMCallCompletedEvent":
            observer._on_completed(None, event)
        elif type(event).__name__ == "LLMCallFailedEvent":
            observer._on_failed(None, event)


# ---------------------------------------------------------------------------
# 角色映射与 usage 提取（纯函数）
# ---------------------------------------------------------------------------


def test_role_name_to_role_maps_readable_names() -> None:
    assert role_name_to_role("Research Analyst") == "research"
    assert role_name_to_role("Financial Analyst") == "analysis"
    assert role_name_to_role("Report Writer") == "writer"
    assert role_name_to_role("信息搜集 Agent") == "research"
    assert role_name_to_role("财报分析 Agent") == "analysis"
    assert role_name_to_role("报告撰写 Agent") == "writer"
    assert role_name_to_role("Unknown Role") is None
    assert role_name_to_role(None) is None
    assert role_name_to_role("") is None


def test_agent_id_mapping_takes_precedence_over_readable_name(
    llm_config: LLMConfig,
) -> None:
    """生产路径用稳定 agent_id 映射，不依赖中英文展示名称。"""
    observer = LlmCallObserver(
        llm_config,
        agent_roles={"agent-自定义角色": "writer"},
    )
    before = _sample_total(
        "llm_requests_total", role="writer", model="qwen-test", status="success"
    )
    _drive(
        observer,
        _make_started_event("自定义角色", "qwen-test"),
        _make_completed_event(
            "自定义角色",
            "qwen-test",
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        ),
    )
    after = _sample_total(
        "llm_requests_total", role="writer", model="qwen-test", status="success"
    )
    assert after - before == 1.0


def test_extract_usage_tokens_dict_and_object() -> None:
    usage_dict = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 20},
    }
    tokens = extract_usage_tokens(usage_dict)
    assert tokens == {"input": 100, "output": 50, "cached_input": 20}
    assert has_usage(usage_dict) is True


def test_has_usage_false_when_empty_or_none() -> None:
    assert has_usage(None) is False
    assert has_usage({}) is False
    assert has_usage({"prompt_tokens": 0, "completion_tokens": 0}) is False


# ---------------------------------------------------------------------------
# 计数正确性（一次调用只计一次、三角色不三倍）
# ---------------------------------------------------------------------------


def test_single_call_counts_once(llm_config: LLMConfig) -> None:
    """一次成功调用 → success=1（不三倍）。"""
    observer = LlmCallObserver(llm_config)
    before = _sample_total(
        "llm_requests_total", role="research", model="qwen-test", status="success"
    )
    _drive(
        observer,
        _make_started_event("Research Analyst", "qwen-test"),
        _make_completed_event(
            "Research Analyst",
            "qwen-test",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ),
    )

    after = _sample_total(
        "llm_requests_total", role="research", model="qwen-test", status="success"
    )
    assert after - before == 1.0


def test_three_roles_not_tripled(llm_config: LLMConfig) -> None:
    """三个角色各一次调用 → 每个角色 success=1（不是 3）。"""
    observer = LlmCallObserver(llm_config)
    before = {
        role: _sample_total(
            "llm_requests_total", role=role, model="qwen-test", status="success"
        )
        for role in ("research", "analysis", "writer")
    }
    for role_name in ("Research Analyst", "Financial Analyst", "Report Writer"):
        _drive(
            observer,
            _make_started_event(role_name, "qwen-test"),
            _make_completed_event(
                role_name,
                "qwen-test",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
        )

    for role in ("research", "analysis", "writer"):
        after = _sample_total(
            "llm_requests_total", role=role, model="qwen-test", status="success"
        )
        assert after - before[role] == 1.0


# ---------------------------------------------------------------------------
# 成功/失败与耗时
# ---------------------------------------------------------------------------


def test_success_records_duration(llm_config: LLMConfig) -> None:
    """成功调用记录耗时（duration observe）。"""
    observer = LlmCallObserver(llm_config)
    before_count = _sample_total(
        "llm_request_duration_seconds_count",
        role="research",
        model="qwen-test",
        status="success",
    )
    _drive(
        observer,
        _make_started_event("Research Analyst", "qwen-test"),
        _make_completed_event(
            "Research Analyst",
            "qwen-test",
            duration=2.5,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        ),
    )

    after_count = _sample_total(
        "llm_request_duration_seconds_count",
        role="research",
        model="qwen-test",
        status="success",
    )
    assert after_count - before_count == 1.0


def test_failure_records_count_and_duration(llm_config: LLMConfig) -> None:
    """失败调用记录 failure 次数与耗时。"""
    observer = LlmCallObserver(llm_config)
    before = _sample_total(
        "llm_requests_total", role="research", model="qwen-test", status="failure"
    )
    before_duration = _sample_total(
        "llm_request_duration_seconds_count",
        role="research",
        model="qwen-test",
        status="failure",
    )
    _drive(
        observer,
        _make_started_event("Research Analyst", "qwen-test"),
        _make_failed_event("Research Analyst"),
    )

    after = _sample_total(
        "llm_requests_total", role="research", model="qwen-test", status="failure"
    )
    assert after - before == 1.0
    after_duration = _sample_total(
        "llm_request_duration_seconds_count",
        role="research",
        model="qwen-test",
        status="failure",
    )
    assert after_duration - before_duration == 1.0


def test_failure_does_not_count_missing(llm_config: LLMConfig) -> None:
    """失败不产生 usage missing（missing=有响应但无 usage）。"""
    observer = LlmCallObserver(llm_config)
    before = _sample_total("llm_usage_missing_total", role="research", model="qwen-test")
    _drive(
        observer,
        _make_started_event("Research Analyst", "qwen-test"),
        _make_failed_event("Research Analyst"),
    )
    after = _sample_total("llm_usage_missing_total", role="research", model="qwen-test")
    assert after == before


# ---------------------------------------------------------------------------
# Token / usage missing
# ---------------------------------------------------------------------------


def test_observer_does_not_double_record_token_metrics(llm_config: LLMConfig) -> None:
    """事件观测器只记次数/耗时；Token 由 Agent TokenProcess 差值统一记录。"""
    observer = LlmCallObserver(llm_config)
    before = {
        token_type: _sample_total(
            "llm_tokens_total",
            role="analysis",
            model="qwen-test",
            type=token_type,
        )
        for token_type in ("input", "output", "cached_input")
    }
    _drive(
        observer,
        _make_started_event("Financial Analyst", "qwen-test"),
        _make_completed_event(
            "Financial Analyst",
            "qwen-test",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        ),
    )

    for token_type in ("input", "output", "cached_input"):
        after = _sample_total(
            "llm_tokens_total",
            role="analysis",
            model="qwen-test",
            type=token_type,
        )
        assert after == before[token_type]


def test_missing_usage_only_increments_missing(llm_config: LLMConfig) -> None:
    """无 usage → 只增加 missing，不写 token，也不伪造 0。"""
    observer = LlmCallObserver(llm_config)
    before = _sample_total("llm_usage_missing_total", role="writer", model="qwen-test")
    _drive(
        observer,
        _make_started_event("Report Writer", "qwen-test"),
        _make_completed_event("Report Writer", "qwen-test"),  # 无 usage
    )

    after = _sample_total("llm_usage_missing_total", role="writer", model="qwen-test")
    assert after - before == 1.0


# ---------------------------------------------------------------------------
# 事件总线真实 emit 触发
# ---------------------------------------------------------------------------


def test_subscribe_and_event_bus_emit(llm_config: LLMConfig) -> None:
    """subscribe() 注册 handler 后，crewai_event_bus.emit 真实触发计数。"""
    from crewai.events.event_bus import crewai_event_bus

    observer = LlmCallObserver(llm_config)
    before = _sample_total(
        "llm_requests_total", role="research", model="qwen-test", status="success"
    )
    scope = observer.subscribe()
    assert scope is not None
    try:
        started_future = crewai_event_bus.emit(
            None,
            event=_make_started_event("Research Analyst", "qwen-test"),
        )
        if started_future is not None:
            started_future.result(timeout=2.0)
        completed_future = crewai_event_bus.emit(
            None,
            event=_make_completed_event(
                "Research Analyst",
                "qwen-test",
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            ),
        )
        if completed_future is not None:
            completed_future.result(timeout=2.0)
    finally:
        scope.__exit__(None, None, None)

    after = _sample_total(
        "llm_requests_total", role="research", model="qwen-test", status="success"
    )
    assert after - before == 1.0


def test_subscribe_returns_none_when_no_event_bus(
    llm_config: LLMConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事件总线不可用时 subscribe 返回 None（静默降级）。"""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "crewai.events.event_bus":
            raise ImportError("no event bus")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    observer = LlmCallObserver(llm_config)
    assert observer.subscribe() is None


# ---------------------------------------------------------------------------
# label 安全性
# ---------------------------------------------------------------------------


def test_labels_no_sensitive_or_high_cardinality(llm_config: LLMConfig) -> None:
    """指标 label 不包含 URL/公司/job_id/api_key 等高基数或敏感信息。"""
    observer = LlmCallObserver(llm_config)
    _drive(
        observer,
        _make_started_event("Research Analyst", "qwen-test"),
        _make_completed_event(
            "Research Analyst",
            "qwen-test",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ),
    )
    for _, labels, _ in _samples():
        assert not any(
            k in labels
            for k in ("url", "job_id", "company", "api_key", "authorization", "prompt")
        )
        for value in labels.values():
            assert "http://" not in value and "sk-" not in value, (
                f"label 泄漏敏感信息: {value}"
            )
