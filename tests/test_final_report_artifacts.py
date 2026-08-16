"""P06-07 前置修复：最终报告工件（08/09）端到端测试。

覆盖：publisher 生成 MD/PDF、API 列表含 08/09、MD 可解码、PDF %PDF-、
Content-Type、幂等登记、合并迁移、非法路径、发布失败不误报成功。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from invest_research.api.app import create_app
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
from invest_research.infrastructure.db.application_stores import (
    SqlArtifactCatalogStore,
    SqlArtifactContentStore,
)
from invest_research.infrastructure.db.models import Artifact as ArtifactORM
from invest_research.infrastructure.db.models import Base, ResearchJob
from invest_research.infrastructure.queue.execution_recorder import ExecutionRecorder
from invest_research.reporting.artifact_publisher import (
    ReportArtifactPublisher,
    ReportArtifactPublishError,
)

_AS_OF = date(2025, 10, 31)


def _request() -> ResearchRequest:
    return ResearchRequest(
        input_company="AAPL",
        as_of_date=_AS_OF,
        language="zh-CN",
        requested_forms=("10-K", "10-Q"),
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
            markdown="# t\n\n## 执行摘要\n执行摘要内容",
            citation_keys=["c1"],
        ),
        quality_report=QualityReport(
            version="quality_report_v1", all_passed=True, recommendation="published"
        ),
        run_manifest={"status": "published"},
    )


def _factory(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _insert_job(factory, job_id: uuid.UUID) -> None:
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


class _FakeChecker:
    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


def _api_client(tmp_path, factory):
    from invest_research.settings import Settings

    store = SqlArtifactContentStore(factory, str(tmp_path))
    catalog = SqlArtifactCatalogStore(factory)
    app = create_app(
        settings=Settings(llm_api_key="test-key", sec_user_agent_contact="test@example.com"),
        health_checker=_FakeChecker(),
        artifact_catalog_store=catalog,
        artifact_content_store=store,
    )
    return TestClient(app)


def test_publisher_generates_md_and_pdf(tmp_path) -> None:
    job_id = uuid.uuid4()
    published = ReportArtifactPublisher(tmp_path).publish(job_id, _passing_state())

    assert [p["artifact_key"] for p in published] == ["08_report.md", "09_report.pdf"]
    assert [p["artifact_type"] for p in published] == [
        "final_report_markdown",
        "final_report_pdf",
    ]
    assert published[0]["storage_uri"] == f"{job_id}/08_report.md"
    assert published[0]["byte_size"] > 0
    assert published[1]["byte_size"] > 0


def test_publisher_markdown_nonempty_decodable(tmp_path) -> None:
    job_id = uuid.uuid4()
    ReportArtifactPublisher(tmp_path).publish(job_id, _passing_state())

    md = (tmp_path / str(job_id) / "08_report.md").read_bytes()
    assert len(md) > 0
    md.decode("utf-8")


def test_publisher_pdf_starts_with_pdf_magic(tmp_path) -> None:
    job_id = uuid.uuid4()
    ReportArtifactPublisher(tmp_path).publish(job_id, _passing_state())

    pdf = (tmp_path / str(job_id) / "09_report.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 100


def test_publisher_missing_draft_raises(tmp_path) -> None:
    state = _passing_state()
    state.report_draft = None
    with pytest.raises(ReportArtifactPublishError):
        ReportArtifactPublisher(tmp_path).publish(uuid.uuid4(), state)


def test_recorder_idempotent_registration(tmp_path) -> None:
    job_id = uuid.uuid4()
    factory = _factory(tmp_path)
    _insert_job(factory, job_id)
    ReportArtifactPublisher(tmp_path).publish(job_id, _passing_state())

    recorder = ExecutionRecorder(factory, str(tmp_path))
    recorder.record(job_id, _passing_state())
    recorder.record(job_id, _passing_state())

    with factory() as session:
        rows = session.execute(
            select(ArtifactORM).where(ArtifactORM.job_id == job_id)
        ).scalars().all()
        keys = [r.artifact_key for r in rows]
    assert len(rows) == len(set(keys))
    assert "08_report.md" in keys
    assert "09_report.pdf" in keys


def test_recorder_merges_legacy_dir(tmp_path) -> None:
    job_id = uuid.uuid4()
    factory = _factory(tmp_path)
    _insert_job(factory, job_id)
    ReportArtifactPublisher(tmp_path).publish(job_id, _passing_state())
    legacy = tmp_path / "AAPL_2025-10-31"
    legacy.mkdir(parents=True)
    (legacy / "07_manifest.json").write_text('{"status": "published"}', encoding="utf-8")

    ExecutionRecorder(factory, str(tmp_path)).record(job_id, _passing_state())

    assert (tmp_path / str(job_id) / "07_manifest.json").is_file()
    assert (tmp_path / str(job_id) / "08_report.md").is_file()
    with factory() as session:
        keys = {
            r.artifact_key
            for r in session.execute(
                select(ArtifactORM).where(ArtifactORM.job_id == job_id)
            ).scalars()
        }
    assert "07_manifest.json" in keys
    assert "09_report.pdf" in keys


def test_api_end_to_end(tmp_path) -> None:
    job_id = uuid.uuid4()
    factory = _factory(tmp_path)
    _insert_job(factory, job_id)
    ReportArtifactPublisher(tmp_path).publish(job_id, _passing_state())
    # 07_manifest.json 由 Flow 中间产物落盘（Publisher 只产 08/09）
    job_dir = tmp_path / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "07_manifest.json").write_text('{"status": "published"}', encoding="utf-8")
    ExecutionRecorder(factory, str(tmp_path)).record(job_id, _passing_state())
    client = _api_client(tmp_path, factory)

    listing = client.get(f"/v1/research-jobs/{job_id}/artifacts")
    assert listing.status_code == 200
    keys = [a["artifact_key"] for a in listing.json()]
    assert "08_report.md" in keys
    assert "09_report.pdf" in keys

    md = client.get(f"/v1/research-jobs/{job_id}/artifacts/08_report.md")
    assert md.status_code == 200
    assert md.headers["content-type"] == "text/markdown; charset=utf-8"
    md.content.decode("utf-8")

    pdf = client.get(f"/v1/research-jobs/{job_id}/artifacts/09_report.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")

    manifest = client.get(f"/v1/research-jobs/{job_id}/artifacts/07_manifest.json")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"] == "application/json"


def test_api_rejects_path_traversal(tmp_path) -> None:
    factory = _factory(tmp_path)
    client = _api_client(tmp_path, factory)
    resp = client.get(
        f"/v1/research-jobs/{uuid.uuid4()}/artifacts/%2e%2e%5csecret",
        follow_redirects=False,
    )
    assert resp.status_code in (400, 404)
