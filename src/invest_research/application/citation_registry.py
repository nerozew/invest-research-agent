"""P06-11F：确定性引用注册表（CitationRegistry）。

设计动机（唯一生成来源）：
- Writer 在生成 Markdown 前必须看到最终合法的 citation key（``src_<hash>`` /
  ``fr_<hash>``），否则模型无法写出合法引用 → ReportDraftAssembler 提取为空
  （P06-11E 真实报告 citation_keys 为空的根因）。
- 本模块由 Python 在 Writer 执行前从 ResearchPack + FinancialAnalysisPack
  确定性生成完整注册表：
  - ``src_`` 复用 ReportDraftAssembler 的 URL hash 算法（build_source_citation_key）；
  - ``fr_`` 复用 P06-11C 的 ``build_fact_ref``；
- ReportDraftAssembler 必须复用同一个 registry，不允许重新实现第二套算法；
- 模型不得自行生成 citation key；不在 registry 中的 key 必须拒绝。

数据流：

    ResearchPack + FinancialAnalysisPack
    → build_citation_registry()            （本模块，唯一生成来源）
    → WriterContextReader 完整交给 Writer
    → Writer 只从 registry 复制 key 到正文
    → ReportDraftAssembler 用同一 registry 提取正文实际出现的 key
    → Quality Gate 校验：合法 key 必须来自 registry

依赖方向：application → domain + 同层 analysis_assembler（复用 build_fact_ref）。
禁止导入 CrewAI/FastAPI/Redis/云厂商 SDK。
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from invest_research.application.report_draft_assembler import build_source_citation_key
from invest_research.domain.models import FinancialAnalysisPack, FinancialFact, ResearchPack, Source

# 引用类型：source（外部来源）/ fact（财务事实）/ claim（既有 claim 键）。
CITATION_TYPE_SOURCE = "source"
CITATION_TYPE_FACT = "fact"
CITATION_TYPE_CLAIM = "claim"


class CitationRegistryEntry(BaseModel):
    """注册表中的单条引用条目（可展示、可校验，供 Writer 复制 key）。"""

    model_config = ConfigDict(frozen=True)

    citation_key: str = Field(min_length=1)
    citation_type: str = Field(min_length=1)  # source / fact / claim
    title: str | None = None
    concept: str | None = None
    canonical_url: str | None = None
    source_id: str | None = None
    period: str | None = None
    value: str | None = None
    unit: str | None = None
    description: str | None = None


def _source_entry(source: Source, citation_key: str) -> CitationRegistryEntry:
    """把 Source 转为注册表条目（可展示说明包含标题与定位）。"""
    description = source.title or ""
    if source.locator:
        description = f"{description}（{source.locator}）" if description else source.locator
    return CitationRegistryEntry(
        citation_key=citation_key,
        citation_type=CITATION_TYPE_SOURCE,
        title=source.title,
        concept=None,
        canonical_url=source.canonical_url,
        source_id=None,
        period=(source.published_at.isoformat() if source.published_at is not None else None),
        value=None,
        unit=None,
        description=description or None,
    )


def _fact_entry(fact: FinancialFact, citation_key: str) -> CitationRegistryEntry:
    """把 FinancialFact 转为注册表条目（含期间/value/unit，可追溯展示）。"""
    period = None
    if fact.period_start is not None and fact.period_end is not None:
        period = f"{fact.period_start.isoformat()}~{fact.period_end.isoformat()}"
    elif fact.instant_date is not None:
        period = fact.instant_date.isoformat()
    description_parts = []
    if fact.label:
        description_parts.append(fact.label)
    if fact.concept:
        description_parts.append(fact.concept)
    return CitationRegistryEntry(
        citation_key=citation_key,
        citation_type=CITATION_TYPE_FACT,
        title=fact.label or fact.concept,
        concept=fact.concept,
        canonical_url=None,
        source_id=fact.source_id or None,
        period=period,
        value=str(fact.value),
        unit=fact.unit,
        description=" ".join(description_parts) or None,
    )


def _claim_entry(key: str) -> CitationRegistryEntry:
    """把既有 claim key 转为注册表条目（无额外来源信息，仅登记合法键）。"""
    return CitationRegistryEntry(
        citation_key=key,
        citation_type=CITATION_TYPE_CLAIM,
        description="既有 claim（由调用方登记）",
    )


class CitationRegistry(BaseModel):
    """确定性引用注册表：Writer 能看到的全部合法 citation key 的唯一来源。

    - ``entries``：全部注册条目（去重保序，key 唯一）；
    - ``as_writer_payload``：返回给 WriterContextReader 的可读结构；
    - ``keys`` / ``contains``：供 ReportDraftAssembler 与 Quality Gate 校验。
    """

    model_config = ConfigDict(frozen=True)

    entries: list[CitationRegistryEntry] = Field(default_factory=list)

    def keys(self) -> set[str]:
        """全部合法 citation key 集合（去重）。"""
        return {e.citation_key for e in self.entries}

    def contains(self, citation_key: str) -> bool:
        """判断 key 是否在注册表中（不在注册表 → 模型伪造，必须拒绝）。"""
        return citation_key in self.keys()

    @property
    def is_empty(self) -> bool:
        """注册表为空：无任何可引用的来源/事实 → Writer 必须进入数据限制表达。"""
        return not self.entries

    def as_writer_payload(self) -> dict[str, object]:
        """返回给 Writer 的可读注册表（完整条目 + 固定格式提示）。"""
        return {
            "format": "[src_<hash>] 或 [fr_<hash>]",
            "entries": [e.model_dump(mode="json") for e in self.entries],
        }


def build_citation_registry(
    research_pack: ResearchPack | None,
    analysis_pack: FinancialAnalysisPack | None,
    claim_keys: Iterable[str] | None = None,
) -> CitationRegistry:
    """确定性构建完整引用注册表（唯一生成来源，不调用 LLM）。

    - ``src_<hash>``：复用 ``build_source_citation_key``（URL sha256 前 12 位）；
    - ``fr_<hash>``：复用 ``build_fact_ref``（P06-11C 的稳定短 hash）；
    - ``claim_keys``：调用方登记的既有 claim 键原样并入（可空）。

    返回的 registry 是 ReportDraftAssembler 与 Quality Gate 共同使用的唯一来源，
    **禁止**在其他位置重新实现第二套 hash 算法。
    """
    seen: set[str] = set()
    entries: list[CitationRegistryEntry] = []

    def _add(entry: CitationRegistryEntry) -> None:
        if entry.citation_key in seen:
            return
        seen.add(entry.citation_key)
        entries.append(entry)

    if research_pack is not None:
        for source in research_pack.sources:
            _add(_source_entry(source, build_source_citation_key(source)))
    if analysis_pack is not None:
        for fact in analysis_pack.facts:
            from invest_research.application.analysis_assembler import build_fact_ref

            _add(_fact_entry(fact, build_fact_ref(fact)))
    if claim_keys:
        for key in claim_keys:
            if isinstance(key, str) and key.strip():
                _add(_claim_entry(key.strip()))
    return CitationRegistry(entries=entries)
