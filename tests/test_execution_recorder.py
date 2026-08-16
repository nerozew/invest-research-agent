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
    assert steps["06_quality_gate"]["status"] == "failed"
    assert steps["06_quality_gate"]["error_json"] == {"recommendation": "rejected"}
    assert steps["07_manifest"]["status"] == "failed"


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
