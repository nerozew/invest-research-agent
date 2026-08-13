"""P02-11 解析降级路由契约测试（fallback pattern）。

验证目标（docs/05 P02-11 验收）：
- HTML 主路径成功时，备用解析器（PDF）完全不调用；
- PDF 主路径成功时，备用解析器（HTML）完全不调用；
- PDF 主解析失败且内容可降级为文本时，HTML 备用恰好调用一次；
- 加密/二进制损坏 PDF 不降级，返回明确失败；
- 空内容在任何解析器调用前被拒绝；
- 主备都失败时返回可测试的 DocumentParseError（含 failed_parsers）；
- media_type 缺失时用文件签名（%PDF 魔数）探测；
- media_type 明确为 HTML 时 PDF 解析器不被调用；
- 不破坏 P02-09 ParsedDocument / P02-10 ParsedPDF 的公开契约。

不发起真实网络；PDF fixture 用 PyMuPDF 在内存生成（与 P02-10 测试一致）。
"""

from __future__ import annotations

from typing import Callable

import fitz  # type: ignore[import-untyped]
import pytest

from invest_research.tools.parser_router import DocumentParseError, parse_document
from invest_research.tools.pdf_parser import ParsedPDF, PDFParseError
from invest_research.tools.sec_html_parser import ParsedDocument

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


def _bomb(*_: object, **__: object) -> object:
    """备用解析器 spy：被调用即失败（证明"完全不调用"）。"""
    raise AssertionError("备用解析器不应被调用")


class _CallSpy:
    """记录调用次数的解析器 spy。"""

    def __init__(self, impl: Callable[..., object]) -> None:
        self._impl = impl
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self._impl(*args, **kwargs)


