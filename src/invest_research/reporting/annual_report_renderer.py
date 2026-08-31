"""P07 年度报告渲染层优化：确定性展示转换（不调用 LLM/DB/网络）。

本模块只做"呈现层"改造，不改门禁语义（引用 key 门禁仍在 Writer 与
ReportDraftAssembler 阶段校验，本模块是发布动作）：

- ``render_citation_links``：把正文 ``[src_<hash>]`` 替换为 ``[来源名](url)``
  可点击链接；``[fr_<hash>]`` 事实无 URL，保持原样；不在注册表的 key 原样保留。
- ``extract_official_statements``：从目标 10-K 解析块按关键字定位封面 / 独立审计
  意见（"fairly present"）/ 302 认证，原文直取；best-effort——未命中返回 None，
  绝不抛异常、绝不阻塞发布（302 主文档大概率不含，属预期降级）。
- ``extract_document_title``：SEC 工件来源名启发式（封面/title 文本）。
- ``build_annual_cover``：报告封面（分析基准日 / 财报期间 / 生成时间 + 官方声明
  摘录）；``generated_at`` 可注入保证 golden 可复现。

依赖方向：reporting → application（``CitationRegistry``）+ domain。
解析块类型（``AnnualParsedTextBlock``）仅用于类型标注（TYPE_CHECKING），运行时按
``.text`` / ``.locator`` 鸭子访问，避免 reporting → infrastructure 的运行时依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Iterable

from invest_research.application.citation_registry import (
    CITATION_TYPE_SOURCE,
    CitationRegistry,
)

if TYPE_CHECKING:
    from invest_research.infrastructure.annual_document_pipeline import AnnualParsedTextBlock

# 引用 token：src_/fr_ + URL/事实 sha256 前 12 位（与 build_source_citation_key /
# build_fact_ref 对齐）。只匹配方括号内的 key，不碰 locator=offset:.. 残留与杂散括号。
_CITATION_TOKEN_RE = re.compile(r"\[((?:src|fr)_[0-9a-f]{12})\]")

# 官方声明节选上限（原文直取，超长只截节选并附定位指引；不改写/不转述）。
_OFFICIAL_EXCERPT_MAX_CHARS = 600
# 来源名标题上限（启发式结果截断）。
_DOCUMENT_TITLE_MAX_CHARS = 120
# 封面页/审计意见搜索窗口。
_COVER_SCAN_BLOCKS = 12

_COVER_KEYWORDS = (
    "10-k",
    "form 10-k",
    "annual report pursuant",
    "annual report to security holders",
)
# 管理层讨论与分析（10-K Item 7）标题关键字（best-effort，含弯引号变体）。
_MDA_KEYWORDS = (
    "management's discussion and analysis",
    "management’s discussion and analysis",
    "item 7. management",
    "item 7  management",
    "item 7 management",
)
# MD&A 标题块之后连带纳入的正文块数（Item 7 是长段落，需多块拼接）。
_MDA_FOLLOWING_BLOCKS = 20
# MD&A 原文直取节选上限（原文不改写，超长截断并附定位指引）。
_MDA_EXCERPT_MAX_CHARS = 3000
# 目录条目/页码等短块阈值（MD&A 标题后的短块不当作正文，用于跳过目录命中）。
_MDA_TITLE_MIN_CHARS = 40
# MD&A 有效正文最低字符量（目录命中达不到则跳过，继续找正文区的 Item 7）。
_MDA_BODY_MIN_CHARS = 200
# MD&A 标题锚点最大长度：真正的 Item 7 标题是短块（约 85 字符）；长正文块（如
# 风险因素里交叉引用 MD&A 的整段话）不是标题，跳过避免摘到风险内容。
_MDA_ANCHOR_MAX_CHARS = 300
# 独立审计意见的官方措辞是 "present fairly, in all material respects"。
_AUDIT_KEYWORDS = ("present fairly", "fairly present")
# 302 认证在独立 EX-31 附件（pipeline 不下载），主文档只按精确措辞 best-effort 命中。
_302_KEYWORDS = ("section 302", "rule 13a-14")


@dataclass(frozen=True)
class OfficialStatement:
    """一条官方声明：英文原文 + 原样定位（offset:N / page:N）+ 中文翻译（best-effort）。"""

    text: str
    locator: str
    translation: str | None = None


@dataclass(frozen=True)
class AnnualOfficialStatements:
    """从主 10-K 解析块 best-effort 定位的官方声明集合（可全空）。"""

    cover_page: OfficialStatement | None = None
    audit_opinion: OfficialStatement | None = None
    certification_302: OfficialStatement | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.cover_page is None
            and self.audit_opinion is None
            and self.certification_302 is None
        )


def _md_cell(value: object) -> str:
    """Markdown 表格单元格转义：竖线→\\|、换行→空格、空值→N/A。"""
    text = str(value).strip() if value is not None else ""
    if not text:
        return "N/A"
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _collapse(text: str) -> str:
    """压缩空白/换行为单空格并去首尾。"""
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    """截断到 limit 字符并在末尾加省略号（若超长）。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def link_label(title: str | None) -> str:
    """链接标签：剔除 ``[]``（会破坏 Markdown 链接语法），空则回退"来源"。"""
    return (title or "来源").strip().replace("[", "").replace("]", "")


