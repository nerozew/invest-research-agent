"""P06-11C：确定性分析包组装器（AnalysisPackAssembler）。

职责（LLM 选择，代码组装）：
- ``build_fact_ref``：为预取 FinancialFact 生成稳定、低长度、无敏感数据的 fact_ref；
- ``AnalysisPackAssembler.assemble``：接收 ``AnalysisSelectionDraft``（只含
  ``selected_fact_refs`` 引用），从原始可信 financial_facts 中确定性取回完整
  ``FinancialFact``，组装出 ``FinancialAnalysisPack``。

为什么需要本服务：
- 旧路径要求 LLM 重新抄写完整 ``FinancialFact``，DeepSeek JSON 文本路径下模型
  没有获得完整嵌套契约，常丢失 ``company_id``/``source_id`` 等必填字段 →
  ``facts.0.company_id: Field required``；
- 本服务保证最终 pack 的 ``company_id``/``source_id``/``value``/``unit``/``period``
  全部来自原始预取事实，LLM 只表达"选择了哪些事实"，不具备改写数值的能力。

错误语义（非网络错误，禁止盲目重试）：
- 未匹配 ref → ``FACT_REFERENCE_UNRESOLVED``；
- 多重匹配 ref → ``FACT_REFERENCE_AMBIGUOUS``；
- 草稿携带来源信息与原始事实不一致 → ``FACT_PROVENANCE_MISMATCH``；
- 草稿本身不合法 → ``SCHEMA_INVALID``。

依赖方向：application → domain（Pydantic 模型）+ 标准库。
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any

from invest_research.application.analysis_metrics import compute_deterministic_metrics
from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnalysisSelectionDraft,
    FinancialAnalysisPack,
    FinancialFact,
    MetricStatus,
)

# fact_ref 稳定前缀（保留可读性；完整引用在 ref 中唯一）
_FACT_REF_PREFIX = "fr"
# 生成事实索引的 payload 最大长度防护（防止超长 accession 等撑爆内存）
_MAX_FACT_INDEX_CHARS = 512


class AnalysisAssemblerError(RuntimeError):
    """组装器确定性失败（携带稳定错误码与失败阶段 04_analysis）。"""

    error_code: str
    failure_stage: str = "04_analysis"

    def __init__(self, error_code: str, message: str, *, fact_ref: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.fact_ref = fact_ref


def _fact_ref_payload(fact: FinancialFact) -> str:
    """构造 fact_ref 的规范化输入串（含 company_id/concept/期间/单位/accession）。

    确定性：仅由事实字段组成，不含 API Key / 环境变量等敏感数据；
    低长度：16 字节 sha256 前 12 位 hex ⇒ 稳定且足够随机。

    注意：**不包含 source_id**。SEC 原始工具生成的 FinancialFact.source_id 是
    占位空串 ``""``，而 ``_serialize_facts`` 展示给 LLM 的 summary 会把它规范化为
    ``sec-companyfacts-<cik>``。若 fact_ref 依赖 source_id，LLM 拿到的 ref 与
    assembler 反序列化后重建的 ref 会不一致 → 全部 FACT_REFERENCE_UNRESOLVED。

    唯一性保证：company_id/concept/period/unit/accession/form_type/fiscal_period
    在原始工具与 `_serialize_facts` 两种表示中完全一致；fixture 中相同期间会出现
    10-Q 与 10-Q/A 两条不同事实（同 period 同 concept 同 value），必须用
    form_type/fiscal_period 区分，否则 fact_ref 碰撞 → FACT_REFERENCE_AMBIGUOUS。
    """
    company = fact.company_id or ""
    period = fact.period_start.isoformat() if fact.period_start is not None else ""
    period_end = fact.period_end.isoformat() if fact.period_end is not None else ""
    instant = fact.instant_date.isoformat() if fact.instant_date is not None else ""
    accession = fact.accession_number or ""
    unit = fact.unit or ""
    form_type = fact.form_type or ""
    fiscal_period = fact.fiscal_period or ""
    payload = "|".join(
        (
            company,
            fact.concept,
            period,
            period_end,
            instant,
            unit,
            accession,
            form_type,
            fiscal_period,
        )
    )
    return payload[:_MAX_FACT_INDEX_CHARS]


def build_fact_ref(fact: FinancialFact) -> str:
    """为单个 FinancialFact 生成稳定 fact_ref（确定性代码，LLM 不参与）。

    格式：``fr_<12位hex>``；相同事实永远得到相同 ref，不同事实碰撞概率极低。
    """
    digest = hashlib.sha256(_fact_ref_payload(fact).encode("utf-8")).hexdigest()
    return f"{_FACT_REF_PREFIX}_{digest[:12]}"


def _make_fact_index(facts: list[FinancialFact]) -> dict[str, list[FinancialFact]]:
    """构建 fact_ref → 原始事实列表索引（同一 ref 可能有多个匹配 → 歧义）。

    相同 company/concept/unit/period/accession 的事实视为同一事实：
    若两个 Fact 的 fact_ref 相同，说明它们代表同一条 SEC 数据（去重或歧义）。
    """
    index: dict[str, list[FinancialFact]] = {}
    for fact in facts:
        ref = build_fact_ref(fact)
        index.setdefault(ref, []).append(fact)
    return index


class AnalysisPackAssembler:
    """确定性组装器：AnalysisSelectionDraft → FinancialAnalysisPack。

    输入：
    - ``draft``：LLM 输出的选择草稿；
    - ``source_facts``：原始可信预取 FinancialFact 集合；
    - ``company_identity_hint``：可选公司身份提示（仅用于校验 company_id 一致性，
      不参与组装；缺失不报错）。

    处理：
    1. 按 ``selected_fact_refs`` 从原始事实集合取回完整 FinancialFact；
    2. 只接受唯一匹配；未匹配 → FACT_REFERENCE_UNRESOLVED，多重匹配 →
       FACT_REFERENCE_AMBIGUOUS；
    3. 重复 ref 幂等去重（保留首次出现顺序）；
    4. completeness=unavailable 时允许空 refs；partial 必须带 limitations；
    5. 最终本地 Pydantic 生成 FinancialAnalysisPack。
    """

    def __init__(self) -> None:
        self._index: dict[str, list[FinancialFact]] = {}

    def assemble(
        self,
        draft: AnalysisSelectionDraft,
        source_facts: list[FinancialFact],
        *,
        company_identity_hint: str | None = None,
        job_id: str = "analysis",
    ) -> FinancialAnalysisPack:
        """把选择草稿 + 原始事实组装为最终 FinancialAnalysisPack。"""
        self._index = _make_fact_index(source_facts)
        resolved: list[FinancialFact] = []
        seen: set[str] = set()
        unresolved: list[str] = []
        ambiguous: list[str] = []

        for ref in draft.selected_fact_refs:
            # 重复 ref：幂等去重（保留首次出现顺序），不视为错误。
            if ref in seen:
                continue
            seen.add(ref)
            matches = self._index.get(ref, [])
            if not matches:
                unresolved.append(ref)
                continue
            if len(matches) > 1:
                ambiguous.append(ref)
                continue
            match = matches[0]
            # 来源一致性：草稿本身不携带事实内容，此处只校验 company_identity_hint
            # 与原始事实的 company_id 是否一致（防止跨公司串数据）。
            if company_identity_hint is not None and match.company_id != company_identity_hint:
                raise AnalysisAssemblerError(
                    ErrorCode.FACT_PROVENANCE_MISMATCH.value,
                    f"fact_ref {ref} 的公司身份与预期不一致",
                    fact_ref=ref,
                )
            resolved.append(match)

        if ambiguous:
            raise AnalysisAssemblerError(
                ErrorCode.FACT_REFERENCE_AMBIGUOUS.value,
                f"存在多重匹配 fact_ref: {', '.join(ambiguous[:5])}",
                fact_ref=ambiguous[0],
            )
        if unresolved:
            raise AnalysisAssemblerError(
                ErrorCode.FACT_REFERENCE_UNRESOLVED.value,
                f"存在未解析 fact_ref: {', '.join(unresolved[:5])}",
                fact_ref=unresolved[0],
            )

        # 指标不能依赖 LLM 是否恰好调用了 FinancialCalculator。LLM 只选事实，
        # 10 项核心指标统一由本地 Decimal 公式从完整可信 source_facts 计算。
        return _build_deterministic_pack(
            period_end=draft.period_end,
            facts=resolved,
            source_facts=source_facts,
            analysis_notes=draft.analysis_notes,
            limitations=list(draft.limitations),
            unavailable_reason=draft.unavailable_reason,
            requested_completeness=draft.completeness,
            job_id=job_id,
        )


def canonicalize_analysis_pack(
    pack: FinancialAnalysisPack,
    source_facts: list[FinancialFact],
    *,
    job_id: str = "analysis",
) -> FinancialAnalysisPack:
    """让原生 Pydantic（如 Qwen）路径也使用相同的确定性指标计算。"""
    return _build_deterministic_pack(
        period_end=pack.period_end,
        facts=list(pack.facts),
        source_facts=source_facts,
        analysis_notes=pack.analysis_notes,
        limitations=list(pack.limitations),
        unavailable_reason=pack.unavailable_reason,
        requested_completeness=pack.completeness,
        job_id=job_id,
    )


def _build_deterministic_pack(
    *,
    period_end: date,
    facts: list[FinancialFact],
    source_facts: list[FinancialFact],
    analysis_notes: str | None,
    limitations: list[str],
    unavailable_reason: str | None,
    requested_completeness: AnalysisCompleteness,
    job_id: str,
) -> FinancialAnalysisPack:
    bundle = compute_deterministic_metrics(source_facts, job_id=job_id, as_of=period_end)
    computed_count = sum(
        metric.status == MetricStatus.COMPUTED for metric in bundle.metrics
    )
    # 删除旧版本由工具 Schema 缺陷产生的失真限制；真实缺数限制由确定性计算器重建。
    placeholder_limitations = {
        "部分数据不可用；请在此说明缺失项及原因",
    }
    cleaned = [
        item
        for item in limitations
        if item.strip() not in placeholder_limitations
        and "FinancialCalculator" not in item
        and "Pydantic date type" not in item
        and "date type not fully defined" not in item
    ]
    merged_limitations = list(dict.fromkeys([*cleaned, *bundle.limitations]))

    if computed_count == len(bundle.metrics):
        completeness = AnalysisCompleteness.COMPLETE
        unavailable = None
    elif requested_completeness == AnalysisCompleteness.COMPLETE and facts:
        # 保留既有契约：complete 表示已有关键 facts 或 metrics，并不要求十项
        # 指标全部可计算；不可计算项仍通过 limitations 如实披露。
        completeness = AnalysisCompleteness.COMPLETE
        unavailable = None
    elif requested_completeness == AnalysisCompleteness.PARTIAL:
        completeness = AnalysisCompleteness.PARTIAL
        unavailable = None
        if not merged_limitations:
            merged_limitations.append("部分核心指标缺少可比期间或必要 SEC 财务事实")
    elif computed_count > 0 or facts:
        completeness = AnalysisCompleteness.PARTIAL
        unavailable = None
        if not merged_limitations:
            merged_limitations.append("部分核心指标缺少可比期间或必要 SEC 财务事实")
    else:
        # unavailable 的严格契约要求 facts/metrics 均为空，不能把 10 个
        # NOT_COMPUTABLE 占位指标伪装成可用分析。
        completeness = AnalysisCompleteness.UNAVAILABLE
        unavailable = unavailable_reason or "缺少可用于确定性财务计算的 SEC 事实"

    return FinancialAnalysisPack(
            version="analysis_pack_v2",
            schema_version="analysis_pack_v2",
            period_end=period_end,
            facts=facts if completeness != AnalysisCompleteness.UNAVAILABLE else [],
            metrics=(
                list(bundle.metrics)
                if completeness != AnalysisCompleteness.UNAVAILABLE
                else []
            ),
            analysis_notes=analysis_notes,
            limitations=merged_limitations,
            completeness=completeness,
            unavailable_reason=unavailable,
        )


def parse_fact_records(records: list[dict[str, Any]]) -> list[FinancialFact]:
    """把预取 financial_facts JSON 记录反序列化为 FinancialFact 列表。

    供 flow_wiring 从 ``PrefetchResult.financial_facts_summary`` 的字符串 JSON
    重建原始可信事实集合，再交给 AnalysisPackAssembler。
    """
    facts: list[FinancialFact] = []
    for record in records:
        record = dict(record)
        # Decimal 序列化回来是 str；Pydantic 自动转 Decimal。
        value: Decimal = Decimal(str(record.get("value", "0")))
        period_start = record.get("period_start")
        period_end = record.get("period_end")
        instant_date = record.get("instant_date")
        facts.append(
            FinancialFact(
                company_id=str(record["company_id"]),
                source_id=str(record.get("source_id") or ""),
                taxonomy=str(record.get("taxonomy") or "us-gaap"),
                concept=str(record["concept"]),
                label=record.get("label"),
                value=value,
                unit=str(record.get("unit") or ""),
                period_start=date.fromisoformat(period_start) if period_start else None,
                period_end=date.fromisoformat(period_end) if period_end else None,
                instant_date=date.fromisoformat(instant_date) if instant_date else None,
                fiscal_year=record.get("fiscal_year"),
                fiscal_period=record.get("fiscal_period"),
                form_type=record.get("form_type"),
                frame=record.get("frame"),
                accession_number=record.get("accession_number"),
                fact_version=str(record.get("fact_version") or "v1"),
            )
        )
    return facts
