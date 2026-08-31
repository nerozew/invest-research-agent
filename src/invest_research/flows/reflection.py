"""P03-20 受控反思路由（ReflectionController，确定性，不直接调 Agent）。

对齐 docs/04 §2.5（路由决策表）与 docs/05 P03-20：
- 无问题 → PUBLISH；仅非关键警告 → PUBLISH_PARTIAL；
- 可修复（REVISE）且修订次数未耗尽 → REVISE（一次）；
- 缺证据（补证）且次数未耗尽 → SUPPLEMENT（一次）；
- 数字篡改/关键引用仍缺/次数耗尽 → REJECT。

次数硬上限：revision ≤ MAX_REVISION_COUNT、supplement ≤ MAX_SUPPLEMENT_COUNT；
超出或重复 → REJECT（无无限循环）。
每次决策与已用次数都记录为结构化 attempt log（审计）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from invest_research.domain.quality import QualityAction

MAX_REVISION_COUNT = 1
MAX_SUPPLEMENT_COUNT = 1


@dataclass(frozen=True)
class ReflectionAttempt:
    """单轮反思尝试的审计记录。"""

    action: QualityAction
    revision_used: int
    supplement_used: int
    outcome: str  # publish / publish_partial / revise / supplement / repeat_reject / reject


@dataclass
class ReflectionController:
    """受控反思路由器：按质量建议 + 计数给出唯一动作。

    用法（由 P03-21 E2E 驱动）：
        ctrl = ReflectionController()
        step = ctrl.step(rec, revision_used=..., supplement_used=...)
        # 再依 step.outcome 调 revision / supplement / 终态
    """

    history: list[ReflectionAttempt] = field(default_factory=list)

    def step(
        self,
        action: QualityAction,
        revision_used: int,
        supplement_used: int,
        has_warnings: bool = False,
    ) -> ReflectionAttempt:
        """根据质量动作与已用次数，决定唯一下一步。

        - NONE：无问题 → publish；仅警告 → publish_partial；
        - REVISE_REPORT：修订 ≤1 次；再出现 → repeat_reject；
        - SUPPLEMENT_RESEARCH：补证 ≤1 次；再出现 → repeat_reject；
        - 其余（REJECT 等）→ reject（无循环）。
        """
        if action == QualityAction.NONE:
            outcome = "publish_partial" if has_warnings else "publish"
            return self._record(action, revision_used, supplement_used, outcome)

        if action == QualityAction.REVISE_REPORT:
            if revision_used >= MAX_REVISION_COUNT:
                return self._record(action, revision_used, supplement_used, "repeat_reject")
            return self._record(action, revision_used + 1, supplement_used, "revise")

        if action == QualityAction.SUPPLEMENT_RESEARCH:
            if supplement_used >= MAX_SUPPLEMENT_COUNT:
                return self._record(action, revision_used, supplement_used, "repeat_reject")
            return self._record(action, revision_used, supplement_used + 1, "supplement")

        # REJECT / REANALYZE / 未知 → reject（无循环）
        return self._record(action, revision_used, supplement_used, "reject")

    def _record(
        self,
        act: QualityAction,
        rev: int,
        sup: int,
        outcome: str,
    ) -> ReflectionAttempt:
        attempt = ReflectionAttempt(
            action=act, revision_used=rev, supplement_used=sup, outcome=outcome
        )
        self.history.append(attempt)
        return attempt
