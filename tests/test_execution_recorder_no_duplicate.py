"""P06-06B：ExecutionRecorder 与 SqlProgressSink 并存时不重复插入步骤。"""

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
from invest_research.infrastructure.db.models import Base, ResearchJob, WorkflowStep
from invest_research.infrastructure.db.progress import SqlProgressSink
from invest_research.infrastructure.queue.execution_recorder import ExecutionRecorder

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


def test_recorder_does_not_duplicate_steps_created_by_progress_sink(tmp_path) -> None:
    """步骤已由 SqlProgressSink 创建时，recorder.record 不重复插入并补全版本摘要。

    - 总数仍为 8（不重复插入）；
    - 已成功的步骤保留成功状态与时间（不被 recorder 覆盖）；
    - recorder 补全 output_schema_version / output_json（最终工件登记）。
    """
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
                status="running",
                config_snapshot={},
            )
        )
        session.commit()

    # 1. SqlProgressSink 先幂等创建 00-07 并推进一个步骤
    sink = SqlProgressSink(factory)
    sink.initialize_steps(job_id)
    sink.mark_step_running(job_id, "04_analysis")
    sink.mark_step_succeeded(job_id, "04_analysis")

    # 2. ExecutionRecorder 再 record（P06-06B：避免重复插入，改为补全）
    recorder = ExecutionRecorder(factory, str(tmp_path))
    recorder.record(job_id, _passing_state())

    with factory() as session:
        steps = session.query(WorkflowStep).filter_by(job_id=job_id).all()
        assert len(steps) == 8  # 关键：不重复插入
        by_name = {s.step_name: s for s in steps}
        # 已成功的分析步骤保留成功状态与 completed_at（不被 recorder 覆盖）
        assert by_name["04_analysis"].status == "succeeded"
        assert by_name["04_analysis"].completed_at is not None
        # recorder 补全了版本摘要与最终输出
        assert by_name["02_research"].output_schema_version == "research_pack_v1"
        assert by_name["07_manifest"].output_json == {"status": "published"}
