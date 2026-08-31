"""P07-01 年度并行研究管线的纯领域契约。

本模块只定义未来 DAG 调度、证据校验和受控 ReAct 所需的不可变数据对象；不包含
数据库、网络、LLM 或调度实现。现有 ``workflow_steps`` 状态机保持不变。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchMode(StrEnum):
    """研究运行模式；``annual_deep`` 在 P07 后续阶段接入运行时。"""

    LEGACY = "legacy"
    ANNUAL_DEEP = "annual_deep"


class ResearchNodeKind(StrEnum):
    """年度研究 DAG 的稳定节点类别。"""

    DISCOVER_ANNUAL_FILINGS = "discover_annual_filings"
    FETCH_COMPANY_FACTS = "fetch_company_facts"
    DOWNLOAD_FILING = "download_filing"
    PARSE_FILING = "parse_filing"
    VALIDATE_EVIDENCE = "validate_evidence"
    BUILD_ANNUAL_COMPARISON = "build_annual_comparison"
    REQUEST_SUPPLEMENT = "request_supplement"
    ANALYZE_FINANCIALS = "analyze_financials"
    WRITE_SECTION = "write_section"
    FINALIZE_REPORT = "finalize_report"


class ResearchNodeStatus(StrEnum):
    """P07 节点状态，独立于旧 ``StepStatus``。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ResearchNodeStatus.SUCCEEDED,
            ResearchNodeStatus.FAILED_TERMINAL,
            ResearchNodeStatus.BLOCKED,
            ResearchNodeStatus.CANCELLED,
        }


class EvidenceKind(StrEnum):
    """可路由给分析或写作节点的已结构化证据类别。"""

    TARGET_ANNUAL_FILING = "target_annual_filing"
    COMPARATOR_ANNUAL_FILING = "comparator_annual_filing"
    COMPANY_FACTS = "company_facts"
    FINANCIAL_FACT_SET = "financial_fact_set"
    BUSINESS_OVERVIEW = "business_overview"
    RISK_FACTORS = "risk_factors"
    MANAGEMENT_DISCUSSION = "management_discussion"
    MATERIAL_EVENT = "material_event"
    ANALYST_OPINION = "analyst_opinion"
    RATING_AGENCY = "rating_agency"


class EvidenceValidationStatus(StrEnum):
    """证据工件的生命周期；只有 ``VALIDATED`` 可供下游消费。"""

    PRODUCED = "produced"
    VALIDATED = "validated"
    CONSUMED = "consumed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class CoverageRequirement(StrEnum):
    """年度研究覆盖账本中的可审计要求。"""

    TARGET_ANNUAL_FILING = "target_annual_filing"
    TARGET_FINANCIAL_FACTS = "target_financial_facts"
    COMPARATOR_FINANCIAL_FACTS = "comparator_financial_facts"
    COMPARATOR_ANNUAL_FILING = "comparator_annual_filing"
    CURRENT_BUSINESS_OVERVIEW = "current_business_overview"
    CURRENT_RISK_FACTORS = "current_risk_factors"
    COMPARATOR_RISK_FACTORS = "comparator_risk_factors"
    COMPARATOR_MANAGEMENT_DISCUSSION = "comparator_management_discussion"


class CoverageStatus(StrEnum):
    """单项覆盖状态。"""

    PENDING = "pending"
    SATISFIED = "satisfied"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class AnnualReadiness(StrEnum):
    """年度管线可向下游交付的四种明确结果。"""

    FULL_READY = "full_ready"
    FINANCIAL_ONLY_READY = "financial_only_ready"
    NARRATIVE_ONLY_READY = "narrative_only_ready"
    BLOCKED = "blocked"


class ResearchDecisionAction(StrEnum):
    """受控 ReAct 的可执行决策集合。"""

    CONTINUE_SEARCH = "continue_search"
    READY_FOR_ANALYSIS = "ready_for_analysis"
    PARTIAL_READY = "partial_ready"
    BLOCKED = "blocked"


