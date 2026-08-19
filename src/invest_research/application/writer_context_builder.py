"""P06-11I：确定性 Writer 紧凑上下文构建器（WriterContextBuilder）。

背景（根因）：
- 旧 Writer 在 CrewAI 工具循环中反复调用 ``WriterContextReader``，DeepSeek 把大量
  token 消耗在 ``tool_calls[].function.arguments``，final answer 只有 103~114 字符
  → 完整报告从未生成 → REPORT_INVALID；
- 新流程不再创建带 WriterContextReader 的 Agent/Crew 工具循环，而是由本模块在
  本地**确定性**加载 ResearchPack + FinancialAnalysisPack + CitationRegistry，
  构建一份紧凑写作上下文，再由一次无工具 LLM 调用直接输出 Markdown。

本模块职责（纯确定性，不调用 LLM、不发起网络）：
- 把两个 pack、引用注册表、报告写作规则压缩为单段用户消息；
- 保留：公司身份、数据截止日、报告语言、全部合法 citation keys、报告必需章节、
  禁止投资建议与引用格式要求；
- 按预算确定性截断：facts / limitations / sources 超过限制时按优先级丢弃，
  **公司身份、写作规则、CitationRegistry 的合法 key 永远完整保留**；
- 输出低基数统计（context_chars / estimated_tokens / fact_count / source_count /
  citation_count / truncated），供 Jaeger/Prometheus 只记录长度与数量，
  绝不记录 prompt 正文。

截断优先级（任务要求，从高到低）：
1. 公司身份（名称/CIK/截止日/语言）；
2. 财务事实（facts，含 citation key / label / period / value / unit）；
3. 来源与 citation key（sources 摘要 + 全部合法 key）；
4. limitations / unavailable_reason；
5. 写作规则（必需章节 / 禁止投资建议 / 引用格式）。

依赖边界：application → domain + 同层（citation_registry / report_draft_assembler）。
禁止导入 CrewAI、FastAPI、Redis、云厂商 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from invest_research.application.citation_registry import CitationRegistry
from invest_research.domain.models import (
    AnalysisCompleteness,
    FinancialAnalysisPack,
    ResearchPack,
    ResearchRequest,
)

# 与 writer_task.REPORT_SECTIONS / ReportDraftAssembler._REQUIRED_SECTIONS 语义一致，
# 但这里是**独立复刻**的文字段（写进上下文），避免从 agents/writer_task 导入
# （application 层不得依赖 agents 层；两个模块本就允许不同措辞）。
_REQUIRED_SECTIONS_TEXT = (
    "执行摘要、公司与业务概览、近期重要事件与行业背景、财务表现、关键指标表、"
    "风险因素与催化因素、数据限制、来源清单与非投资建议声明"
)

# 写入规则的固定文字（短、稳定，便于测试断言）。
_WRITING_RULES = (
    "必须包含以下章节：" + _REQUIRED_SECTIONS_TEXT + "。\n"
    "禁止给出买入/卖出建议、目标价、持仓比例或确定性收益承诺；必须保留非投资建议声明。\n"
    "引用格式：只能使用下方 citation keys 中列出的合法 key，写成 [src_<hash>] 或 "
    "[fr_<hash>]；禁止自行生成、拼接或猜测任何 key。"
)

# 估算公式：中文约 1~2 字符/token、英文约 4 字符/token；取 /3 为保守统一估算。
# 只用于低基数可观测性与预算（不用于权威计费）。
_ESTIMATED_TOKENS_DIVISOR = 3


class WriterContextBuildError(RuntimeError):
    """上下文超限且必须保留的块也无法放下（禁止静默丢弃全部 citation key）。"""

    error_code = "WRITER_CONTEXT_OVERFLOW"


@dataclass(frozen=True)
class WriterContextLimits:
    """紧凑上下文的确定性预算（全部可配置，测试可注入小预算验证裁剪）。"""

    max_chars: int = 6000
    max_estimated_tokens: int = 2000
    max_facts: int = 40
    max_sources: int = 20
    # 单个来源摘要长度上限（字符）。
    per_source_summary_chars: int = 300
    # 全部来源摘要合计上限（字符）。
    max_source_summary_chars: int = 1500


@dataclass(frozen=True)
class BuiltWriterContext:
    """构建结果：单段文本 + 低基数统计（绝不保存完整 pack / prompt）。"""

    text: str
    context_chars: int
    estimated_tokens: int
    fact_count: int
    source_count: int
    citation_count: int
    truncated: bool
    dropped_sections: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """确定性 token 估算（字符数 // 3，最小 1）。仅用于预算，不用于计费。"""
    return max(1, len(text) // _ESTIMATED_TOKENS_DIVISOR)


def _fmt_fact(fact: Any) -> str:
    """把单个 FinancialFact 压缩为一行（只含可复现的确定性字段）。"""
    period: str | None = None
    if fact.period_start is not None and fact.period_end is not None:
        period = f"{fact.period_start.isoformat()}~{fact.period_end.isoformat()}"
    elif fact.instant_date is not None:
        period = fact.instant_date.isoformat()
    label = fact.label or fact.concept
    ref = ""
    try:
        from invest_research.application.analysis_assembler import build_fact_ref

        ref = build_fact_ref(fact)
    except Exception:  # noqa: BLE001 - 单个事实引用失败不中断整个构建
        ref = ""
    parts = [f"concept={fact.concept}", f"value={fact.value}", f"unit={fact.unit}"]
    if period:
        parts.append(f"period={period}")
    if label:
        parts.append(f"label={label}")
    if ref:
        parts.append(f"ref=[{ref}]")
    return "；".join(parts)


def _fmt_analysis_status(pack: FinancialAnalysisPack | None) -> str:
    """把分析包完整性状态压缩为一行（complete/partial/unavailable + 原因）。"""
    if pack is None:
        return "未提供财务分析包（无法引用任何财务事实）"
    status = str(pack.completeness.value if pack.completeness else "partial")
    lines = [f"completeness={status}"]
    if pack.completeness == AnalysisCompleteness.PARTIAL and pack.limitations:
        lines.append("limitations=" + "；".join(pack.limitations))
    if pack.completeness == AnalysisCompleteness.UNAVAILABLE and pack.unavailable_reason:
        lines.append("unavailable_reason=" + pack.unavailable_reason)
    if pack.analysis_notes:
        lines.append("notes=" + pack.analysis_notes)
    return "\n".join(lines)


class WriterContextBuilder:
    """确定性构建紧凑写作上下文（每次调用产出独立文本，无内部可变状态跨调用）。"""

    def __init__(self, limits: WriterContextLimits | None = None) -> None:
        self._limits = limits if limits is not None else WriterContextLimits()

    def build(
        self,
        request: ResearchRequest,
        research_pack: ResearchPack | None,
        analysis_pack: FinancialAnalysisPack | None,
        citation_registry: CitationRegistry,
    ) -> BuiltWriterContext:
        """把输入压缩为单段写作上下文。

        - ``citation_registry`` 必须已构建（唯一合法 citation key 来源）；
        - ``research_pack`` / ``analysis_pack`` 允许缺失（如实说明，不伪造）；
        - 公司身份、写作规则与全部合法 citation keys 永远保留；
        - 超限时按优先级（fact > limitations > sources 细节）确定性裁剪。
        """
        hard_sections: list[str] = []

        # 1. 公司身份（最高优先级，永远保留）
        hard_sections.append(
            self._company_section(request, research_pack)
        )

        # 2. 写作规则（优先级 5；短且必需，永远保留）
        hard_sections.append("写作规则：\n" + _WRITING_RULES)

        # 3. CitationRegistry 合法 keys（永远完整保留，禁止静默截掉全部 key）
        citation_section = self._citation_section(citation_registry)
        hard_sections.append(citation_section)

        hard_text = "\n\n".join(hard_sections)
        if len(hard_text) > self._limits.max_chars:
            raise WriterContextBuildError(
                "Writer 紧凑上下文超限且必须保留的块（公司身份/写作规则/全部 citation "
                "keys）也无法放下；禁止静默丢弃全部 citation registry"
            )
        if estimate_tokens(hard_text) > self._limits.max_estimated_tokens:
            raise WriterContextBuildError(
                "Writer 紧凑上下文预估 token 超限且必须保留的块也放不下"
            )

        body_sections: list[str] = []
        dropped: list[str] = []

        # 4. 财务事实（优先级 2；按 max_facts 条数 + 预算裁剪）
        self._append_facts_section(body_sections, dropped, analysis_pack, hard_text)

        # 5. 限制与不可用原因（优先级 4；与分析状态合并展示）
        self._append_limits_section(body_sections, analysis_pack)

        # 6. 来源（优先级 3；按 max_sources + 摘要预算裁剪）
        self._append_sources_section(body_sections, dropped, research_pack, hard_text)

        # 汇总：逐步加入 body section，超出预算即停止（确定性截断）。
        output = hard_text
        truncated = False
        for section in body_sections:
            candidate = f"\n\n{section}" if output else section
            new_len = len(output) + len(candidate)
            if new_len > self._limits.max_chars:
                truncated = True
                continue
            if estimate_tokens(output + candidate) > self._limits.max_estimated_tokens:
                truncated = True
                continue
            output += candidate

        return BuiltWriterContext(
            text=output,
            context_chars=len(output),
            estimated_tokens=estimate_tokens(output),
            fact_count=self._fact_count(analysis_pack, output),
            source_count=self._source_count(research_pack, output),
            citation_count=len(citation_registry.keys()),
            truncated=truncated or bool(dropped),
            dropped_sections=dropped,
        )

    # ------------------------------------------------------------------
    # 私有构造辅助
    # ------------------------------------------------------------------

    def _company_section(
        self, request: ResearchRequest, research_pack: ResearchPack | None
    ) -> str:
        identity = research_pack.company_identity if research_pack is not None else None
        if identity is not None:
            name = identity.legal_name
            cik = identity.cik
            ticker = identity.ticker or ""
        else:
            name = request.input_company
            cik = "未知（未解析）"
            ticker = ""
        ticker_part = f"（ticker={ticker}）" if ticker else ""
        return (
            f"公司：{name}{ticker_part}\n"
            f"CIK：{cik}\n"
            f"数据截止日：{request.as_of_date.isoformat()}\n"
            f"报告语言：{request.language}"
        )

    def _citation_section(self, registry: CitationRegistry) -> str:
        keys = sorted(registry.keys())
        if not keys:
            return "citation keys：（无可用来源/事实，不得伪造任何 key，在数据限制章节如实说明）"
        # 只列出 key 本身；描述性字段按需省略（key 完整保留）。
        return "citation keys（只能复制以下合法 key）：\n" + ", ".join(keys)

    def _append_facts_section(
        self,
        body: list[str],
        dropped: list[str],
        analysis_pack: FinancialAnalysisPack | None,
        hard_text: str,
    ) -> None:
        if analysis_pack is None or not analysis_pack.facts:
            return
        facts = list(analysis_pack.facts)[: self._limits.max_facts]
        if len(analysis_pack.facts) > len(facts):
            dropped.append("facts_overflow")
        lines = [f"财务事实（{len(facts)} 条，仅含 SEC 原始值，禁止改写）："]
        for fact in facts:
            lines.append("- " + _fmt_fact(fact))
        section = "\n".join(lines)
        if self._within_budget(hard_text, section):
            body.append(section)
        else:
            dropped.append("facts")

    def _append_limits_section(
        self, body: list[str], analysis_pack: FinancialAnalysisPack | None
    ) -> None:
        if analysis_pack is None:
            return
        status_text = _fmt_analysis_status(analysis_pack)
        if not status_text:
            return
        body.append("财务分析状态：\n" + status_text)

    def _append_sources_section(
        self,
        body: list[str],
        dropped: list[str],
        research_pack: ResearchPack | None,
        hard_text: str,
    ) -> None:
        if research_pack is None or not research_pack.sources:
            return
        sources = list(research_pack.sources)
        if len(sources) > self._limits.max_sources:
            sources = sources[: self._limits.max_sources]
            dropped.append("sources_overflow")
        lines: list[str] = [f"可信来源（{len(sources)} 个）："]
        total_chars = 0
        for src in sources:
            key = _source_citation_key(src)
            title = (src.title or "").strip()
            if len(title) > self._limits.per_source_summary_chars:
                title = title[: self._limits.per_source_summary_chars] + "…"
            url = src.canonical_url or ""
            line = f"- [{key}] {title}（{url}）"
            if total_chars + len(line) > self._limits.max_source_summary_chars:
                dropped.append("source_summaries")
                break
            total_chars += len(line)
            lines.append(line)
        section = "\n".join(lines)
        if self._within_budget(hard_text, section):
            body.append(section)
        else:
            dropped.append("sources")

    def _within_budget(self, hard_text: str, section: str) -> bool:
        candidate = f"{hard_text}\n\n{section}"
        if len(candidate) > self._limits.max_chars:
            return False
        return estimate_tokens(candidate) <= self._limits.max_estimated_tokens

    # ------------------------------------------------------------------
    # 低基数统计辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _fact_count(analysis_pack: FinancialAnalysisPack | None, output: str) -> int:
        """统计实际写入上下文的 fact 条数（按 'concept=' 前缀出现次数，确定性）。"""
        if analysis_pack is None or not output:
            return 0
        return sum(1 for line in output.splitlines() if "concept=" in line)

    @staticmethod
    def _source_count(research_pack: ResearchPack | None, output: str) -> int:
        """统计实际写入上下文的 source 条数（按 '- [src_' 前缀出现次数，确定性）。"""
        if research_pack is None or not output:
            return 0
        return sum(1 for line in output.splitlines() if line.startswith("- [src_"))


def _source_citation_key(source: Any) -> str:
    """复用 ReportDraftAssembler 的 src_ 算法（唯一生成来源，禁止第二套 hash）。"""
    from invest_research.application.report_draft_assembler import (
        build_source_citation_key,
    )

    return build_source_citation_key(source)


__all__ = [
    "BuiltWriterContext",
    "WriterContextBuildError",
    "WriterContextBuilder",
    "WriterContextLimits",
    "estimate_tokens",
]
