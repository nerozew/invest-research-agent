"""P06-11K-5：生产接线 + fake Docker 故障注入全链路验收。

覆盖（交接提示词测试清单）：
1. 生产接线单测：
   - ``_cached_execute(diagnostics_provider=...)`` 正常执行产生 tool_request /
     tool_response 摘要事件（真实工具统一执行路径）；
   - cache 命中不重复捕获结果（每次仅 1 个 request 事件，cached_hit 分支直接 return）；
   - ``build_research_tools`` 签名已支持 ``diagnostics_provider``（K-4 遗留贯通点）；
   - worker ``_build_live_component_factory`` 源码确认把 ``diagnostics_provider`` /
     ``diagnostics_factory`` 传入 build_research_tools / build_flow_runner（K-5 补齐，
     静态断言避免触发 Celery app 构建副作用）。
2. fake Docker 故障注入验收（隔离 fake 栈，不联网）：
   - fake crew 抛 Writer Schema 失败 → 任务到达 failed（LiveFlowExecutionError）；
   - 诊断包落盘（manifest.json / stage_payloads.jsonl / validation_errors.json /
     failure.json），字段顺序可见 Request→ResearchPack→AnalysisPack→Writer Context→
     Writer Response→Validation Error；
   - 敏感字段已脱敏（api_key / authorization / cookie / reasoning_content 绝不在包内）；
   - 启用测试 tracer 时 span 内诊断事件 trace_id 与该任务 flow.run span 一致。
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from invest_research.application.diagnostics.capture import DiagnosticCapture
from invest_research.application.diagnostics.models import (
    DiagnosticCaptureMode,
    DiagnosticCapturePolicy,
    DiagnosticDirection,
    ValidationErrorEntry,
)
from invest_research.application.diagnostics.persistence import build_bundle_files
from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.performance import PerformanceRecorder
from invest_research.infrastructure.real_tools import (
    _cached_execute,
    build_research_tools,
)
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="MSFT",
        as_of_date=date(2025, 1, 1),
        language="zh-CN",
        requested_forms=["10-K"],
    )


def _success(namespace: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        kind="success",
        value=SimpleNamespace(model_dump=lambda mode="json": namespace),
    )


def _make_capture(job_id: uuid.UUID) -> DiagnosticCapture:
    buffer = BoundedDiagnosticBuffer(
        job_id=str(job_id),
        policy=DiagnosticCapturePolicy(capture_mode=DiagnosticCaptureMode.ALL_PAYLOAD),
    )
    return DiagnosticCapture(buffer=buffer)


# ---------------------------------------------------------------------------
# 1a. 生产接线单测：真实工具执行路径产生 tool_request / tool_response 摘要
# ---------------------------------------------------------------------------


def test_cached_execute_produces_tool_request_response_summaries() -> None:
    job_id = uuid.uuid4()
    capture = _make_capture(job_id)
    cache = ToolCallCache()
    recorder = PerformanceRecorder()

    def _run() -> Any:
        return _success(
            {
                "cik": "0000789019",
                "resolved": True,
                "legal_name": "MICROSOFT CORP",
                "api_key": "sk-secret-123",
            }
        )

    _cached_execute(
        cache=cache,
        recorder=recorder,
        tool_name="company_resolver",
        params={"input_company": "MSFT"},
        serialize_fn=lambda r: json.dumps(r.value.model_dump(mode="json"), default=str),
        execute_fn=_run,
        budget=ToolBudget(caps={"company_resolver": 10}),
        diagnostics_provider=lambda: capture,
    )

    events = capture.buffer.events() if capture.buffer is not None else []
    # payload_kind 是 DiagnosticEvent 顶层字段（非 payload 内键）。
    kinds = [ev.payload_kind for ev in events if ev.payload_kind]
    # _cached_execute 产生：request(None) + request(False) + response = 3 事件。
    assert kinds.count("tool_request") >= 2
    assert kinds.count("tool_response") >= 1
    # 结果摘要不包含敏感键（api_key 被脱敏为 ***，不出现明文）。
    payloads = [ev.payload for ev in events if ev.payload is not None]
    joined = json.dumps(payloads, ensure_ascii=False)
    assert "sk-secret-123" not in joined


def test_cached_execute_cache_hit_only_params_no_duplicate_result() -> None:
    """缓存命中：不执行真实工具、不重复捕获结果（每次仅 1 个 request 事件）。"""
    job_id = uuid.uuid4()
    capture = _make_capture(job_id)
    cache = ToolCallCache()
    key = cache.key("web_search", {"query": "q", "as_of": "2025-01-01"})
    cache.put(key, '{"ok": true, "count": 0, "results": []}')

    def _hit_execute() -> int:
        _cached_execute(
            cache=cache,
            recorder=PerformanceRecorder(),
            tool_name="web_search",
            params={"query": "q", "as_of": "2025-01-01"},
            serialize_fn=lambda r: "ok",
            execute_fn=lambda: (_ for _ in ()).throw(
                AssertionError("cache hit must not execute")
            ),
            budget=ToolBudget(caps={"web_search": 10}),
            diagnostics_provider=lambda: capture,
        )
        return len(capture.buffer.events()) if capture.buffer is not None else 0

    # 第一次（缓存命中）：仅开头 request(None) 事件，cached_hit=True 分支直接 return。
    first_count = _hit_execute()
    assert first_count == 1
    # 第二次（仍命中）：同样仅 +1，结果不重复捕获。
    second_count = _hit_execute()
    assert second_count == 2
    events = capture.buffer.events() if capture.buffer is not None else []
    assert all(ev.payload_kind == "tool_request" for ev in events)
    assert not any(ev.payload_kind == "tool_response" for ev in events)


def test_cached_execute_budget_exhausted_params_only() -> None:
    """预算耗尽：不执行真实工具、不捕获结果（稳定错误码由失败 JSON 携带）。"""
    job_id = uuid.uuid4()
    capture = _make_capture(job_id)
    budget = ToolBudget(caps={"web_search": 0})

    _cached_execute(
        cache=ToolCallCache(),
        recorder=PerformanceRecorder(),
        tool_name="web_search",
        params={"query": "q", "as_of": "2025-01-01"},
        serialize_fn=lambda r: "ok",
        execute_fn=lambda: (_ for _ in ()).throw(AssertionError("budget exhausted")),
        budget=budget,
        diagnostics_provider=lambda: capture,
    )
    events = capture.buffer.events() if capture.buffer is not None else []
    assert len(events) == 2
    assert all(ev.payload_kind == "tool_request" for ev in events)
    assert not any(ev.payload_kind == "tool_response" for ev in events)


# ---------------------------------------------------------------------------
# 1b. 生产接线：build_research_tools 签名 + worker 工厂注入点（静态断言）
# ---------------------------------------------------------------------------


def test_build_research_tools_accepts_diagnostics_provider() -> None:
    """build_research_tools 签名已支持 diagnostics_provider（K-4 遗留贯通点）。"""
    sig = inspect.signature(build_research_tools)
    assert "diagnostics_provider" in sig.parameters


def test_worker_component_factory_injects_diagnostics_wiring() -> None:
    """worker ``_build_live_component_factory`` 已把 diagnostics_provider /
    diagnostics_factory / set_job_id 注入工具与 FlowRunner（K-5 补齐，静态断言）。

    import worker 模块会触发 Celery app 构建与 DB 探活（顶层
    ``celery_app = _build_celery_app()``），导致测试卡死在网络超时。
    因此直接读取源码文件文本做确定性断言（零 import 副作用）。
    """
    worker_source = (
        Path(__file__).resolve().parents[1]
        / "src/invest_research/infrastructure/queue/worker.py"
    ).read_text(encoding="utf-8")
    # _build_live_component_factory 内的 build_research_tools 调用必须传 diagnostics_provider。
    assert "diagnostics_provider=diagnostics_provider" in worker_source
    # diagnostics_factory 在共享 state 中产出 capture（与 tools provider 同一 capture）。
    assert "def diagnostics_factory" in worker_source
    # _PerJobFlowRunner.run 必须把 diagnostics_factory 透传给 build_flow_runner。
    assert "diagnostics_factory=self._component_factory.diagnostics_factory" in worker_source
    # set_job_id 每次 run 前刷新诊断 job_id。
    assert "self._component_factory.set_job_id(self.job_id)" in worker_source


# ---------------------------------------------------------------------------
# 2. fake Docker 故障注入全链路（隔离 fake 栈，不联网）
# ---------------------------------------------------------------------------


def _fake_crew_factory(error_stage: str) -> Callable[..., Any]:
    def factory(cfg: Any, tools: list[Any]) -> Any:
        class _Crew:
            tasks: list[Any] = []
            agents: list[Any] = []

            def kickoff(self, inputs: dict[str, Any]) -> Any:
                if error_stage == "writer":
                    raise RuntimeError("writer schema failed")
                return object()

        return _Crew()

    return factory


def test_fake_writer_failure_bundle_redacted_and_traceable(
    tmp_path: Path,
) -> None:
    """fake Writer Schema 失败 → LiveFlowExecutionError → 诊断包落盘 + trace_id 一致。"""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from invest_research.agents.llm_factory import LLMConfig
    from invest_research.infrastructure.flow_wiring import (
        LiveFlowExecutionError,
        LiveResearchFlowRunner,
    )
    from invest_research.infrastructure.observability.tracing import setup_tracing, span

    exporter = InMemorySpanExporter()
    setup_tracing(service_name="test-p06-11k5", exporter=exporter)

    job_id = uuid.uuid4()
    buffer = BoundedDiagnosticBuffer(
        job_id=str(job_id),
        policy=DiagnosticCapturePolicy(capture_mode=DiagnosticCaptureMode.ALL_PAYLOAD),
    )
    capture = DiagnosticCapture(buffer=buffer)

    runner = LiveResearchFlowRunner(
        config=LLMConfig(
            provider="openai_compatible",
            vendor="generic",
            base_url="https://example.invalid",
            api_key=SecretStr("placeholder"),
            model_research="fake",
            model_analysis="fake",
            model_writer="fake",
        ),
        artifact_root=str(tmp_path),
        crew_factory=_fake_crew_factory("writer"),
        diagnostics_factory=lambda: capture,
    )
    runner.job_id = job_id

    with pytest.raises(LiveFlowExecutionError):
        with span("flow.run", {"input_company": "MSFT"}):
            # 预置含敏感字段的事件 + runner 内部 _diag_capture 自动事件
            # 都发生在同一 flow.run span 内（trace_id 一致）。
            trace_id, _ = runner._diag_trace_ids()
            capture.capture(
                stage="02_research",
                component="agent",
                payload_kind="research_inputs",
                data={
                    "input_company": "MSFT",
                    "api_key": "sk-secret-123",
                    "authorization": "Bearer abc",
                    "cookie": "session=xyz",
                    "reasoning_content": "internal reasoning",
                },
                direction=DiagnosticDirection.INPUT,
                trace_id=trace_id,
            )
            runner.run(_request())

    # 诊断包落盘。
    diag_dir = tmp_path / str(job_id) / "diagnostics"
    assert (diag_dir / "manifest.json").exists()
    assert (diag_dir / "stage_payloads.jsonl").exists()
    assert (diag_dir / "validation_errors.json").exists()
    assert (diag_dir / "failure.json").exists()

    # 失败收口事件（exception + finalize_failure）已入缓冲。
    events = buffer.events()
    error_codes = [ev.error_code for ev in events if ev.error_code]
    assert error_codes, "失败任务应至少记录一个稳定 error_code"

    # 敏感字段绝不在包内（读写 stage_payloads 全文校验）。
    stage_text = (diag_dir / "stage_payloads.jsonl").read_text(encoding="utf-8")
    for sensitive in ("sk-secret-123", "Bearer abc", "session=xyz", "internal reasoning"):
        assert sensitive not in stage_text

    # trace_id 与该任务 flow.run span 一致（span 内所有事件同一 trace）。
    spans = exporter.get_finished_spans()
    flow_span = next(s for s in spans if s.name == "flow.run")
    span_ctx = flow_span.get_span_context()
    assert span_ctx is not None and span_ctx.trace_id != 0
    expected_trace = format(span_ctx.trace_id, "032x")
    assert expected_trace != "0" * 32
    traced_events = [ev for ev in events if ev.trace_id is not None]
    assert traced_events, "span 内应至少有一个带 trace_id 的诊断事件"
    for ev in traced_events:
        assert ev.trace_id == expected_trace, (
            f"诊断事件 {ev.sequence}({ev.payload_kind}) trace_id 应与 flow.run 一致"
        )


def test_failure_bundle_sequence_and_validation_error() -> None:
    """阶段序列可见 + Validation Error 落盘（经 build_bundle_files 过滤保留）。"""
    job_id = uuid.uuid4()
    buffer = BoundedDiagnosticBuffer(
        job_id=str(job_id),
        policy=DiagnosticCapturePolicy(capture_mode=DiagnosticCaptureMode.ALL_PAYLOAD),
    )
    capture = DiagnosticCapture(buffer=buffer)
    # 按验收顺序依次捕获各阶段（sequence 升序保证顺序可见）。
    capture.capture(
        stage="02_research",
        component="agent",
        payload_kind="research_inputs",
        data={"input_company": "MSFT"},
        direction=DiagnosticDirection.INPUT,
    )
    capture.capture(
        stage="02_research",
        component="assembler",
        payload_kind="ResearchPack",
        data={"version": "research_pack_v1", "sources": []},
        direction=DiagnosticDirection.OUTPUT,
    )
    capture.capture(
        stage="04_analysis",
        component="agent",
        payload_kind="analysis_inputs",
        data={"input_company": "MSFT"},
        direction=DiagnosticDirection.INPUT,
    )
    capture.capture(
        stage="04_analysis",
        component="assembler",
        payload_kind="FinancialAnalysisPack",
        data={"version": "analysis_pack_v1", "facts": []},
        direction=DiagnosticDirection.OUTPUT,
    )
    capture.capture(
        stage="05_writer",
        component="context_builder",
        payload_kind="writer_context",
        data={"context_chars": 100, "estimated_tokens": 25},
        direction=DiagnosticDirection.INPUT,
    )
    capture.capture(
        stage="05_writer",
        component="llm",
        payload_kind="writer_response_content",
        data={"content_chars": 500, "finish_reason": "stop"},
        direction=DiagnosticDirection.OUTPUT,
    )
    capture.record_validation_error(
        stage="05_writer",
        component="assembler",
        errors=[
            ValidationErrorEntry(
                field="markdown",
                expected="非空 Markdown",
                actual="空",
                error_type="missing",
                message="缺少必需章节",
            )
        ],
    )
    capture.finalize_failure(error_code="SCHEMA_INVALID", failure_stage="05_writer")

    files = build_bundle_files(
        buffer,
        failed=True,
        error_code="SCHEMA_INVALID",
        failure_stage="05_writer",
    )

    # stage_payloads 按 sequence 顺序保留各阶段（顺序可见）。
    payload_lines = files.stage_payloads.decode("utf-8").strip().splitlines()
    payloads = [json.loads(line) for line in payload_lines]
    kinds = [p["payload_kind"] for p in payloads]
    assert kinds == [
        "research_inputs",
        "ResearchPack",
        "analysis_inputs",
        "FinancialAnalysisPack",
        "writer_context",
        "writer_response_content",
    ]

    # validation_errors 落盘。
    validation = json.loads(files.validation_errors.decode("utf-8"))
    assert len(validation["errors"]) == 1
    assert validation["errors"][0]["field"] == "markdown"

    # failure.json 记录稳定错误信息。
    failure = json.loads(files.failure.decode("utf-8"))
    assert failure["finalized"] == "failure"
    assert failure["error_code"] == "SCHEMA_INVALID"
    assert failure["failure_stage"] == "05_writer"
