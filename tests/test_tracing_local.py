"""P06-05 OpenTelemetry 本地配置与链路测试（离线：不联网、不启动 collector）。

验证目标（docs/05 P06-05）：
- 导出器选择：无 endpoint → 控制台；配置 endpoint → OTLP HTTP；
- span 层级可关联：api.request → worker.process → flow.run → tool.* 共享
  trace_id，父子关系正确（进程内模拟跨层传播）；
- 真实代码路径已打点：API middleware（api.request）、Celery task
  （worker.process）、LiveResearchFlowRunner（flow.run）、工具计时包装（tool.*）；
- 本地 collector 配置文件（deploy/otel-collector.yaml）可解析且结构正确。
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from invest_research.agents.llm_factory import LLMConfig
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.infrastructure.observability.tracing import (
    build_span_exporter,
    setup_tracing,
    span,
    trace_id_from_context,
)
from invest_research.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def memory_exporter() -> InMemorySpanExporter:
    """把全局 tracer provider 换成内存导出（setup_tracing 支持重复配置，
    最后一次调用生效；每次测试独立 exporter，互不污染）。"""
    exporter = InMemorySpanExporter()
    setup_tracing(exporter=exporter)
    yield exporter
    exporter.clear()


# ---- 1. 导出器配置 ----

def test_build_exporter_default_is_console() -> None:
    assert isinstance(build_span_exporter(None), ConsoleSpanExporter)


def test_build_exporter_with_endpoint_is_otlp() -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    assert isinstance(build_span_exporter("http://localhost:4318"), OTLPSpanExporter)


# ---- 2. span 层级（API → Worker → Flow → Tool 可关联）----

def test_span_hierarchy_shares_trace_id(memory_exporter: InMemorySpanExporter) -> None:
    """进程内模拟跨层传播：4 层 span 共享同一 trace_id 且父子链正确。"""
    with span("api.request", {"http.method": "POST"}):
        with span("worker.process", {"job_id": "job-1"}):
            with span("flow.run", {"input_company": "AAPL"}):
                with span("tool.sec_submissions", {"tool.name": "sec_submissions"}):
                    pass

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 4
    by_name = {s.name: s for s in spans}
    assert set(by_name) == {"api.request", "worker.process", "flow.run", "tool.sec_submissions"}

    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1  # 同一条 trace

    api = by_name["api.request"]
    worker = by_name["worker.process"]
    flow = by_name["flow.run"]
    tool = by_name["tool.sec_submissions"]
    assert worker.parent.span_id == api.context.span_id
    assert flow.parent.span_id == worker.context.span_id
    assert tool.parent.span_id == flow.context.span_id
    # 属性透传（低基数）
    assert by_name["flow.run"].attributes["input_company"] == "AAPL"


def test_trace_id_available_within_span(memory_exporter: InMemorySpanExporter) -> None:
    assert trace_id_from_context() is None  # 无 span 时为 None
    with span("probe"):
        assert trace_id_from_context() is not None


# ---- 3. 真实代码路径打点 ----

def _settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="sk-test",
        sec_user_agent_contact="test@example.com",
    )


def _fake_outputs(as_of: date) -> SimpleNamespace:
    identity = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")
    research = ResearchPack(
        version="research_pack_v1",
        company_identity=identity,
        as_of_date=as_of,
        sources=[
            Source(
                source_type=SourceType.SEC_FILING,
                canonical_url="https://example.com/filing",
                title="Latest 10-K",
                accessed_at=as_of,
            )
        ],
    )
    analysis = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=as_of,
        facts=[
            FinancialFact(
                company_id="0000789019",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue",
                value=100,
                unit="USD",
                period_start=date(as_of.year - 1, 1, 1),
                period_end=as_of,
            )
        ],
    )
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft Corp 报告",
        markdown=(
            "# t\n\n## 执行摘要\n内容\n## 公司与业务概览\n内容\n"
            "## 财务表现\n内容\n## 风险\n内容\n## 数据限制\n内容\n"
            "## 非投资建议\n内容"
        ),
        citation_keys=["c1"],
    )
    return SimpleNamespace(tasks_output=[research, analysis, draft])


def test_flow_run_span_from_live_runner(
    memory_exporter: InMemorySpanExporter,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """LiveResearchFlowRunner.run 产生 flow.run span 且带请求属性。"""
    from invest_research.infrastructure.flow_wiring import LiveResearchFlowRunner

    class _FakeCrew:
        def kickoff(self, inputs=None):  # type: ignore[no-untyped-def]
            return _fake_outputs(date(2025, 12, 31))

    runner = LiveResearchFlowRunner(
        config=LLMConfig.from_settings(_settings()),
        artifact_root=str(tmp_path_factory.mktemp("trace_artifacts")),
        crew_factory=lambda cfg, rt: _FakeCrew(),  # type: ignore[no-any-return]
    )
    runner.run(ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31)))

    flow_spans = [s for s in memory_exporter.get_finished_spans() if s.name == "flow.run"]
    assert len(flow_spans) == 1
    attrs = flow_spans[0].attributes
    assert attrs is not None
    assert attrs["input_company"] == "MSFT"
    assert attrs["as_of_date"] == "2025-12-31"


def test_worker_task_span(memory_exporter: InMemorySpanExporter) -> None:
    """Celery task 执行产生 worker.process span 且带 job_id。"""
    from invest_research.infrastructure.queue.celery_app import create_celery_app
    from invest_research.infrastructure.queue.tasks import (
        TASK_PROCESS_JOB,
        register_tasks,
    )

    seen: list[str] = []

    class _FakeHandler:
        def process(self, job_id: uuid.UUID) -> None:
            seen.append(str(job_id))

    app = create_celery_app(broker_url="memory://")
    register_tasks(app, _FakeHandler())  # type: ignore[arg-type]
    job_id = uuid.uuid4()
    result = app.tasks[TASK_PROCESS_JOB].run(str(job_id))

    assert result == str(job_id)
    assert seen == [str(job_id)]
    worker_spans = [s for s in memory_exporter.get_finished_spans() if s.name == "worker.process"]
    assert len(worker_spans) == 1
    assert worker_spans[0].attributes is not None
    assert worker_spans[0].attributes["job_id"] == str(job_id)


def test_tool_span_from_timed_wrapper(memory_exporter: InMemorySpanExporter) -> None:
    """工具执行包装产生 tool.<name> span（recorder 为 None 时也打点）。"""
    from invest_research.infrastructure.real_tools import _timed

    with _timed(None, "sec_submissions"):
        pass

    tool_spans = [
        s for s in memory_exporter.get_finished_spans() if s.name == "tool.sec_submissions"
    ]
    assert len(tool_spans) == 1
    assert tool_spans[0].attributes is not None
    assert tool_spans[0].attributes["tool.name"] == "sec_submissions"


def test_api_middleware_span(memory_exporter: InMemorySpanExporter) -> None:
    """API middleware 为每个 HTTP 请求产生 api.request span。"""
    from fastapi.testclient import TestClient

    from invest_research.api.app import create_app

    app = create_app(settings=_settings())
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200

    api_spans = [s for s in memory_exporter.get_finished_spans() if s.name == "api.request"]
    assert len(api_spans) == 1
    attrs = api_spans[0].attributes
    assert attrs is not None
    assert attrs["http.method"] == "GET"
    assert attrs["http.route"] == "/health"


# ---- 4. collector 配置文件 ----

def test_collector_config_parses() -> None:
    """deploy/otel-collector.yaml 可解析且结构正确（本地链路查看配置）。"""
    import yaml

    path = PROJECT_ROOT / "deploy" / "otel-collector.yaml"
    assert path.exists()
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config is not None

    http = config["receivers"]["otlp"]["protocols"]["http"]
    assert http["endpoint"] == "0.0.0.0:4318"
    traces = config["service"]["pipelines"]["traces"]
    assert "otlp" in traces["receivers"]
    assert "debug" in traces["exporters"]