class ResearchDecisionReasonCode(StrEnum):
    """受控补证决策的稳定、可聚合原因码。"""

    COVERAGE_COMPLETE = "coverage_complete"
    SUPPLEMENT_REQUIRED = "supplement_required"
    CONFIRMED_UNAVAILABLE = "confirmed_unavailable"
    SUPPLEMENT_ALREADY_ATTEMPTED = "supplement_already_attempted"
    DECISION_BUDGET_EXHAUSTED = "decision_budget_exhausted"
    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"
    NO_EVIDENCE_GAIN = "no_evidence_gain"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    COVERAGE_UNRESOLVED = "coverage_unresolved"


class ResearchNode(BaseModel):
    """可重试的 DAG 节点；调度持久化将在 P07 后续阶段实现。"""

    model_config = ConfigDict(frozen=True)

    node_key: str = Field(min_length=1, max_length=200)
    kind: ResearchNodeKind
    status: ResearchNodeStatus = ResearchNodeStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    input_fingerprint: str | None = None
    output_artifact_keys: tuple[str, ...] = ()
    blocked_reason: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> "ResearchNode":
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count 不能大于 max_attempts")
        if self.status is ResearchNodeStatus.BLOCKED and not self.blocked_reason:
            raise ValueError("blocked 节点必须提供 blocked_reason")
        if self.status is not ResearchNodeStatus.BLOCKED and self.blocked_reason is not None:
            raise ValueError("只有 blocked 节点可以提供 blocked_reason")
        return self


class NodeDependency(BaseModel):
    """DAG 中一个有向依赖边。"""

    model_config = ConfigDict(frozen=True)

    upstream_node_key: str = Field(min_length=1, max_length=200)
    downstream_node_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _not_self_dependency(self) -> "NodeDependency":
        if self.upstream_node_key == self.downstream_node_key:
            raise ValueError("节点不能依赖自身")
        return self


class EvidenceArtifact(BaseModel):
    """带来源、期间和校验状态的不可变证据工件。"""

    model_config = ConfigDict(frozen=True)

    artifact_key: str = Field(min_length=1, max_length=500)
    kind: EvidenceKind
    source_url: str = Field(min_length=1, max_length=2048)
    locator: str | None = None
    fiscal_year: int | None = Field(default=None, ge=1900, le=9999)
    content_checksum: str = Field(min_length=1, max_length=128)
    parser_version: str | None = None
    validation_status: EvidenceValidationStatus = EvidenceValidationStatus.PRODUCED


class CoverageItem(BaseModel):
    """Coverage Ledger 的单条需求、证据和缺失原因。"""

    model_config = ConfigDict(frozen=True)

    requirement: CoverageRequirement
    status: CoverageStatus = CoverageStatus.PENDING
    evidence_artifact_keys: tuple[str, ...] = ()
    missing_reason: str | None = None
    consumable_by: tuple[ResearchNodeKind, ...] = ()

    @model_validator(mode="after")
    def _validate_coverage(self) -> "CoverageItem":
        if self.status is CoverageStatus.SATISFIED and not self.evidence_artifact_keys:
            raise ValueError("已满足的覆盖项必须关联至少一个证据工件")
        if self.status is CoverageStatus.MISSING and not self.missing_reason:
            raise ValueError("缺失的覆盖项必须提供 missing_reason")
        if self.status is not CoverageStatus.MISSING and self.missing_reason is not None:
            raise ValueError("只有缺失的覆盖项可以提供 missing_reason")
        return self


