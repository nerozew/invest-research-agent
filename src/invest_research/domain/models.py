"""ResearchRequest 与 CompanyIdentity 领域模型（P01-02）。

依据：
- `docs/01-PRD.md` FR-001（创建任务）、FR-002（公司身份解析）、§1（语言）；
- `docs/02-ARCHITECTURE.md` §7 关键数据对象：ResearchRequest、CompanyIdentity；
- `docs/03-DATABASE.md` §3：`companies.cik` 固定 10 位数字、
  `research_jobs.requested_forms` 默认 `["10-K", "10-Q"]`。

依赖边界：本层只允许使用 Pydantic（跨模块对象契约的基础库），
禁止导入 CrewAI/FastAPI/SQLAlchemy/Redis 等。
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from invest_research.domain.quality import QualityRecommendation

_CIK_PATTERN = re.compile(r"^\d{10}$")
_ALLOWED_LANGUAGES = frozenset({"zh-CN", "en"})
_DEFAULT_FORMS: tuple[str, ...] = ("10-K", "10-Q")


class ResearchRequest(BaseModel):
    """用户发起研究任务时的原始输入（workflow 步骤 00 的输入）。"""

    model_config = ConfigDict(frozen=True)

    # 公司名称或股票代码（必填，去空格后非空）
    input_company: str = Field(min_length=1, max_length=200)

    # 数据截止日：不晚于当前日期（未来日期被拒）
    as_of_date: date

    # 报告语言：zh-CN 为主，可配置英文
    language: str = Field(default="zh-CN")

    # 请求的 SEC 表单类型（非空，默认 10-K + 10-Q）
    requested_forms: tuple[str, ...] = Field(default=_DEFAULT_FORMS)

    @field_validator("input_company")
    @classmethod
    def _strip_company(cls, value: str) -> str:
        """去除首尾空白；纯空白输入在 min_length 前先被拦截。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("input_company 不能为空或纯空白")
        return cleaned

    @field_validator("as_of_date")
    @classmethod
    def _not_future_as_of(cls, value: date) -> date:
        """数据截止日不能是未来日期（PRD：日期边界）。"""
        if value > date.today():
            raise ValueError("as_of_date 不能是未来日期")
        return value

    @field_validator("language")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        """语言只能为 zh-CN 或 en。"""
        if value not in _ALLOWED_LANGUAGES:
            raise ValueError(f"language 仅支持 {'、'.join(sorted(_ALLOWED_LANGUAGES))}")
        return value

    @field_validator("requested_forms")
    @classmethod
    def _forms_not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """requested_forms 不能为空。"""
        if not value:
            raise ValueError("requested_forms 不能为空")
        return value


class CompanyIdentity(BaseModel):
    """解析后的公司身份（workflow 步骤 01 的输出，FR-002）。"""

    model_config = ConfigDict(frozen=True)

    # 中央索引键（Central Index Key），固定 10 位数字
    cik: str

    # 股票代码（可选，有些公司可能无 ticker）
    ticker: str | None = None

    # 法定名称（必填非空）
    legal_name: str = Field(min_length=1, max_length=300)

    # 交易所（可选）
    exchange: str | None = None

    # 资产所在行业分类代码 SIC(可选)
    sic: str | None = None

    @field_validator("cik")
    @classmethod
    def _validate_cik(cls, value: str) -> str:
        """CIK 必须恰好 10 位数字（对齐 companies.cik 约束）。"""
        cleaned = value.strip()
        if not _CIK_PATTERN.fullmatch(cleaned):
            raise ValueError("cik 必须为 10 位数字（例如 0000789019）")
        return cleaned

    @field_validator("legal_name")
    @classmethod
    def _strip_legal_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("legal_name 不能为空或纯空白")
        return cleaned


# ---------------------------------------------------------------------------
# 来源 / 关联 / 申报 / 文档（P01-03）
# 对齐 docs/03-DATABASE.md §3：sources、job_sources、filings、documents
# ---------------------------------------------------------------------------


class SourceType(StrEnum):
    """外部来源类型（sources.source_type CHECK 取值）。"""

    SEC_FILING = "sec_filing"
    SEC_XBRL = "sec_xbrl"
    WEB = "web"
    COMPANY_IR = "company_ir"
    UPLOADED = "uploaded"


class ParseStatus(StrEnum):
    """文档解析状态（documents.parse_status CHECK 取值）。"""

    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class Source(BaseModel):
    """外部来源的规范化目录（sources 表）。

    关键约束：canonical_url 唯一（去重）、accessed_at 必填。
    ``locator``：来源内定位（章节/页码/锚点），P05-13 要求每条引用可定位到
    URL + locator；可选，允许缺省（如整站来源）。
    """

    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    canonical_url: str = Field(min_length=1, max_length=2048)
    title: str | None = None
    publisher: str | None = None
    published_at: date | None = None
    accessed_at: date
    content_checksum: str | None = None
    locator: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("canonical_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("canonical_url 不能为空或纯空白")
        return cleaned


class JobSource(BaseModel):
    """任务与来源的多对多关联（job_sources 表）。"""

    model_config = ConfigDict(frozen=True)

    job_id: str
    source_id: str
    purpose: str = Field(min_length=1)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    selection_reason: str | None = None


class Filing(BaseModel):
    """SEC 申报元数据（filings 表）。

    关键约束：accession_number 唯一；is_amendment 默认 False。
    """

    model_config = ConfigDict(frozen=True)

    accession_number: str = Field(min_length=1)
    form_type: str = Field(min_length=1)
    filing_date: date
    report_period: date | None = None
    primary_document_url: str = Field(min_length=1)
    is_amendment: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)


