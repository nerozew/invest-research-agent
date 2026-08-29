"""前端研究模式的受控请求参数。

这里仅定义产品入口约束：年度模式不让用户在 UI 中误选 10-Q 或 fast；后端 API
仍保持兼容，不在此处替代领域层校验。
"""

from __future__ import annotations

from invest_research.domain.annual_pipeline import ResearchMode


def request_options_for_mode(
    research_mode: ResearchMode,
    *,
    requested_forms: tuple[str, ...],
    research_profile: str,
) -> tuple[tuple[str, ...], str]:
    """返回模式对应的表单与档位；annual_deep 始终使用 10-K 和 deep。"""
    if research_mode is ResearchMode.ANNUAL_DEEP:
        return ("10-K",), "deep"
    return requested_forms, research_profile
