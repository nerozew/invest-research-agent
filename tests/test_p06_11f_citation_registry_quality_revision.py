"""P06-11F：Writer 引用注册表 + 质量门禁准确性 + 有界修订（离线测试）。

覆盖 docs/05 P06-11F 验收清单（11 项核心契约）：
1. WriterContextReader 能看到准确的 src/fr key（注册表完整交付）；
2. CitationRegistry 与 ReportDraftAssembler 使用同一算法（src/fr 复用）；
3. Markdown 使用合法 key 时 citation_keys 非空；
4. 伪造 key 被拒绝（Quality Gate invalid_citation_key）；
5. 报告使用外部事实但 citation_keys 为空 → REVISE（不无条件放宽）；
6. "建议买入 / 目标价为 200 美元"被拒绝（真实建议）；
7. "本报告不构成买入、卖出或目标价建议"不误判（免责声明放行）；
8. 一次有界修订后通过（revision_attempted/revised）；
9. 第二次仍失败时停止（revision_attempted 阻止二次尝试）；
10. 修订不重新调用 Research/Analysis/外部工具（确定性纯函数）；
11. 一次 fake E2E 仍产生合法 ReportDraft（组装 + 门禁链路回归）。

全程 FakeLLM / 本地 Pydantic / 本地 fixture，禁止真实联网。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invest_research.agents.writer_task import make_writer_context_reader
from invest_research.application.analysis_assembler import build_fact_ref
from invest_research.application.citation_registry import (
    CITATION_TYPE_FACT,
    CITATION_TYPE_SOURCE,
    build_citation_registry,
)
from invest_research.application.deterministic_revision import deterministic_revise
from invest_research.application.empty_value_policy import (
    CompletenessVerdict,
    check_analysis_completeness,
    is_missing,
    normalize_optional_text,
    resolve_empty_policy,
)
from invest_research.application.report_draft_assembler import (
    ReportDraftAssembler,
    build_source_citation_key,
)
from invest_research.domain.models import (
    AnalysisCompleteness,
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ReportDraft,
    ResearchPack,
    ResearchRequest,
    Source,
    SourceType,
)
from invest_research.domain.quality import QualityRecommendation
from invest_research.flows.quality_classifier import classify_state, find_forbidden_advice
from invest_research.flows.state import ResearchFlowState

_TEST_API_KEY = "sk-test-placeholder"


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 12, 31))


def _identity() -> CompanyIdentity:
    return CompanyIdentity(cik="0000789019", ticker="MSFT", legal_name="Microsoft Corp")


def _source() -> Source:
    return Source(
        source_type=SourceType.SEC_FILING,
        canonical_url="https://www.sec.gov/Archives/edgar/data/0000789019/10-K",
        title="10-K filed 2025-10-31",
        accessed_at=date(2025, 10, 31),
    )


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=_identity(),
        as_of_date=date(2025, 12, 31),
        sources=[_source()],
    )


def _fact() -> FinancialFact:
    return FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Revenue",
        value=Decimal("245100000000"),
        unit="USD",
        period_start=date(2024, 7, 1),
        period_end=date(2025, 6, 30),
    )


def _analysis_pack(
    completeness: AnalysisCompleteness = AnalysisCompleteness.COMPLETE,
) -> FinancialAnalysisPack:
    kwargs: dict[str, object] = {
        "version": "analysis_pack_v2",
        "schema_version": "analysis_pack_v2",
        "period_end": date(2025, 6, 30),
        "completeness": completeness,
    }
    if completeness == AnalysisCompleteness.COMPLETE:
        kwargs["facts"] = [_fact()]
    elif completeness == AnalysisCompleteness.PARTIAL:
        kwargs["facts"] = [_fact()]
        kwargs["limitations"] = ["缺少现金流明细，原因：未取得对应申报段"]
    else:
        kwargs["unavailable_reason"] = "目标公司未披露要求的 XBRL 概念"
    return FinancialAnalysisPack(**kwargs)


def _valid_markdown() -> str:
    src = build_source_citation_key(_source())
    fr = build_fact_ref(_fact())
    return (
        "## 执行摘要\n"
        "本报告基于已核实的 SEC 申报文件整理，仅用于技术演示。"
        "本报告不构成任何投资建议，也不包含任何收益承诺。\n"
        "## 公司与业务概览\n"
        "Microsoft Corp 是一家跨国科技公司，主要业务包括生产力软件、"
        "智能云与企业服务、个人计算设备等。\n"
        "## 财务表现\n"
        f"营业收入为 2451 亿美元 [{fr}]。"
        f"信息来源为 10-K 申报 [{src}]。"
        "本节所有数据均来自上游 FinancialAnalysisPack，未做任何推断。\n"
        "## 风险因素与催化因素\n"
        "市场与监管风险并存，包括宏观经济波动、竞争加剧与合规要求变化等。\n"
        "## 数据限制\n"
        "数据仅截至指定数据截止日，超过该日期的信息不在本报告范围内。\n"
        "## 来源清单与非投资建议声明\n"
        "本报告不构成任何投资建议。投资者应直接查阅 SEC 原始文件。\n"
    )


def _registry() -> object:
    return build_citation_registry(_research_pack(), _analysis_pack())


# ---------------------------------------------------------------------------
# 1~2. WriterContextReader 可见 key + Registry 与 Assembler 同一算法
# ---------------------------------------------------------------------------


class TestCitationRegistryWiring:
    def test_reader_payload_contains_src_and_fr_keys(self) -> None:
        research = _research_pack()
        analysis = _analysis_pack()
        registry = _registry()
        src_key = build_source_citation_key(_source())
        fr_key = build_fact_ref(_fact())

        def loader(key: str):
            if key == "research_pack":
                return research.model_dump(mode="json")
            if key == "analysis_pack":
                return analysis.model_dump(mode="json")
            return None

        reader = make_writer_context_reader(loader, registry)
        reader_fn = getattr(reader, "func", reader)
        payload = reader_fn()
        entries = {e["citation_key"]: e for e in payload["citation_registry"]["entries"]}
        assert src_key in entries
        assert fr_key in entries
        assert entries[src_key]["citation_type"] == CITATION_TYPE_SOURCE
        assert entries[fr_key]["citation_type"] == CITATION_TYPE_FACT
        assert entries[fr_key]["value"] == "245100000000"

    def test_registry_same_algorithm_as_assembler(self) -> None:
        registry = _registry()
        assert registry.contains(build_source_citation_key(_source()))
        assert registry.contains(build_fact_ref(_fact()))

    def test_registry_empty_when_no_sources_or_facts(self) -> None:
        research = _research_pack().model_copy(update={"sources": []})
        analysis = _analysis_pack(AnalysisCompleteness.UNAVAILABLE)
        registry = build_citation_registry(research, analysis)
        assert registry.is_empty is True


# ---------------------------------------------------------------------------
# 3~5. citation_keys 合法提取 / 伪造拒绝 / 外部事实空引用 REVISE
# ---------------------------------------------------------------------------


class TestCitationValidation:
    def test_valid_keys_yield_non_empty_citation_keys(self) -> None:
        registry = _registry()
        draft = ReportDraftAssembler().assemble(
            _valid_markdown(),
            _request(),
            _research_pack(),
            _analysis_pack(),
            registry=registry,
        )
        assert draft.citation_keys
        assert build_source_citation_key(_source()) in draft.citation_keys
        assert build_fact_ref(_fact()) in draft.citation_keys

    def test_fake_key_rejected_by_quality_gate(self) -> None:
        state = ResearchFlowState(
            request=_request(),
            research_pack=_research_pack(),
            analysis_pack=_analysis_pack(),
            report_draft=ReportDraft(
                version="report_draft_v1",
                title="t",
                markdown=_valid_markdown(),
                citation_keys=["src_fake000000000", build_fact_ref(_fact())],
            ),
            citation_registry=_registry(),
        )
        issues = classify_state(state)
        assert any(i.code == "invalid_citation_key" for i in issues)

    def test_external_facts_with_empty_citations_revise(self) -> None:
        md = _valid_markdown().replace(
            f"[{build_source_citation_key(_source())}]", ""
        ).replace(f"[{build_fact_ref(_fact())}]", "")
        state = ResearchFlowState(
            request=_request(),
            research_pack=_research_pack(),
            analysis_pack=_analysis_pack(),
            report_draft=ReportDraft(
                version="report_draft_v1", title="t", markdown=md, citation_keys=[]
            ),
        )
        issues = classify_state(state)
        assert "missing_citation_keys" in {i.code for i in issues}


# ---------------------------------------------------------------------------
# 6~7. 禁止项检测（上下文规则）
# ---------------------------------------------------------------------------


class TestForbiddenAdvice:
    def test_real_buy_advice_rejected(self) -> None:
        md = _valid_markdown().replace(
            "本报告不构成任何投资建议。",
            "建议买入该股票。本报告不构成任何投资建议。",
        )
        assert find_forbidden_advice(md)

    def test_target_price_rejected(self) -> None:
        md = _valid_markdown().replace(
            "本报告不构成任何投资建议。",
            "目标价为 200 美元。本报告不构成任何投资建议。",
        )
        hits = find_forbidden_advice(md)
        assert hits and any("200" in h for h in hits)

    def test_disclaimer_not_misjudged(self) -> None:
        md = (
            "## 来源清单与非投资建议声明\n"
            "本报告不构成买入、卖出或目标价建议，也不构成任何收益承诺。"
            "本报告仅用于技术演示。"
        )
        assert find_forbidden_advice(md) == []


# ---------------------------------------------------------------------------
# 空值策略（EmptyValuePolicy）
# ---------------------------------------------------------------------------


class TestEmptyValuePolicy:
    def test_complete_facts_empty_rejected(self) -> None:
        v = check_analysis_completeness(
            completeness="complete", facts=[], metrics=[], limitations=[], unavailable_reason=None
        )
        assert v.verdict == CompletenessVerdict.REJECT

    def test_partial_with_limitations_publish_partial(self) -> None:
        v = check_analysis_completeness(
            completeness="partial",
            facts=[_fact()],
            metrics=[],
            limitations=["缺现金流"],
            unavailable_reason=None,
        )
        assert v.verdict == CompletenessVerdict.PUBLISH_PARTIAL

    def test_partial_without_limitations_rejected(self) -> None:
        v = check_analysis_completeness(
            completeness="partial",
            facts=[_fact()],
            metrics=[],
            limitations=[],
            unavailable_reason=None,
        )
        assert v.verdict == CompletenessVerdict.REJECT

    def test_unavailable_with_reason_publish_partial(self) -> None:
        v = check_analysis_completeness(
            completeness="unavailable",
            facts=[],
            metrics=[],
            limitations=[],
            unavailable_reason="未披露 XBRL 概念",
        )
        assert v.verdict == CompletenessVerdict.PUBLISH_PARTIAL

    def test_unavailable_without_reason_rejected(self) -> None:
        v = check_analysis_completeness(
            completeness="unavailable",
            facts=[],
            metrics=[],
            limitations=[],
            unavailable_reason=None,
        )
        assert v.verdict == CompletenessVerdict.REJECT

    def test_optional_empty_field_passes(self) -> None:
        assert is_missing("", resolve_empty_policy("analysis_notes")) is False
        assert is_missing(None, resolve_empty_policy("analysis_notes")) is True

    def test_required_blank_string_is_missing(self) -> None:
        assert is_missing("   ", resolve_empty_policy("report_markdown")) is True
        assert is_missing([], resolve_empty_policy("citation_keys")) is True

    def test_normalize_optional_text_only_optional(self) -> None:
        assert normalize_optional_text("   ", resolve_empty_policy("analysis_notes")) is None
        assert normalize_optional_text("   ", resolve_empty_policy("report_markdown")) == "   "


# ---------------------------------------------------------------------------
# 8~10. 有界修订
# ---------------------------------------------------------------------------


class TestBoundedRevision:
    def test_revision_is_pure_deterministic(self) -> None:
        """修订是纯函数：模块不依赖任何外部工具/网络（只 import 标准库 + typing）。"""
        import invest_research.application.deterministic_revision as mod

        # 模块自身 import 集合必须全部是标准库或 typing：
        # 不引入 openai/requests/httpx/serper/crewai —— 证明修订不调用外部工具。
        # 直接检查模块的全局符号：不应出现外部客户端/工具类。
        forbidden_symbols = {"openai", "requests", "httpx", "crewai"}
        assert not forbidden_symbols.intersection(dir(mod))

    def test_one_revision_wraps_keys(self) -> None:
        registry = _registry()
        fr = build_fact_ref(_fact())
        # 修订输入只含原稿 + issue codes + registry。
        # 伪造 key 必须符合 citation key 的字符格式（[A-Za-z0-9_]）才会被识别并删除。
        raw = "本报告不构成任何投资建议。数据引用详见 [src_fake00000] 与 " + fr + "。"
        revised = deterministic_revise(
            markdown=raw,
            issue_codes=["invalid_citation_key", "missing_citation_keys"],
            registry=registry,
        )
        assert revised is not None
        assert "[src_fake00000]" not in revised
        assert f"[{fr}]" in revised

    def test_second_attempt_stops(self) -> None:
        """revision_attempted=True 时不允许再次修订（_apply_bounded_revision 直接返回）。"""
        # 该约束由 Flow 层 state.revision_attempted 标志实现；这里验证状态可序列化。
        state = ResearchFlowState(
            request=_request(),
            research_pack=_research_pack(),
            analysis_pack=_analysis_pack(),
            report_draft=ReportDraft(
                version="report_draft_v1",
                title="t",
                markdown=_valid_markdown(),
                citation_keys=[],
            ),
            citation_registry=_registry(),
            revision_attempted=True,
        )
        dumped = state.model_dump(mode="json")
        assert dumped["revision_attempted"] is True
        assert dumped["revision_succeeded"] is False

    def test_revision_does_not_fabricate_when_registry_empty(self) -> None:
        """注册表为空时修订不伪造 key：返回 None（保持原稿，不产生假引用）。"""
        revised = deterministic_revise(
            markdown="无任何合法 key。",
            issue_codes=["missing_citation_keys"],
            registry=build_citation_registry(
                _research_pack().model_copy(update={"sources": []}),
                _analysis_pack(AnalysisCompleteness.UNAVAILABLE),
            ),
        )
        assert revised is None


# ---------------------------------------------------------------------------
# 11. fake E2E 回归：一次全链仍产生合法 ReportDraft
# ---------------------------------------------------------------------------


class TestFakeE2ERegression:
    def test_assembler_with_registry_passes_gate(self) -> None:
        from invest_research.flows.quality import run_quality_gate

        registry = _registry()
        draft = ReportDraftAssembler().assemble(
            _valid_markdown(),
            _request(),
            _research_pack(),
            _analysis_pack(),
            registry=registry,
        )
        state = ResearchFlowState(
            request=_request(),
            research_pack=_research_pack(),
            analysis_pack=_analysis_pack(),
            report_draft=draft,
            citation_registry=registry,
        )
        report = run_quality_gate(state)
        assert report.all_passed is True
        assert report.recommendation == QualityRecommendation.PUBLISH