def render_citation_links(markdown: str, registry: CitationRegistry) -> str:
    """把正文 ``[src_<hash>]`` 替换为 ``[来源名]`` 纯文本（不带 URL）。

    - 可点击链接只保留在报告末尾的来源清单；正文只显示来源名，避免每处都可点；
    - ``[fr_<hash>]``（事实无 URL）、不在注册表的 key、无 URL 的条目一律原样保留；
    - 只替换 key token，不修改 `` locator=offset:..`` 残留与杂散括号；
    - 该函数是纯展示转换：调用方必须保证它在引用 key 门禁/提取之后执行。
    """
    by_key = {entry.citation_key: entry for entry in registry.entries}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        entry = by_key.get(key)
        if (
            entry is not None
            and entry.citation_type == CITATION_TYPE_SOURCE
            and entry.canonical_url
        ):
            return f"[{link_label(entry.title)}]"
        return match.group(0)

    return _CITATION_TOKEN_RE.sub(_replace, markdown)


def render_citation_numbers(
    markdown: str, registry: CitationRegistry
) -> tuple[str, dict[str, int]]:
    """把正文 ``[src_<hash>]``/``[fr_<hash>]`` 替换为论文式编号 ``[1]``/``[2]``。

    - 编号按 key 在正文**首次出现顺序**分配（确定性）；同一 key 多处出现用同一编号；
    - source 与 fact 引用都编号（统一论文式）；
    - **不在注册表的 key 替换为 ``[?]``**（未解析引用），绝不向用户暴露内部 hash；
    - 调用方必须保证它在引用 key 门禁/提取之后执行。
    """
    by_key = {entry.citation_key: entry for entry in registry.entries}
    key_to_number: dict[str, int] = {}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in by_key:
            if key not in key_to_number:
                key_to_number[key] = len(key_to_number) + 1
            return f"[{key_to_number[key]}]"
        return "[?]"

    return _CITATION_TOKEN_RE.sub(_replace, markdown), key_to_number


_LOCATOR_MARKER_RE = re.compile(
    r"\s*locator\s*=\s*offset:\d+"  # 正文 LLM 标记：locator=offset:N
    r"|（定位：offset:\d+）"  # 官方声明摘录：定位：offset:N
    r"|，定位\s+offset:\d+"  # MD&A 指引：见 SEC 申报，定位 offset:N
    r"|\s+offset:\d+"  # 裸 offset:N（LLM 直接写 [13] offset:xxx）
)


def strip_locator_markers(markdown: str) -> str:
    """从正文移除各类 ``offset:N`` 定位标记（追溯由文末引用列表的 URL 承担）。

    覆盖：LLM 正文 ``locator=offset:N``、官方声明摘录 ``（定位：offset:N）``、
    MD&A 指引 ``定位 offset:N``——避免向用户暴露内部偏移数字。
    """
    return _LOCATOR_MARKER_RE.sub("", markdown)


