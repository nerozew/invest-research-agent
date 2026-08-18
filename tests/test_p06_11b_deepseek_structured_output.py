"""P06-11B：DeepSeek 与 CrewAI 结构化输出不兼容修复（离线测试，不联网）。

验证目标（docs/05 P06-11B 验收）：
1. DeepSeek Research/Analysis/Writer Task 不配置原生 output_pydantic；
2. Qwen 继续保持原有结构化输出行为（绑定 output_pydantic）；
3. DeepSeek 返回合法 JSON 文本时，本地 PackBoundary 成功解析为对应 Pydantic Pack；
4. 缺少 version/title/markdown 等必填字段仍然失败；
5. 工具参数不能当作 FinancialAnalysisPack；
6. ArtifactReader 参数不能当作 ReportDraft；
7. 模拟 response_format 400 时分类为 STRUCTURED_OUTPUT_UNSUPPORTED（不可重试）；
8. 修复不触发 beta.chat.completions.parse（Task 不绑定 output_pydantic/output_json）；
9. API Key 不出现在 repr/异常/错误消息；
10. Qwen/DeepSeek 三角色模型配置仍分别生效。

全程使用 FakeLLM / 本地 PackBoundary / 纯分类器，禁止真实联网。
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import SecretStr

from invest_research.agents.analysis_task import build_analysis_task
from invest_research.agents.llm_factory import (
    FakeLLM,
    LLMConfig,
    LLMRole,
    StructuredOutputMode,
    structured_output_mode,
)
from invest_research.agents.pack_parsing import PackBoundary
from invest_research.agents.research_task import build_research_task
from invest_research.agents.writer_task import build_writer_task
from invest_research.application.failure_classifier import classify_failure
from invest_research.domain.errors import ErrorCode, is_retryable
from invest_research.domain.models import FinancialAnalysisPack, ReportDraft
from invest_research.infrastructure.flow_wiring import (
    _is_structured_output_unsupported,
    _structured_output_stage,
)

# 测试全部使用占位密钥（非真实 key）；构建真实 LLM 的路径不在此触发。
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
        temperature=0.2,
        timeout=60.0,
        enable_thinking=None,
    )


def _fake(config: LLMConfig, role: LLMRole) -> FakeLLM:
    return FakeLLM(config=config, role=role)


# ---------------------------------------------------------------------------
# 1~3. DeepSeek 三角色 Task 不配置原生 output_pydantic
# ---------------------------------------------------------------------------


def test_deepseek_research_task_has_no_native_output_pydantic() -> None:
    """DeepSeek Research Task 不配置原生 output_pydantic（原行为已安全）。"""
    config = _config("deepseek")
    task = build_research_task(config, fake=_fake(config, LLMRole.RESEARCH))
    assert task.output_pydantic is None
    assert task.output_json is None


def test_deepseek_analysis_task_has_no_native_output_pydantic() -> None:
    """DeepSeek Analysis Task 不配置原生 output_pydantic（P06-11B）。"""
    config = _config("deepseek")
    task = build_analysis_task(config, fake=_fake(config, LLMRole.ANALYSIS))
    assert task.output_pydantic is None
    assert task.output_json is None
    assert "最终答案只能是一个 JSON object" in task.description


def test_deepseek_writer_task_has_no_native_output_pydantic() -> None:
    """DeepSeek Writer Task 不配置原生 output_pydantic（P06-11B）。"""
    config = _config("deepseek")
    task = build_writer_task(config, fake=_fake(config, LLMRole.WRITER))
    assert task.output_pydantic is None
    assert task.output_json is None
    assert "最终答案只能是一个 JSON object" in task.description


# ---------------------------------------------------------------------------
# 4. Qwen 保留原生行为 + generic 回退 + vendor 决策
# ---------------------------------------------------------------------------


def test_qwen_analysis_task_keeps_native_output_pydantic() -> None:
    """Qwen Analysis Task 继续绑定 output_pydantic=FinancialAnalysisPack。"""
    config = _config("qwen")
    task = build_analysis_task(config, fake=_fake(config, LLMRole.ANALYSIS))
    assert task.output_pydantic is FinancialAnalysisPack
    assert "最终答案只能是一个 JSON object" not in task.description


def test_qwen_writer_task_keeps_native_output_pydantic() -> None:
    """Qwen Writer Task 继续绑定 output_pydantic=ReportDraft。"""
    config = _config("qwen")
    task = build_writer_task(config, fake=_fake(config, LLMRole.WRITER))
    assert task.output_pydantic is ReportDraft
    assert "最终答案只能是一个 JSON object" not in task.description


def test_generic_falls_back_to_local_json_validation() -> None:
    """generic 默认采用 JSON 文本 + 本地校验。"""
    config = _config("generic")
    assert structured_output_mode(config) == StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION
    task = build_analysis_task(config, fake=_fake(config, LLMRole.ANALYSIS))
    assert task.output_pydantic is None
    assert task.output_json is None


def test_structured_output_mode_uses_vendor_not_base_url() -> None:
    """能力决策只依赖 LLM_VENDOR，不根据 base_url 猜测。"""
    qwen = _config("qwen")
    deepseek = _config("deepseek")
    qwen2 = qwen.model_copy(update={"base_url": "https://deepseek.example.com/v1"})
    deepseek2 = deepseek.model_copy(update={"base_url": "https://dashscope.aliyuncs.com/v1"})
    assert structured_output_mode(qwen2) == StructuredOutputMode.NATIVE_PYDANTIC
    assert structured_output_mode(deepseek2) == StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION


# ---------------------------------------------------------------------------
# 5. DeepSeek 合法 JSON 文本 → 本地解析为对应 Pack
# ---------------------------------------------------------------------------

_LEGAL_ANALYSIS_JSON = (
    '{"version": "analysis_pack_v2", "period_end": "2025-06-30", '
    '"completeness": "partial", "limitations": ["缺少部分数据"]}'
)
_LEGAL_WRITER_JSON = (
    '{"version": "report_draft_v1", "title": "测试报告", '
    '"markdown": "# 测试报告\\n内容" }'
)


def test_deepseek_json_text_parses_to_analysis_pack_locally() -> None:
    """DeepSeek 返回合法 JSON 文本 → 本地解析为 FinancialAnalysisPack。"""
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(_LEGAL_ANALYSIS_JSON, FinancialAnalysisPack, stage="analysis")
    assert errors == []
    assert isinstance(pack, FinancialAnalysisPack)
    assert pack.period_end == date(2025, 6, 30)


def test_deepseek_json_text_parses_to_report_draft_locally() -> None:
    """DeepSeek 返回合法 JSON 文本 → 本地解析为 ReportDraft。"""
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(_LEGAL_WRITER_JSON, ReportDraft, stage="writer")
    assert errors == []
    assert isinstance(pack, ReportDraft)
    assert pack.title == "测试报告"


def test_deepseek_json_text_roundtrip_via_task_output_path() -> None:
    """FakeLLM 无 response_model 时返回 JSON 文本，PackBoundary 可解析。"""
    config = _config("deepseek")
    fake = FakeLLM(config=config, role=LLMRole.WRITER, responses=[_LEGAL_WRITER_JSON])
    raw = fake.call(messages="请生成报告")
    assert isinstance(raw, str)
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(raw, ReportDraft, stage="writer")
    assert errors == []
    assert isinstance(pack, ReportDraft)
    assert pack.markdown.startswith("# 测试报告")


# ---------------------------------------------------------------------------
# 6. 缺少必填字段仍然失败
# ---------------------------------------------------------------------------


def test_missing_version_fails_analysis_pack() -> None:
    """缺少 version 的 JSON 不能解析为 FinancialAnalysisPack。"""
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(
        '{"period_end": "2025-06-30"}', FinancialAnalysisPack, stage="analysis"
    )
    assert pack is None
    assert any(e.error_code == "MISSING_FIELD" for e in errors)


def test_missing_markdown_fails_report_draft() -> None:
    """缺少 markdown 的 JSON 不能解析为 ReportDraft。"""
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(
        '{"version": "report_draft_v1", "title": "x"}', ReportDraft, stage="writer"
    )
    assert pack is None
    assert any(e.error_code == "MISSING_FIELD" for e in errors)


# ---------------------------------------------------------------------------
# 7~8. 工具参数不能当作最终 Pack
# ---------------------------------------------------------------------------


def test_tool_params_not_parsed_as_analysis_pack() -> None:
    """FinancialFactQuery 工具参数不能当作 FinancialAnalysisPack。"""
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(
        '{"concept": "revenue", "period_end": "2025-09-27"}',
        FinancialAnalysisPack,
        stage="analysis",
    )
    assert pack is None
    assert errors


def test_artifact_reader_params_not_parsed_as_report_draft() -> None:
    """ArtifactReader 参数不能当作 ReportDraft。"""
    boundary = PackBoundary(max_repairs=0)
    pack, errors = boundary.parse(
        '{"artifact_key": "research_pack"}', ReportDraft, stage="writer"
    )
    assert pack is None
    assert errors


# ---------------------------------------------------------------------------
# 9. response_format 400 → STRUCTURED_OUTPUT_UNSUPPORTED
# ---------------------------------------------------------------------------


def test_response_format_rejected_classified_as_structured_output_unsupported() -> None:
    """模拟 DeepSeek 实测错误 → 分类为 STRUCTURED_OUTPUT_UNSUPPORTED。"""
    exc = ValueError("HTTP 400: This response_format type is unavailable now")
    info = classify_failure(exc, stage="04_analysis")
    assert info.error_code == ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED.value
    assert info.failure_stage == "04_analysis"
    assert not is_retryable(info.error_code)


def test_structured_output_unsupported_not_misclassified() -> None:
    """不得归为 INTERNAL_BUG / NETWORK_TRANSIENT / ITERATION_LIMIT。"""
    exc = ValueError(
        "OpenAIError: This response_format type is unavailable now (HTTP 400) "
        "while calling openai beta.chat.completions.parse"
    )
    info = classify_failure(exc, stage="05_writer")
    assert info.error_code == ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED.value
    assert info.error_code not in {
        ErrorCode.INTERNAL_BUG.value,
        ErrorCode.NETWORK_TRANSIENT.value,
        ErrorCode.ITERATION_LIMIT.value,
    }


def test_response_format_priority_over_iteration_exhaustion() -> None:
    """同一次执行同时出现迭代耗尽与 response_format 400 → 保留 response_format 根因。"""
    exc = RuntimeError(
        "Maximum iterations reached. force_final_answer. "
        "BadRequestError: HTTP 400 This response_format type is unavailable now"
    )
    info = classify_failure(exc, stage="04_analysis")
    assert info.error_code == ErrorCode.STRUCTURED_OUTPUT_UNSUPPORTED.value


def test_flow_wiring_helpers_detect_structured_output_unsupported() -> None:
    """flow_wiring 辅助函数正确识别结构化输出拒绝。"""
    assert _is_structured_output_unsupported(
        "badrequesterror: http 400: this response_format type is unavailable now"
    )
    assert _is_structured_output_unsupported("json_schema response format not supported")
    assert not _is_structured_output_unsupported("connection reset by peer")

    class _FakeExc(Exception):
        title = "FinancialAnalysisPack"

    assert _structured_output_stage(_FakeExc()) == "04_analysis"
    assert _structured_output_stage(ValueError("x")) == "05_writer"


# ---------------------------------------------------------------------------
# 10. 修复不会触发 beta.chat.completions.parse
# ---------------------------------------------------------------------------


def test_deepseek_tasks_never_bind_output_pydantic_or_output_json() -> None:
    """DeepSeek 三个 Task 均不绑定 output_pydantic/output_json，避免 Converter 触发 beta。"""
    config = _config("deepseek")
    tasks = [
        build_research_task(config, fake=_fake(config, LLMRole.RESEARCH)),
        build_analysis_task(config, fake=_fake(config, LLMRole.ANALYSIS)),
        build_writer_task(config, fake=_fake(config, LLMRole.WRITER)),
    ]
    for task in tasks:
        assert task.output_pydantic is None
        assert task.output_json is None


def test_deepseek_crew_assembly_uses_no_native_pydantic() -> None:
    """通过 build_research_crew 构造的 DeepSeek Crew 各 Task 均无原生 Pydantic。"""
    from invest_research.agents.crew_factory import build_research_crew

    config = _config("deepseek")
    fakes = {
        "research": _fake(config, LLMRole.RESEARCH),
        "analysis": _fake(config, LLMRole.ANALYSIS),
        "writer": _fake(config, LLMRole.WRITER),
    }
    crew = build_research_crew(config, fakes)
    for task in crew.tasks:
        assert task.output_pydantic is None
        assert task.output_json is None


# ---------------------------------------------------------------------------
# 11. API Key 不出现在 repr/异常/错误消息
# ---------------------------------------------------------------------------


def test_api_key_not_exposed_in_task_repr_or_exceptions() -> None:
    """构建 DeepSeek Task 的 repr/异常/错误消息不含 API Key。"""
    config = _config("deepseek")
    task = build_analysis_task(config, fake=_fake(config, LLMRole.ANALYSIS))
    repr_text = repr(task)
    assert _TEST_API_KEY not in repr_text
    assert "sk-" not in repr_text

    exc = ValueError(f"HTTP 400 (api_key={_TEST_API_KEY}): response_format unavailable")
    info = classify_failure(exc, stage="04_analysis")
    assert _TEST_API_KEY not in info.error_message
    assert "sk-" not in info.error_message


# ---------------------------------------------------------------------------
# 12. Qwen/DeepSeek 三角色模型配置仍分别生效
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", ["qwen", "deepseek", "generic"])
def test_three_role_model_config_applies_per_vendor(vendor: str) -> None:
    """Qwen/DeepSeek 的 research/analysis/writer 三角色模型配置分别生效。"""
    config = _config(vendor)
    assert config.model_for(LLMRole.RESEARCH) == f"{vendor}-research"
    assert config.model_for(LLMRole.ANALYSIS) == f"{vendor}-analysis"
    assert config.model_for(LLMRole.WRITER) == f"{vendor}-writer"
    fake = _fake(config, LLMRole.ANALYSIS)
    assert fake.model_name == f"{vendor}-analysis"
    assert _TEST_API_KEY not in repr(fake)


@pytest.mark.parametrize("vendor", ["qwen", "deepseek", "generic"])
def test_structured_output_mode_mapping(vendor: str) -> None:
    """供应商能力映射：qwen→原生，deepseek/generic→本地校验。"""
    config = _config(vendor)
    expected = (
        StructuredOutputMode.NATIVE_PYDANTIC
        if vendor == "qwen"
        else StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION
    )
    assert structured_output_mode(config) == expected
