"""SEC HTML 解析器（P02-09）：保留标题/章节/纯文本/locator。"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIP_TAGS = frozenset({"script", "style"})


@dataclass(frozen=True)
class ParsedTextBlock:
    text: str
    tag: str = "text"
    is_heading: bool = False
    location: int = 0


@dataclass(frozen=True)
class ParsedDocument:
    blocks: tuple[ParsedTextBlock, ...] = ()
    headings: tuple[ParsedTextBlock, ...] = ()


class _SecHTMLParser(HTMLParser):
    """收集可见文本；识别 h1-h6 为标题块；跳过 script/style 内容；累计偏移作 locator。"""

    def __init__(self) -> None:
        super().__init__()
        self._blocks: list[ParsedTextBlock] = []
        self._headings: list[ParsedTextBlock] = []
        self._pending_heading = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth <= 0 and tag in _HEADING_TAGS:
            self._pending_heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        self._pending_heading = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        stripped = " ".join(data.split())
        if not stripped:
            return
        loc = sum(len(b.text) for b in self._blocks)
        block = ParsedTextBlock(
            text=stripped,
            tag="h*" if self._pending_heading else "text",
            is_heading=self._pending_heading,
            location=loc,
        )
        self._blocks.append(block)
        if self._pending_heading:
            self._headings.append(block)


def parse_html(html: str) -> ParsedDocument:
    parser = _SecHTMLParser()
    parser.feed(html)
    parser.close()
    return ParsedDocument(blocks=tuple(parser._blocks), headings=tuple(parser._headings))