class CoverageLedger(BaseModel):
    """年度证据覆盖的可审计快照。

    ``target_fiscal_year`` 在 P07-02 的 filing selector 运行前可为 ``None``；本对象
    仅记录已经作出的覆盖判定，不自行搜索或补齐证据。
    """

    model_config = ConfigDict(frozen=True)

    target_fiscal_year: int | None = Field(default=None, ge=1900, le=9999)
    items: tuple[CoverageItem, ...]
    readiness: AnnualReadiness

    @model_validator(mode="after")
    def _validate_readiness(self) -> "CoverageLedger":
        by_requirement = {item.requirement: item for item in self.items}
        if len(by_requirement) != len(self.items):
            raise ValueError("Coverage Ledger 不能包含重复 requirement")

        def satisfied(requirement: CoverageRequirement) -> bool:
            item = by_requirement.get(requirement)
            return item is not None and item.status is CoverageStatus.SATISFIED

        financial_base = all(
            satisfied(requirement)
            for requirement in (
                CoverageRequirement.TARGET_ANNUAL_FILING,
                CoverageRequirement.TARGET_FINANCIAL_FACTS,
                CoverageRequirement.COMPARATOR_FINANCIAL_FACTS,
            )
        )
        comparator_filing = satisfied(CoverageRequirement.COMPARATOR_ANNUAL_FILING)
        target_filing = satisfied(CoverageRequirement.TARGET_ANNUAL_FILING)

        if self.readiness is AnnualReadiness.FULL_READY and not (
            financial_base and comparator_filing
        ):
            raise ValueError("full_ready 需要目标 10-K、两年财务事实及上一年度 10-K")
        if self.readiness is AnnualReadiness.FINANCIAL_ONLY_READY and not (
            financial_base and not comparator_filing
        ):
            raise ValueError("financial_only_ready 需要财务基础且缺少上一年度 10-K")
        comparator_item = by_requirement.get(CoverageRequirement.COMPARATOR_ANNUAL_FILING)
        if self.readiness is AnnualReadiness.FINANCIAL_ONLY_READY and (
            comparator_item is None or comparator_item.status is not CoverageStatus.MISSING
        ):
            raise ValueError("financial_only_ready 必须记录上一年度 10-K 缺失原因")
        target_item = by_requirement.get(CoverageRequirement.TARGET_ANNUAL_FILING)
        if self.readiness is AnnualReadiness.NARRATIVE_ONLY_READY and (
            target_filing or target_item is None or target_item.status is not CoverageStatus.MISSING
        ):
            raise ValueError("narrative_only_ready 必须记录目标 10-K 缺失原因")
        return self


class SupplementRequest(BaseModel):
    """一次受控补证所允许创建的唯一节点。

    ``target_fiscal_year`` 在尚未发现任何年度 filing 时可以为 ``None``；其余场景
    由调用方传入已知的目标年度，供后续调度器生成稳定节点键。
    """

    model_config = ConfigDict(frozen=True)

    requirement: CoverageRequirement
    target_fiscal_year: int | None = Field(default=None, ge=1900, le=9999)
    node_kind: ResearchNodeKind

    @model_validator(mode="after")
    def _validate_node_kind(self) -> "SupplementRequest":
        expected_kind = {
            CoverageRequirement.TARGET_ANNUAL_FILING: ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,
            CoverageRequirement.COMPARATOR_ANNUAL_FILING: ResearchNodeKind.DISCOVER_ANNUAL_FILINGS,
            CoverageRequirement.TARGET_FINANCIAL_FACTS: ResearchNodeKind.FETCH_COMPANY_FACTS,
            CoverageRequirement.COMPARATOR_FINANCIAL_FACTS: ResearchNodeKind.FETCH_COMPANY_FACTS,
            CoverageRequirement.CURRENT_BUSINESS_OVERVIEW: ResearchNodeKind.REQUEST_SUPPLEMENT,
            CoverageRequirement.CURRENT_RISK_FACTORS: ResearchNodeKind.REQUEST_SUPPLEMENT,
            CoverageRequirement.COMPARATOR_RISK_FACTORS: ResearchNodeKind.REQUEST_SUPPLEMENT,
            CoverageRequirement.COMPARATOR_MANAGEMENT_DISCUSSION: (
                ResearchNodeKind.REQUEST_SUPPLEMENT
            ),
        }[self.requirement]
        if self.node_kind is not expected_kind:
            raise ValueError(
                f"{self.requirement.value} 只能请求 {expected_kind.value} 节点"
            )
        return self