def build_reference_list(registry: CitationRegistry, key_to_number: dict[str, int]) -> str:
    """生成论文式文末引用列表：``[1] [标题](url)``（可点击）。

    - 按编号顺序列出；source 有 ``canonical_url`` → ``[n] [标题](url)``；
    - fact 无 URL → ``[n] <标题>（SEC XBRL 事实）``（不伪装链接）；
    - 空映射返回空串（调用方据此省略该节）。
    """
    if not key_to_number:
        return ""
    by_key = {entry.citation_key: entry for entry in registry.entries}
    lines = ["## 引用", ""]
    for key, num in sorted(key_to_number.items(), key=lambda kv: kv[1]):
        entry = by_key.get(key)
        label = link_label(entry.title) if entry is not None else "来源"
        if entry is not None and entry.canonical_url:
            lines.append(f"[{num}] [{label}]({entry.canonical_url})")
        else:
            lines.append(f"[{num}] {label}（SEC XBRL 事实）")
        lines.append("")  # 每条之间空行（论文式排版，避免密集）
    return "\n".join(lines)


def _find_block(
    blocks: Iterable[AnnualParsedTextBlock],
    keywords: tuple[str, ...],
    *,
    limit: int | None = None,
    ignore_case: bool = True,
) -> OfficialStatement | None:
    """在解析块中按关键字定位首个命中块；``limit`` 限定扫描前 N 块。"""
    for index, block in enumerate(blocks):
        if limit is not None and index >= limit:
            break
        text = block.text.strip()
        if not text:
            continue
        lowered = text.lower()
        if any(keyword in lowered for keyword in keywords):
            return OfficialStatement(text=text, locator=block.locator)
    return None


def extract_official_statements(
    blocks: Iterable[AnnualParsedTextBlock],
) -> AnnualOfficialStatements:
    """从主 10-K 解析块定位封面 / 独立审计意见 / 302 认证（best-effort）。

    任何未命中项返回 None；blocks 为空返回空集合；绝不在缺失时抛异常。
    """
    cover = _find_block(blocks, _COVER_KEYWORDS, limit=_COVER_SCAN_BLOCKS)
    audit = _find_block(blocks, _AUDIT_KEYWORDS)
    cert = _find_block(blocks, _302_KEYWORDS)
    return AnnualOfficialStatements(cover_page=cover, audit_opinion=audit, certification_302=cert)


def _looks_like_next_item(text: str) -> bool:
    """判断是否已进入 MD&A 之后的下一个大标题（Item 8 财务报表）。

    真正的 Item 8 标题是短块、且整块就是标题本身（以 "Item 8" 开头，去掉空白/句点
    等标点后紧跟 "Financial Statements..." 措辞）。正文里引用 Item 8 措辞的句子——
    无论长短（如 KO 引导段整段引用、或短句 "…included in Item 8. Financial
    Statements and Supplementary Data."）——不是标题，不得误判。
    """
    stripped = text.strip()
    if not stripped or len(stripped) >= _MDA_ANCHOR_MAX_CHARS:
        return False
    lowered = stripped.lower()
    if not lowered.startswith("item 8"):
        return False
    # 去掉 "Item 8" 后的空白/标点（句点、冒号、连字符、en/em 破折号等），
    # 剩余部分必须本身就以财务报表标题措辞开头——仅"句中包含"不算标题形态。
    rest = lowered[len("item 8"):].lstrip(" \t\n\r.:;,-–—")
    return rest.startswith("financial statements")


def extract_mda(
    blocks: Iterable[AnnualParsedTextBlock],
    *,
    max_chars: int = _MDA_EXCERPT_MAX_CHARS,
) -> OfficialStatement | None:
    """从目标 10-K 解析块定位管理层讨论与分析（Item 7）原文（best-effort）。

    - 依次尝试每个 MD&A 标题命中点：跳过目录条目（标题后无实质正文），直到找到
      真正正文区（标题后紧跟长段落）；AMZN 等 10-K 常在目录与正文各出现一次；
    - 命中正文区后连带纳入其后若干正文块，逐块拼接到 ``max_chars``；
    - 跳过短块（目录条目/页码），遇到 Item 8（财务报表）标题即停；
    - 未命中或无实质正文返回 None（不阻塞）。
    """
    blocks_list = list(blocks)
    candidates = [
        index
        for index, block in enumerate(blocks_list)
        if any(keyword in block.text.lower() for keyword in _MDA_KEYWORDS)
    ]
    for start_index in candidates:
        mda = _collect_mda(blocks_list, start_index, max_chars)
        if mda is not None:
            return mda
    return None


