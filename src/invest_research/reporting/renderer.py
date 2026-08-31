"""P06-01 用 Jinja2 固化 Markdown 报告模板。

目标（docs/05 P06-01）：
- 报告骨架（封面信息、来源清单与引用、结构化数据限制、固定非投资建议声明）
  由 Jinja2 模板**确定性**生成：同一输入必然得到同一输出（golden test 逐字节比对）；
- 报告正文（Writer 初稿的 ``ReportDraft.markdown``）原样嵌入，不篡改 grounded content；
- 章节与引用稳定：模板固定章节不随 LLM 输出漂移，来源清单按结构化数据渲染。

安全边界：
- 渲染输入只接受白名单字段（``ReportRenderInput``），不含任何密钥/请求详情；
- 模板对表格单元格做 ``md_cell`` 转义（竖线/换行），防止注入破坏结构。

依赖方向：本模块只依赖 domain/flow 模型 + jinja2，不导入 CrewAI/FastAPI。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field

from invest_research.flows.state import ResearchFlowState

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 固定免责声明：模板总是输出该文本，即使 LLM 初稿遗漏也不受影响。
DEFAULT_DISCLAIMER = (
    "本报告由 invest-research 自动化系统生成，内容仅用于信息整理与技术演示，"
    "不构成任何投资建议、要约或收益承诺。投资者在做出任何投资决策前，"
    "应咨询专业财务顾问，并直接查阅公司向美国证券交易委员会（SEC）提交的原始文件。"
    "投资涉及风险，可能导致本金损失。"
)

_LANGUAGE_LABELS: dict[str, str] = {
    "zh-CN": "中文",
    "en": "English",
}


class ReportSource(BaseModel):
    """来源清单中的一条（渲染用；只取白名单字段，防止任意对象注入模板）。"""

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    url: str = Field(min_length=1)
    locator: str | None = None
    publisher: str | None = None


class ReportRenderInput(BaseModel):
    """报告渲染输入：全部为模板白名单字段，绝不包含密钥。

    ``generated_at`` 由调用方注入固定值（golden test 传固定时间保证可复现）。
    """

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    ticker: str | None = None
    cik: str | None = None
    as_of_date: str = Field(min_length=1)  # ISO 日期
    generated_at: str = Field(min_length=1)  # ISO 时间
    language: str = "zh-CN"
    body_markdown: str = Field(min_length=1)
    citation_keys: list[str] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    disclaimer: str = DEFAULT_DISCLAIMER


def _md_cell(value: object) -> str:
    """Markdown 表格单元格转义：竖线→\\|、换行→空格、空值→N/A。"""
    text = str(value).strip() if value is not None else ""
    if not text:
        return "N/A"
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _language_label(value: str) -> str:
    return _LANGUAGE_LABELS.get(value, value)


def _source_line(src: ReportSource) -> str:
    """把一条来源渲染成列表项文本（在 Python 侧拼装，避免模板行尾块标签吞换行）。

    - 标题 + 发布方作为可点击链接的标签（``[text](url)``），Markdown 与 PDF 都生成链接；
    - 标签内只剔除 ``[]`` 字符（会破坏 Markdown 链接语法），括号可保留。
    """
    label = _md_cell(src.title or "来源")
    if src.publisher:
        label += f"（{_md_cell(src.publisher)}）"
    label = label.replace("[", "").replace("]", "")
    parts: list[str] = [f"[{label}]({src.url})"]
    if src.locator:
        parts.append(f"（定位：{_md_cell(src.locator)}）")
    return "".join(parts)


def _strip_leading_report_h1(markdown: str) -> str:
    """Strip the writer's leading H1 because the template owns the report title."""
    lines = markdown.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return markdown
    match = re.fullmatch(r"#\s+(.+?)\s*", lines[first].strip())
    if match is None:
        return markdown
    del lines[first]
    while first < len(lines) and not lines[first].strip():
        del lines[first]
    return "\n".join(lines)


class ReportRenderer:
    """加载 Jinja2 模板并渲染报告 Markdown（确定性：同输入必同输出）。"""

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=False,  # 输出的是 Markdown 而非 HTML
            undefined=StrictUndefined,  # 模板漏字段立即报错，不静默输出空串
            trim_blocks=True,  # 去掉块标签后的换行；不用 lstrip_blocks（会吞掉列表项之间的换行）
            keep_trailing_newline=True,
        )
        self._env.filters["md_cell"] = _md_cell
        self._env.filters["language_label"] = _language_label
        self._env.globals["source_line"] = _source_line

    def render(self, data: ReportRenderInput) -> str:
        """渲染完整报告 Markdown。"""
        template = self._env.get_template("report.md.j2")
        return template.render(data=data)


def build_render_input(
    state: ResearchFlowState,
    generated_at: datetime | None = None,
) -> ReportRenderInput | None:
    """从 Flow state 构建渲染输入；缺少 report_draft 时返回 None（无法渲染）。"""
    draft = state.report_draft
    if draft is None:
        return None

    request = state.request
    research = state.research_pack
    analysis = state.analysis_pack

    identity = research.company_identity if research is not None else None
    legal_name = (
        identity.legal_name
        if identity is not None
        else request.input_company
        if request is not None
        else "N/A"
    )
    sources: list[ReportSource] = []
    if research is not None:
        for src in research.sources:
            sources.append(
                ReportSource(
                    title=src.title,
                    url=src.canonical_url,
                    locator=src.locator,
                    publisher=src.publisher,
                )
            )

    limitations: list[str] = list(analysis.limitations) if analysis is not None else []
    gen = (
        generated_at.isoformat(timespec="seconds")
        if generated_at is not None
        else datetime.now().isoformat(timespec="seconds")
    )
    return ReportRenderInput(
        title=draft.title,
        legal_name=legal_name,
        ticker=identity.ticker if identity is not None else None,
        cik=identity.cik if identity is not None else None,
        as_of_date=request.as_of_date.isoformat() if request is not None else "",
        generated_at=gen,
        language=request.language if request is not None else "zh-CN",
        body_markdown=_strip_leading_report_h1(draft.markdown),
        citation_keys=list(draft.citation_keys),
        sources=sources,
        limitations=limitations,
    )
