"""P06-11A：LLM 真实调用事件观测器（Prometheus 指标 + Jaeger span）。

审计结论（CrewAI 1.6.1，稳定入口，不使用全局 monkey patch）：

- ``crewai.llm.LLM`` 在每次真实模型调用边界通过 ``crewai_event_bus`` emit 三个官方事件：
  - ``LLMCallStartedEvent``（携带 ``model``、``from_agent``、``from_task``）；
  - ``LLMCallCompletedEvent``（携带 ``model``、``from_agent``、``response``；
    某些路径仅保留文本 response、不保留 usage；**无 duration 字段**）；
  - ``LLMCallFailedEvent``（携带 ``error``、``from_agent``、``from_task``；
    **无 model/duration 字段**）。
- 耗时：事件无 duration 字段，用 Started→(Completed|Failed) 的本地起止时间实测。
- 事件 ``LLMEventBase.__init__`` 会把 ``from_agent`` 转换为 ``agent_role``
  （即 Agent.role 可读名，如 "Financial Analyst"），本模块再映射为稳定的
  research/analysis/writer 内部角色。
- 每个真实模型请求只在此处记录一次次数/耗时；token 由
  ``flow_wiring`` 在 Crew 前后读取每个 Agent 自带的 TokenProcess 差值，
  因为 CrewAI 1.6.1 事件会在部分路径丢弃 usage。

安全边界：
- 不记录 prompt、response、API Key、base_url、公司名、job_id；
- span 属性只放 provider/model/role/status（Span 可记录 token 数与 duration）；
- 指标写入失败绝不影响业务（尽力而为，脱敏日志）。

模块边界：只依赖 ``crewai.events`` 类型（导入前检查存在性）与本地
``metrics_events`` / ``tracing``；不导入 CrewAI Agent/Crew（避免循环依赖）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

from invest_research.agents.llm_factory import LLMConfig
from invest_research.infrastructure.observability.metrics_events import (
    count_llm_request,
    count_llm_usage_missing,
    label_provider_model,
    observe_llm_duration,
)
from invest_research.infrastructure.observability.tracing import get_tracer

__all__ = [
    "LlmCallObserver",
    "role_name_to_role",
    "extract_usage_tokens",
]

_LOGGER = logging.getLogger(__name__)

# Agent.role 可读名 → 内部稳定角色（与 crew_factory._agent_role_config 一致）。
_ROLE_NAME_TO_ROLE: tuple[tuple[str, str], ...] = (
    ("research analyst", "research"),
    ("信息搜集 agent", "research"),
    ("financial analyst", "analysis"),
    ("财报分析 agent", "analysis"),
    ("report writer", "writer"),
    ("报告撰写 agent", "writer"),
)


@dataclass(frozen=True)
class _StartedCall:
    """一次真实 LLM 调用的双时钟起点。"""

    monotonic: float
    wall_time_ns: int


def role_name_to_role(agent_role: str | None) -> str | None:
    """把 Agent.role 可读名映射为内部稳定角色（research/analysis/writer）。

    未知角色返回 None（由调用方丢弃，不猜测、不硬编码未知 label）。
    """
    if not agent_role:
        return None
    lowered = str(agent_role).strip().lower()
    for readable, role in _ROLE_NAME_TO_ROLE:
        if readable in lowered or lowered in readable:
            return role
    return None


def _get_usage_token(usage: Any, key: str) -> int:
    """从 LiteLLM Usage（object/dict）安全提取单个 token 计数字段。"""
    if usage is None:
        return 0
    try:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    except Exception:  # noqa: BLE001 - 事件数据不可靠时视作 0（不中断）
        value = None
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _get_prompt_tokens_details(usage: Any) -> Any:
    """提取 LiteLLM ``prompt_tokens_details``（dict 或 object，缺失返回 None）。"""
    if usage is None:
        return None
    try:
        return usage.get("prompt_tokens_details") if isinstance(usage, dict) else getattr(
            usage, "prompt_tokens_details", None
        )
    except Exception:  # noqa: BLE001
        return None


def extract_usage_tokens(usage: Any) -> dict[str, int]:
    """从真实模型响应 usage 提取 input/output/cached_input token 计数。

    仅承认真实 usage 中的数值字段；缺失的字段返回 0（由调用方决定是否把
    整条 usage 视为缺失——``has_usage`` 负责判定）。
    """
    details = _get_prompt_tokens_details(usage)
    cached = 0
    if details is not None:
        cached = _get_usage_token(details, "cached_tokens")
    if cached == 0:
        # LiteLLM 有的供应商走 cache_read_input_tokens / cache_creation_input_tokens
        cached = _get_usage_token(usage, "cache_read_input_tokens")
        cached = max(cached, _get_usage_token(usage, "cache_creation_input_tokens"))
    return {
        "input": _get_usage_token(usage, "prompt_tokens"),
        "output": _get_usage_token(usage, "completion_tokens"),
        "cached_input": cached,
    }


def has_usage(usage: Any) -> bool:
    """判定一条 usage 是否包含可消费的真实 token 计数。

    任何输入/输出 token > 0 即视为有真实 usage（不做估算、不把 0 当真实值）。
    """
    tokens = extract_usage_tokens(usage)
    return tokens["input"] > 0 or tokens["output"] > 0


class LlmCallObserver:
    """订阅 CrewAI LLM 事件，在每次真实模型调用边界写指标与 Jaeger span。

    用法（与 flow_wiring._subscribe_task_progress 一致）：

    .. code-block:: python

        observer = LlmCallObserver(config)
        scope = observer.subscribe()
        try:
            result = crew.kickoff(inputs=inputs)
        finally:
            scope.__exit__(None, None, None)

    - ``subscribe()`` 返回 ``crewai_event_bus.scoped_handlers()`` context manager；
      调用方必须在 kickoff 完成后退出，防止 handler 泄漏到下一个 Job。
    - 事件总线不存在（旧版 CrewAI）时 ``subscribe`` 返回 None，静默跳过观测。
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        agent_roles: Mapping[str, str] | None = None,
    ) -> None:
        self._config = config
        # 生产路径优先使用 Crew 组装时建立的 agent_id→稳定角色映射；名称只作兼容回退。
        self._agent_roles = dict(agent_roles or {})
        # (role, model) -> 双时钟起点；monotonic 计算耗时，wall clock 构造真实 span 时间线。
        self._started_at: dict[tuple[str, str], _StartedCall] = {}
        # 事件模型类型（惰性解析一次；不可用时禁用观测）
        self._event_types: tuple[Any, Any, Any] | None = None

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    def subscribe(self) -> Any | None:
        """注册事件 handler，返回 scoped_handlers() context manager（或 None）。"""
        event_types = self._resolve_event_types()
        if event_types is None:
            return None
        started, completed, failed = event_types
        try:
            from crewai.events.event_bus import crewai_event_bus
        except ImportError:
            return None

        scope = crewai_event_bus.scoped_handlers()
        scope.__enter__()
        crewai_event_bus.on(started)(self._on_started)
        crewai_event_bus.on(completed)(self._on_completed)
        crewai_event_bus.on(failed)(self._on_failed)
        return scope

    @staticmethod
    def _resolve_event_types() -> tuple[Any, Any, Any] | None:
        """返回 (Started, Completed, Failed) 事件类型；不可用返回 None。"""
        try:
            from crewai.events.types.llm_events import (
                LLMCallCompletedEvent,
                LLMCallFailedEvent,
                LLMCallStartedEvent,
            )
        except ImportError:
            return None
        return LLMCallStartedEvent, LLMCallCompletedEvent, LLMCallFailedEvent

    # ------------------------------------------------------------------
    # 事件 handler（全部尽力而为，绝不抛异常影响业务）
    # ------------------------------------------------------------------

    def _on_started(self, source: Any, event: Any) -> None:
        try:
            role = self._event_role(event)
            model = str(getattr(event, "model", "") or "").strip()
            if role is None or not model:
                return
            self._started_at[(role, model)] = _StartedCall(
                monotonic=time.monotonic(),
                wall_time_ns=time.time_ns(),
            )
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_call_observer started handling failed")

    def _on_completed(self, source: Any, event: Any) -> None:
        try:
            role = self._event_role(event)
            model = str(getattr(event, "model", "") or "").strip()
            if role is None or not model:
                return
            provider, model_lbl = label_provider_model(
                self._config.vendor, model, base_url=self._config.base_url
            )
            # duration：事件无 duration 字段，用 Started→Completed 本地起止实测。
            duration = self._event_duration(role, model)
            usage = self._event_usage(event)
            if not has_usage(usage):
                count_llm_usage_missing(provider, model_lbl, role)
            count_llm_request(provider, model_lbl, role, "success")
            observe_llm_duration(provider, model_lbl, role, "success", duration)
            self._record_span(provider, model_lbl, role, "success", duration, usage)
            self._started_at.pop((role, model), None)
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_call_observer completed handling failed")

    def _on_failed(self, source: Any, event: Any) -> None:
        try:
            role = self._event_role(event)
            if role is None:
                return
            # Failed 事件在 CrewAI 1.6.1 没有 model 字段；先兼容未来版本，
            # 再回退最近一次 Started 的 model 配对；找不到则丢弃（不猜测）。
            model = str(getattr(event, "model", "") or "").strip() or self._latest_model(role)
            if model is None:
                return
            provider, model_lbl = label_provider_model(
                self._config.vendor, model, base_url=self._config.base_url
            )
            duration = self._event_duration(role, model)
            count_llm_request(provider, model_lbl, role, "failure")
            observe_llm_duration(provider, model_lbl, role, "failure", duration)
            # 失败不产生 token：由上游响应缺失触发 missing 计数？——失败时无响应
            # usage，不应记 missing（missing=有响应但无 usage）；只记失败次数与耗时。
            self._record_span(provider, model_lbl, role, "failure", duration, None)
            self._started_at.pop((role, model), None)
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_call_observer failed handling failed")

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _event_duration(self, role: str, model: str) -> float:
        """取 Started→Completed/Failed 的本地起止实测耗时（秒）。"""
        started = self._started_at.get((role, model))
        if started is None:
            return 0.0
        return max(time.monotonic() - started.monotonic, 0.0)

    def _event_role(self, event: Any) -> str | None:
        """事件角色解析：稳定 agent_id 优先，Agent.role 名称仅作回退。"""
        agent_id = str(getattr(event, "agent_id", "") or "").strip()
        if agent_id:
            mapped = self._agent_roles.get(agent_id)
            if mapped in ("research", "analysis", "writer", "revision"):
                return mapped
        return role_name_to_role(getattr(event, "agent_role", None))

    def _event_usage(self, event: Any) -> Any:
        """取真实模型响应 usage。

        CrewAI 1.6.1 的 ``LLMCallCompletedEvent`` 没有独立 ``usage`` 字段，
        token 位于 ``event.response.usage``。先读事件字段是为了兼容
        可能将 usage 上移的未来版本，随后同时兼容 object/dict 响应。
        """
        try:
            direct_usage = getattr(event, "usage", None)
            if direct_usage is not None:
                return direct_usage
            response = getattr(event, "response", None)
            if isinstance(response, dict):
                return response.get("usage")
            return getattr(response, "usage", None)
        except Exception:  # noqa: BLE001
            return None

    def _latest_model(self, role: str) -> str | None:
        """最近一次 Started 但未 Completed/Failed 的 model（按开始时间取最新）。"""
        candidates = [
            (started.monotonic, model)
            for (r, model), started in self._started_at.items()
            if r == role
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][1]

    def _record_span(
        self,
        provider: str,
        model: str,
        role: str,
        status: str,
        duration: float,
        usage: Any | None,
    ) -> None:
        """创建 Jaeger ``llm.request`` span（属性仅低基数/非敏感字段）。

        禁止记录 prompt、response、API Key、公司名、job_id；token 合并为一个
        非敏感计数属性（input/output/cached_input 合计 total）。
        """
        try:
            tracer = get_tracer("llm")
            attributes: dict[str, Any] = {
                "llm.provider": provider,
                "llm.model": model,
                "llm.role": role,
                "llm.status": status,
            }
            if duration >= 0:
                attributes["llm.duration_s"] = duration
            if has_usage(usage):
                tokens = extract_usage_tokens(usage)
                attributes["llm.tokens_total"] = (
                    tokens["input"] + tokens["output"] + tokens["cached_input"]
                )
            started = self._started_at.get((role, model))
            start_time = started.wall_time_ns if started is not None else None
            llm_span = tracer.start_span(
                "llm.request",
                attributes=attributes,
                start_time=start_time,
            )
            llm_span.end(end_time=time.time_ns())
        except Exception:  # noqa: BLE001 - 观测尽力而为
            _LOGGER.warning("llm_call_observer span recording skipped")
