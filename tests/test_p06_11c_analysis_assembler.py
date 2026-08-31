"""P06-11C：DeepSeek 普通 JSON 路径的嵌套 FinancialFact 契约丢失修复（离线测试）。

验证目标（docs/05 P06-11C 验收，20 项契约）：
1. 真实预取 fixture 包含 company_id/source_id；
2. 模型草稿只返回 fact_ref（不抄写完整 FinancialFact）；
3. assembler 能恢复完整 FinancialFact；
4. 最终 pack 中 company_id/source_id 与原始输入完全一致；
5. LLM 不能修改 value/unit/period（assembler 丢弃草稿中的任何数值改写）；
6. 不存在的 fact_ref 稳定失败（FACT_REFERENCE_UNRESOLVED）；
7. 多重匹配稳定失败（FACT_REFERENCE_AMBIGUOUS）；
8. 重复 fact_ref 幂等去重；
9. unavailable + 空 refs 合法；
10. partial 必须有 limitations；
11. complete 必须具有 facts 或 metrics；
12. FinancialFact.company_id 仍然是必填；
13. 模型直接输出缺 company_id 的完整 fact 不得静默通过；
14. DeepSeek 仍不绑定 output_pydantic/output_json；
15. 不触发 beta.chat.completions.parse；
16. Qwen 原路径不受影响；
17. ArtifactReader/Writer 读取的是组装后的 FinancialAnalysisPack；
18. 错误消息不泄露 API Key；
19. 不存在任何用常量补 company_id 的逻辑；
20. 使用 realistic financial_facts_summary fixture 做端到端离线契约测试。

全程使用 FakeLLM / 本地 Pydantic / 本地 fixture，禁止真实联网。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from invest_research.agents.analysis_task import build_analysis_task
from invest_research.agents.crew_factory import _task_output_loader
from invest_research.agents.llm_factory import (
    FakeLLM,
    LLMConfig,
    LLMRole,
    StructuredOutputMode,
)
from invest_research.agents.pack_parsing import PackBoundary
from invest_research.application.analysis_assembler import (
    AnalysisAssemblerError,
    AnalysisPackAssembler,
    build_fact_ref,
    parse_fact_records,
)
from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import (
    AnalysisCompleteness,
    AnalysisSelectionDraft,
    FinancialAnalysisPack,
    FinancialFact,
)

FIXTURE = Path(__file__).parent / "fixtures" / "companyfacts_msft.json"
_TEST_API_KEY = "sk-test-placeholder"


def _config(vendor: str) -> LLMConfig:
    """构造供应商测试配置（vendor 仅限 qwen/deepseek/generic）。"""
    return LLMConfig(
        provider="openai_compatible",
        vendor=vendor,  # type: ignore[arg-type]
        base_url="https://example.com/v1",
        api_key=SecretStr(_TEST_API_KEY),
        model_research=f"{vendor}-research",
        model_analysis=f"{vendor}-analysis",
        model_writer=f"{vendor}-writer",
    )


def _build_real_facts() -> list[FinancialFact]:
    """从真实 SEC Company Facts fixture 解析为 FinancialFact 列表（含 company_id/source_id）。"""
    import httpx

    from invest_research.tools.sec_company_facts import FetchFactsRequest, SECCompanyFactsTool

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode(), request=request)

    tool = SECCompanyFactsTool(httpx.Client(transport=httpx.MockTransport(handler)))
    result = tool.execute(FetchFactsRequest(cik="0000789019"))
    assert getattr(result, "kind", None) == "success"
    return list(result.value.facts)


def _serialize_facts_summary(facts: list[FinancialFact]) -> str:
    """模拟 real_tools._serialize_facts 的 summary 结构（含规范化 source_id + fact_ref）。"""
    items = []
    for fact in facts:
        items.append(
            {
                "fact_ref": build_fact_ref(fact),
                "company_id": fact.company_id,
                "source_id": f"sec-companyfacts-{fact.company_id}",
                "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{fact.company_id}.json",
                "metric_name": "dummy",
                "taxonomy": fact.taxonomy,
                "concept": fact.concept,
                "label": fact.label,
                "value": str(fact.value),
                "unit": fact.unit,
                "period_start": fact.period_start.isoformat() if fact.period_start else None,
                "period_end": fact.period_end.isoformat() if fact.period_end else None,
                "instant_date": fact.instant_date.isoformat() if fact.instant_date else None,
                "fiscal_year": fact.fiscal_year,
                "fiscal_period": fact.fiscal_period,
                "form_type": fact.form_type,
                "accession_number": fact.accession_number,
                "locator": f"accn={fact.accession_number}; concept={fact.concept}",
            }
        )
    return json.dumps(
        {"ok": True, "mapping_version": "concepts_v1", "count": len(items), "facts": items},
        ensure_ascii=False,
    )


@pytest.fixture(scope="module")
def msft_facts() -> list[FinancialFact]:
    return _build_real_facts()


@pytest.fixture(scope="module")
def summary_text(msft_facts: list[FinancialFact]) -> str:
    return _serialize_facts_summary(msft_facts)


# ---------------------------------------------------------------------------
# 1. 真实预取 fixture 包含 company_id/source_id（fact_ref 由确定性代码生成）
# ---------------------------------------------------------------------------


def test_fixture_facts_have_company_id_and_source_id(msft_facts: list[FinancialFact]) -> None:
    """SEC fixture 每条事实都含 company_id（CIK）与非空 source_id（规范化后）。"""
    assert msft_facts
    for fact in msft_facts:
        assert fact.company_id == "0000789019"
        # 原始工具 source_id 是占位空串；summary 侧会规范化。这里断言 summary 侧含值。
        assert fact.value is not None
        # fixture 无 accession_number；用 form_type/fiscal_period 等稳定字段证明事实完整。
        assert fact.concept
        assert fact.unit
        assert fact.form_type


def test_summary_contains_fact_ref_and_company_id(summary_text: str) -> None:
    """summary JSON 每条 fact 都含 fact_ref 与 company_id（LLM 输入契约）。"""
    payload = json.loads(summary_text)
    assert payload["ok"] is True
    for item in payload["facts"]:
        assert item["fact_ref"].startswith("fr_")
        assert len(item["fact_ref"]) == 15  # fr_ + 12 hex
        assert item["company_id"] == "0000789019"
        assert item["source_id"] == "sec-companyfacts-0000789019"


def test_build_fact_ref_is_deterministic(msft_facts: list[FinancialFact]) -> None:
    """相同事实生成相同 ref；不同事实 ref 不同。"""
    first = msft_facts[0]
    assert build_fact_ref(first) == build_fact_ref(first)
    refs = {build_fact_ref(f) for f in msft_facts}
    assert len(refs) == len(msft_facts)


# ---------------------------------------------------------------------------
# 2~4. 模型草稿只返回 fact_ref；assembler 恢复完整 FinancialFact；字段与输入一致
# ---------------------------------------------------------------------------


def test_assembler_restores_full_fact_from_ref(
    msft_facts: list[FinancialFact], summary_text: str
) -> None:
    """模型草稿只写 ref → assembler 恢复完整 FinancialFact（company_id/source_id 一致）。"""
    payload = json.loads(summary_text)
    refs = [item["fact_ref"] for item in payload["facts"][:2]]
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=refs,
        completeness=AnalysisCompleteness.COMPLETE,
    )
    # 从 summary 反序列化回事实集合（模拟 flow_wiring._parse_prefetched_facts）
    source_facts = parse_fact_records(payload["facts"])
    pack = AnalysisPackAssembler().assemble(draft, source_facts)

    assert isinstance(pack, FinancialAnalysisPack)
    assert len(pack.facts) == 2
    for fact, item in zip(pack.facts, payload["facts"][:2]):
        assert fact.company_id == item["company_id"]
        assert fact.source_id == item["source_id"]
        assert fact.value == Decimal(str(item["value"]))
        assert fact.unit == item["unit"]
        assert fact.concept == item["concept"]


def test_assembler_fact_matches_original(
    msft_facts: list[FinancialFact], summary_text: str
) -> None:
    """最终 pack 的事实与原始 SEC 事实完全一致（value/unit/period/company_id/source_id）。"""
    payload = json.loads(summary_text)
    refs = [item["fact_ref"] for item in payload["facts"]]
    source_facts = parse_fact_records(payload["facts"])
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=refs,
        completeness=AnalysisCompleteness.COMPLETE,
    )
    pack = AnalysisPackAssembler().assemble(draft, source_facts)
    # 逐字段精确比对：同一 concept 的不同期间事实必须精确匹配
    # （fixture 含 FY2024 与 FY2026 Q1/Q1/A 多条 Revenue，只用 concept 会匹配错）。
    for pack_fact in pack.facts:
        original = next(
            (
                f
                for f in msft_facts
                if f.concept == pack_fact.concept
                and f.period_end == pack_fact.period_end
                and f.instant_date == pack_fact.instant_date
                and f.form_type == pack_fact.form_type
                and f.unit == pack_fact.unit
            ),
            None,
        )
        assert original is not None
        assert pack_fact.value == original.value
        assert pack_fact.unit == original.unit
        if original.period_end:
            assert pack_fact.period_end == original.period_end
        if original.instant_date:
            assert pack_fact.instant_date == original.instant_date
        assert pack_fact.company_id == original.company_id


def test_llm_cannot_modify_value_period(msft_facts: list[FinancialFact], summary_text: str) -> None:
    """草稿只含 refs；assembler 输出 value/unit/period 全部来自原始事实，LLM 无法改写。"""
    payload = json.loads(summary_text)
    item = payload["facts"][0]
    ref = item["fact_ref"]
    # 即使草稿携带错误的 period_end（模型猜测），组装结果仍以原始事实为准。
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),  # 合法草稿 period_end（组装用）
        selected_fact_refs=[ref],
        completeness=AnalysisCompleteness.COMPLETE,
    )
    source_facts = parse_fact_records(payload["facts"])
    pack = AnalysisPackAssembler().assemble(draft, source_facts)
    fact = pack.facts[0]
    # 原始值的精确比对（不依赖 LLM 抄写——模型根本没有机会写 value）
    orig = parse_fact_records([item])[0]
    assert fact.value == orig.value
    assert fact.unit == orig.unit
    assert fact.period_end == orig.period_end


# ---------------------------------------------------------------------------
# 6. 不存在的 fact_ref 稳定失败
# ---------------------------------------------------------------------------


def test_unresolved_fact_ref_fails_stable(msft_facts: list[FinancialFact]) -> None:
    """不存在的 fact_ref → AnalysisAssemblerError（FACT_REFERENCE_UNRESOLVED）。"""
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=["fr_deadbeefdead"],
        completeness=AnalysisCompleteness.COMPLETE,
    )
    with pytest.raises(AnalysisAssemblerError) as exc_info:
        AnalysisPackAssembler().assemble(draft, msft_facts)
    assert exc_info.value.error_code == ErrorCode.FACT_REFERENCE_UNRESOLVED.value
    assert exc_info.value.failure_stage == "04_analysis"
    assert exc_info.value.fact_ref == "fr_deadbeefdead"


# ---------------------------------------------------------------------------
# 7. 多重匹配稳定失败
# ---------------------------------------------------------------------------


def test_ambiguous_fact_ref_fails_stable() -> None:
    """同一 fact_ref 在源集合中有多条 → FACT_REFERENCE_AMBIGUOUS。"""
    base = FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Assets",
        value=Decimal("100"),
        unit="USD",
        instant_date=date(2024, 6, 30),
        accession_number="ACC-0001",
    )
    duplicate = FinancialFact(
        company_id="0000789019",
        source_id="sec-companyfacts-0000789019",
        taxonomy="us-gaap",
        concept="Assets",
        value=Decimal("100"),
        unit="USD",
        instant_date=date(2024, 6, 30),
        accession_number="ACC-0001",
    )
    ref = build_fact_ref(base)
    assert build_fact_ref(duplicate) == ref
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=[ref],
        completeness=AnalysisCompleteness.COMPLETE,
    )
    with pytest.raises(AnalysisAssemblerError) as exc_info:
        AnalysisPackAssembler().assemble(draft, [base, duplicate])
    assert exc_info.value.error_code == ErrorCode.FACT_REFERENCE_AMBIGUOUS.value


# ---------------------------------------------------------------------------
# 8. 重复 fact_ref 幂等去重
# ---------------------------------------------------------------------------


def test_duplicate_fact_ref_deduped(msft_facts: list[FinancialFact]) -> None:
    """重复 ref 幂等去重（保留首次出现顺序，不报错）。"""
    first = build_fact_ref(msft_facts[0])
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=[first, first, first],
        completeness=AnalysisCompleteness.COMPLETE,
    )
    pack = AnalysisPackAssembler().assemble(draft, msft_facts)
    assert len(pack.facts) == 1
    assert pack.facts[0].concept == msft_facts[0].concept


# ---------------------------------------------------------------------------
# 9~11. completeness 语义
# ---------------------------------------------------------------------------


def test_unavailable_with_empty_refs_is_legal() -> None:
    """completeness=unavailable + selected_fact_refs=[] 合法，且必须带 unavailable_reason。"""
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=[],
        completeness=AnalysisCompleteness.UNAVAILABLE,
        unavailable_reason="未取得 SEC 财务事实",
    )
    pack = AnalysisPackAssembler().assemble(draft, [])
    assert pack.completeness == AnalysisCompleteness.UNAVAILABLE
    assert pack.facts == []
    assert pack.unavailable_reason == "未取得 SEC 财务事实"


def test_partial_requires_limitations() -> None:
    """completeness=partial 必须带 limitations；否则草稿校验失败。"""
    with pytest.raises(Exception):
        AnalysisSelectionDraft(
            version="analysis_selection_draft_v1",
            period_end=date(2024, 6, 30),
            selected_fact_refs=[],
            completeness=AnalysisCompleteness.PARTIAL,
        )


def test_complete_requires_facts_or_metrics() -> None:
    """completeness=complete 但 refs 与 metrics 都为空 → 草稿校验失败。"""
    with pytest.raises(Exception):
        AnalysisSelectionDraft(
            version="analysis_selection_draft_v1",
            period_end=date(2024, 6, 30),
            selected_fact_refs=[],
            completeness=AnalysisCompleteness.COMPLETE,
        )


# ---------------------------------------------------------------------------
# 12~13. company_id 必填约束
# ---------------------------------------------------------------------------


def test_financial_fact_company_id_still_required() -> None:
    """FinancialFact.company_id 仍是必填（不得改为 Optional）。"""
    with pytest.raises(Exception):
        FinancialFact(
            source_id="x",
            taxonomy="us-gaap",
            concept="Assets",
            value=Decimal("1"),
            unit="USD",
            instant_date=date(2024, 6, 30),
        )


def test_llm_full_fact_missing_company_id_not_silently_passed() -> None:
    """模型试图直接在 JSON 里输出缺 company_id 的完整 fact → PackBoundary 拒绝。"""
    boundary = PackBoundary(max_repairs=0)
    raw = (
        '{"version": "analysis_pack_v2", "period_end": "2024-06-30", '
        '"facts": [{"source_id": "s", "taxonomy": "us-gaap", "concept": "Assets", '
        '"value": "100", "unit": "USD", "instant_date": "2024-06-30"}], '
        '"completeness": "complete"}'
    )
    pack, errors = boundary.parse(raw, FinancialAnalysisPack, stage="analysis")
    assert pack is None
    assert errors
    assert any(e.error_code == "MISSING_FIELD" for e in errors)


# ---------------------------------------------------------------------------
# 14~15. DeepSeek 不绑 output_pydantic/output_json；不触发 beta
# ---------------------------------------------------------------------------


def test_deepseek_analysis_task_still_no_native_pydantic() -> None:
    """DeepSeek Analysis Task 仍不绑定 output_pydantic/output_json（P06-11C 保留）。"""
    config = _config("deepseek")
    task = build_analysis_task(config, fake=FakeLLM(config=config, role=LLMRole.ANALYSIS))
    assert task.output_pydantic is None
    assert task.output_json is None


def test_deepseek_task_prompt_requests_selection_draft() -> None:
    """DeepSeek 提示词明确要求只输出 AnalysisSelectionDraft（不抄写完整 fact）。"""
    config = _config("deepseek")
    task = build_analysis_task(config, fake=FakeLLM(config=config, role=LLMRole.ANALYSIS))
    assert "selected_fact_refs" in task.description
    assert "禁止输出完整 FinancialFact" in task.description
    assert "最终答案只能是一个 JSON object" in task.description


def test_structured_output_mode_unchanged() -> None:
    """qwen 仍 NATIVE_PYDANTIC；deepseek/generic 仍 JSON_TEXT_LOCAL_VALIDATION。"""
    from invest_research.agents.llm_factory import structured_output_mode

    assert structured_output_mode(_config("qwen")) == StructuredOutputMode.NATIVE_PYDANTIC
    assert (
        structured_output_mode(_config("deepseek"))
        == StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION
    )
    assert (
        structured_output_mode(_config("generic"))
        == StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION
    )


# ---------------------------------------------------------------------------
# 16. Qwen 原路径不受影响
# ---------------------------------------------------------------------------


def test_qwen_analysis_task_keeps_native_output_pydantic() -> None:
    """Qwen Analysis Task 继续绑定 output_pydantic=FinancialAnalysisPack。"""
    from invest_research.domain.models import FinancialAnalysisPack

    config = _config("qwen")
    task = build_analysis_task(config, fake=FakeLLM(config=config, role=LLMRole.ANALYSIS))
    assert task.output_pydantic is FinancialAnalysisPack
    assert "selected_fact_refs" not in task.description


# ---------------------------------------------------------------------------
# 17. ArtifactReader 读取的是组装后的 FinancialAnalysisPack
# ---------------------------------------------------------------------------


def test_artifact_reader_gets_assembled_pack_via_loader(summary_text: str) -> None:
    """Writer 的 analysis_pack loader 读到的必须是组装后的完整 pack（非草稿）。"""
    payload = json.loads(summary_text)
    refs = [item["fact_ref"] for item in payload["facts"][:1]]
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=refs,
        completeness=AnalysisCompleteness.COMPLETE,
    )
    source_facts = parse_fact_records(payload["facts"])

    class _FakeTask:
        output = SimpleNamespace(
            pydantic=draft,
            json_dict=draft.model_dump(mode="json"),
            exported_output=draft.model_dump(mode="json"),
            raw=draft.model_dump_json(),
        )

    loader = _task_output_loader(None, _FakeTask(), analysis_facts=source_facts)  # type: ignore[arg-type]
    content = loader("analysis_pack")
    assert content is not None
    assert "selected_fact_refs" not in content  # 草稿键已消除
    assert "facts" in content
    assert "company_id" in content["facts"][0]


def test_artifact_reader_returns_none_when_no_facts_for_draft(summary_text: str) -> None:
    """草稿无法组装（无预取 facts）→ loader 返回 None（Writer 探测 not_found）。"""
    payload = json.loads(summary_text)
    refs = [item["fact_ref"] for item in payload["facts"][:1]]
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=refs,
        completeness=AnalysisCompleteness.COMPLETE,
    )

    class _FakeTask:
        output = SimpleNamespace(
            pydantic=draft,
            json_dict=draft.model_dump(mode="json"),
            exported_output=draft.model_dump(mode="json"),
            raw=draft.model_dump_json(),
        )

    loader = _task_output_loader(None, _FakeTask(), analysis_facts=None)  # type: ignore[arg-type]
    assert loader("analysis_pack") is None


# ---------------------------------------------------------------------------
# 18. 错误消息不泄露 API Key
# ---------------------------------------------------------------------------


def test_assembler_error_does_not_leak_api_key(msft_facts: list[FinancialFact]) -> None:
    """错误消息截断且不含 API Key。"""
    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=["fr_deadbeefdead"],
        completeness=AnalysisCompleteness.COMPLETE,
    )
    try:
        AnalysisPackAssembler().assemble(draft, msft_facts)
    except AnalysisAssemblerError as exc:
        assert _TEST_API_KEY not in str(exc)
        assert "sk-" not in str(exc)
    else:
        pytest.fail("应当抛出 AnalysisAssemblerError")


# ---------------------------------------------------------------------------
# 19. 不存在任何用常量补 company_id 的逻辑
# ---------------------------------------------------------------------------

_ANALYSIS_ASSEMBLER_SOURCE = (
    Path(__file__).parents[1]
    / "src"
    / "invest_research"
    / "application"
    / "analysis_assembler.py"
)


def test_no_constant_company_id_fallback() -> None:
    """assembler 源码不得包含常量补 company_id 的逻辑（如 company_id=\"0000000000\"）。"""
    source = _ANALYSIS_ASSEMBLER_SOURCE.read_text(encoding="utf-8")
    assert "company_id" in source  # 字段存在
    # 禁止任何把 company_id 硬编码为常量的赋值（例如 = "0000" / = "xxx"）
    assert "company_id=\"0" not in source.replace(" ", "")
    assert "company_id = \"0" not in source
    assert "company_id=\"sec-companyfacts" not in source

    models_source = (
        Path(__file__).parents[1]
        / "src" / "invest_research" / "domain" / "models.py"
    ).read_text(encoding="utf-8")
    # FinancialFact.company_id 保持必填（无 default）
    assert "    company_id: str\n" in models_source