class Document(BaseModel):
    """下载文档及其解析状态（documents 表）。"""

    model_config = ConfigDict(frozen=True)

    source_id: str
    filing_id: str | None = None
    media_type: str = Field(min_length=1)
    storage_uri: str = Field(min_length=1)
    content_checksum: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    parse_status: ParseStatus
    parser_name: str | None = None
    parser_version: str | None = None
    parsed_at: date | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 财务事实 / 派生指标（P01-04）
# 对齐 docs/03-DATABASE.md §3：financial_facts、computed_metrics
# ---------------------------------------------------------------------------


class FinancialFact(BaseModel):
    """XBRL/申报中的原始财务事实（financial_facts 表）。

    关键约束（对齐 03-DATABASE DDL）：
    - value 使用 Decimal（金额/比例避免二进制浮点误差）；
    - period_start 与 instant_date 必须二选一（期间型 vs 时点型）；
    - fact_version 默认 v1（口径修复通过新增版本实现，不覆盖原文）。
    """

    model_config = ConfigDict(frozen=True)

    company_id: str
    filing_id: str | None = None
    source_id: str
    taxonomy: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    label: str | None = None
    # 允许负值/零：净亏损、负现金流是真实业务（对齐 MetricResult 的口径，见下）
    value: Decimal
    unit: str = Field(min_length=1)
    period_start: date | None = None
    period_end: date | None = None
    instant_date: date | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form_type: str | None = None
    frame: str | None = None
    accession_number: str | None = None
    fact_version: str = "v1"

    @model_validator(mode="after")
    def _check_period_xor_instant(self) -> "FinancialFact":
        """期间（period_start+period_end）与时点（instant_date）必须二选一。"""
        has_period = self.period_start is not None or self.period_end is not None
        has_instant = self.instant_date is not None
        if has_period == has_instant:
            raise ValueError(
                "financial_fact 必须且只能有期间(period_start/end)或时点(instant_date)之一"
            )
        return self


class MetricStatus(StrEnum):
    """派生指标计算状态（computed_metrics.status CHECK 取值）。"""

    COMPUTED = "computed"
    NOT_COMPUTABLE = "not_computable"
    AMBIGUOUS = "ambiguous"
    FAILED_VALIDATION = "failed_validation"


class MetricResult(BaseModel):
    """版本化派生指标（computed_metrics 表）。

    对应 PRD §8：公式带版本（如 financial_metrics_v1），
    计算输入保存 concept、期间、单位和来源，可追溯。
    """

    model_config = ConfigDict(frozen=True)

    job_id: str
    metric_name: str = Field(min_length=1)
    period_end: date
    # 注意：增长率/利润率允许负值（收入下降、经营亏损是正常业务），
    # 不可设 gt=0；仅约束 value 为有限 Decimal 即可（PRD §8 负数场景）。
    value: Decimal | None = None
    unit: str = Field(min_length=1)
    status: MetricStatus
    formula_version: str = Field(min_length=1)
    inputs_json: dict[str, object] = Field(default_factory=dict)
    explanation: str | None = None

    @model_validator(mode="after")
    def _value_required_when_computed(self) -> "MetricResult":
        """status=computed 时必须提供 value；不可计算时 value 为空。"""
        if self.status == MetricStatus.COMPUTED and self.value is None:
            raise ValueError("status=computed 时 value 不能为空")
        if self.status != MetricStatus.COMPUTED and self.value is not None:
            raise ValueError(f"status={self.status.value} 时 value 必须为空")
        return self


# ---------------------------------------------------------------------------
# 结构化 Agent 输出 Pack（P01-05）
# 对齐 docs/02-ARCHITECTURE.md §7 与 docs/04-WORKFLOW-RELIABILITY.md §2 步骤契约
# 每个 pack：结构化输出 + 显式 version（可追溯/可失效重算）
# ---------------------------------------------------------------------------


class ResearchPack(BaseModel):
    """信息搜集 Agent 的输出（workflow 步骤 02）。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)  # 例如 "research_pack_v1"
    company_identity: CompanyIdentity
    as_of_date: date
    sources: list[Source] = Field(min_length=1)  # 至少一个来源
    coverage_notes: str | None = None
    conflicts: list[str] = Field(default_factory=list)  # 发现的矛盾/冲突


class FinancialAnalysisPack(BaseModel):
    """财报分析 Agent 的输出（workflow 步骤 04）。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)  # 例如 "analysis_pack_v1"
    period_end: date
    # P05.5-deploy-fix：允许空 facts——无可用财务事实时如实为空（报告据实标注数据限制），
    # 而不是让整个流水线崩溃（LLM 输出空 facts 是真实边界情况）
    facts: list[FinancialFact] = Field(default_factory=list)
    metrics: list[MetricResult] = Field(default_factory=list)
    analysis_notes: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ReportDraft(BaseModel):
    """报告撰写 Agent 的输出（workflow 步骤 05）。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)  # 例如 "report_draft_v1"
    title: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    citation_keys: list[str] = Field(default_factory=list)  # 报告内引用的 claim 键


class QualityReport(BaseModel):
    """硬质量门禁结果（workflow 步骤 06）。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)  # 例如 "quality_report_v1"
    all_passed: bool
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendation: QualityRecommendation  # 收紧为枚举（P03-16）
