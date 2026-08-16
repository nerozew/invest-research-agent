"""P06-02 Markdown to PDF rendering with PyMuPDF Story.

The first implementation drew every Markdown line as plain text. It produced a
technically valid PDF, but exposed Markdown markers, used CJK glyph metrics for
Latin text, and split tables at arbitrary positions. This implementation first
parses a deliberately small CommonMark profile to safe HTML, then lets MuPDF's
layout engine handle typography, tables, links, and pagination.

No HTML from the report is executed: markdown-it-py is configured with
html=False and link validation remains enabled. The renderer never fetches
remote assets.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pymupdf
from markdown_it import MarkdownIt

# mypy: disable-error-code="no-untyped-call,no-any-return,arg-type"
# PyMuPDF's Story / DocumentWriter stubs are incomplete in 1.28.x.

_MARGIN = 50.0
_FOOTER_HEIGHT = 28.0
_SMALL_SIZE = 9.0
_CJK_FONT = "china-s"

_CSS = """
body {
  color: #172033;
  font-family: sans-serif;
  font-size: 10.5pt;
  line-height: 1.48;
  margin: 0;
}
h1, h2, h3 {
  color: #101828;
  font-weight: bold;
  break-after: avoid;
  page-break-after: avoid;
}
h1 { font-size: 20pt; margin: 0 0 12pt 0; }
h2 {
  font-size: 15pt;
  margin: 14pt 0 6pt 0;
  padding-bottom: 3pt;
  border-bottom: 0.6pt solid #d0d5dd;
}
h3 { font-size: 12.5pt; margin: 10pt 0 4pt 0; }
p { margin: 0 0 7pt 0; }
blockquote {
  color: #475467;
  background: #f8fafc;
  border-left: 3pt solid #98a2b3;
  margin: 5pt 0 9pt 0;
  padding: 5pt 8pt;
}
ul, ol { margin: 3pt 0 8pt 18pt; padding: 0; }
li { margin: 0 0 3pt 0; }
strong { font-weight: bold; color: #101828; }
code {
  font-family: monospace;
  font-size: 9pt;
  color: #344054;
  background: #f2f4f7;
  padding: 1pt 2pt;
}
a { color: #175cd3; text-decoration: underline; }
hr { border: 0; border-top: 0.7pt solid #d0d5dd; margin: 10pt 0; }
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 9.2pt;
  margin: 6pt 0 10pt 0;
  page-break-inside: auto;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td {
  border: 0.6pt solid #b9c0cc;
  padding: 4pt 5pt;
  text-align: left;
  vertical-align: top;
}
th { background: #eef2f6; color: #101828; font-weight: bold; }
"""


def _markdown_parser() -> MarkdownIt:
    """Return the deterministic, HTML-disabled Markdown parser."""
    parser = MarkdownIt(
        "commonmark",
        options_update={
            "html": False,
            "linkify": False,
            "typographer": False,
            "breaks": False,
        },
    )
    parser.enable("table")
    return parser


def _normalise_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip("#").strip()).casefold()


def strip_duplicate_leading_h1(markdown: str, title: str | None) -> str:
    """Remove a leading H1 only when it duplicates the supplied report title."""
    if not title:
        return markdown
    lines = markdown.splitlines()
    first_content = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_content is None:
        return markdown
    match = re.fullmatch(r"#\s+(.+?)\s*", lines[first_content].strip())
    if match is None or _normalise_heading(match.group(1)) != _normalise_heading(title):
        return markdown
    del lines[first_content]
    while first_content < len(lines) and not lines[first_content].strip():
        del lines[first_content]
    return "\n".join(lines)


def _page_rects(_: int, __: pymupdf.Rect) -> tuple[pymupdf.Rect, pymupdf.Rect, pymupdf.Matrix]:
    media = pymupdf.paper_rect("a4")
    content = pymupdf.Rect(
        _MARGIN,
        _MARGIN,
        media.width - _MARGIN,
        media.height - _MARGIN - _FOOTER_HEIGHT,
    )
    return media, content, pymupdf.Identity


class MarkdownPdfRenderer:
    """Render a safe Markdown subset to an A4 PDF."""

    def render(self, markdown: str, out_path: Path, *, title: str | None = None) -> Path:
        """Render Markdown to out_path; its parent directory must exist."""
        out_path.write_bytes(self.render_to_bytes(markdown, title=title))
        return out_path

    def render_to_bytes(self, markdown: str, *, title: str | None = None) -> bytes:
        """Render Markdown to PDF bytes without network or filesystem lookups."""
        if not markdown or not markdown.strip():
            raise ValueError("markdown 内容为空，无法渲染 PDF")

        body = strip_duplicate_leading_h1(markdown, title)
        body_html = _markdown_parser().render(body)
        title_html = f"<h1>{html.escape(title)}</h1>" if title else ""
        story = pymupdf.Story(
            f"<html><body>{title_html}{body_html}</body></html>",
            user_css=_CSS,
        )
        doc = story.write_with_links(_page_rects)
        try:
            self._remove_empty_links(doc)
            if title:
                doc.set_metadata({**doc.metadata, "title": title, "creator": "invest-research"})
            self._draw_footer(doc)
            return doc.tobytes(garbage=3, deflate=True)
        finally:
            doc.close()

    @staticmethod
    def _remove_empty_links(doc: pymupdf.Document) -> None:
        """Drop zero-area continuation annotations emitted by Story at font boundaries."""
        for page_index in range(len(doc)):
            page: pymupdf.Page = doc[page_index]
            for link in page.get_links():
                rect = link.get("from")
                if rect is not None and (rect.width <= 0 or rect.height <= 0):
                    page.delete_link(link)

    @staticmethod
    def _draw_footer(doc: pymupdf.Document) -> None:
        total = len(doc)
        for index in range(total):
            page: pymupdf.Page = doc[index]
            text = f"第 {index + 1} / {total} 页"
            width = pymupdf.get_text_length(text, fontname=_CJK_FONT, fontsize=_SMALL_SIZE)
            page.insert_text(
                (page.rect.width - _MARGIN - width, page.rect.height - 25),
                text,
                fontname=_CJK_FONT,
                fontsize=_SMALL_SIZE,
                color=(0.45, 0.45, 0.45),
            )


def render_page_png(pdf_path: Path, page_index: int, out_path: Path, dpi: int = 150) -> Path:
    """Render one PDF page to PNG for visual inspection."""
    doc = pymupdf.open(pdf_path)
    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError(f"page_index {page_index} 超出范围（共 {len(doc)} 页）")
        pix = doc[page_index].get_pixmap(dpi=dpi, alpha=False)
        pix.save(str(out_path))
    finally:
        doc.close()
    return out_path
