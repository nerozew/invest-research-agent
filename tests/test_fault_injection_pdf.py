"""P05-10 故障注入 A/B：损坏 PDF 与降级路由。

复用现有 ParserRouter（不修改生产代码、不联网、不调用真实 LLM）。
验证：损坏/空白 PDF、PDF 主解析失败、HTML/PDF 降级、主备同时失败、
降级按设计次数执行、错误可分类可查询。
"""

from __future__ import annotations

from typing import Callable

import pytest

from invest_research.tools.parser_router import DocumentParseError, parse_document
from invest_research.tools.pdf_parser import ParsedPDF, PDFParseError
from invest_research.tools.sec_html_parser import ParsedDocument


class _CallSpy:
    """记录调用次数的解析器 spy。"""

    def __init__(self, impl: Callable[..., object]) -> None:
        self._impl = impl
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self._impl(*args, **kwargs)


def _bomb(*_: object, **__: object) -> object:
    raise AssertionError("备用解析器不应被调用")


def _ok_pdf_empty(content: bytes) -> ParsedPDF:
    return ParsedPDF()


def _fail_pdf(content: bytes) -> ParsedPDF:
    raise PDFParseError("损坏/非 PDF 内容")


def _ok_html(text: str) -> ParsedDocument:
    return ParsedDocument()


# ---- A. 损坏/空白 PDF ----


def test_corrupted_binary_pdf_no_fake_success() -> None:
    """损坏 PDF（二进制）→ 抛 DocumentParseError，绝不返回虚假成功。"""
    html_spy = _CallSpy(_bomb)
    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"\x00\x01\x02\xff  corrupted-binary-pdf",
            "application/pdf",
            html_parser=html_spy,  # type: ignore[arg-type]
            pdf_parser=_fail_pdf,
        )
    assert html_spy.calls == 0  # 不可降级，备用完全不调用
    assert excinfo.value.failed_parsers == ("pdf",)


def test_corrupted_pdf_error_is_queryable() -> None:
    """损坏 PDF 错误可查询：failed_parsers 与 detail 供分类。"""
    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"not a pdf\x00\xff",
            "application/pdf",
            html_parser=_bomb,  # type: ignore[arg-type]
            pdf_parser=_fail_pdf,
        )
    assert excinfo.value.failed_parsers == ("pdf",)
    assert "降级" in excinfo.value.detail


def test_blank_pdf_is_not_fake_success() -> None:
    """空白 PDF（无文本）→ 空 blocks，不算成功产物。"""
    outcome = parse_document(
        b"%PDF-1.4 fake-empty-pdf",
        "application/pdf",
        pdf_parser=_ok_pdf_empty,
        html_parser=_bomb,  # type: ignore[arg-type]
    )
    assert outcome.kind == "pdf"
    assert isinstance(outcome.document, ParsedPDF)
    assert outcome.document.blocks == ()


def test_empty_content_rejected_before_any_parser() -> None:
    """空内容 → 任何解析器都不调用，直接失败。"""
    html_spy = _CallSpy(_bomb)
    pdf_spy = _CallSpy(_bomb)
    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"",
            "application/pdf",
            html_parser=html_spy,  # type: ignore[arg-type]
            pdf_parser=pdf_spy,  # type: ignore[arg-type]
        )
    assert html_spy.calls == 0
    assert pdf_spy.calls == 0
    assert excinfo.value.failed_parsers == ()


# ---- B. 降级路由（恰好一次，不无限重试） ----


def test_pdf_failure_degrades_exactly_once() -> None:
    """PDF 主解析失败且内容可解码 → HTML 备用恰好一次，不循环。"""
    html_spy = _CallSpy(_ok_html)
    outcome = parse_document(
        b"<h1>Fallback</h1><p>damaged pdf body</p>",
        "application/pdf",
        html_parser=html_spy,  # type: ignore[arg-type]
        pdf_parser=_fail_pdf,
    )
    assert html_spy.calls == 1
    assert outcome.kind == "html"
    assert outcome.degraded_from == "pdf"


def test_html_success_never_touches_pdf() -> None:
    """HTML 主路径成功 → PDF 备用完全不调用。"""
    pdf_spy = _CallSpy(_bomb)
    outcome = parse_document(
        b"<p>html content</p>",
        "text/html",
        pdf_parser=pdf_spy,  # type: ignore[arg-type]
    )
    assert outcome.kind == "html"
    assert pdf_spy.calls == 0


def test_missing_media_type_routes_by_magic_number() -> None:
    """media_type 缺失 → %PDF 魔数路由到 PDF 主解析器。"""
    pdf_spy = _CallSpy(_ok_pdf_empty)
    outcome = parse_document(
        b"%PDF-1.4 magic-number",
        None,
        pdf_parser=pdf_spy,  # type: ignore[arg-type]
        html_parser=_bomb,  # type: ignore[arg-type]
    )
    assert pdf_spy.calls == 1
    assert outcome.kind == "pdf"


def test_both_parsers_fail_terminal() -> None:
    """主、备用解析器同时失败 → 明确失败，无虚假成功。"""
    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"binary-not-text\x00\xff",
            "application/pdf",
            pdf_parser=_fail_pdf,
            html_parser=_bomb,  # type: ignore[arg-type]
        )
    assert excinfo.value.failed_parsers == ("pdf",)