def _collect_mda(
    blocks_list: list[AnnualParsedTextBlock],
    start_index: int,
    max_chars: int,
) -> OfficialStatement | None:
    """从候选起点收集 MD&A 正文；无实质正文返回 None（调用方试下一个候选）。

    锚点必须是短标题块：长正文（如风险因素里交叉引用 MD&A 的整段话）不是
    Item 7 标题，直接跳过；锚点本身不参与正文计数。
    """
    anchor = blocks_list[start_index]
    if len(anchor.text.strip()) >= _MDA_ANCHOR_MAX_CHARS:
        return None
    parts: list[str] = []
    total = 0
    for block in blocks_list[start_index + 1 : start_index + 1 + _MDA_FOLLOWING_BLOCKS]:
        text = block.text.strip()
        if not text:
            continue
        if _looks_like_next_item(text):
            break
        if len(text) < _MDA_TITLE_MIN_CHARS:
            # 目录条目/页码等短块不当作正文（TOC 的 Item 7 命中后全是短条目）。
            continue
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    if total < _MDA_BODY_MIN_CHARS:
        return None
    excerpt = "\n\n".join(parts)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "…"
    return OfficialStatement(text=excerpt, locator=anchor.locator)


def build_mda_section(blocks: Iterable[AnnualParsedTextBlock]) -> str:
    """渲染 ``## 管理层讨论与分析`` 章节（原文直取节选）；未命中返回空串。"""
    mda = extract_mda(blocks)
    if mda is None:
        return ""
    lines = [
        "## 管理层讨论与分析",
        "",
        f"> 原文直取（节选；完整原文见 SEC 申报，定位 {mda.locator}）",
        "",
    ]
    for line in mda.text.splitlines() or [mda.text]:
        lines.append(f"> {line}")
    lines.append("")
    return "\n".join(lines)


def build_mda_summary_section(summary: str, locator: str | None = None) -> str:
    """渲染 ``## 管理层讨论与分析`` 章节（LLM 摘译的中文要点说明）。

    - ``summary`` 为 LLM 生成的 ≤500 字中文摘译（分析 Item 7 挑重点）；
    - 附一行来源指引（完整英文原文见 SEC 申报 + 定位），不整段英文直取；
    - 供 ``annual_runtime`` 在 executor 存在时替换 ``build_mda_section`` 原文直取。
    """
    locator_note = f"，定位 {locator}" if locator else ""
    lines = [
        "## 管理层讨论与分析",
        "",
        f"> 本节为管理层讨论与分析的要点摘译（中文）；完整英文原文见 SEC 申报{locator_note}。",
        "",
        summary.strip(),
        "",
    ]
    return "\n".join(lines)


def extract_document_title(
    blocks: Iterable[AnnualParsedTextBlock],
    *,
    company_name: str | None = None,
    form_type: str | None = None,
) -> str | None:
    """从解析块提取 SEC 工件来源名（封面/title 文本启发式）。

    取前 ``_COVER_SCAN_BLOCKS`` 块中命中表单关键字/公司名的最高分块；无命中返回
    None（调用方回退到稳定短标签）。
    """
    company_lower = company_name.lower() if company_name else None
    form_lower = form_type.lower() if form_type else None
    best_score = 0
    best_text: str | None = None
    for index, block in enumerate(blocks):
        if index >= _COVER_SCAN_BLOCKS:
            break
        text = block.text.strip()
        if not text:
            continue
        lowered = text.lower()
        score = 0
        if "form 10-k" in lowered or "10-k" in lowered:
            score += 3
        if form_lower and form_lower in lowered:
            score += 2
        if company_lower and company_lower in lowered:
            score += 2
        if "annual report" in lowered:
            score += 1
        if score > best_score:
            best_score = score
            best_text = text
    if best_text is None:
        return None
    return _truncate(_collapse(best_text), _DOCUMENT_TITLE_MAX_CHARS)


