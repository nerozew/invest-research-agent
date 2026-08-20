"""P06-11D：确定性报告草稿组装器（ReportDraftAssembler）。

数据流（确定性，不调用 LLM）：

    ResearchPack + FinancialAnalysisPack
    → WriterContextReader
    → Writer 只输出 Markdown 报告
    → ReportDraftAssembler
    → ReportDraft
    → Quality Gate

为什么需要本服务（P06-11D）：
- 要求 DeepSeek/generic Writer 把几千字 Markdown 包装成 JSON 极不可靠：
  DeepSeek 普通 Chat Completion 没有服务端结构化约束（response_format 仅提示词，
  实测常返回 12 token 左右的简短自然语言说明），PackBoundary 因而拒绝
  （NOT_A_PACK "输出是普通自然语言，不是结构化 Pack"）；
- 本服务让 Writer **只输出 Markdown 正文**，由本地确定性代码生成
  ``version`` / ``title`` / ``citation_keys``，模型不再需要伪造或猜测这些字段。

确定性字段来源：
- ``version``：代码固定 ``report_draft_v1``，禁止模型生成；
- ``title``：从 ``ResearchPack.company_identity.legal_name`` / ``ticker`` 与
  ``as_of_date`` 确定性生成；无 research_pack 时用 ``request.input_company`` 兜底；
- ``markdown``：Writer 输出原文（允许带引号/换行/表格，无需 JSON 转义）；
- ``citation_keys``：只从可信来源（research_pack.sources、analysis_pack.facts、
  调用方传入的现有 claim_keys）构建候选键，再收集正文中**实际出现**的候选键。
  模型无法伪造 citation key（伪造键不在候选集合内）。

拒绝规则（确定性，稳定 error_code）：
- 空文本 / 过短说明 → ``REPORT_INVALID``（REPORT_INVALID，不可重试）；
- 明显是工具参数 / Action Input / 拒绝说明 → ``REPORT_INVALID``；
- 明显截断（``finish_reason=length`` 或正文末尾呈现截断痕迹）→
  ``REPORT_TRUNCATED``；
- 完全缺失必需章节（连一个标准章节都没有的随意文本）→ ``REPORT_INVALID``；
- 完整章节缺失清单仍由现有 Quality Gate 以 REVISE 语义处理（不重复抢占）。

依赖方向：application → domain + 同层 analysis_assembler（复用 build_fact_ref）。
禁止导入 CrewAI/FastAPI/Redis/云厂商 SDK。
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Iterable

from invest_research.application.analysis_assembler import build_fact_ref
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
)

# P06-11D：ReportDraft 版本由代码固定，禁止模型生成（与 ReportDraft.version 契约一致）。
_REPORT_DRAFT_VERSION = "report_draft_v1"

# 最小合法正文长度（字符）。过短说明（如"无法生成报告"）必须在组装阶段被拒绝，
# 不能进入最终发布。
_MIN_MARKDOWN_CHARS = 200

# 报告必需章节（与 flows/quality_classifier.REQUIRED_SECTIONS 保持一致）。
# assembler 只要求**至少一个**标准章节出现（证明是报告正文而非随意文本）；
# 完整章节缺失清单由现有 Quality Gate 以 REVISE 语义处理（测试项 11 允许进入门禁）。
_REQUIRED_SECTIONS: tuple[str, ...] = (
    "执行摘要",
    "公司与业务概览",
    "财务表现",
    "风险",
    "数据限制",
    "非投资建议",
)

# 拒绝文本：模型输出中的常见拒绝/占位说明。命中即不是合法正文。
_REJECTION_PHRASES: tuple[str, ...] = (
    "无法生成报告",
    "无法提供报告",
    "不能生成报告",
    "抱歉",
    "对不起",
    "我无法",
    "我不能",
    "无法完成",
    "请稍后重试",
    "作为人工智能",
    "拒绝回答",
)

# 截断痕迹：正文末尾出现的未完标志。
_TRUNCATION_TAIL_PATTERNS: tuple[str, ...] = (
    "...",
    "……",
    "（未完",
    "待续",
    "to be continued",
    "To be continued",
)

# 工具调用 / Action 前缀：正文不应以这些开头（工具参数被当最终答案的常见形态）。
_TOOL_ACTION_PREFIXES: tuple[str, ...] = (
    "Action:",
    "Action Input:",
    "Thought:",
    "tool_call",
    "artifact_key",
    "action_input",
)


class ReportAssemblerError(RuntimeError):
    """ReportDraftAssembler 确定性失败（携带稳定错误码与失败阶段 05_writer）。"""

    error_code: str

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_stage = "05_writer"


def build_source_citation_key(source: Source) -> str:
    """为单个来源生成确定性 citation key（``src_`` + URL sha256 前 12 位 hex）。

    只依赖来源 canonical_url，不包含任何密钥/路径；与 fact_ref 风格一致。
    本函数是 ``src_`` key 的唯一算法来源：CitationRegistry 与
    ReportDraftAssembler 都复用此实现，禁止在其它位置重写第二套 URL hash。
    """
    digest = hashlib.sha256(source.canonical_url.encode("utf-8")).hexdigest()
    return f"src_{digest[:12]}"


# 向后兼容别名（P06-11D 测试引用私有名；新代码一律使用 build_source_citation_key）。
_source_citation_key = build_source_citation_key


def build_report_title(
    identity: CompanyIdentity | None,
    as_of_date: date | None,
    *,
    fallback: str,
) -> str:
    """从可信公司身份 + 数据截止日确定性生成报告标题。

    格式：``<legal_name>（<ticker>）投资研究初稿（数据截止 <YYYY-MM-DD>）``；
    ticker 缺失时省略括号部分；无身份信息时用 fallback（request.input_company）兜底。
    """
    if identity is not None:
        base = identity.legal_name
        if identity.ticker:
            base = f"{base}（{identity.ticker}）"
    else:
        base = fallback
    if as_of_date is not None:
        base = f"{base}（数据截止 {as_of_date.isoformat()}）"
    return f"{base}投资研究初稿"


def _candidate_citation_keys(
    research_pack: ResearchPack | None,
    analysis_pack: FinancialAnalysisPack | None,
    claim_keys: Iterable[str] | None,
) -> list[str]:
    """构建确定性 citation key 候选集合（去重保序）。

    来源：
    - research_pack.sources → ``src_<hash>``；
    - analysis_pack.facts → ``fr_<hash>``（复用 P06-11C 的 build_fact_ref）；
    - 调用方传入的现有 claim_keys 原样并入。

    模型无法注入候选集合之外的 key → 不能伪造 citation key。
    """
    seen: set[str] = set()
    out: list[str] = []
    for key in _iter_candidates(research_pack, analysis_pack, claim_keys):
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _iter_candidates(
    research_pack: ResearchPack | None,
    analysis_pack: FinancialAnalysisPack | None,
    claim_keys: Iterable[str] | None,
) -> Iterable[str]:
    if research_pack is not None:
        for source in research_pack.sources:
            yield _source_citation_key(source)
    if analysis_pack is not None:
        for fact in analysis_pack.facts:
            yield build_fact_ref(fact)
    if claim_keys:
        for key in claim_keys:
            if isinstance(key, str) and key.strip():
                yield key.strip()


def _extract_cited_keys(
    markdown: str,
    research_pack: ResearchPack | None,
    analysis_pack: FinancialAnalysisPack | None,
    claim_keys: Iterable[str] | None,
    registry: Any | None = None,
) -> list[str]:
    """确定性提取正文中实际出现的 citation key（去重保序，不伪造）。

    - 传入 ``registry``（P06-11F CitationRegistry）时，候选键**只来自注册表**
      ——注册表是唯一生成来源，禁止在此重算第二套 hash；
    - 未传 registry 时回退旧候选集合（向后兼容既有测试/调用方）。

    候选键带 ``src_`` / ``fr_`` 等特殊前缀，普通 Markdown 文本误命中概率极低；
    逐 key 做子串匹配即可（key 长度短、前缀独特）。
    """
    if registry is not None:
        candidates = sorted(registry.keys(), key=lambda k: k)
    else:
        candidates = _candidate_citation_keys(research_pack, analysis_pack, claim_keys)
    return [key for key in candidates if key in markdown]


def _looks_rejected(markdown: str) -> bool:
    """识别明显的拒绝/工具调用文本（本身不是报告正文）。"""
    stripped = markdown.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if any(prefix in lowered for prefix in _TOOL_ACTION_PREFIXES):
        return True
    for phrase in _REJECTION_PHRASES:
        if phrase in lowered:
            return True
    return False


def _looks_truncated(markdown: str, *, finish_reason: str | None) -> bool:
    """识别明显截断：finish_reason=length 或正文末尾出现未完标志。"""
    if finish_reason is not None and finish_reason.lower() == "length":
        return True
    tail = markdown.rstrip()[-80:]
    return any(pattern in tail for pattern in _TRUNCATION_TAIL_PATTERNS)


def _has_required_section(markdown: str) -> bool:
    """正文至少包含一个标准必需章节（证明是报告正文而非随意文本）。"""
    return any(section in markdown for section in _REQUIRED_SECTIONS)


class ReportDraftAssembler:
    """确定性组装器：Writer Markdown → ReportDraft（不调用 LLM）。"""

    def assemble(
        self,
        writer_output: str,
        request: ResearchRequest,
        research_pack: ResearchPack | None,
        analysis_pack: FinancialAnalysisPack | None,
        *,
        claim_keys: Iterable[str] | None = None,
        finish_reason: str | None = None,
        registry: Any | None = None,
    ) -> ReportDraft:
        """把 Writer 的原始 Markdown 组装为合法 ReportDraft。

        - ``writer_output``：Writer 最终输出的 Markdown 正文；
        - ``request``：ResearchRequest（as_of_date / input_company 兜底标题）；
        - ``research_pack`` / ``analysis_pack``：上游两个 pack（缺失时仍可组装，
          但 citation_keys 会相应减少，完整性交给 Quality Gate）；
        - ``claim_keys``：现有引用/claim 信息（可选），并入 citation 候选；
        - ``finish_reason``：供应商返回的 finish_reason（"length" → 截断拒绝）；
        - ``registry``（P06-11F）：CitationRegistry——传入时 citation key
          候选**只来自注册表**（不在此重算第二套 hash）；未传时回退旧逻辑。
        """
        text = str(writer_output or "").strip()
        if not text:
            raise ReportAssemblerError("REPORT_INVALID", "Writer 输出为空，无法组装 ReportDraft")
        if len(text) < _MIN_MARKDOWN_CHARS:
            raise ReportAssemblerError(
                "REPORT_INVALID",
                f"Writer 输出过短（{len(text)} 字符），不像合法报告正文",
            )
        if _looks_rejected(text):
            raise ReportAssemblerError("REPORT_INVALID", "Writer 输出是拒绝/工具说明，不是报告正文")
        if _looks_truncated(text, finish_reason=finish_reason):
            raise ReportAssemblerError(
                "REPORT_TRUNCATED", "Writer 输出明显截断，禁止发布不完整报告"
            )
        if not _has_required_section(text):
            raise ReportAssemblerError(
                "REPORT_INVALID", "Writer 输出缺少任何必需章节，不是合法报告正文"
            )

        identity = research_pack.company_identity if research_pack is not None else None
        as_of_date = (
            request.as_of_date
            if request is not None
            else (research_pack.as_of_date if research_pack is not None else None)
        )
        title = build_report_title(
            identity,
            as_of_date,
            fallback=request.input_company if request is not None else "未知公司",
        )
        citation_keys = _extract_cited_keys(
            text, research_pack, analysis_pack, claim_keys, registry=registry
        )
        return ReportDraft(
            version=_REPORT_DRAFT_VERSION,
            title=title,
            markdown=text,
            citation_keys=citation_keys,
        )
