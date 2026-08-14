"""company / job / step ORM 模型（P01-08）。

对齐 docs/03-DATABASE.md §4 DDL 基线：companies、research_jobs、workflow_steps。
仅作映射；业务状态校验在 domain 层（JobStatus/StepStatus）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from invest_research.infrastructure.db.base import Base

# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 公司 / 任务 / 步骤（P01-08）
# ----------------------------------------------------------------


class Company(Base):
    """companies：规范化公司身份（CIK 唯一且 10 位）。"""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cik: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    legal_name: Mapped[str] = mapped_column(String(300), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sic: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fiscal_year_end: Mapped[str | None] = mapped_column(String(4), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ResearchJob(Base):
    """research_jobs：用户请求与整体状态。"""

    __tablename__ = "research_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id"), nullable=True
    )
    input_company: Mapped[str] = mapped_column(String(200), nullable=False)
    input_ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="zh-CN")
    requested_forms: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    current_step: Mapped[str | None] = mapped_column(String(50), nullable=True)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowStep(Base):
    """workflow_steps：工作流每一步的状态与结构化 I/O。"""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "step_name", name="uq_workflow_steps_job_step"),
        UniqueConstraint("job_id", "sequence_no", name="uq_workflow_steps_job_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(50), nullable=False)
    sequence_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# 来源 / 申报 / 文档（P01-09）
# ----------------------------------------------------------------


class Source(Base):
    """sources：外部来源的规范化目录。"""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobSource(Base):
    """job_sources：任务与来源多对多。"""

    __tablename__ = "job_sources"
    __table_args__ = (UniqueConstraint("job_id", "source_id", "purpose", name="pk_job_sources"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="RESTRICT"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(String(50), primary_key=True)
    relevance_score: Mapped[float | None] = mapped_column(nullable=True)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Filing(Base):
    """filings：SEC 申报元数据。"""

    __tablename__ = "filings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("companies.id"), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    form_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    primary_document_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sources.id"), nullable=True
    )
    is_amendment: Mapped[bool] = mapped_column(nullable=False, default=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    """documents：下载文档与解析状态。"""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "content_checksum",
            "parser_name",
            "parser_version",
            name="uq_documents_checksum_parser",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sources.id"), nullable=False)
    filing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("filings.id"), nullable=True
    )
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    parser_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


class DocumentChunk(Base):
    """document_chunks：可检索的文本块。"""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_doc_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


class FinancialFact(Base):
    """financial_facts：XBRL 原始财务事实。"""

    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "source_id",
            "taxonomy",
            "concept",
            "unit",
            "period_start",
            "period_end",
            "accession_number",
            "fact_version",
            name="uq_financial_facts_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("companies.id"), nullable=False)
    filing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("filings.id"), nullable=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sources.id"), nullable=False)
    taxonomy: Mapped[str] = mapped_column(String(100), nullable=False)
    concept: Mapped[str] = mapped_column(String(300), nullable=False)
    label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    value: Mapped[Decimal] = mapped_column(Numeric(38, 10), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    instant_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(20), nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    frame: Mapped[str | None] = mapped_column(String(50), nullable=True)
    accession_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fact_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


class ComputedMetric(Base):
    """computed_metrics：版本化派生指标。"""

    __tablename__ = "computed_metrics"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "metric_name",
            "period_end",
            "formula_version",
            name="uq_computed_metrics_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(38, 10), nullable=True)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(50), nullable=False)
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    """reports：最终报告版本。"""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("job_id", "version", name="uq_reports_job_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    markdown_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    pdf_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Citation(Base):
    """citations：报告 claim 与 source 的映射。"""

    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "claim_key",
            "source_id",
            "locator",
            name="uq_citations_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    claim_key: Mapped[str] = mapped_column(String(200), nullable=False)
    section_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sources.id"), nullable=False)
    locator: Mapped[str | None] = mapped_column(String(500), nullable=True)
    supports_claim: Mapped[bool | None] = mapped_column(nullable=True)
    validation_message: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 审计 / 工件（P01-11）
# 对齐 docs/03-DATABASE.md §4：tool_invocations、artifacts
# 幂等唯一约束：invocation_key 唯一；artifact_key 唯一。
# ---------------------------------------------------------------------------


class ToolInvocation(Base):
    """tool_invocations：工具调用审计（幂等键防重复）。"""

    __tablename__ = "tool_invocations"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "tool_name",
            "invocation_key",
            "attempt_no",
            name="uq_tool_invocations_power_idempotent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    invocation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    request_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    """artifacts：中间工件 manifest（job 内 artifact_key 唯一）。"""

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("job_id", "artifact_key", name="uq_artifacts_job_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_steps.id"), nullable=True
    )
    artifact_key: Mapped[str] = mapped_column(String(200), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKeyRow(Base):
    """idempotency_keys：Idempotency-Key → 已创建任务（P04-10A 生产幂等池）。

    - key 唯一：数据库 UNIQUE 约束兜底，防止并发下同一 key 创建两个任务；
    - request_fingerprint：请求体规范化指纹，判断同 key 是否同一请求（同请求复用，
      异请求 409）；
    - status 冗余保存创建时刻任务状态，供幂等复用返回。
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("key", name="uq_idempotency_keys_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