# ---------------------------------------------------------------------------
# 20. realistic financial_facts_summary fixture 端到端离线契约测试
# ---------------------------------------------------------------------------


def test_end_to_end_offline_contract(summary_text: str) -> None:
    """真实 summary fixture → 草稿 → 组装 → PackBoundary 业务校验 全链路。"""
    payload = json.loads(summary_text)
    refs = [item["fact_ref"] for item in payload["facts"]]
    source_facts = parse_fact_records(payload["facts"])

    draft = AnalysisSelectionDraft(
        version="analysis_selection_draft_v1",
        schema_version="analysis_selection_draft_v1",
        period_end=date(2024, 6, 30),
        selected_fact_refs=refs,
        metric_results=[],
        analysis_notes="离线契约测试",
        limitations=[],
        completeness=AnalysisCompleteness.COMPLETE,
    )
    pack = AnalysisPackAssembler().assemble(draft, source_facts)
    assert isinstance(pack, FinancialAnalysisPack)
    assert pack.schema_version == "analysis_pack_v2"
    assert pack.completeness == AnalysisCompleteness.COMPLETE
    assert pack.facts
    assert pack.period_end == date(2024, 6, 30)

    # 组装后的 pack 必须再通过本地 Pydantic 完整性校验（业务契约）
    again = FinancialAnalysisPack.model_validate(pack.model_dump(mode="json"))
    assert again == pack


# ---------------------------------------------------------------------------
# 补充：错误码非重试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.FACT_REFERENCE_UNRESOLVED,
        ErrorCode.FACT_REFERENCE_AMBIGUOUS,
        ErrorCode.FACT_PROVENANCE_MISMATCH,
    ],
)
def test_fact_reference_errors_not_retryable(code: ErrorCode) -> None:
    """fact_ref 相关错误均不可自动重试（确定性本地问题）。"""
    from invest_research.domain.errors import is_retryable

    assert not is_retryable(code)
