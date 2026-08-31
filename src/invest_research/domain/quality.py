"""P03-16 结构化质量问题与修订请求（Phase 3.5，域层纯 Pydantic）。

对齐 docs/04 §2.5 与 docs/01 §7.5：质量门禁输出结构化问题，由受控路由决定
发布 / 定向修订 / 补证 / 拒绝。本模块只定义数据契约，不含门禁逻辑。

- ``QualityReport`` 保留在 ``domain/models.py``（避免重复模型），
  仅将其 ``recommendation`` 收紧为 ``QualityRecommendation`` 枚举；
- ``QualityIssue`` 是单条结构化问题（code/severity/stage/message/action/…）；
- ``RevisionRequest`` 承载"定向修订"的输入（问题 + 修订序号 + 允许/禁止动作）；
- ``SupplementResearchRequest`` 承载"补证"请求（缺什么证据、来源类型、as-of、次数）。

依赖边界：本层只允许标准库与 Pydantic，禁止导入 CrewAI/FastAPI/SQLAlchemy。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# 枚举值用小写字符串，与现有 QualityReport.recommendation 的 "published"/"rejected"
# 兼容，确保"收紧为枚举"不回退门禁行为。


class QualitySeverity(StrEnum):
    """问题严重级别。"""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class QualityAction(StrEnum):
    """质量门禁建议的下一步动作（由 ReflectionController 执行）。"""

    NONE = "none"
    REVISE_REPORT = "revise_report"
    SUPPLEMENT_RESEARCH = "supplement_research"
    REANALYZE = "reanalyze"
    REJECT = "reject"


class QualityRecommendation(StrEnum):
    """最终发布建议（与现有 QualityReport.recommendation 兼容）。"""

    PUBLISH = "published"
    PUBLISH_PARTIAL = "publish_partial"
    REVISE = "revise"
    REJECT = "rejected"


class QualityIssue(BaseModel):
    """单条结构化质量问题（可被序列化、可喂回定向修订）。

    - ``related_claim`` / ``citation_key`` 可空：部分问题（如"缺章节"）不挂在单个 claim。
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    severity: QualitySeverity
    stage: str = Field(min_length=1)
    message: str = Field(min_length=1)
    action: QualityAction
    related_claim: str | None = None
    citation_key: str | None = None


class RevisionRequest(BaseModel):
    """Writer 定向修订请求（P03-18 输入）。

    - ``issues`` 允许为空列表（空修订请求仍可序列化，边界由调用方校验）；
    - ``allowed_actions`` / ``forbidden_actions`` 限定本次修订边界（防御越权行为）。
    """

    model_config = ConfigDict(frozen=True)

    issues: list[QualityIssue] = Field(default_factory=list)
    revision_number: int = Field(ge=1)
    original_draft_version: str = Field(min_length=1)
    allowed_actions: tuple[QualityAction, ...] = ()
    forbidden_actions: tuple[QualityAction, ...] = ()


class SupplementResearchRequest(BaseModel):
    """补证请求（P03-19 输入）。

    - ``required_source_type`` 用字符串承载来源类型（如 sec_filing / web），
      避免在本层依赖具体 SourceType 以防循环 import；P03-19 可收紧。
    - ``attempt_number`` ge=0：0 表示初始请求，1 表示第一次补证（最多一次）。
    """

    model_config = ConfigDict(frozen=True)

    missing_evidence: str = Field(min_length=1)
    related_claim: str | None = None
    required_source_type: str | None = None
    as_of_date: date
    attempt_number: int = Field(default=0, ge=0)