def human_kind(kind_value: str) -> str:
    """把 EvidenceKind 枚举值映射为人类可读来源名（来源名提取失败时的回退）。"""
    return {
        "target_annual_filing": "目标年度 10-K",
        "comparator_annual_filing": "上一年度 10-K",
        "company_facts": "SEC Company Facts（XBRL）",
        "financial_fact_set": "SEC 财务报表事实（XBRL）",
        "business_overview": "业务概览（网页搜索）",
        "risk_factors": "风险因素（网页搜索）",
        "management_discussion": "管理层讨论（网页搜索）",
        "material_event": "重大事件（网页搜索）",
        "analyst_opinion": "分析师观点（网页搜索）",
        "rating_agency": "评级机构（网页搜索）",
    }.get(kind_value, kind_value)


def _statement_lines(
    label: str, statement: OfficialStatement, note: str | None = None
) -> list[str]:
    """把一条官方声明渲染为加粗标题 + 英文原文 blockquote + 中文翻译（如有）。

    英文原文始终保留（作为出处）；``translation`` 为 LLM best-effort 翻译，
    缺失/失败时仅显示英文（不阻塞）。
    """
    lines = [f"**{label}**（定位：{statement.locator}）", ""]
    excerpt = _truncate(statement.text, _OFFICIAL_EXCERPT_MAX_CHARS)
    for line in excerpt.splitlines() or [excerpt]:
        lines.append(f"> {line}")
    lines.append("")
    if statement.translation:
        lines.append("**中文翻译**")
        lines.append("")
        for line in statement.translation.strip().splitlines() or [statement.translation]:
            lines.append(line)
        lines.append("")
    if note:
        lines.append(note)
        lines.append("")
    return lines


def build_annual_cover(
    *,
    legal_name: str,
    ticker: str | None,
    cik: str | None,
    as_of_date: date | str,
    target_fiscal_year: int | None,
    comparator_fiscal_year: int | None,
    generated_at: datetime | str | None,
    official_statements: AnnualOfficialStatements | None = None,
) -> str:
    """生成年度报告封面（分析基准日 / 财报期间 / 生成时间 + 官方声明摘录）。

    - ``generated_at`` 可注入固定值（golden 测试可复现）；None 由调用方决定默认；
    - 官方声明三项全空时整段"官方声明摘录"省略；
    - 302 未命中但封面/审计意见命中时，追加一行说明（best-effort 披露，不阻塞）。
    """
    as_of = as_of_date.isoformat() if isinstance(as_of_date, date) else str(as_of_date)
    gen = (
        generated_at.isoformat(timespec="seconds")
        if isinstance(generated_at, datetime)
        else (generated_at or "-")
    )
    if target_fiscal_year is not None and comparator_fiscal_year is not None:
        period = f"FY{target_fiscal_year} 对比 FY{comparator_fiscal_year}"
    elif target_fiscal_year is not None:
        period = f"FY{target_fiscal_year}（无上年对比）"
    else:
        period = "-"

    lines = ["## 报告概览", "", "| 项目 | 内容 |", "| :--- | :--- |"]
    rows = (
        ("公司", legal_name),
        ("代码", ticker or "-"),
        ("CIK", cik or "-"),
        ("分析基准日", as_of),
        ("财报期间", period),
        ("报告生成时间", gen),
    )
    for key, value in rows:
        lines.append(f"| {_md_cell(key)} | {_md_cell(value)} |")

    statements = official_statements or AnnualOfficialStatements()
    if not statements.is_empty:
        lines.append("")
        lines.append("### 官方声明摘录")
        lines.append("")
        if statements.cover_page is not None:
            lines.extend(_statement_lines("封面页", statements.cover_page))
        if statements.audit_opinion is not None:
            lines.extend(_statement_lines("独立审计意见", statements.audit_opinion))
        if statements.certification_302 is not None:
            lines.extend(_statement_lines("302 认证", statements.certification_302))
        elif statements.cover_page is not None or statements.audit_opinion is not None:
            lines.append("302 认证：主文档未包含（EX-31 附件未下载，best-effort 降级，不阻塞）")
            lines.append("")

    return "\n".join(lines)


__all__ = [
    "AnnualOfficialStatements",
    "OfficialStatement",
    "build_annual_cover",
    "build_mda_section",
    "build_mda_summary_section",
    "build_reference_list",
    "extract_document_title",
    "extract_mda",
    "extract_official_statements",
    "human_kind",
    "link_label",
    "render_citation_links",
    "render_citation_numbers",
    "strip_locator_markers",
]