def _make_pdf(text: str = "Hello SEC") -> bytes:
    """用 PyMuPDF 在内存生成一页文本 PDF（与 P02-10 测试同构）。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return bytes(data)


def _ok_pdf_parser(content: bytes) -> ParsedPDF:
    return ParsedPDF()


def _fail_pdf_parser(content: bytes) -> ParsedPDF:
    raise PDFParseError("损坏/非 PDF 内容")


# ---------------------------------------------------------------------------
# HTML 主路径
# ---------------------------------------------------------------------------


def test_html_main_path_success_never_calls_fallback() -> None:
    """HTML 主解析成功：备用 PDF spy 完全不调用。"""
    outcome = parse_document(
        b"<h1>Title</h1><p>Body</p>",
        "text/html",
        pdf_parser=_bomb,  # type: ignore[arg-type]
    )

    assert isinstance(outcome.document, ParsedDocument)
    assert outcome.kind == "html"
    assert outcome.parser_name == "html"
    assert outcome.degraded_from is None
    assert [b.text for b in outcome.document.headings] == ["Title"]
    assert [b.text for b in outcome.document.blocks] == ["Title", "Body"]


def test_text_media_type_never_calls_pdf_parser() -> None:
    """media_type=text/html 时，PDF 解析器完全不调用（单向路由）。"""
    pdf_spy = _CallSpy(_bomb)
    outcome = parse_document(
        b"<p>only html</p>",
        "text/html",
        pdf_parser=pdf_spy,  # type: ignore[arg-type]
    )

    assert outcome.kind == "html"
    assert pdf_spy.calls == 0


# ---------------------------------------------------------------------------
# PDF 主路径
# ---------------------------------------------------------------------------


def test_pdf_main_path_success_never_calls_fallback() -> None:
    """PDF 主解析成功：备用 HTML spy 完全不调用。"""
    html_spy = _CallSpy(_bomb)
    outcome = parse_document(
        _make_pdf(),
        "application/pdf",
        html_parser=html_spy,  # type: ignore[arg-type]
    )

    assert isinstance(outcome.document, ParsedPDF)
    assert outcome.kind == "pdf"
    assert outcome.parser_name == "pdf"
    assert outcome.degraded_from is None
    assert len(outcome.document.blocks) == 1
    assert outcome.document.blocks[0].page_number == 1
    assert html_spy.calls == 0


def test_pdf_result_preserves_pdf_contract() -> None:
    """P02-10 契约不被破坏：PDF 结果保留页码（1-based）。"""
    outcome = parse_document(_make_pdf("SEC Filing"), "application/pdf")
    doc = outcome.document
    assert isinstance(doc, ParsedPDF)
    assert doc.blocks[0].text == "SEC Filing"
    assert doc.blocks[0].page_number == 1


# ---------------------------------------------------------------------------
# PDF 失败 → HTML 降级（恰好一次）
# ---------------------------------------------------------------------------


def test_pdf_failure_falls_back_to_html_exactly_once() -> None:
    """PDF 主解析失败且内容为 HTML 文本 → HTML 备用恰好调用一次。"""
    html_spy = _CallSpy(lambda s: ParsedDocument())
    outcome = parse_document(
        b"<h1>Fallback</h1><p>Text body</p>",
        "application/pdf",  # 内容实为 HTML，模拟错误协商
        html_parser=html_spy,  # type: ignore[arg-type]
        pdf_parser=_fail_pdf_parser,
    )

    assert html_spy.calls == 1
    assert outcome.kind == "html"
    assert outcome.parser_name == "html"
    assert outcome.degraded_from == "pdf"
    assert isinstance(outcome.document, ParsedDocument)


def test_media_type_missing_magic_number_routes_to_pdf() -> None:
    """media_type 缺失时用文件签名（%PDF 魔数）探测为 PDF。"""
    pdf_spy = _CallSpy(_ok_pdf_parser)
    outcome = parse_document(
        b"%PDF-1.4 fake-header",
        None,
        pdf_parser=pdf_spy,  # type: ignore[arg-type]
    )

    assert pdf_spy.calls == 1
    assert outcome.kind == "pdf"


# ---------------------------------------------------------------------------
# 拒绝降级与失败结果
# ---------------------------------------------------------------------------


def test_binary_pdf_failure_does_not_degrade() -> None:
    """二进制损坏 PDF（不可解码为文本）→ 不降级 → DocumentParseError。"""
    html_spy = _CallSpy(_bomb)
    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"\x00\x01\x02\xff  not-utf8 binary",
            "application/pdf",
            html_parser=html_spy,  # type: ignore[arg-type]
            pdf_parser=_fail_pdf_parser,
        )

    assert html_spy.calls == 0
    assert excinfo.value.failed_parsers == ("pdf",)


def test_encrypted_pdf_does_not_degrade() -> None:
    """加密 PDF（二进制内容，不可解码）→ 不触发 HTML 降级。"""
    # 语义：加密 PDF 是二进制，即使 parse_pdf 抛 PDFParseError，
    # 路由也判定不可降级，防止把乱码当 HTML。
    html_spy = _CallSpy(_bomb)
    with pytest.raises(DocumentParseError):
        parse_document(
            b"%PDF-1.7 encrypted-binary-stream\x00\xff\xfe",
            "application/pdf",
            html_parser=html_spy,  # type: ignore[arg-type]
            pdf_parser=_fail_pdf_parser,
        )
    assert html_spy.calls == 0


def test_both_parsers_fail_returns_clear_failure() -> None:
    """主备都失败（不可降级）→ 明确、可测试的失败结果（含 failed_parsers）。"""
    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"not pdf nor html but binary\x00\x01",
            "application/pdf",
            pdf_parser=_fail_pdf_parser,
            html_parser=_bomb,  # type: ignore[arg-type]
        )

    assert excinfo.value.failed_parsers == ("pdf",)
    assert "降级" in str(excinfo.value)


def test_empty_content_rejected_before_any_parser() -> None:
    """空内容：任何解析器都不得被调用，直接返回明确失败。"""
    html_spy = _CallSpy(_bomb)
    pdf_spy = _CallSpy(_bomb)

    with pytest.raises(DocumentParseError) as excinfo:
        parse_document(
            b"",
            "text/html",
            html_parser=html_spy,  # type: ignore[arg-type]
            pdf_parser=pdf_spy,  # type: ignore[arg-type]
        )

    assert html_spy.calls == 0
    assert pdf_spy.calls == 0
    assert excinfo.value.failed_parsers == ()
