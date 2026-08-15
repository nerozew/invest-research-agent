"""P06-01/02：报告渲染包（Jinja2 固化 Markdown 模板 + Markdown→PDF 渲染）。

包含：
- ``renderer.ReportRenderer``：确定性 Markdown 渲染（同输入必同输出）；
- ``renderer.ReportRenderInput``：渲染输入（全部白名单字段，无密钥）；
- ``renderer.build_render_input``：从 ``ResearchFlowState`` 构建渲染输入；
- ``pdf.MarkdownPdfRenderer``：Markdown→PDF（内置 CJK 字体、链接、分页）；
- ``pdf.render_page_png``：PDF 页 → PNG 截图（人工视觉确认辅助）。
"""

from invest_research.reporting.pdf import MarkdownPdfRenderer, render_page_png
from invest_research.reporting.renderer import (
    DEFAULT_DISCLAIMER,
    ReportRenderer,
    ReportRenderInput,
    ReportSource,
    build_render_input,
)

__all__ = [
    "DEFAULT_DISCLAIMER",
    "MarkdownPdfRenderer",
    "ReportRenderInput",
    "ReportRenderer",
    "ReportSource",
    "build_render_input",
    "render_page_png",
]
