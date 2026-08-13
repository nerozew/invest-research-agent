"""P03-16 结构化质量问题与修订请求测试（纯 Pydantic，不联网）。

验证目标（docs/05 P03-16 验收）：
- 6 个类型可用：QualitySeverity / QualityAction / QualityRecommendation /
  QualityIssue / RevisionRequest / SupplementResearchRequest；
- JSON round-trip：序列化→反序列化往返无损；
- 非法枚举被拒（ValidationError）；
- 空问题列表可序列化（边界，判定由调用方负责）；
- 次数边界：revision_number >= 1、supplement attempt_number >= 0；
- 模型不可变（frozen）。
- QualityReport.recommendation 已收紧为 QualityRecommendation（结合现有模型，不重复定义）。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from invest_research.domain.models import QualityReport
from invest_research.domain.quality import (
    QualityAction,
    QualityIssue,
    QualityRecommendation,
    QualitySeverity,
    RevisionRequest,
    SupplementResearchRequest,
)


def _issue() -> QualityIssue:
    return QualityIssue(
        code="missing_section",
        severity=QualitySeverity.ERROR,
        stage="writer",
        message="报告缺少执行摘要章节",
        action=QualityAction.REVISE_REPORT,
        related_claim="claim-1",
        citation_key="cite-1",
    )


def test_enum_values_stable() -> None:
    assert QualitySeverity.WARNING.value == "warning"
    assert QualityRecommendation.REJECT.value == "rejected"
    assert QualityRecommendation.PUBLISH.value == "published"
    assert QualityAction.SUPPLEMENT_RESEARCH.value == "supplement_research"


def test_quality_report_recommendation_is_enum() -> None:
    """结合现有 QualityReport：recommendation 已是枚举，不是裸字符串。"""
    report = QualityReport(
        version="quality_report_v1",
        all_passed=True,
        recommendation="published",  # 字符串自动解析为枚举
    )
    assert report.recommendation is QualityRecommendation.PUBLISH


def test_quality_issue_roundtrip() -> None:
    restored = QualityIssue.model_validate_json(_issue().model_dump_json())
    assert restored == _issue()


def test_revision_request_roundtrip() -> None:
    req = RevisionRequest(
        issues=[_issue()],
        revision_number=1,
        original_draft_version="report_draft_v1",
        allowed_actions=(QualityAction.REVISE_REPORT,),
        forbidden_actions=(QualityAction.REJECT,),
    )
    restored = RevisionRequest.model_validate_json(req.model_dump_json())
    assert restored == req


def test_supplement_request_roundtrip() -> None:
    req = SupplementResearchRequest(
        missing_evidence="缺少近三年收入数据",
        related_claim=None,
        required_source_type="sec_filing",
        as_of_date=date(2025, 12, 31),
        attempt_number=1,
    )
    restored = SupplementResearchRequest.model_validate_json(req.model_dump_json())
    assert restored == req


def test_invalid_severity_rejected() -> None:
    with pytest.raises(ValidationError):
        QualityIssue(
            code="x",
            severity="fatal",  # 不在枚举
            stage="writer",
            message="m",
            action=QualityAction.REVISE_REPORT,
        )


def test_invalid_action_rejected() -> None:
    with pytest.raises(ValidationError):
        QualityIssue(
            code="x",
            severity=QualitySeverity.ERROR,
            stage="writer",
            message="m",
            action="publish_anyway",  # 不在 QualityAction
        )


def test_revision_empty_issues_serializable() -> None:
    """空问题列表仍可序列化（判定由调用方负责，模型不阻断）。"""
    req = RevisionRequest(
        issues=[],
        revision_number=1,
        original_draft_version="report_draft_v1",
    )
    restored = RevisionRequest.model_validate_json(req.model_dump_json())
    assert restored.issues == []


def test_quality_issue_optional_claim_fields() -> None:
    """related_claim / citation_key 可空（如"缺章节"问题不挂单个 claim）。"""
    issue = QualityIssue(
        code="missing_section",
        severity=QualitySeverity.ERROR,
        stage="writer",
        message="缺章节",
        action=QualityAction.REVISE_REPORT,
    )
    assert issue.related_claim is None
    assert issue.citation_key is None


def test_revision_number_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RevisionRequest(
            issues=[],
            revision_number=0,
            original_draft_version="v1",
        )


def test_supplement_attempt_number_ge_zero() -> None:
    """attempt_number >= 0；超过补证上限（如 2）由调用方路由拒绝，模型只约束非负。"""
    req = SupplementResearchRequest(
        missing_evidence="x",
        as_of_date=date(2025, 12, 31),
        attempt_number=0,
    )
    assert req.attempt_number == 0
    with pytest.raises(ValidationError):
        SupplementResearchRequest(
            missing_evidence="x",
            as_of_date=date(2025, 12, 31),
            attempt_number=-1,
        )


def test_models_are_frozen() -> None:
    issue = _issue()
    with pytest.raises(ValidationError):
        issue.code = "changed"  # type: ignore[misc]  # frozen 拒绝赋值

    req = RevisionRequest(issues=[], revision_number=1, original_draft_version="v1")
    with pytest.raises(ValidationError):
        req.revision_number = 2  # type: ignore[misc]
