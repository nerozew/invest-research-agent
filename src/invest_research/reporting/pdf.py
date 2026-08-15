"""P06-02 Markdown → PDF 渲染（PyMuPDF，纯本地、无外部字体依赖）。

目标（docs/05 P06-02）：
- 中文渲染：使用 PyMuPDF 内置 CJK 字体 ``china-s``（无需外部字体文件）；
- 链接：``[text](url)`` 渲染为可点击的 link annotation；
- 分页：内容超出页高时自动换页，页脚带页码；
- 输出：``render`` 存文件 / ``render_to_bytes`` 返回字节（供工件存储）；
- 人工确认辅助：``render_page_png`` 把指定页渲染成 PNG 截图。

支持的基础 Markdown 子集（确定性、可测试）：
``#/##/###`` 标题、``-/*`` 无序列表、``1.`` 数字列表、``>`` 引用、
``---`` 分隔线、``| a | b |`` 表格行、普通段落、``[text](url)`` 行内链接。
不在子集内的语法（如 ``**`` 粗体）按普通文本原样输出，不影响渲染。

安全：只渲染纯文本/URL；不执行任何 HTML/脚本；URL 只用于 link annotation。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# mypy: disable-error-code="no-untyped-call,no-any-return,var-annotated,arg-type"
# PyMuPDF 1.28 的 stubs 不完整（多个方法未标注），按调用点逐处豁免噪音太大；
# 本模块只做文本/链接/分页渲染，禁用这几类错误码即可保持严格检查其余部分。

# 内置 CJK 字体：简体中文（PyMuPDF 内置，无外部字体文件）
_CJK_FONT = "china-s"
_MARGIN = 50.0
_LINE_SPACING = 1.45
_PAGE_LIMIT_PAD = _MARGIN  # 页底保留空间（页脚区域）

# Markdown 行内链接：[label](url)
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")

_HEADING_SIZES: dict[str, float] = {"h1": 18.0, "h2": 15.0, "h3": 13.0}
_BODY_SIZE = 10.5
_SMALL_SIZE = 9.0


@dataclass(frozen=True)
class _Block:
    """解析后的 Markdown 块（渲染单元）。"""

    kind: str  # h1/h2/h3/bullet/numbered/quote/table/rule/paragraph
    text: str = ""
    index: int | None = None  # numbered 列表项序号


def _parse_blocks(markdown: str) -> list[_Block]:
    """把 Markdown 文本解析为块列表（逐行分类，确定性）。"""
    blocks: list[_Block] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            blocks.append(_Block(kind=f"h{len(heading.group(1))}", text=heading.group(2)))
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            blocks.append(_Block(kind="rule"))
        elif stripped.startswith("> "):
            blocks.append(_Block(kind="quote", text=stripped[2:]))
        elif re.fullmatch(r"[-*]\s+.*", stripped):
            blocks.append(_Block(kind="bullet", text=re.sub(r"^[-*]\s+", "", stripped)))
        else:
            numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
            if numbered:
                blocks.append(
                    _Block(kind="numbered", text=numbered.group(2), index=int(numbered.group(1)))
                )
            elif stripped.startswith("|") and stripped.endswith("|"):
                blocks.append(_Block(kind="table", text=stripped))
            else:
                blocks.append(_Block(kind="paragraph", text=stripped))
    return blocks


def _split_segments(text: str) -> list[tuple[str, str | None]]:
    """把文本按行内链接切分为 (text, url|None) 段。"""
    segments: list[tuple[str, str | None]] = []
    pos = 0
    for match in _LINK_RE.finditer(text):
        if match.start() > pos:
            segments.append((text[pos : match.start()], None))
        segments.append((match.group(1), match.group(2)))
        pos = match.end()
    if pos < len(text):
        segments.append((text[pos:], None))
    return segments


def _text_width(text: str, fontsize: float) -> float:
    return pymupdf.get_text_length(text, fontname=_CJK_FONT, fontsize=fontsize)


def _wrap_segments(
    segments: list[tuple[str, str | None]], max_width: float, fontsize: float
) -> list[list[tuple[str, str | None]]]:
    """贪心换行：优先在空格处断行，CJK 无空格时按字符断行。"""
    lines: list[list[tuple[str, str | None]]] = []
    cur: list[tuple[str, str | None]] = []
    cur_width = 0.0

    for text, url in segments:
        idx = 0
        while idx < len(text):
            remaining = text[idx:]
            remaining_width = _text_width(remaining, fontsize)
            if cur_width + remaining_width <= max_width:
                cur.append((remaining, url))
                cur_width += remaining_width
                break

            # 当前行放不下整个段：数出能放下的字符数
            fit = 0
            acc = 0.0
            for ch in remaining:
                cw = _text_width(ch, fontsize)
                if acc + cw > max_width - cur_width:
                    break
                acc += cw
                fit += 1
            if fit == 0:
                if cur:
                    lines.append(cur)
                    cur = []
                    cur_width = 0.0
                    continue
                fit = 1  # 单个字符也超宽：强制放一个，避免死循环

            chunk = remaining[:fit]
            space = chunk.rfind(" ")
            if space > 0:
                chunk = chunk[:space]
                if not chunk:  # 回退：整块只有一个空格
                    chunk = remaining[:fit]
            cur.append((chunk, url))
            cur_width += _text_width(chunk, fontsize)
            idx += len(chunk)

    if cur:
        lines.append(cur)
    return lines


class MarkdownPdfRenderer:
    """Markdown → PDF 渲染器（PyMuPDF，A4，内置 CJK 字体）。"""

    def render(self, markdown: str, out_path: Path, *, title: str | None = None) -> Path:
        """渲染 Markdown 到 PDF 文件；空内容抛 ValueError。"""
        out_path.write_bytes(self.render_to_bytes(markdown, title=title))
        return out_path

    def render_to_bytes(self, markdown: str, *, title: str | None = None) -> bytes:
        """渲染 Markdown 到 PDF 字节（供工件存储）。"""
        if not markdown or not markdown.strip():
            raise ValueError("markdown 内容为空，无法渲染 PDF")
        doc = pymupdf.open()
        try:
            page = doc.new_page()  # A4 默认 595x842
            y = _MARGIN
            if title:
                page, y = self._draw_heading(doc, page, y, title, "h1")
                y += 6
            for block in _parse_blocks(markdown):
                page, y = self._draw_block(doc, page, y, block)
            self._draw_footer(doc)
            return doc.tobytes()
        finally:
            doc.close()

    # ---- 块渲染 ----

    def _draw_block(
        self, doc: pymupdf.Document, page: pymupdf.Page, y: float, block: _Block
    ) -> tuple[pymupdf.Page, float]:
        if block.kind in _HEADING_SIZES:
            return self._draw_heading(doc, page, y, block.text, block.kind)
        if block.kind == "bullet":
            return self._draw_paragraph(
                doc, page, y, block.text, _BODY_SIZE, bullet="• ", indent=8
            )
        if block.kind == "numbered":
            bullet = f"{block.index}. "
            return self._draw_paragraph(
                doc, page, y, block.text, _BODY_SIZE, bullet=bullet, indent=14
            )
        if block.kind == "quote":
            return self._draw_paragraph(
                doc, page, y, block.text, _BODY_SIZE, color=(0.35, 0.35, 0.35), indent=12
            )
        if block.kind == "rule":
            return self._draw_rule(doc, page, y)
        if block.kind == "table":
            return self._draw_paragraph(doc, page, y, block.text, _BODY_SIZE)
        return self._draw_paragraph(doc, page, y, block.text, _BODY_SIZE)

    def _draw_heading(
        self, doc: pymupdf.Document, page: pymupdf.Page, y: float, text: str, kind: str
    ) -> tuple[pymupdf.Page, float]:
        fontsize = _HEADING_SIZES[kind]
        lines = _wrap_segments(_split_segments(text), page.rect.width - 2 * _MARGIN, fontsize)
        line_height = fontsize * _LINE_SPACING
        for line in lines:
            page, y = self._ensure_space(doc, page, y, line_height)
            self._draw_line(page, _MARGIN, y, line, fontsize)
            y += line_height
        return page, y + 3

    def _draw_paragraph(
        self,
        doc: pymupdf.Document,
        page: pymupdf.Page,
        y: float,
        text: str,
        fontsize: float,
        *,
        bullet: str | None = None,
        indent: float = 0.0,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> tuple[pymupdf.Page, float]:
        max_width = page.rect.width - 2 * _MARGIN - indent
        lines = _wrap_segments(_split_segments(text), max_width, fontsize)
        line_height = fontsize * _LINE_SPACING
        bullet_width = _text_width(bullet, fontsize) if bullet else 0.0
        for i, line in enumerate(lines):
            page, y = self._ensure_space(doc, page, y, line_height)
            x = _MARGIN + indent
            if bullet is not None and i == 0:
                page.insert_text((x, y), bullet, fontname=_CJK_FONT, fontsize=fontsize)
                x += bullet_width
            self._draw_line(page, x, y, line, fontsize, color=color)
            y += line_height
        return page, y

    def _draw_rule(
        self, doc: pymupdf.Document, page: pymupdf.Page, y: float
    ) -> tuple[pymupdf.Page, float]:
        line_height = _BODY_SIZE * _LINE_SPACING
        page, y = self._ensure_space(doc, page, y, line_height)
        page.draw_line(
            pymupdf.Point(_MARGIN, y),
            pymupdf.Point(page.rect.width - _MARGIN, y),
            color=(0.75, 0.75, 0.75),
            width=0.7,
        )
        return page, y + line_height

    # ---- 基础绘制 ----

    def _ensure_space(
        self, doc: pymupdf.Document, page: pymupdf.Page, y: float, needed: float
    ) -> tuple[pymupdf.Page, float]:
        if y + needed > page.rect.height - _PAGE_LIMIT_PAD:
            page = doc.new_page()
            return page, _MARGIN
        return page, y

    def _draw_line(
        self,
        page: pymupdf.Page,
        x: float,
        y: float,
        line: list[tuple[str, str | None]],
        fontsize: float,
        *,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        """绘制一行（支持行内链接）；画完后为链接段添加 link annotation。"""
        links: list[tuple[pymupdf.Rect, str]] = []
        for text, url in line:
            page.insert_text((x, y), text, fontname=_CJK_FONT, fontsize=fontsize, color=color)
            width = _text_width(text, fontsize)
            if url is not None:
                rect = pymupdf.Rect(x, y - fontsize * 0.95, x + width, y + 2)
                links.append((rect, url))
            x += width
        for rect, url in links:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": url})

    def _draw_footer(self, doc: pymupdf.Document) -> None:
        """页脚页码：第 i / n 页（右下角）。"""
        total = len(doc)
        for i, page in enumerate(doc):
            text = f"第 {i + 1} / {total} 页"
            width = _text_width(text, _SMALL_SIZE)
            page.insert_text(
                (page.rect.width - _MARGIN - width, page.rect.height - 25),
                text,
                fontname=_CJK_FONT,
                fontsize=_SMALL_SIZE,
                color=(0.45, 0.45, 0.45),
            )


def render_page_png(pdf_path: Path, page_index: int, out_path: Path, dpi: int = 150) -> Path:
    """把 PDF 指定页渲染为 PNG 截图（供人工视觉确认）。"""
    doc = pymupdf.open(pdf_path)
    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError(f"page_index {page_index} 超出范围（共 {len(doc)} 页）")
        pix = doc[page_index].get_pixmap(dpi=dpi)
        pix.save(str(out_path))
    finally:
        doc.close()
    return out_path
