"""P03-20 受控反思路由测试（ReflectionController，纯函数，不联网）。

验证目标（docs/05 P03-20）：
- NONE（无警告）→ publish；NONE + 警告 → publish_partial；
- REVISE_REPORT 且修订未耗尽 → revise（计数 +1）；再出现 → repeat_reject（耗尽）；
- SUPPLEMENT_RESEARCH 且补证未耗尽 → supplement（计数 +1）；再出现 → repeat_reject；
- REJECT → reject（无循环）；
- history 审计记录含 action/计数/outcome。
"""

from __future__ import annotations

from invest_research.domain.quality import QualityAction
from invest_research.flows.reflection import (
    MAX_REVISION_COUNT,
    MAX_SUPPLEMENT_COUNT,
    ReflectionController,
)


def test_publish() -> None:
    ctrl = ReflectionController()
    step = ctrl.step(QualityAction.NONE, 0, 0)
    assert step.outcome == "publish"
    assert step.revision_used == 0 and step.supplement_used == 0


def test_publish_partial_on_warnings() -> None:
    ctrl = ReflectionController()
    step = ctrl.step(QualityAction.NONE, 0, 0, has_warnings=True)
    assert step.outcome == "publish_partial"


def test_revise_once_then_reject_on_repeat() -> None:
    ctrl = ReflectionController()
    first = ctrl.step(QualityAction.REVISE_REPORT, 0, 0)
    assert first.outcome == "revise"
    assert first.revision_used == 1
    second = ctrl.step(QualityAction.REVISE_REPORT, first.revision_used, 0)
    assert second.outcome == "repeat_reject"
    assert second.revision_used == MAX_REVISION_COUNT


def test_supplement_once_then_reject_on_repeat() -> None:
    ctrl = ReflectionController()
    first = ctrl.step(QualityAction.SUPPLEMENT_RESEARCH, 0, 0)
    assert first.outcome == "supplement"
    assert first.supplement_used == 1
    second = ctrl.step(QualityAction.SUPPLEMENT_RESEARCH, 0, first.supplement_used)
    assert second.outcome == "repeat_reject"
    assert second.supplement_used == MAX_SUPPLEMENT_COUNT


def test_reject_no_loop() -> None:
    ctrl = ReflectionController()
    step = ctrl.step(QualityAction.REJECT, 0, 0)
    assert step.outcome == "reject"


def test_history_is_auditable() -> None:
    ctrl = ReflectionController()
    ctrl.step(QualityAction.NONE, 0, 0)
    ctrl.step(QualityAction.REVISE_REPORT, 0, 0)
    ctrl.step(QualityAction.REJECT, 1, 0)
    assert len(ctrl.history) == 3
    assert [h.outcome for h in ctrl.history] == ["publish", "revise", "reject"]
