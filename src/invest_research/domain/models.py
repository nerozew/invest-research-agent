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


class ResearchProfileMode(StrEnum):
    """每任务研究档位（P06-06A）。

    - ``FAST``：低迭代、低重试预算，适合演示与初步报告（不降级 AI 质量保证，
      只是预算更少）；
    - ``DEEP``：更充分研究，耗时与模型费用更高（默认，与旧请求兼容）。
    """

    FAST = "fast"
    DEEP = "deep"


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

    # P06-06A：每任务研究档位（fast/deep）。默认 deep 保证旧请求兼容；
    # Pydantic 自动校验非法值（API 422）。
    research_profile: str = Field(default=ResearchProfileMode.DEEP.value)

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

    @field_validator("research_profile")
    @classmethod
    def _validate_research_profile(cls, value: str) -> str:
        """研究档位只能为 fast 或 deep（P06-06A）。"""
        try:
            ResearchProfileMode(value)
        except ValueError as exc:
            raise ValueError("research_profile 仅支持 fast 或 deep") from exc
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


class AnalysisCompleteness(StrEnum):
    """分析结果完整性状态（P06-09A）。

    - ``COMPLETE``：分析数据完整，关键分析结果（facts 或 metrics）非空；
    - ``PARTIAL``：只有部分可用结果，必须用 ``limitations`` 说明缺哪些数据及原因；
    - ``UNAVAILABLE``：没有可用分析结果，必须提供 ``unavailable_reason``，
      不得伪造财务指标（facts/metrics 必须为空）。
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class FinancialAnalysisPack(BaseModel):
    """财报分析 Agent 的输出（workflow 步骤 04，schema v2）。

    P06-09A 引入 ``completeness`` 三态（complete/partial/unavailable）：
    - 旧版 ``version="analysis_pack_v1"`` 工件按宽松模式读取（兼容历史数据，不强行
      套用新状态校验）；
    - 新版 ``schema_version="analysis_pack_v2"`` 严格校验状态与内容是否一致；
    - ``unavailable`` 是合法业务结果，不是系统异常：Writer 只报告数据不可用，
      不得推断不存在的数据。
    """

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)  # 例如 "analysis_pack_v2"
    # P06-09A：显式 schema 版本标识（区别于旧 version 自由文本，供兼容读取分支判断）
    schema_version: str = Field(default="analysis_pack_v2")
    period_end: date
    # P05.5-deploy-fix：允许空 facts——无可用财务事实时如实为空（报告据实标注数据限制），
    # 而不是让整个流水线崩溃（LLM 输出空 facts 是真实边界情况）
    facts: list[FinancialFact] = Field(default_factory=list)
    metrics: list[MetricResult] = Field(default_factory=list)
    analysis_notes: str | None = None
    limitations: list[str] = Field(default_factory=list)
    # P06-09A：结果完整性状态；默认 partial 保持旧语义（允许空 facts+limitations）
    completeness: AnalysisCompleteness = AnalysisCompleteness.PARTIAL
    # P06-09A：unavailable 时必须提供的不可用原因；其它状态必须为空
    unavailable_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _infer_schema_version(cls, data: object) -> object:
        """旧版 ``version="analysis_pack_v1"`` 工件自动标记 schema_version=v1。

        v1 无 ``completeness``/``schema_version`` 字段；读取历史工件时按旧语义
        标记为 v1（宽松校验分支），不强行套用新状态规则。
        """
        if isinstance(data, dict):
            if "schema_version" in data:
                return data
            version = data.get("version")
            if isinstance(version, str) and version.endswith("_v1"):
                data = {**data, "schema_version": "analysis_pack_v1"}
        return data

    @model_validator(mode="after")
    def _validate_completeness(self) -> "FinancialAnalysisPack":
        """跨字段校验：状态与内容必须一致（仅 v2 严格；v1 旧版宽松）。

        - complete：关键分析结果（facts 或 metrics）不得为空；unavailable_reason 为空；
        - partial：必须明确 limitations（说明缺哪些数据及原因）；unavailable_reason 为空；
        - unavailable：必须提供 unavailable_reason；facts/metrics 必须为空（不得伪造指标）。
        """
        # v1 旧版工件：保持旧语义，不做新状态强制校验（兼容历史数据读取）。
        if self.schema_version == "analysis_pack_v1":
            return self

        if self.completeness == AnalysisCompleteness.COMPLETE:
            if not self.facts and not self.metrics:
                raise ValueError("completeness=complete 时 facts 或 metrics 至少一项非空")
            if self.unavailable_reason is not None:
                raise ValueError("completeness=complete 时 unavailable_reason 必须为空")
        elif self.completeness == AnalysisCompleteness.PARTIAL:
            if not self.limitations:
                raise ValueError("completeness=partial 时必须提供 limitations（缺失数据及原因）")
            if self.unavailable_reason is not None:
                raise ValueError("completeness=partial 时 unavailable_reason 必须为空")
        elif self.completeness == AnalysisCompleteness.UNAVAILABLE:
            if not self.unavailable_reason:
                raise ValueError("completeness=unavailable 时必须提供 unavailable_reason")
            if self.facts or self.metrics:
                raise ValueError(
                    "completeness=unavailable 时 facts/metrics 必须为空（不得伪造指标）"
                )
        return self


class AnalysisSelectionDraft(BaseModel):
    """财报分析 Agent 的“选择草稿”（P06-11C，仅限 JSON 文本本地校验路径）。

    设计动机（LLM 选择，代码组装）：
    - 不再要求 LLM 重新抄写完整 ``FinancialFact``（嵌套契约容易被模型丢弃，
      如 DeepSeek JSON 文本路径丢 ``company_id`` → ``facts.0.company_id: Field
      required``）；
    - LLM 只返回一组 ``selected_fact_refs`` 引用，真正的 ``FinancialFact``
      由 ``AnalysisPackAssembler`` 从原始可信预取事实中确定性取回；
    - ``metric_results`` 仅承载确定性的指标计算引用（本任务范围：若无法安全捕获
      FinancialCalculator 返回值，保持空并由 assembler 输出 partial 说明限制）。
    """

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)  # 例如 "analysis_selection_draft_v1"
    schema_version: str = Field(default="analysis_selection_draft_v1")
    period_end: date
    # LLM 选中的事实引用（稳定短 hash，由 build_fact_ref 确定性生成）。
    # 允许为空：completeness=unavailable 时合法；重复引用由 assembler 幂等去重。
    selected_fact_refs: list[str] = Field(default_factory=list)
    # 确定性指标计算引用（本任务范围可能为空；非空时必须携带完整结果或在组装时丢弃）。
    metric_results: list[MetricResult] = Field(default_factory=list)
    analysis_notes: str | None = None
    limitations: list[str] = Field(default_factory=list)
    completeness: AnalysisCompleteness = AnalysisCompleteness.PARTIAL
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _validate_selection_draft(self) -> "AnalysisSelectionDraft":
        """跨字段校验：状态与内容一致（对齐 FinancialAnalysisPack v2 语义）。

        - unavailable：selected_fact_refs/metric_results 必须为空，且必须有
          unavailable_reason；
        - partial：必须提供 limitations；
        - complete：selected_fact_refs 或 metric_results 至少一项非空，
          unavailable_reason 为空。
        """
        if self.completeness == AnalysisCompleteness.COMPLETE:
            if not self.selected_fact_refs and not self.metric_results:
                raise ValueError(
                    "completeness=complete 时 selected_fact_refs 或 metric_results 至少一项非空"
                )
            if self.unavailable_reason is not None:
                raise ValueError("completeness=complete 时 unavailable_reason 必须为空")
        elif self.completeness == AnalysisCompleteness.PARTIAL:
            if not self.limitations:
                raise ValueError("completeness=partial 时必须提供 limitations")
            if self.unavailable_reason is not None:
                raise ValueError("completeness=partial 时 unavailable_reason 必须为空")
        elif self.completeness == AnalysisCompleteness.UNAVAILABLE:
            if not self.unavailable_reason:
                raise ValueError("completeness=unavailable 时必须提供 unavailable_reason")
            if self.selected_fact_refs or self.metric_results:
                raise ValueError(
                    "completeness=unavailable 时 selected_fact_refs/metric_results 必须为空"
                )
        return self


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
