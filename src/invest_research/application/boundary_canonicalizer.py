"""P06-11E：BoundaryCanonicalizer（语义等价的边界规范化器）。

只允许语义等价规范化（不改变业务含义）：
- ``Optional[str]`` 的 ``""`` / 纯空白 → ``None``；
- 清理首尾空白（字符串字段/字符串列表项）；
- **不修改数字**；
- **不修改枚举**；
- **不补 company_id / source_id**；
- **不生成不存在的事实**；
- ``unavailable + 空原因`` 仍然失败（规范化为 None 后交由 Pydantic
  跨字段校验拒绝，本模块不阻止失败）。

字段逐项审计（白名单）：
- ``AnalysisSelectionDraft.unavailable_reason``；
- ``FinancialAnalysisPack.unavailable_reason``；
- 两个模型的 ``analysis_notes``（strip）、``limitations``（逐项 strip）。
- 其它字段必须逐项审计，不允许全局任意转换。未声明的字段原样保留。

依赖方向：application → domain（Pydantic 模型）+ 标准库。
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from invest_research.domain.models import (
    AnalysisSelectionDraft,
    FinancialAnalysisPack,
)

T = TypeVar("T", bound=BaseModel)


def _empty_to_none(value: Any) -> Any:
    """Optional[str] 的 ""/纯空白 → None；其余原样返回。"""
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned
    return value


def _strip_str(value: Any) -> Any:
    """清理首尾空白（仅字符串）；非字符串原样返回。"""
    return value.strip() if isinstance(value, str) else value


def _strip_str_list(values: Any) -> Any:
    """逐项清理字符串列表首尾空白；非列表原样返回。"""
    if isinstance(values, list):
        return [_strip_str(item) for item in values]
    return values


# ---------------------------------------------------------------------------
# 字段白名单（逐项审计，禁止全局任意转换）
# ---------------------------------------------------------------------------

# 空/纯空白 → None（仅限 Optional[str] 语义字段）
_EMPTY_TO_NONE_FIELDS: dict[type[BaseModel], frozenset[str]] = {
    AnalysisSelectionDraft: frozenset({"unavailable_reason"}),
    FinancialAnalysisPack: frozenset({"unavailable_reason"}),
}

# 仅清理首尾空白（不改变语义，不制造/删除内容）
_STRIP_FIELDS: dict[type[BaseModel], frozenset[str]] = {
    AnalysisSelectionDraft: frozenset({"analysis_notes"}),
    FinancialAnalysisPack: frozenset({"analysis_notes"}),
}

# 字符串列表逐项清理首尾空白
_STRIP_LIST_FIELDS: dict[type[BaseModel], frozenset[str]] = {
    AnalysisSelectionDraft: frozenset({"limitations", "selected_fact_refs"}),
    FinancialAnalysisPack: frozenset({"limitations"}),
}


class BoundaryCanonicalizer:
    """语义等价规范化器：输入模型 dict，输出规范化后的 dict（不修改原始对象）。"""

    def canonicalize_for(
        self,
        model: type[T],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """按模型类型白名单规范化 dict。

        - 未在任一白名单中的字段**原样保留**（不修改数字/枚举/嵌套对象）；
        - 返回新 dict，不修改输入的 ``data``。
        """
        out = dict(data)
        for field in _EMPTY_TO_NONE_FIELDS.get(model, frozenset()):
            if field in out:
                out[field] = _empty_to_none(out[field])
        for field in _STRIP_FIELDS.get(model, frozenset()):
            if field in out:
                out[field] = _strip_str(out[field])
        for field in _STRIP_LIST_FIELDS.get(model, frozenset()):
            if field in out:
                out[field] = _strip_str_list(out[field])
        return out
