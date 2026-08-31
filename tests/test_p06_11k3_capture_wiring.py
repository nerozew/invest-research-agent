"""P06-11K-3：核心阶段交接接线（fake 三类失败收口）精简测试。"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig
from invest_research.application.diagnostics.capture import DiagnosticCapture
from invest_research.application.diagnostics.models import (
    DiagnosticCaptureMode,
    DiagnosticCapturePolicy,
)
from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer
from invest_research.domain.models import CompanyIdentity, ResearchRequest
from invest_research.infrastructure.flow_wiring import (
    LiveFlowExecutionError,
    LiveResearchFlowRunner,
)


def _config() -> LLMConfig:
    return LLMConfig(
        provider="openai_compatible",
        vendor="generic",
        base_url="https://example.invalid",
        api_key=SecretStr("placeholder"),
        model_research="fake",
        model_analysis="fake",
        model_writer="fake",
    )


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="MSFT",
        as_of_date=date(2025, 1, 1),
        language="zh-CN",
        requested_forms=["10-K"],
    )


def _identity() -> CompanyIdentity:
    return CompanyIdentity(
        ticker="MSFT", cik="0000789019", legal_name="Microsoft Corporation", exchange="NASDAQ"
    )


def _fake_crew_factory(error_stage: str):
    def factory(cfg, tools):
        class _Crew:
            tasks = []
            agents = []

            def kickoff(self, inputs):
                if error_stage == "research":
                    raise RuntimeError("research failed")
                return object()

        return _Crew()

    return factory


def _make_runner(tmp_path: Path, error_stage: str) -> LiveResearchFlowRunner:
    job_id = uuid.uuid4()
    buffer = BoundedDiagnosticBuffer(
        job_id=str(job_id),
        policy=DiagnosticCapturePolicy(capture_mode=DiagnosticCaptureMode.ALL_PAYLOAD),
    )
    diagnostics = DiagnosticCapture(buffer=buffer)
    runner = LiveResearchFlowRunner(
        config=_config(),
        artifact_root=str(tmp_path),
        crew_factory=_fake_crew_factory(error_stage),
        diagnostics_factory=lambda: diagnostics,
    )
    runner.job_id = job_id
    return runner


def test_diagnostics_off_noop(tmp_path: Path) -> None:
    job_id = uuid.uuid4()
    runner = LiveResearchFlowRunner(config=_config(), artifact_root=str(tmp_path))
    runner.job_id = job_id
    # 不注入 diagnostics_factory → None，run 收口也应静默不抛
    with pytest.raises(RuntimeError):
        runner.run(_request())


def _assert_bundle_exists(runner: LiveResearchFlowRunner, tmp_path: Path) -> Path:
    diag = tmp_path / str(runner.job_id) / "diagnostics"
    assert (diag / "manifest.json").exists()
    assert (diag / "failure.json").exists()
    return diag


def test_fake_research_failure_bundle(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, "research")
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    _assert_bundle_exists(runner, tmp_path)


def test_fake_analysis_failure_bundle(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, "analysis")
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    _assert_bundle_exists(runner, tmp_path)


def test_fake_writer_failure_bundle(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path, "writer")
    with pytest.raises(LiveFlowExecutionError):
        runner.run(_request())
    _assert_bundle_exists(runner, tmp_path)


def test_pack_handoff_traceable(tmp_path: Path) -> None:
    """Research→Analysis→Writer 交接序列可追踪（fake success 路径留到 K-5 全链）。"""
    job_id = uuid.uuid4()
    buffer = BoundedDiagnosticBuffer(
        job_id=str(job_id),
        policy=DiagnosticCapturePolicy(capture_mode=DiagnosticCaptureMode.ALL_PAYLOAD),
    )
    diag = DiagnosticCapture(buffer=buffer)
    cap = diag.capture(
        stage="02_research",
        component="assembler",
        payload_kind="ResearchPack",
        data={"company": "MSFT", "api_key": "sk-abc"},
    )
    assert cap is not None
    assert cap.data["company"] == "MSFT"
    assert cap.data["api_key"] == "***"
