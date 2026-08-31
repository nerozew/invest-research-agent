"""PDF 解析器主路径（P02-10）：PyMuPDF 按页提取文本。"""

from __future__ import annotations

from dataclasses import dataclass


class PDFParseError(Exception):
    """PDF 无法解析（损坏/加密/非 PDF）。"""


@dataclass(frozen=True)
class PDFTextBlock:
    text: str
    page_number: int  # 1-based


@dataclass(frozen=True)
class ParsedPDF:
    blocks: tuple[PDFTextBlock, ...] = ()


def parse_pdf(content: bytes) -> ParsedPDF:
    """解析 PDF 字节，返回按页文本块；空页跳过；无效/加密抛 PDFParseError。"""
    import fitz  # type: ignore[import-untyped]  # PyMuPDF 无 stub，按需豁免

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PDFParseError(f"无法打开 PDF: {exc}") from exc

    if document.is_encrypted:
        raise PDFParseError("PDF 已加密，无法解析")

    blocks: list[PDFTextBlock] = []
    for page_index, page in enumerate(document, start=1):
        text = " ".join(page.get_text().split())
        if text:
            blocks.append(PDFTextBlock(text=text, page_number=page_index))
    document.close()
    return ParsedPDF(blocks=tuple(blocks))
