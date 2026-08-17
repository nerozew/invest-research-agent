"""P06-09 离线契约测试：结构化边界解析 + 错误分类 + 上游真实交接。

覆盖项（对齐任务要求 8 条）：
- 空 facts（FinancialAnalysisPack.facts=[] 合法）；
- 非法 JSON（无法解析 → PackParseError/SCHEMA_INVALID）；
- 字段缺失（缺必需字段 → PackParseError/SCHEMA_INVALID，不静默）；
- 多余字段（Pydantic 默认拒绝未知字段 → 明确失败，不吞错）；
- 代码围栏（```json ... ``` 文本可提取 JSON）；
- 上游真实 Pack 交接（crew_factory._task_output_loader 读到真实 pack 内容）；
- 错误分类（schema/超时/网络/5xx/429/认证/内部错误映射稳定 error_code）；
- 失败消息脱敏（密钥/token/路径被隐藏）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from invest_research.agents.crew_factory import _task_output_loader
from invest_research.agents.pack_parsing import PackParseError, dump_task_output, parse_pack_output
from invest_research.application.failure_classifier import classify_failure, sanitize_message
from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import (
    CompanyIdentity,
    FinancialAnalysisPack,
    FinancialFact,
    ResearchPack,
    Source,
    SourceType,
)


class _DummyModel(ResearchPack):
    """测试用最小 ResearchPack（复用 domain 模型做契约校验）。"""


def _identity() -> CompanyIdentity:
    return CompanyIdentity(cik="0000789019", legal_name="Microsoft Corp")


def _source() -> Source:
    return Source(
        source_type=SourceType.SEC_FILING,
        canonical_url="https://www.sec.gov/Archives/edgar/data/10-K",
        title="10-K filed 2025-10-31",
        accessed_at=date(2025, 10, 31),
    )


def _research_dict() -> dict[str, object]:
    return {
        "version": "research_pack_v1",
        "company_identity": _identity().model_dump(mode="json"),
        "as_of_date": "2025-10-31",
        "sources": [_source().model_dump(mode="json")],
    }


# ---------------------------------------------------------------------------
# 1. 空 facts / 合法边界
# ---------------------------------------------------------------------------


def test_financial_analysis_pack_allows_empty_facts() -> None:
    """空 facts 是合法业务边界（无可用财务事实时如实为空），不崩溃。"""
    pack = FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2025, 10, 31),
        facts=[],
    )
    assert pack.facts == []


# ---------------------------------------------------------------------------
# 2. 非法 JSON
# ---------------------------------------------------------------------------


def test_parse_invalid_json_text_raises_schema_invalid() -> None:
    """非法 JSON 文本统一抛 PackParseError（error_code=SCHEMA_INVALID）。"""
    with pytest.raises(PackParseError) as excinfo:
        parse_pack_output("这不是 JSON {{{", FinancialAnalysisPack)
    assert excinfo.value.error_code == ErrorCode.SCHEMA_INVALID.value
    assert "无法解析 FinancialAnalysisPack 输出" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 3. 字段缺失
# ---------------------------------------------------------------------------


def test_parse_missing_required_field_raises_schema_invalid() -> None:
    """缺必需字段（如缺 version）→ 明确失败，不静默接受。"""
    bad = {"company_identity": _identity().model_dump(mode="json"), "as_of_date": "2025-10-31"}
    with pytest.raises(PackParseError) as excinfo:
        parse_pack_output(bad, ResearchPack)
    assert excinfo.value.error_code == ErrorCode.SCHEMA_INVALID.value


# ---------------------------------------------------------------------------
# 4. 多余字段
# ---------------------------------------------------------------------------


def test_parse_extra_field_rejected() -> None:
    """多余字段（Pydantic 默认 forbid）→ 明确失败，不吞错。"""
    bad = dict(_research_dict())
    bad["unknown_field"] = "surprise"  # type: ignore[assignment]
    with pytest.raises(PackParseError) as excinfo:
        parse_pack_output(bad, ResearchPack)
    assert excinfo.value.error_code == ErrorCode.SCHEMA_INVALID.value
    assert "未声明字段" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5. 代码围栏
# ---------------------------------------------------------------------------


def test_parse_code_fenced_json_text_extracts_object() -> None:
    """LLM 输出带 ```json 围栏/前后缀时能提取 JSON 对象。"""
    raw = (
        "```json\n"
        + '{"version": "research_pack_v1", "company_identity": '
        + '{ "cik": "0000789019", "legal_name": "Microsoft Corp" },'
        + '"as_of_date": "2025-10-31", '
        + '"sources": [{"source_type": "sec_filing", "canonical_url": '
        + '"https://www.sec.gov/Archives/edgar/data/10-K", "accessed_at": "2025-10-31"}] }\n'
        + "```"
    )
    pack = parse_pack_output(raw, ResearchPack)
    assert pack.version == "research_pack_v1"
    assert pack.company_identity.cik == "0000789019"


# ---------------------------------------------------------------------------
# 6. 上游真实 Pack 交接（ArtifactReader loader 读到真实内容）
# ---------------------------------------------------------------------------


def _stub_task(pack: object) -> SimpleNamespace:
    return SimpleNamespace(output=SimpleNamespace(pydantic=pack, raw=""))