class ToolBudgetAvailability(BaseModel):
    """策略读取的单个工具剩余额度快照，不持有或修改 ``ToolBudget``。"""

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1, max_length=100)
    remaining_calls: int = Field(ge=0)


class ResearchDecisionBudget(BaseModel):
    """P07-06 的保守补证边界；不包含模型 Token，因为本阶段没有 LLM。"""

    model_config = ConfigDict(frozen=True)

    max_decisions: int = Field(default=2, ge=1)
    max_elapsed_seconds: float = Field(default=120.0, gt=0)
    max_consecutive_no_gain: int = Field(default=1, ge=1)


class ResearchObservation(BaseModel):
    """一次决策前的只读事实快照。

    Scheduler 在后续阶段写回已验证工件和预算，本模型只承载其快照，因此策略可以
    保持纯函数式且可单测。
    """

    model_config = ConfigDict(frozen=True)

    coverage_ledger: CoverageLedger
    attempted_requirements: tuple[CoverageRequirement, ...] = ()
    confirmed_unavailable_requirements: tuple[CoverageRequirement, ...] = ()
    decisions_used: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    consecutive_no_gain: int = Field(default=0, ge=0)
    remaining_tool_budget: tuple[ToolBudgetAvailability, ...] = ()

    @model_validator(mode="after")
    def _validate_snapshot(self) -> "ResearchObservation":
        ledger_requirements = {item.requirement for item in self.coverage_ledger.items}
        for name, requirements in (
            ("attempted_requirements", self.attempted_requirements),
            ("confirmed_unavailable_requirements", self.confirmed_unavailable_requirements),
        ):
            if len(set(requirements)) != len(requirements):
                raise ValueError(f"{name} 不能包含重复 requirement")
            if not set(requirements).issubset(ledger_requirements):
                raise ValueError(f"{name} 必须引用 Coverage Ledger 中的 requirement")
        tool_names = [entry.tool_name for entry in self.remaining_tool_budget]
        if len(set(tool_names)) != len(tool_names):
            raise ValueError("remaining_tool_budget 不能包含重复 tool_name")
        return self


class ResearchDecision(BaseModel):
    """Research Agent 对 Coverage Ledger 作出的受控、可审计下一步决定。"""

    model_config = ConfigDict(frozen=True)

    action: ResearchDecisionAction
    reason: str = Field(min_length=1)
    reason_code: ResearchDecisionReasonCode = ResearchDecisionReasonCode.SUPPLEMENT_REQUIRED
    gap_requirements: tuple[CoverageRequirement, ...] = ()
    requested_node_kinds: tuple[ResearchNodeKind, ...] = ()
    supplement_requests: tuple[SupplementRequest, ...] = ()

    @model_validator(mode="after")
    def _validate_action(self) -> "ResearchDecision":
        if self.action is ResearchDecisionAction.CONTINUE_SEARCH:
            if (
                not self.gap_requirements
                or not self.requested_node_kinds
                or not self.supplement_requests
            ):
                raise ValueError("continue_search 必须说明缺口并请求至少一个具体补证节点")
            if len(set(self.gap_requirements)) != len(self.gap_requirements):
                raise ValueError("continue_search 不能包含重复缺口")
            request_requirements = tuple(
                request.requirement for request in self.supplement_requests
            )
            request_kinds = tuple(request.node_kind for request in self.supplement_requests)
            if self.gap_requirements != request_requirements:
                raise ValueError("continue_search 的缺口必须与补证请求一一对应")
            if self.requested_node_kinds != request_kinds:
                raise ValueError("continue_search 的节点类别必须与补证请求一致")
        if (
            self.action
            in {
                ResearchDecisionAction.READY_FOR_ANALYSIS,
                ResearchDecisionAction.PARTIAL_READY,
                ResearchDecisionAction.BLOCKED,
            }
            and (self.requested_node_kinds or self.supplement_requests)
        ):
            raise ValueError("非 continue_search 决策不得请求新的节点")
        return self


class AnnualComparisonStatus(StrEnum):
    """年度财务比较包的确定性计算状态。"""

    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
