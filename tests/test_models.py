"""P01-02 ResearchRequest / CompanyIdentity 模型校验测试。"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invest_research.domain.models import (
    CompanyIdentity,
    Document,
    Filing,
    FinancialAnalysisPack,
    FinancialFact,
    JobSource,
    MetricResult,
    MetricStatus,
    ParseStatus,
    QualityReport,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)

TODAY = date.today()


# ---------------------------------------------------------------------------
# ResearchRequest：正常构建
# ---------------------------------------------------------------------------


def test_research_request_builds_with_defaults() -> None:
    """默认语言 zh-CN、默认 forms (10-K, 10-Q)。"""
    req = ResearchRequest(input_company=" Microsoft ", as_of_date=TODAY)
    assert req.input_company == "Microsoft"  # 首尾空白被去除
    assert req.language == "zh-CN"
    assert req.requested_forms == ("10-K", "10-Q")


def test_research_request_accepts_english_language() -> None:
    """语言可以显式配置为 en。"""
    req = ResearchRequest(input_company="MSFT", as_of_date=TODAY, language="en")
    assert req.language == "en"


# ---------------------------------------------------------------------------
# ResearchRequest：空输入边界
# ---------------------------------------------------------------------------


def test_research_request_empty_company_rejected() -> None:
    """空字符串被拒绝。"""
    with pytest.raises(ValidationError):
        ResearchRequest(input_company="", as_of_date=TODAY)


def test_research_request_whitespace_company_rejected() -> None:
    """纯空白输入被拒绝（strip 后为空）。"""
    with pytest.raises(ValidationError):
        ResearchRequest(input_company="   ", as_of_date=TODAY)


# ---------------------------------------------------------------------------
# ResearchRequest：日期边界
# ---------------------------------------------------------------------------


def test_research_request_future_as_of_rejected() -> None:
    """未来 as_of_date 被拒绝。"""
    with pytest.raises(ValidationError):
        ResearchRequest(input_company="AAPL", as_of_date=TODAY + timedelta(days=1))


def test_research_request_today_allowed() -> None:
    """今天作为 as_of_date 合法。"""
    req = ResearchRequest(input_company="AAPL", as_of_date=TODAY)
    assert req.as_of_date == TODAY


# ---------------------------------------------------------------------------
# ResearchRequest：语言边界
# ---------------------------------------------------------------------------


def test_research_request_unsupported_language_rejected() -> None:
    """不支持的语言（如 ja）被拒绝。"""
    with pytest.raises(ValidationError):
        ResearchRequest(input_company="MSFT", as_of_date=TODAY, language="ja")


def test_research_request_empty_forms_rejected() -> None:
    """requested_forms 空元组被拒绝。"""
    with pytest.raises(ValidationError):
        ResearchRequest(
            input_company="MSFT",
            as_of_date=TODAY,
            requested_forms=(),
        )


# ---------------------------------------------------------------------------
# ResearchRequest：frozen 不可变
# ---------------------------------------------------------------------------


def test_research_request_is_frozen() -> None:
    """frozen=True：尝试修改字段抛异常。"""
    req = ResearchRequest(input_company="MSFT", as_of_date=TODAY)
    with pytest.raises(ValueError):
        req.input_company = "AAPL"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CompanyIdentity：CIK 校验
# ---------------------------------------------------------------------------


def test_company_identity_valid_cik() -> None:
    """10 位数字 CIK 合法。"""
    company = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corporation")
    assert company.cik == "0000789019"
    assert company.ticker is None  # 可选字段默认 None


def test_company_identity_short_cik_rejected() -> None:
    """少于 10 位 CIK 被拒绝。"""
    with pytest.raises(ValidationError):
        CompanyIdentity(cik="789019", legal_name="Microsoft")


def test_company_identity_non_numeric_cik_rejected() -> None:
    """含非数字字符的 CIK 被拒绝。"""
    with pytest.raises(ValidationError):
        CompanyIdentity(cik="ABC7890190", legal_name="Microsoft")


def test_company_identity_blank_legal_name_rejected() -> None:
    """纯空白法定名称被拒绝。"""
    with pytest.raises(ValidationError):
        CompanyIdentity(cik="0000789019", legal_name="   ")


def test_company_identity_is_frozen() -> None:
    """CompanyIdentity 同样不可变。"""
    company = CompanyIdentity(cik="0000789019", legal_name="Microsoft Corporation")
    with pytest.raises(ValueError):
        company.cik = "0000000001"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 来源 / 申报 / 文档模型（P01-03）：JSON round-trip
# ---------------------------------------------------------------------------


def test_source_json_round_trip() -> None:
    """Source 序列化后反序列化应无损还原。"""
    source = Source(
        source_type=SourceType.SEC_FILING,
        canonical_url="https://www.sec.gov/Archives/edgar/data/789019/0001564590",
        title="Microsoft 10-K",
        publisher="SEC",
        accessed_at=date(2026, 8, 11),
        content_checksum="abc123",
    )
    restored = Source.model_validate_json(source.model_dump_json())
    assert restored == source
    assert restored.source_type == SourceType.SEC_FILING
    assert restored.accessed_at == date(2026, 8, 11)


def test_source_canonical_url_required() -> None:
    """canonical_url 必填且非空白。"""
    with pytest.raises(ValidationError):
        Source(
            source_type=SourceType.WEB,
            canonical_url="   ",
            accessed_at=TODAY,
        )


def test_job_source_round_trip_and_score_bounds() -> None:
    """JobSource round-trip；relevance_score 越界被拒。"""
    job_source = JobSource(
        job_id="job-1",
        source_id="src-1",
        purpose="financials",
        relevance_score=0.85,
    )
    restored = JobSource.model_validate_json(job_source.model_dump_json())
    assert restored == job_source

    with pytest.raises(ValidationError):
        JobSource(job_id="job-1", source_id="src-1", purpose="x", relevance_score=1.5)


def test_filing_round_trip() -> None:
    """Filing round-trip；is_amendment 默认 False。"""
    filing = Filing(
        accession_number="0001564590-26-000001",
        form_type="10-K",
        filing_date=date(2026, 7, 29),
        report_period=date(2026, 6, 30),
        primary_document_url="https://www.sec.gov/.../0001564590-26-000001-index.htm",
    )
    assert filing.is_amendment is False
    restored = Filing.model_validate_json(filing.model_dump_json())
    assert restored == filing


def test_document_round_trip_and_parse_status() -> None:
    """Document round-trip；ParseStatus 保真；byte_size 不能为负。"""
    doc = Document(
        source_id="src-1",
        filing_id="fil-1",
        media_type="text/html",
        storage_uri="artifacts/job-1/documents/doc-1.html",
        content_checksum="sha256:abc",
        byte_size=2048,
        parse_status=ParseStatus.PARSED,
        parser_name="docling",
        parser_version="1.0",
    )
    restored = Document.model_validate_json(doc.model_dump_json())
    assert restored == doc
    assert restored.parse_status == ParseStatus.PARSED

    with pytest.raises(ValidationError):
        Document(
            source_id="src-1",
            media_type="text/html",
            storage_uri="u",
            content_checksum="c",
            byte_size=-1,
            parse_status=ParseStatus.PENDING,
        )


# ---------------------------------------------------------------------------
# 财务事实 / 派生指标模型（P01-04）
# ---------------------------------------------------------------------------


def test_financial_fact_period_xor_instant() -> None:
    """FinancialFact 的期间与时点必须二选一。"""
    period_fact = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="Revenues",
        value=Decimal("1000000.00"),
        unit="USD",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
    )
    assert period_fact.period_start == date(2026, 1, 1)
    assert period_fact.fact_version == "v1"

    instant_fact = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="Assets",
        value=Decimal("5000000.00"),
        unit="USD",
        instant_date=date(2026, 3, 31),
    )
    assert instant_fact.instant_date == date(2026, 3, 31)


def test_financial_fact_both_period_and_instant_rejected() -> None:
    """同时给期间和时点必须被拒绝。"""
    with pytest.raises(ValidationError):
        FinancialFact(
            company_id="c1",
            source_id="s1",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("1000000.00"),
            unit="USD",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
            instant_date=date(2026, 3, 31),
        )


def test_financial_fact_neither_period_nor_instant_rejected() -> None:
    """期间与时点都缺失必须被拒绝。"""
    with pytest.raises(ValidationError):
        FinancialFact(
            company_id="c1",
            source_id="s1",
            taxonomy="us-gaap",
            concept="Revenues",
            value=Decimal("1000000.00"),
            unit="USD",
        )


def test_financial_fact_uses_decimal_type() -> None:
    """value 必须是 Decimal 类型（防二进制浮点误差）。"""
    fact = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="Revenues",
        value="1000000.00",  # 字符串自动转 Decimal
        unit="USD",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
    )
    assert isinstance(fact.value, Decimal)
    assert fact.value == Decimal("1000000.00")


def test_financial_fact_negative_value_allowed() -> None:
    """value 允许负值/零：净亏损、负现金流是真实业务（对齐 MetricResult 口径）。"""
    fact = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="NetIncomeLoss",
        value=Decimal("-5000000.00"),
        unit="USD",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
    )
    assert fact.value == Decimal("-5000000.00")

    zero = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="LongTermDebt",
        value=Decimal("0"),
        unit="USD",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
    )
    assert zero.value == Decimal("0")


def test_metric_result_computed_requires_value() -> None:
    """status=computed 时必须带 value。"""
    metric = MetricResult(
        job_id="j1",
        metric_name="gross_margin",
        period_end=date(2026, 3, 31),
        value=Decimal("0.42"),
        unit="ratio",
        status=MetricStatus.COMPUTED,
        formula_version="financial_metrics_v1",
    )
    assert metric.value == Decimal("0.42")

    with pytest.raises(ValidationError):
        MetricResult(
            job_id="j1",
            metric_name="gross_margin",
            period_end=date(2026, 3, 31),
            value=None,
            unit="ratio",
            status=MetricStatus.COMPUTED,
            formula_version="financial_metrics_v1",
        )


def test_metric_result_not_computable_must_be_value_free() -> None:
    """非 computed 状态必须 value 为空。"""
    metric = MetricResult(
        job_id="j1",
        metric_name="roe",
        period_end=date(2026, 3, 31),
        value=None,
        unit="ratio",
        status=MetricStatus.NOT_COMPUTABLE,
        formula_version="financial_metrics_v1",
        explanation="分母（平均所有者权益）为 0",
    )
    assert metric.status == MetricStatus.NOT_COMPUTABLE

    with pytest.raises(ValidationError):
        MetricResult(
            job_id="j1",
            metric_name="roe",
            period_end=date(2026, 3, 31),
            value=Decimal("0.1"),
            unit="ratio",
            status=MetricStatus.NOT_COMPUTABLE,
            formula_version="financial_metrics_v1",
        )


# ---------------------------------------------------------------------------
# 结构化 Agent 输出 Pack（P01-05）：必需字段 + version
# ---------------------------------------------------------------------------


def _sample_company() -> CompanyIdentity:
    return CompanyIdentity(cik="0000789019", legal_name="Microsoft Corporation")


def _sample_source() -> Source:
    return Source(
        source_type=SourceType.SEC_FILING,
        canonical_url="https://www.sec.gov/Archives/edgar/data/789019/0001564590",
        accessed_at=TODAY,
    )


def test_research_pack_requires_at_least_one_source() -> None:
    """ResearchPack 必须至少一个来源；空 sources 被拒。"""
    pack = ResearchPack(
        version="research_pack_v1",
        company_identity=_sample_company(),
        as_of_date=TODAY,
        sources=[_sample_source()],
    )
    assert pack.version == "research_pack_v1"

    with pytest.raises(ValidationError):
        ResearchPack(
            version="research_pack_v1",
            company_identity=_sample_company(),
            as_of_date=TODAY,
            sources=[],
        )


def test_research_pack_version_required() -> None:
    """ResearchPack version 必填非空。"""
    with pytest.raises(ValidationError):
        ResearchPack(
            version="",
            company_identity=_sample_company(),
            as_of_date=TODAY,
            sources=[_sample_source()],
        )


def test_financial_analysis_pack_requires_facts() -> None:
    """FinancialAnalysisPack 必须至少一个 fact；空 facts 被拒。"""
    fact = FinancialFact(
        company_id="c1",
        source_id="s1",
        taxonomy="us-gaap",
        concept="Revenues",
        value=Decimal("1000000.00"),
        unit="USD",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
    )
    pack = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2026, 3, 31),
        facts=[fact],
    )
    assert pack.metrics == []  # 默认空列表

    with pytest.raises(ValidationError):
        FinancialAnalysisPack(
            version="analysis_pack_v1",
            period_end=date(2026, 3, 31),
            facts=[],
        )


def test_report_draft_requires_content() -> None:
    """ReportDraft 必须 title 与 markdown 非空。"""
    draft = ReportDraft(
        version="report_draft_v1",
        title="Microsoft 研究报告",
        markdown="# 报告",
        citation_keys=["c1"],
    )
    assert draft.citation_keys == ["c1"]

    with pytest.raises(ValidationError):
        ReportDraft(version="report_draft_v1", title="", markdown="# 报告")


def test_quality_report_fields() -> None:
    """QualityReport 必需字段齐全；all_passed=False 可带 issues。"""
    ok = QualityReport(
        version="quality_report_v1",
        all_passed=True,
        issues=[],
        recommendation="published",
    )
    assert ok.all_passed is True

    rejected = QualityReport(
        version="quality_report_v1",
        all_passed=False,
        issues=["缺引用"],
        recommendation="rejected",
    )
    assert rejected.all_passed is False
    assert "缺引用" in rejected.issues