def test_task_output_loader_reads_upstream_real_pack() -> None:
    """Writer 的 ArtifactReader loader 从上游 Task 读到真实 pack（非 readable 占位符）。"""
    research_task = _stub_task(_research_pack())
    analysis_task = _stub_task(_analysis_pack())
    loader = _task_output_loader(research_task, analysis_task)

    research_content = loader("research_pack")
    assert research_content is not None
    assert research_content["version"] == "research_pack_v1"
    assert research_content["company_identity"]["cik"] == "0000789019"
    assert "status" not in research_content  # 不是占位符 readable

    analysis_content = loader("analysis_pack")
    assert analysis_content is not None
    assert analysis_content["version"] == "analysis_pack_v1"
    assert analysis_content["facts"][1]["concept"] == "Revenue-value"


def _research_pack() -> ResearchPack:
    return ResearchPack(
        version="research_pack_v1",
        company_identity=_identity(),
        as_of_date=date(2025, 10, 31),
        sources=[_source()],
    )


def _analysis_pack() -> FinancialAnalysisPack:
    return FinancialAnalysisPack(
        version="analysis_pack_v1",
        period_end=date(2025, 10, 31),
        facts=[
            FinancialFact(
                company_id="c1",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue-open",
                value=Decimal("100"),
                unit="USD",
                period_start=date(2024, 11, 1),
                period_end=date(2025, 10, 31),
            ),
            FinancialFact(
                company_id="c1",
                source_id="s1",
                taxonomy="us-gaap",
                concept="Revenue-value",
                value=Decimal("200"),
                unit="USD",
                period_start=date(2024, 11, 1),
                period_end=date(2025, 10, 31),
            ),
        ],
    )


def test_dump_task_output_handles_raw_json_text() -> None:
    """Research 未绑 output_pydantic 时通过 raw JSON 文本兜底读取内容。"""
    raw = (
        '{"version": "research_pack_v1", "company_identity": '
        '{ "cik": "0000789019", "legal_name": "Microsoft Corp" },'
        '"as_of_date": "2025-10-31", "sources": [] }'
    )
    output = SimpleNamespace(pydantic=None, raw=raw, json_dict=None)
    assert dump_task_output(output) is not None
    assert dump_task_output(output)["version"] == "research_pack_v1"  # type: ignore[index]


def test_dump_task_output_none_for_bad_raw() -> None:
    """raw 不是合法 JSON dict 时不伪造内容（返回 None→Reader 报 not_found）。"""
    output = SimpleNamespace(pydantic=None, raw="not json", json_dict=None)
    assert dump_task_output(output) is None


# ---------------------------------------------------------------------------
# 7. 错误分类（稳定 error_code）
# ---------------------------------------------------------------------------


def test_classify_schema_invalid() -> None:
    exc = PackParseError("FinancialAnalysisPack", "非法输出")
    info = classify_failure(exc, stage="04_analysis")
    assert info.error_code == ErrorCode.SCHEMA_INVALID.value
    assert info.failure_stage == "04_analysis"


def test_classify_timeout() -> None:
    exc = TimeoutError("request timed out after 60s")
    info = classify_failure(exc, stage="02_research")
    assert info.error_code == ErrorCode.TIMEOUT.value
    assert info.failure_stage == "02_research"


def test_classify_connection_error() -> None:
    exc = ConnectionError("Connection refused")
    info = classify_failure(exc)
    assert info.error_code == ErrorCode.NETWORK_TRANSIENT.value


def test_classify_rate_limited() -> None:
    exc = RuntimeError("HTTP 429 Too Many Requests: rate limit exceeded")
    info = classify_failure(exc)
    assert info.error_code == ErrorCode.RATE_LIMITED.value


def test_classify_upstream_5xx() -> None:
    exc = RuntimeError("HTTP 503 Service Unavailable")
    info = classify_failure(exc)
    assert info.error_code == ErrorCode.UPSTREAM_5XX.value


def test_classify_auth_error() -> None:
    exc = RuntimeError("HTTP 401 Unauthorized")
    info = classify_failure(exc)
    assert info.error_code == ErrorCode.AUTH_ERROR.value


def test_classify_internal_bug_fallback() -> None:
    exc = ValueError("unexpected internal state")
    info = classify_failure(exc, stage="05_writer")
    # ValueError → INPUT_INVALID（分类器自带非失败分类）
    assert info.error_code == ErrorCode.INPUT_INVALID.value


def test_classify_unknown_runtime_error_falls_back_internal_bug() -> None:
    class _Weird(RuntimeError):
        pass

    exc = _Weird("something odd")
    info = classify_failure(exc)
    assert info.error_code == ErrorCode.INTERNAL_BUG.value


# ---------------------------------------------------------------------------
# 8. 消息脱敏
# ---------------------------------------------------------------------------


def test_sanitize_message_hides_secrets_and_paths() -> None:
    msg = (
        "failed to call https://api.example.com with api_key=sk-secret123 "
        "and token=abc, file at C:\\Users\\me\\secret\\key.pem"
    )
    cleaned = sanitize_message(msg)
    assert "sk-secret123" not in cleaned
    assert "key.pem" not in cleaned
    # 键名保留（便于定位错误源），但值被脱敏为 <redacted>
    assert "api_key=<redacted>" in cleaned
    assert "token=<redacted>" in cleaned


def test_sanitize_message_truncates_long_text() -> None:
    long_msg = "x" * 2000
    assert len(sanitize_message(long_msg)) <= 500
