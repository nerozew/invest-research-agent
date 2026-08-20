"""P06-11J：完整调用链观测器（LLM Span 生命周期修复 + CrewAI 工具循环）。

对比旧 ``LlmCallObserver`` 的修复：
1. LLMCallStartedEvent 时创建 ``llm.request`` span handle，Completed/Failed 时
   结束**同一个** span（不再在 Completed 时新建零耗时 span）；
2. 用 per-role FIFO deque + 自增 call_index 配对多次同角色调用，
   禁止用 (role, model) 键覆盖前一次调用；
3. 订阅 CrewAI ``ToolUsageStarted/Finished/Error`` → ``crewai.tool.<name>``
   span + 低基数指标；
4. 记录 ``agent_iteration_total`` / ``agent_max_iteration_total``；
5. 写入 Job-local ExecutionTimeline（脱敏，失败不影响业务）。

P06-11K-4：LLM 三态摘要（content / tool_calls / empty）入诊断包 +
Jaeger Span 只加 diagnostic.* 低基数属性（绝不塞完整 Payload）。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Deque, Tuple

from invest_research.agents.llm_factory import LLMConfig
from invest_research.infrastructure.observability.execution_timeline import (
    ExecutionTimelineSink,
)
from invest_research.infrastructure.observability.llm_call_observer import (
    extract_usage_tokens,
    has_usage,
    role_name_to_role,
)
from invest_research.infrastructure.observability.metrics_events import label_provider_model
from invest_research.infrastructure.observability.tracing import get_tracer

__all__ = ["LlmFullObserver"]

_LOGGER = logging.getLogger(__name__)


@dataclass
class _LlmSpanHandle:
    """一次 LLM 调用的 span handle + 元数据。"""

    span: Any
    role: str
    model: str
    call_index: int
    started_monotonic: float
    started_wall_ns: int


@dataclass
class _ToolCallHandle:
    """一次 CrewAI 工具调用的 span handle。"""

    span: Any
    tool_name: str
    started_monotonic: float
    call_index: int


_EventBinding = Tuple[Any, Callable[[Any, Any], None]]


def _stable_tool_name(tool_name: str | None) -> str | None:
    """稳定工具名：去空白、去类路径，保留短名；未知返回 None。"""
    if not tool_name:
        return None
    name = str(tool_name).strip()
    if not name:
        return None
    # 处理 "Class.method" / "module.path.Class.method" 取最后一段
    candidate = name.rsplit(".", 1)[-1]
    return candidate or None


class LlmFullObserver:
    """订阅 CrewAI LLM 与 ToolUsage 事件，维护 span 生命周期（Job-local）。

    - ``subscribe()`` 返回 ``crewai_event_bus.scoped_handlers()`` context manager；
      调用方必须在 kickoff 完成后退出，防止 handler 泄漏到下一个 Job；
    - ``close()`` 清理 pending 队列（幂等），禁止跨 Job 泄漏；
    - 所有 handler 尽力而为，绝不抛异常影响业务。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        agent_roles: dict[str, str] | None = None,
        timeline: ExecutionTimelineSink | None = None,
        diagnostics_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config
        self._agent_roles = dict(agent_roles or {})
        self._timeline = timeline
        # P06-11K-4：惰性读取当前 Job 的 DiagnosticCapture（tool/LLM 摘要捕获）。
        self._diagnostics_provider = diagnostics_provider
        self._llm_pending: dict[str, Deque[_LlmSpanHandle]] = {
            "research": deque(),
            "analysis": deque(),
            "writer": deque(),
            "revision": deque(),
        }
        self._llm_call_index = 0
        self._tool_pending: Deque[_ToolCallHandle] = deque()
        self._tool_call_index = 0
        self._event_types: tuple[_EventBinding, ...] | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    def subscribe(self) -> Any | None:
        """注册事件 handler，返回 scoped_handlers() context manager（或 None）。"""
        event_types = self._resolve_event_types()
        if event_types is None:
            return None
        try:
            from crewai.events.event_bus import crewai_event_bus
        except ImportError:
            return None
        scope = crewai_event_bus.scoped_handlers()
        scope.__enter__()
        for event_type, handler in event_types:
            crewai_event_bus.on(event_type)(handler)
        return scope

    def close(self) -> None:
        """清理 pending span handle（尽力而为；未完成 span 直接结束）。"""
        if self._closed:
            return
        self._closed = True
        for queue in self._llm_pending.values():
            while queue:
                llm_handle = queue.popleft()
                try:
                    llm_handle.span.end(end_time=time.time_ns())
                except Exception:  # noqa: BLE001
                    pass
        while self._tool_pending:
            tool_handle = self._tool_pending.popleft()
            try:
                tool_handle.span.end(end_time=time.time_ns())
            except Exception:  # noqa: BLE001
                pass

    def _resolve_event_types(self) -> tuple[_EventBinding, ...] | None:
        """解析事件类型；不可用返回 None（以实际安装源码为准，不硬编码事件名）。"""
        try:
            from crewai.events.types.agent_events import (
                AgentExecutionCompletedEvent,
                AgentExecutionStartedEvent,
            )
            from crewai.events.types.llm_events import (
                LLMCallCompletedEvent,
                LLMCallFailedEvent,
                LLMCallStartedEvent,
            )
            from crewai.events.types.task_events import (
                TaskCompletedEvent,
                TaskFailedEvent,
                TaskStartedEvent,
            )
            from crewai.events.types.tool_usage_events import (
                ToolUsageErrorEvent,
                ToolUsageFinishedEvent,
                ToolUsageStartedEvent,
            )
        except ImportError:
            return None
        return (
            (LLMCallStartedEvent, self._on_llm_started),
            (LLMCallCompletedEvent, self._on_llm_completed),
            (LLMCallFailedEvent, self._on_llm_failed),
            (ToolUsageStartedEvent, self._on_tool_started),
            (ToolUsageFinishedEvent, self._on_tool_finished),
            (ToolUsageErrorEvent, self._on_tool_error),
            (AgentExecutionStartedEvent, self._on_agent_started),
            (AgentExecutionCompletedEvent, self._on_agent_completed),
            (TaskStartedEvent, self._timeline_task_started),
            (TaskCompletedEvent, self._timeline_task_completed),
            (TaskFailedEvent, self._timeline_task_failed),
        )

    # ------------------------------------------------------------------
    # LLM 事件
    # ------------------------------------------------------------------

    def _on_llm_started(self, source: Any, event: Any) -> None:
        try:
            role = self._event_role(event)
            model = str(getattr(event, "model", "") or "").strip()
            if role is None or not model or self._closed:
                return
            self._llm_call_index += 1

            tracer = get_tracer("llm")
            span = tracer.start_span(
                "llm.request",
                attributes={
                    "llm.model": model,
                    "llm.role": role,
                    "llm.status": "running",
                },
                start_time=time.time_ns(),
            )
            self._llm_pending[role].append(
                _LlmSpanHandle(
                    span=span,
                    role=role,
                    model=model,
                    call_index=self._llm_call_index,
                    started_monotonic=time.monotonic(),
                    started_wall_ns=time.time_ns(),
                )
            )
            self._timeline_record(
                stage="llm.request",
                event_type="start",
                role=role,
                call_index=self._llm_call_index,
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_full_observer started failed")

    def _on_llm_completed(self, source: Any, event: Any) -> None:
        try:
            role = self._event_role(event)
            if role is None or self._closed:
                return
            handle = self._pop_llm_handle(role)
            if handle is None:
                return
            span = handle.span
            try:
                provider, model_lbl = label_provider_model(
                    self._config.vendor, handle.model, base_url=self._config.base_url
                )
                duration = max(time.monotonic() - handle.started_monotonic, 0.0)
                usage = self._event_usage(event)
                span.set_attribute("llm.provider", provider)
                span.set_attribute("llm.model", model_lbl)
                span.set_attribute("llm.role", role)
                span.set_attribute("llm.status", "success")
                span.set_attribute("llm.call_index", int(handle.call_index))
                if duration >= 0:
                    span.set_attribute("llm.duration_s", duration)
                self._apply_response_kind_attrs(span, event, usage)
                # P06-11K-4：LLM 三态摘要（content / tool_calls / empty）入诊断包。
                self._capture_llm_summary(event, role=role, status="success")
                # P06-11K-4：Span 只加 diagnostic.* 低基数属性（不塞 Payload）。
                self._set_diagnostic_span_attrs(
                    span,
                    event_id=f"llm.{role}.{handle.call_index}",
                    payload_kind="llm_response",
                    input_size=None,
                    output_size=None,
                    validation_error_count=0,
                )
                self._count_llm_metrics(provider, model_lbl, role, "success", duration, usage)
                span.end(end_time=time.time_ns())
            except Exception:  # noqa: BLE001 - 属性/指标尽力而为
                span.end(end_time=time.time_ns())
            self._timeline_record(
                stage="llm.request",
                event_type="completed",
                role=role,
                call_index=handle.call_index,
                status="success",
                duration_ms=duration * 1000.0,
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_full_observer completed failed")

    def _on_llm_failed(self, source: Any, event: Any) -> None:
        try:
            role = self._event_role(event)
            if role is None or self._closed:
                return
            handle = self._pop_llm_handle(role)
            if handle is None:
                return
            span = handle.span
            try:
                provider, model_lbl = label_provider_model(
                    self._config.vendor, handle.model, base_url=self._config.base_url
                )
                duration = max(time.monotonic() - handle.started_monotonic, 0.0)
                span.set_attribute("llm.provider", provider)
                span.set_attribute("llm.model", model_lbl)
                span.set_attribute("llm.role", role)
                span.set_attribute("llm.status", "failure")
                span.set_attribute("llm.call_index", int(handle.call_index))
                if duration >= 0:
                    span.set_attribute("llm.duration_s", duration)
                error_text = str(getattr(event, "error", "") or "")
                span.record_exception(
                    Exception(error_text[:200])  # noqa: TRY002 - 观测用
                )
                # P06-11K-4：失败 LLM 摘要（error_code 为稳定错误码）。
                self._capture_llm_summary(
                    event, role=role, status="failure", error_code="LLM_CALL_FAILED"
                )
                # P06-11K-4：Span 只加 diagnostic.* 低基数属性。
                self._set_diagnostic_span_attrs(
                    span,
                    event_id=f"llm.{role}.{handle.call_index}",
                    payload_kind="llm_response_failure",
                    input_size=None,
                    output_size=None,
                    validation_error_count=0,
                )
                span.end(end_time=time.time_ns())
                self._count_llm_metrics(provider, model_lbl, role, "failure", duration, None)
            except Exception:  # noqa: BLE001 - 属性/指标尽力而为
                span.end(end_time=time.time_ns())
            self._timeline_record(
                stage="llm.request",
                event_type="failed",
                role=role,
                call_index=handle.call_index,
                status="failure",
                duration_ms=duration * 1000.0,
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_full_observer failed handler")

    # ------------------------------------------------------------------
    # CrewAI 工具循环事件
    # ------------------------------------------------------------------

    def _on_tool_started(self, source: Any, event: Any) -> None:
        try:
            if self._closed:
                return
            name = _stable_tool_name(getattr(event, "tool_name", None))
            if name is None:
                return
            self._tool_call_index += 1
            tracer = get_tracer("crewai")
            span = tracer.start_span(
                f"crewai.tool.{name}",
                attributes={
                    "tool.name": name,
                    "crewai.tool.call_index": self._tool_call_index,
                    "status": "running",
                },
                start_time=time.time_ns(),
            )
            self._tool_pending.append(
                _ToolCallHandle(
                    span=span,
                    tool_name=name,
                    started_monotonic=time.monotonic(),
                    call_index=self._tool_call_index,
                )
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("crewai tool started failed")

    def _on_tool_finished(self, source: Any, event: Any) -> None:
        try:
            name = _stable_tool_name(getattr(event, "tool_name", None))
            handle = self._pop_tool_handle(name)
            if handle is None:
                return
            duration = max(time.monotonic() - handle.started_monotonic, 0.0)
            span = handle.span
            try:
                from_cache = bool(getattr(event, "from_cache", False))
                output = getattr(event, "output", None)
                output_chars = len(str(output)) if not isinstance(output, (bytes, bytearray)) else 0
                span.set_attribute("status", "success")
                span.set_attribute("cache_hit", from_cache)
                span.set_attribute("crewai.tool.result_chars", int(output_chars))
                span.set_attribute("crewai.tool.duration_s", duration)
                span.end(end_time=time.time_ns())
            except Exception:  # noqa: BLE001
                span.end(end_time=time.time_ns())
            self._timeline_record(
                stage="crewai.tool",
                event_type="completed",
                tool_name=handle.tool_name,
                call_index=handle.call_index,
                status="success",
                duration_ms=duration * 1000.0,
                output_chars=output_chars if "output_chars" in locals() else None,
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("crewai tool finished failed")

    def _on_tool_error(self, source: Any, event: Any) -> None:
        try:
            name = _stable_tool_name(getattr(event, "tool_name", None))
            handle = self._pop_tool_handle(name)
            if handle is None:
                return
            duration = max(time.monotonic() - handle.started_monotonic, 0.0)
            span = handle.span
            try:
                span.set_attribute("status", "failure")
                span.set_attribute("crewai.tool.duration_s", duration)
                error_text = str(getattr(event, "error", "") or "")
                span.record_exception(
                    Exception(error_text[:200])  # noqa: TRY002 - 观测用
                )
                span.end(end_time=time.time_ns())
            except Exception:  # noqa: BLE001
                span.end(end_time=time.time_ns())
            self._timeline_record(
                stage="crewai.tool",
                event_type="error",
                tool_name=handle.tool_name,
                call_index=handle.call_index,
                status="failure",
                duration_ms=duration * 1000.0,
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("crewai tool error handler")

    # ------------------------------------------------------------------
    # Agent 迭代
    # ------------------------------------------------------------------

    def _on_agent_started(self, source: Any, event: Any) -> None:
        try:
            agent = getattr(event, "agent", None)
            role = self._agent_role_of(agent)
            if role is None:
                return
            from invest_research.infrastructure.observability.metrics_events import count_agent_run

            count_agent_run(role, "live", "unknown", "unknown", "success")
        except Exception:  # noqa: BLE001
            _LOGGER.warning("crewai agent started failed")

    def _on_agent_completed(self, source: Any, event: Any) -> None:
        try:
            agent = getattr(event, "agent", None)
            role = self._agent_role_of(agent)
            if role is None:
                return
            max_iter = int(getattr(agent, "max_iter", 0) or 0)
            executor = getattr(agent, "agent_executor", None)
            iterations = int(getattr(executor, "iterations", 0) or 0)
            from invest_research.infrastructure.observability.metrics_events import (
                count_agent_iteration_limit,
            )

            if max_iter > 0:
                count_agent_iteration_limit(role, "live")
                if iterations >= max_iter:
                    self._timeline_record(
                        stage="agent.max_iter",
                        event_type="max_iter_reached",
                        role=role,
                        status="exhausted",
                    )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("crewai agent completed failed")

    # ------------------------------------------------------------------
    # Timeline（task 级事件 → 脱敏时间线；失败不影响业务）
    # ------------------------------------------------------------------

    def _timeline_task_started(self, source: Any, event: Any) -> None:
        if self._timeline is None:
            return
        try:
            task = getattr(event, "task", None)
            agent = getattr(task, "agent", None) if task is not None else None
            role = self._agent_role_of(agent) or (str(getattr(task, "role", "") or "") or None)
            self._timeline.record(
                stage="agent.task",
                event_type="started",
                role=role,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("timeline task started failed")

    def _timeline_task_completed(self, source: Any, event: Any) -> None:
        if self._timeline is None:
            return
        try:
            task = getattr(event, "task", None)
            agent = getattr(task, "agent", None) if task is not None else None
            role = self._agent_role_of(agent) or (str(getattr(task, "role", "") or "") or None)
            self._timeline.record(
                stage="agent.task",
                event_type="completed",
                role=role,
                status="success",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("timeline task completed failed")

    def _timeline_task_failed(self, source: Any, event: Any) -> None:
        if self._timeline is None:
            return
        try:
            task = getattr(event, "task", None)
            agent = getattr(task, "agent", None) if task is not None else None
            role = self._agent_role_of(agent) or (str(getattr(task, "role", "") or "") or None)
            self._timeline.record(
                stage="agent.task",
                event_type="failed",
                role=role,
                status="failure",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("timeline task failed handler")

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _event_role(self, event: Any) -> str | None:
        """事件角色解析：agent_id 映射优先，Agent.role 名称回退。"""
        agent_id = str(getattr(event, "agent_id", "") or "").strip()
        if agent_id:
            mapped = self._agent_roles.get(agent_id)
            if mapped in ("research", "analysis", "writer", "revision"):
                return mapped
        return role_name_to_role(getattr(event, "agent_role", None))

    def _agent_role_of(self, agent: Any) -> str | None:
        """从 Agent 对象读取稳定角色（id 映射优先，role 名回退）。"""
        if agent is None:
            return None
        agent_id = str(getattr(agent, "id", "") or "").strip()
        if agent_id:
            mapped = self._agent_roles.get(agent_id)
            if mapped in ("research", "analysis", "writer", "revision"):
                return mapped
        return role_name_to_role(getattr(agent, "role", None))

    def _pop_llm_handle(self, role: str) -> _LlmSpanHandle | None:
        """从 per-role FIFO 队列取最早开始的 handle（先进先出配对）。"""
        queue = self._llm_pending.get(role)
        if not queue:
            return None
        return queue.popleft()

    def _pop_tool_handle(self, tool_name: str | None) -> _ToolCallHandle | None:
        """从工具队列中按名匹配最早开始的 handle（无匹配返回 None）。"""
        if not self._tool_pending:
            return None
        if tool_name is None:
            return self._tool_pending.popleft()
        for idx, handle in enumerate(self._tool_pending):
            if handle.tool_name == tool_name:
                del self._tool_pending[idx]
                return handle
        return None

    def _event_usage(self, event: Any) -> Any:
        """从 LLM 事件取 usage（事件字段优先，response.usage 回退）。"""
        try:
            direct = getattr(event, "usage", None)
            if direct is not None:
                return direct
            response = getattr(event, "response", None)
            if isinstance(response, dict):
                return response.get("usage")
            return getattr(response, "usage", None)
        except Exception:  # noqa: BLE001
            return None

    def _set_diagnostic_span_attrs(
        self,
        span: Any,
        *,
        event_id: str,
        payload_kind: str,
        input_size: int | None,
        output_size: int | None,
        validation_error_count: int,
    ) -> None:
        """P06-11K-4：Span 只加 diagnostic.* 白名单低基数属性（不塞 Payload）。

        属性白名单（与任务文档一致）：
        diagnostic.event_id / diagnostic.available / diagnostic.payload_kind /
        diagnostic.input_size / diagnostic.output_size /
        diagnostic.validation_error_count。禁止把完整 Payload 塞入 Span。
        """
        try:
            span.set_attribute("diagnostic.event_id", str(event_id))
            span.set_attribute("diagnostic.available", True)
            span.set_attribute("diagnostic.payload_kind", str(payload_kind))
            if input_size is not None:
                span.set_attribute("diagnostic.input_size", int(input_size))
            if output_size is not None:
                span.set_attribute("diagnostic.output_size", int(output_size))
            span.set_attribute("diagnostic.validation_error_count", int(validation_error_count))
        except Exception:  # noqa: BLE001 - span 属性尽力而为
            pass

    def _apply_response_kind_attrs(self, span: Any, event: Any, usage: Any) -> None:
        """记录 response_kind / content_chars / tool_call_count / finish_reason。"""
        try:
            tokens = extract_usage_tokens(usage)
            span.set_attribute("llm.input_tokens", int(tokens["input"]))
            span.set_attribute("llm.output_tokens", int(tokens["output"]))
            span.set_attribute("llm.cached_input_tokens", int(tokens["cached_input"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            response = getattr(event, "response", None)
            content = ""
            if isinstance(response, str):
                content = response
            elif isinstance(response, dict):
                content = response.get("content") or ""
            else:
                content = getattr(response, "content", "") or ""
            span.set_attribute("llm.content_chars", int(len(str(content))))
            call_type = str(getattr(event, "call_type", "") or "")
            if "tool" in call_type.lower():
                kind = "tool_call"
            elif content:
                kind = "content"
            else:
                kind = "empty"
            span.set_attribute("llm.response_kind", kind)
            try:
                finish = (
                    response.get("finish_reason")
                    if isinstance(response, dict)
                    else getattr(response, "finish_reason", None)
                )
            except Exception:  # noqa: BLE001
                finish = None
            if finish is not None:
                span.set_attribute("llm.finish_reason", str(finish))
        except Exception:  # noqa: BLE001
            pass

    def _capture_llm_summary(
        self,
        event: Any,
        *,
        role: str,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """P06-11K-4：LLM 调用摘要（content / tool_calls / empty 三态）入诊断包。

        - 从事件提取 content / call_type / finish_reason / tokens；
        - 摘要只含计数/长度/角色，绝不包含回复正文或工具参数；
        - 由 DiagnosticCapture.capture 再做一次递归脱敏（纵深防御）。
        """
        if self._diagnostics_provider is None:
            return
        try:
            diagnostics = self._diagnostics_provider()
            if diagnostics is None:
                return
            event_usage = self._event_usage(event)
            tokens = extract_usage_tokens(event_usage)
            response = getattr(event, "response", None)
            content = ""
            if isinstance(response, str):
                content = response
            elif isinstance(response, dict):
                content = response.get("content") or ""
            else:
                content = getattr(response, "content", "") or ""
            content_chars = int(len(str(content)))
            call_type = str(getattr(event, "call_type", "") or "")
            if "tool" in call_type.lower() or getattr(event, "tool_calls", None):
                kind = "tool_calls"
            elif content_chars:
                kind = "content"
            else:
                kind = "empty"
            finish = None
            try:
                finish = (
                    response.get("finish_reason")
                    if isinstance(response, dict)
                    else getattr(response, "finish_reason", None)
                )
            except Exception:  # noqa: BLE001
                finish = None
            from invest_research.application.diagnostics.models import DiagnosticDirection
            from invest_research.application.diagnostics.tool_summaries import (
                build_llm_response_summary,
            )

            summary = build_llm_response_summary(
                kind,
                role=role,
                content_chars=content_chars,
                finish_reason=str(finish) if finish is not None else None,
                input_tokens=int(tokens["input"]) if tokens["input"] is not None else None,
                output_tokens=int(tokens["output"]) if tokens["output"] is not None else None,
                tool_call_count=len(getattr(event, "tool_calls", None) or []),
                error_code=error_code,
            )
            diagnostics.capture(
                stage="llm",
                component="llm_full_observer",
                payload_kind=f"llm_response_{kind}",
                data={**summary, "status": status},
                direction=DiagnosticDirection.OUTPUT,
            )
        except Exception:  # noqa: BLE001 - 诊断尽力而为
            _LOGGER.warning("llm diagnostics capture skipped role=%s", role)

    def _count_llm_metrics(
        self,
        provider: str,
        model: str,
        role: str,
        status: str,
        duration: float,
        usage: Any,
    ) -> None:
        """低基数 LLM 指标（尽力而为）。"""
        try:
            from invest_research.infrastructure.observability.metrics_events import (
                count_llm_request,
                count_llm_usage_missing,
                observe_llm_duration,
            )

            count_llm_request(provider, model, role, status)
            observe_llm_duration(provider, model, role, status, duration)
            if not has_usage(usage):
                count_llm_usage_missing(provider, model, role)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("llm_full_observer metrics failed")

    def _timeline_record(self, **kwargs: Any) -> None:
        if self._timeline is None:
            return
        try:
            self._timeline.record(**kwargs)
        except Exception:  # noqa: BLE001 - Timeline 写入失败不影响业务
            pass
