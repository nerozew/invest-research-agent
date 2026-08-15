"""P06-01：报告渲染包（Jinja2 固化 Markdown 模板）。

包含：
- ``renderer.ReportRenderer``：确定性 Markdown 渲染（同输入必同输出）；
- ``renderer.ReportRenderInput``：渲染输入（全部白名单字段，无密钥）；
- ``renderer.build_render_input``：从 ``ResearchFlowState`` 构建渲染输入。
"""

from invest_research.reporting.renderer import (
    DEFAULT_DISCLAIMER,
    ReportRenderer,
    ReportRenderInput,
    ReportSource,
    build_render_input,
)

__all__ = [
    "DEFAULT_DISCLAIMER",
    "ReportRenderInput",
    "ReportRenderer",
    "ReportSource",
    "build_render_input",
]
