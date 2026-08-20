"""P06-11J: offline observability acceptance (compact)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from invest_research.infrastructure.observability.execution_timeline import ExecutionTimelineSink
from invest_research.infrastructure.observability.llm_full_observer import LlmFullObserver
from invest_research.infrastructure.observability.stage_tracing import stage_span

CFG = SimpleNamespace(vendor="deepseek", base_url=None, role_overrides={})


def _evt(**kw: object) -> SimpleNamespace:
    return SimpleNamespace(**kw)


@pytest.fixture()
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    from opentelemetry import trace
    from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE  # noqa: PLC2701

    _TRACER_PROVIDER_SET_ONCE._done = False
    trace.set_tracer_provider(provider)
    return exporter


class TestTimeline:
    def test_sequence_sanitized(self, tmp_path: Path) -> None:
        sink = ExecutionTimelineSink(path=tmp_path / "10_execution_timeline.jsonl")
        sink.record(stage="stage.research", event_type="start", role="research")
        sink.record(stage="stage.research", event_type="completed", status="success")
        sink.close()
        lines = (tmp_path / "10_execution_timeline.jsonl").read_text(encoding="utf-8")
        data = [json.loads(x) for x in lines.strip().splitlines()]
        assert [d["sequence"] for d in data] == [1, 2]
        assert "api_key" not in lines.lower()


class TestLlm:
    def test_started_completed_same_span(self, memory_exporter: InMemorySpanExporter) -> None:
        obs = LlmFullObserver(CFG, agent_roles={"a1": "research"})
        obs._on_llm_started(
            None, _evt(type="start", model="m", agent_id="a1", agent_role="research")
        )
        assert len(obs._llm_pending["research"]) == 1
        obs._on_llm_completed(
            None,
            _evt(
                type="completed",
                model="m",
                agent_id="a1",
                agent_role="research",
                response=SimpleNamespace(content="ok", usage=None),
                call_type="llm_call",
            ),
        )
        assert len(obs._llm_pending["research"]) == 0
        spans = [s for s in memory_exporter.get_finished_spans() if s.name == "llm.request"]
        assert len(spans) == 1
        assert spans[0].attributes.get("llm.status") == "success"

    def test_event_callback_span_keeps_captured_stage_parent(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        """CrewAI 回调脱离当前 Context 后，LLM span 仍属于创建时的 stage trace。"""
        with stage_span("stage.research"):
            obs = LlmFullObserver(CFG, agent_roles={"a1": "research"})
        obs._on_llm_started(
            None, _evt(type="start", model="m", agent_id="a1", agent_role="research")
        )
        obs._on_llm_completed(
            None,
            _evt(
                type="completed",
                model="m",
                agent_id="a1",
                agent_role="research",
                response=SimpleNamespace(content="ok", usage=None),
                call_type="llm_call",
            ),
        )
        spans = memory_exporter.get_finished_spans()
        stage = next(span for span in spans if span.name == "stage.research")
        llm = next(span for span in spans if span.name == "llm.request")
        assert llm.parent.span_id == stage.context.span_id
        assert llm.context.trace_id == stage.context.trace_id


class TestStage:
    def test_parent_child(self, memory_exporter: InMemorySpanExporter) -> None:
        with stage_span("stage.research"):
            with stage_span("pack.research.validate"):
                pass
        spans = memory_exporter.get_finished_spans()
        research = [s for s in spans if s.name == "stage.research"]
        pack = [s for s in spans if s.name == "pack.research.validate"]
        assert pack[0].parent.span_id == research[0].context.span_id

    def test_failure_skips_following(self, memory_exporter: InMemorySpanExporter) -> None:
        with pytest.raises(RuntimeError):
            with stage_span("stage.research"):
                raise RuntimeError("boom")
            with stage_span("stage.analysis"):
                pass
        names = {s.name for s in memory_exporter.get_finished_spans()}
        assert "stage.analysis" not in names


class TestTool:
    def test_tool_span(self, memory_exporter: InMemorySpanExporter) -> None:
        obs = LlmFullObserver(CFG, agent_roles={})
        obs._on_tool_started(None, _evt(type="start", tool_name="WriterContextReader"))
        obs._on_tool_finished(
            None,
            _evt(type="finished", tool_name="WriterContextReader", output="tiny", from_cache=False),
        )
        spans = [
            s for s in memory_exporter.get_finished_spans()
            if s.name == "crewai.tool.WriterContextReader"
        ]
        assert len(spans) == 1
        assert spans[0].attributes.get("status") == "success"

    def test_tool_callback_span_keeps_captured_stage_parent(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        with stage_span("stage.analysis"):
            obs = LlmFullObserver(CFG, agent_roles={})
        obs._on_tool_started(None, _evt(type="start", tool_name="FinancialCalculator"))
        obs._on_tool_finished(
            None,
            _evt(
                type="finished",
                tool_name="FinancialCalculator",
                output="ok",
                from_cache=False,
            ),
        )
        spans = memory_exporter.get_finished_spans()
        stage = next(span for span in spans if span.name == "stage.analysis")
        tool = next(span for span in spans if span.name == "crewai.tool.FinancialCalculator")
        assert tool.parent.span_id == stage.context.span_id
        assert tool.context.trace_id == stage.context.trace_id


class TestGrafana:
    def test_queries_real_metrics(self) -> None:
        p = Path(r"deploy/grafana/provisioning/dashboards/research.json")
        raw = p.read_text(encoding="utf-8")
        json.loads(raw)
        for metric in [
            "llm_response_kind_total",
            "crewai_tool_calls_total",
            "agent_max_iteration_total",
            "writer_output_chars",
            "report_invalid_total",
            "stage_duration_seconds",
        ]:
            assert metric in raw
