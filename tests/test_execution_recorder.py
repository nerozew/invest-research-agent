"""P05.5-opt ExecutionRecorder 测试（步骤推导 + 工件移动/登记，不联网）。"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    QualityReport,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.flows.state import ResearchFlowState
from invest_research.infrastructure.db.models import (
    Artifact,
    Base,
    ResearchJob,
    WorkflowStep,
)
from invest_research.infrastructure.queue.execution_recorder import (
    ExecutionRecorder,
    derive_steps,
    merge_artifacts_to_job_dir,
)

_AS_OF = date(2025, 10, 31)


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="AAPL", as_of_date=_AS_OF, language="zh-CN", requested_forms=("10-K", "10-Q")
    )


def _passing_state() -> ResearchFlowState:
    identity = CompanyIdentity(cik="0000320193", ticker="AAPL", legal_name="APPLE INC")
    return ResearchFlowState(
        request=_request(),
        company_identity=identity,
        research_pack=ResearchPack(
            version="research_pack_v1",
            company_identity=identity,
            as_of_date=_AS_OF,
            sources=[
                Source(
                    source_type=SourceType.SEC_FILING,
                    canonical_url="https://www.sec.gov/10k.htm",
                    title="10-K",
                    accessed_at=_AS_OF,
                )
            ],
        ),
        document_manifest={"documents": []},
        analysis_pack=FinancialAnalysisPack(
            version="analysis_pack_v1",
            period_end=date(2025, 9, 27),
            facts=[
                FinancialFact(
                    company_id="0000320193",
                    source_id="s1",
                    taxonomy="us-gaap",
                    concept="Revenue",
                    value=Decimal("391000000000"),
                    unit="USD",
                    period_start=date(2024, 9, 29),
                    period_end=date(2025, 9, 27),
                )
            ],
        ),
        report_draft=ReportDraft(
            version="report_draft_v1",
            title="t",
            markdown="# t",
            citation_keys=["c1"],
        ),
        quality_report=QualityReport(
            version="quality_report_v1", all_passed=True, recommendation="published"
        ),
        run_manifest={"status": "published"},
    )


def test_derive_steps_all_succeeded_on_passing_state() -> None:
    steps = derive_steps(_passing_state())
    assert len(steps) == 8
    assert all(s["status"] == "succeeded" for s in steps)
    names = [s["step_name"] for s in steps]
    assert names == [
        "00_request",
        "01_company_resolve",
        "02_research",
        "03_documents",
        "04_analysis",
        "05_writer",
        "06_quality_gate",
        "07_manifest",
    ]


def test_derive_steps_marks_gate_and_manifest_failed_on_reject() -> None:
    state = _passing_state()
    state.quality_report = QualityReport(
        version="quality_report_v1", all_passed=False, recommendation="rejected"
    )
    state.run_manifest = {"status": "rejected"}
    steps = {s["step_name"]: s for s in derive_steps(state)}
    # StepStatus 无字面 "failed"，写入端用终态失败值（避免 API 读取 500）。
    assert steps["06_quality_gate"]["status"] == "failed_terminal"
    assert steps["06_quality_gate"]["error_json"] == {"recommendation": "rejected"}
    assert steps["07_manifest"]["status"] == "failed_terminal"


def test_merge_artifacts_to_job_dir(tmp_path) -> None:
    root = tmp_path / "artifacts"
    src = root / "AAPL_2025-10-31"
    src.mkdir(parents=True)
    (src / "00_request.json").write_text("{}", encoding="utf-8")
    (src / "07_manifest.json").write_text('{"status":"published"}', encoding="utf-8")

    job_id = uuid.uuid4()
    artifacts = merge_artifacts_to_job_dir(str(root), job_id, _request())

    # 文件合并迁移到 <job_id> 下（旧目录保留，只移文件；不删除避免误删）
    assert (root / str(job_id) / "00_request.json").is_file()
    assert src.exists()
    assert not (src / "00_request.json").exists()
    # 只登记存在的两个文件，且带 checksum/byte_size
    keys = [a["artifact_key"] for a in artifacts]
    assert keys == ["00_request.json", "07_manifest.json"]
    for a in artifacts:
        assert a["content_checksum"]
        assert a["byte_size"] > 0
        assert a["storage_uri"] == f"{job_id}/{a['artifact_key']}"


def test_merge_artifacts_registers_annual_derivatives_but_not_raw_sec_source(tmp_path) -> None:
    root = tmp_path / "artifacts"
    job_id = uuid.uuid4()
    job_dir = root / str(job_id)
    (job_dir / "annual/company-facts").mkdir(parents=True)
    (job_dir / "annual/target").mkdir(parents=True)
    (job_dir / "annual/runtime_state.json").write_text("{}", encoding="utf-8")
    (job_dir / "annual/company-facts/selected.json").write_text("{}", encoding="utf-8")
    (job_dir / "annual/target/parsed.json").write_text("raw filing derivative", encoding="utf-8")

    artifacts = merge_artifacts_to_job_dir(str(root), job_id, _request())

    assert {item["artifact_key"] for item in artifacts} == {
        "annual/runtime_state.json",
        "annual/company-facts/selected.json",
    }


def test_execution_recorder_inserts_steps_and_artifacts(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    job_id = uuid.uuid4()
    with factory() as session:
        session.add(
            ResearchJob(
                id=job_id,
                input_company="AAPL",
                as_of_date=_AS_OF,
                language="zh-CN",
                requested_forms=["10-K", "10-Q"],
                status="succeeded",
                config_snapshot={},
            )
        )
        session.commit()

    # 构造磁盘产物目录（live runner 的布局：company_as_of）
    root = tmp_path / "artifacts"
    src = root / "AAPL_2025-10-31"
    src.mkdir(parents=True)
    for key in (
        "00_request.json",
        "02_research_pack.json",
        "04_financial_analysis_pack.json",
        "05_report_draft.json",
        "06_quality_report.json",
        "07_manifest.json",
    ):
        (src / key).write_text("{}", encoding="utf-8")

    recorder = ExecutionRecorder(factory, str(root))
    recorder.record(job_id, _passing_state())

    with factory() as session:
        steps = session.query(WorkflowStep).filter_by(job_id=job_id).all()
        assert len(steps) == 8
        artifacts = session.query(Artifact).filter_by(job_id=job_id).all()
        assert len(artifacts) == 6
        assert (root / str(job_id) / "07_manifest.json").is_file()


def test_legacy_failed_step_status_is_readable_via_query_store(tmp_path) -> None:
    """回归：历史 execution_recorder 把失败步骤写成字面 ``"failed"``，而
    ``StepStatus`` 只有 ``failed_retryable``/``failed_terminal``，直接读取会
    ValueError 导致 API GET 500。SqlJobQueryStore 必须兼容映射为终态失败。"""
    from invest_research.domain.status import StepStatus
    from invest_research.infrastructure.db.application_stores import SqlJobQueryStore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    job_id = uuid.uuid4()
    with factory() as session:
        job = ResearchJob(
            id=job_id,
            input_company="AAPL",
            as_of_date=_AS_OF,
            language="zh-CN",
            requested_forms=["10-K", "10-Q"],
            status="succeeded",
            config_snapshot={},
        )
        session.add(job)
        session.flush()
        session.add(
            WorkflowStep(
                job_id=job_id,
                sequence_no=3,
                step_name="03_documents",
                status="failed",  # 历史遗留非法字面值
                attempt_count=1,
            )
        )
        session.commit()

    snapshot = SqlJobQueryStore(factory).get(job_id)
    assert snapshot is not None
    step = next(s for s in snapshot.steps if s.step_name == "03_documents")
    assert step.status == StepStatus.FAILED_TERMINAL
