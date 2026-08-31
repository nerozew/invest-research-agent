"""P02-11 解析降级路由（fallback pattern）。

职责：根据 media_type / 文件签名选择主解析器；PDF 主解析失败且内容确为
可解码文本时，**恰好一次**降级到 HTML 备用解析器。不引入新的解析库，
不破坏 P02-09（parse_html → ParsedDocument）与 P02-10（parse_pdf → ParsedPDF）公开契约。

设计决策（对齐已确认的 P02-11 计划）：
- 主解析器判定：优先信任 Content-Type（media_type），缺失/未知时用
  ``%PDF`` 魔数兜底，默认按 ADR-005 走 HTML；
- 降级方向：**仅 PDF → HTML 单向**。HTML 主路径失败不反向调用 PDF，
  因为 HTML 字符串不是 PDF，无条件双向没有收益且违背类型确定性；
- 降级前提：PDF 抛 ``PDFParseError`` 且 content 能**严格**解码为 UTF-8 文本
  （二进制/加密 PDF 不可降级，防止把乱码当 HTML）；
- 显式错误：只捕获 ``PDFParseError``；空内容在任何解析器调用前被拒绝；
  不可降级时抛 ``DocumentParseError``（携带 ``failed_parsers``，可测试）。
  未知异常不静默吞掉，按 .clinerules/02 交给上层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from invest_research.tools.pdf_parser import ParsedPDF, PDFParseError, parse_pdf
from invest_research.tools.sec_html_parser import ParsedDocument, parse_html


class DocumentParseError(Exception):
    """路由无法产出解析结果：已尝试的解析器均失败或不可降级。

    - ``failed_parsers``：已尝试但失败的解析器名（可测试）；
    - 语义对齐 docs/04 §4 的 ``DOCUMENT_UNSUPPORTED``（由上层映射）。
    """

    def __init__(self, failed_parsers: tuple[str, ...], detail: str) -> None:
        self.failed_parsers = failed_parsers
        self.detail = detail
        parsers = ", ".join(failed_parsers) if failed_parsers else "无"
        super().__init__(f"文档解析失败: {detail} (failed_parsers={parsers})")


@dataclass(frozen=True)
class ParseOutcome:
    """路由输出：判别联合，不破坏现有 ParsedDocument/ParsedPDF 契约。

    - ``kind``：实际产出的解析器类型（html | pdf）；
    - ``document``：对应的解析结果（调用方按 kind 收窄类型）；
    - ``parser_name``：实际使用的解析器名（日志/审计用）；
    - ``degraded_from``：若发生了降级，记录主解析器名；否则为 None。
    """

    kind: Literal["html", "pdf"]
    document: ParsedDocument | ParsedPDF
    parser_name: str
    degraded_from: str | None = None


def _normalize_media_type(media_type: str | None) -> str | None:
    """归一化 media_type：去分号参数、小写、去空白。"""
    if not media_type or not media_type.strip():
        return None
    return media_type.split(";")[0].strip().lower()


def _route_parser(media_type: str | None, content: bytes) -> Literal["html", "pdf"]:
    """决定主解析器：优先 Content-Type；缺失/未知用文件签名兜底。"""
    mt = _normalize_media_type(media_type)
    if mt:
        if "html" in mt:
            return "html"
        if "pdf" in mt:
            return "pdf"
        # 未知媒体类型：继续按签名探测（对齐 P02-08 白名单之外的防御）
    if content.startswith(b"%PDF"):
        return "pdf"
    # 默认 HTML（ADR-005：SEC 主文档优先 HTML/iXBRL）
    return "html"


def _is_degradable_text(content: bytes) -> bool:
    """PDF→HTML 降级前提：内容可严格解码为 UTF-8 且不含 NUL 控制字节。

    二进制流/加密 PDF 解码必然失败；含 NUL 字节（\\x00）的负载几乎不可能是
    文本/HTML（NUL 不是合法 HTML 文本），因此也被排除，防止把二进制当 HTML。
    """
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def parse_document(
    content: bytes,
    media_type: str | None,
    *,
    html_parser: Callable[[str], ParsedDocument] = parse_html,
    pdf_parser: Callable[[bytes], ParsedPDF] = parse_pdf,
) -> ParseOutcome:
    """解析降级路由：主解析成功则备用完全不调用；主失败最多降级一次。

    支持的失败类型（显式、可测试）：
    - ``DocumentParseError(failed_parsers=("pdf",))``：PDF 失败且不可降级
      （二进制/加密 PDF）；
    - ``DocumentParseError(failed_parsers=())``：空内容（任何解析器都不调用）。

    禁止（不静默吞噬）：
    - 加密/二进制 PDF 被当作 HTML 解析；
    - 空内容被当作"解析成功"；
    - HTML/PDF 解析器抛出的非预期异常被吞掉（会向上传播）。
    """
    if not content:
        raise DocumentParseError(failed_parsers=(), detail="内容为空，不调用任何解析器")

    route = _route_parser(media_type, content)

    if route == "html":
        # HTML 主路径：宽容解码（SEC 文档编码多样，U+FFFD 替换不可解码字节），
        # 空解析结果属于 P02-09 契约的合法行为，不由路由判定失败。
        text = content.decode("utf-8", errors="replace")
        html_document = html_parser(text)
        return ParseOutcome(kind="html", document=html_document, parser_name="html")

    # PDF 主路径
    try:
        pdf_document = pdf_parser(content)
    except PDFParseError as exc:
        # 仅 PDF 主解析失败才允许考虑降级，且内容必须可解码为文本
        if not _is_degradable_text(content):
            raise DocumentParseError(
                failed_parsers=("pdf",),
                detail="PDF 解析失败且内容不可降级为 HTML",
            ) from exc
        # 恰有一次备用解析：把文本字节交给 HTML 解析器
        text = content.decode("utf-8")
        fallback_document = html_parser(text)
        return ParseOutcome(
            kind="html", document=fallback_document, parser_name="html", degraded_from="pdf"
        )

    return ParseOutcome(kind="pdf", document=pdf_document, parser_name="pdf")
