"""P06-11K-1：诊断配置/事件模型/递归脱敏/Job-local 有界缓冲离线测试。

覆盖任务文档第九节中属于 K-1 的验收：
3. API Key/Authorization/Cookie 被脱敏；
4. 嵌套字典和列表递归脱敏；
5. reasoning_content 永不保存；
6. 工具参数脱敏后可查看；
9. 单事件大小限制；
10. Bundle 总大小限制；
11. Ring Buffer 最大事件限制；
12. 两个并发 Job 不串数据；
13. finalize_failure 重复调用幂等。
外加补充：off/metadata 模式行为、配置 fail-fast。
"""

from __future__ import annotations

import json
import threading
import uuid

import pytest
from pydantic import SecretStr, ValidationError

from invest_research.application.diagnostics.models import (
    DiagnosticCaptureMode,
    DiagnosticCapturePolicy,
    DiagnosticEventType,
    ValidationErrorEntry,
)
from invest_research.application.diagnostics.redaction import (
    RedactionContext,
    redact_payload,
)
from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer
from invest_research.settings import Settings


def _policy(
    mode: DiagnosticCaptureMode = DiagnosticCaptureMode.FAILURE_PAYLOAD,
    *,
    max_event_bytes: int = 65_536,
    max_bundle_bytes: int = 4 * 1024 * 1024,
    max_events: int = 200,
) -> DiagnosticCapturePolicy:
    return DiagnosticCapturePolicy(
        capture_mode=mode,
        max_event_bytes=max_event_bytes,
        max_bundle_bytes=max_bundle_bytes,
        max_events=max_events,
    )


def _job_id() -> str:
    return str(uuid.uuid4())


# ----------------------------------------------------------------------
# 配置（Settings）
# ----------------------------------------------------------------------


class TestSettingsDiagnostics:
    def test_default_capture_mode_off(self) -> None:
        settings = Settings(
            llm_api_key=SecretStr("placeholder-key"),
            sec_user_agent_contact="placeholder@example.com",
        )
        assert settings.diagnostic_capture_mode == "off"
        assert settings.diagnostic_max_event_bytes == 65_536
        assert settings.diagnostic_max_bundle_bytes == 4 * 1024 * 1024
        assert settings.diagnostic_max_events == 200
        assert settings.diagnostic_retention_days == 7

    def test_production_all_payload_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="all_payload"):
            Settings(
                environment="production",
                llm_api_key=SecretStr("real-key-not-placeholder"),
                sec_user_agent_contact="real@example.com",
                diagnostic_capture_mode="all_payload",
            )

    def test_event_bytes_greater_than_bundle_rejected(self) -> None:
        with pytest.raises(ValueError, match="不能大于"):
            Settings(
                llm_api_key=SecretStr("placeholder-key"),
                sec_user_agent_contact="placeholder@example.com",
                diagnostic_max_event_bytes=100,
                diagnostic_max_bundle_bytes=50,
            )

    def test_invalid_capture_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(  # type: ignore[call-arg]
                llm_api_key=SecretStr("placeholder-key"),
                sec_user_agent_contact="placeholder@example.com",
                diagnostic_capture_mode="invalid_mode",
            )


# ----------------------------------------------------------------------
# 递归脱敏
# ----------------------------------------------------------------------


class TestRecursiveRedaction:
    def test_api_key_authorization_cookie_redacted(self) -> None:
        payload = {
            "headers": {
                "authorization": "Bearer abc.def.ghi",
                "cookie": "session=abc123",
            },
            "body": {
                "api_key": "sk-abcdefghijklmnop",
                "password": "hunter2",
                "token": "tok_1234567890",
            },
        }
        result = redact_payload(payload)
        assert "abc.def.ghi" not in json.dumps(result.data, ensure_ascii=False)
        assert "abc123" not in json.dumps(result.data, ensure_ascii=False)
        assert "sk-abcdefghijklmnop" not in json.dumps(result.data, ensure_ascii=False)
        assert "hunter2" not in json.dumps(result.data, ensure_ascii=False)
        assert result.data["headers"]["authorization"] == "***"
        assert result.data["headers"]["cookie"] == "***"
        assert result.data["body"]["api_key"] == "***"
        assert any(f.endswith("authorization") for f in result.redacted_fields)
        assert any(f.endswith("cookie") for f in result.redacted_fields)
        assert any(f.endswith("api_key") for f in result.redacted_fields)

    def test_nested_dict_list_recursive_redaction(self) -> None:
        payload = {
            "items": [
                {"name": "a", "api_key": "sk-aaaabbbb"},
                {"name": "b", "nested": {"secret": "s3cr3t", "keep": 1}},
            ],
            "plain": "hello",
        }
        result = redact_payload(payload)
        assert result.data["items"][0]["api_key"] == "***"
        assert result.data["items"][1]["nested"]["secret"] == "***"
        assert result.data["items"][1]["nested"]["keep"] == 1
        assert result.data["plain"] == "hello"

    def test_reasoning_content_never_saved(self) -> None:
        payload = {
            "content": "final answer",
            "reasoning_content": "internal chain of thought",
            "thinking": "CoT text",
            "chain_of_thought": {"step1": "..."},
        }
        result = redact_payload(payload)
        assert "reasoning_content" not in result.data
        assert "thinking" not in result.data
        assert "chain_of_thought" not in result.data
        assert result.data["content"] == "final answer"
        assert len(result.dropped_fields) == 3

    def test_known_secrets_value_redacted(self) -> None:
        secret = "sk-super-secret-value"
        result = redact_payload(
            {"text": f"key is {secret}"},
            context=RedactionContext(known_secrets=frozenset({secret})),
        )
        assert secret not in json.dumps(result.data, ensure_ascii=False)

    def test_tool_arguments_redacted_then_readable(self) -> None:
        result = redact_payload(
            {
                "tool": "calculate_metric",
                "arguments": {
                    "metric": "gross_margin",
                    "company_id": "cik0000789019",
                    "api_key": "sk-tool-secret",
                },
            }
        )
        data = result.data
        assert data["tool"] == "calculate_metric"
        assert data["arguments"]["metric"] == "gross_margin"
        assert data["arguments"]["company_id"] == "cik0000789019"
        assert data["arguments"]["api_key"] == "***"
        assert "sk-tool-secret" not in json.dumps(data, ensure_ascii=False)

    def test_url_query_secret_removed(self) -> None:
        result = redact_payload(
            {"url": "https://host/path?a=1&api_key=sk-abcdefgh&b=2&token=x"}
        )
        url = result.data["url"]
        assert "api_key=sk-abcdefgh" not in url
        assert "token=x" not in url
        assert "a=1" in url
        assert "b=2" in url
        assert "sk-abcdefgh" not in url


# ----------------------------------------------------------------------
# BoundedDiagnosticBuffer（CaptureSink）
# ----------------------------------------------------------------------


class TestCaptureMode:
    def test_off_mode_noop(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.OFF)
        )
        assert (
            buf.record_payload(
                stage="research", component="agent", payload_kind="pack", data={"a": 1}
            )
            is None
        )
        assert buf.finalize_success() is None
        assert buf.finalize_failure(error_code="X") is None
        assert buf.events() == []

    def test_metadata_mode_no_payload(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.METADATA)
        )
        event = buf.record_payload(
            stage="research",
            component="agent",
            payload_kind="research_pack",
            data={"a": 1, "b": "text"},
        )
        assert event is not None
        assert event.event_type == DiagnosticEventType.METADATA
        assert event.payload is None
        assert event.original_size > 0
        assert event.sha256 is not None

    def test_failure_payload_keeps_payload_in_memory(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(),
            policy=_policy(DiagnosticCaptureMode.FAILURE_PAYLOAD),
        )
        event = buf.record_payload(
            stage="research",
            component="agent",
            payload_kind="research_pack",
            data={"company": "MSFT"},
        )
        assert event is not None
        assert event.payload == {"company": "MSFT"}
        assert event.stored_size > 0


class TestCapacity:
    def test_single_event_size_limit(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(),
            policy=_policy(
                DiagnosticCaptureMode.ALL_PAYLOAD,
                max_event_bytes=1_024,
            ),
        )
        event = buf.record_payload(
            stage="writer",
            component="llm",
            payload_kind="response",
            data={"content": "x" * 10_000},
        )
        assert event is not None
        assert event.truncated is True
        assert event.stored_size <= 1_024
        assert len(event.payload) < 10_000

    def test_bundle_total_size_limit(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(),
            policy=_policy(
                DiagnosticCaptureMode.ALL_PAYLOAD,
                max_bundle_bytes=3_000,
            ),
        )
        # 单个事件（含头部字段）约 2000 字节：预算 3000 保证首个被接受，
        # 两个事件累计 ~4000 > 3000 时第二个被拒（避免预算与单事件大小贴边）。
        first = buf.record_payload(
            stage="research", component="agent", payload_kind="pack",
            data={"content": "y" * 1_500},
        )
        assert first is not None
        second = buf.record_payload(
            stage="analysis", component="agent", payload_kind="pack",
            data={"content": "y" * 1_500},
        )
        assert second is None
        assert buf.stats.over_budget_events == 1
        assert buf.stats.event_count == 1

    def test_ring_buffer_max_events(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(),
            policy=_policy(
                DiagnosticCaptureMode.ALL_PAYLOAD,
                max_events=3,
            ),
        )
        for i in range(6):
            buf.record_payload(
                stage="research", component="agent", payload_kind="pack",
                data={"i": i},
            )
        events = buf.events()
        assert len(events) == 3
        assert [e.payload["i"] for e in events] == [3, 4, 5]
        assert buf.stats.dropped_events == 3


class TestConcurrency:
    def test_two_concurrent_jobs_isolated(self) -> None:
        job_a = _job_id()
        job_b = _job_id()
        buf_a = BoundedDiagnosticBuffer(
            job_id=job_a, policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        buf_b = BoundedDiagnosticBuffer(
            job_id=job_b, policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        errors: list[Exception] = []
        barrier = threading.Barrier(3)

        def worker(buf: BoundedDiagnosticBuffer, data: int) -> None:
            try:
                barrier.wait()
                for i in range(50):
                    buf.record_payload(
                        stage="research",
                        component="agent",
                        payload_kind="pack",
                        data={"job": buf.job_id, "i": i, "v": data},
                    )
            except Exception as exc:  # noqa: BLE001 - 测试收集
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=(buf_a, 1))
        t2 = threading.Thread(target=worker, args=(buf_b, 2))
        t1.start()
        t2.start()
        barrier.wait()
        t1.join()
        t2.join()

        assert not errors
        events_a = buf_a.events()
        events_b = buf_b.events()
        assert len(events_a) == 50 and len(events_b) == 50
        for ev in events_a:
            assert ev.job_id == job_a
            assert ev.payload["v"] == 1
        for ev in events_b:
            assert ev.job_id == job_b
            assert ev.payload["v"] == 2


class TestFinalize:
    def test_finalize_failure_idempotent(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        first = buf.finalize_failure(error_code="SCHEMA_INVALID", failure_stage="writer")
        second = buf.finalize_failure(error_code="SCHEMA_INVALID", failure_stage="writer")
        assert first is not None
        assert second is None
        assert buf.is_finalized is True
        assert buf.events()[-1].event_type == DiagnosticEventType.FINALIZE_FAILURE
        assert buf.events()[-1].error_code == "SCHEMA_INVALID"
        assert buf.events()[-1].stage == "writer"

    def test_finalized_buffer_rejects_new_events(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(),
            policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD),
        )
        buf.finalize_success()
        assert (
            buf.record_payload(
                stage="research", component="agent", payload_kind="pack",
                data={"x": 1},
            )
            is None
        )


class TestDepthDefense:
    def test_reasoning_content_never_enters_buffer(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        event = buf.record_payload(
            stage="writer",
            component="llm",
            payload_kind="response",
            data={"content": "ok", "reasoning_content": "CoT"},
        )
        assert event is None
        assert buf.stats.policy_violations == 1
        assert buf.events() == []


class TestValidationErrorEvent:
    def test_record_validation_error_with_field_expected_actual(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        event = buf.record_validation_error(
            stage="writer",
            component="pack_boundary",
            errors=[
                ValidationErrorEntry(
                    field="markdown", expected="str", actual="missing"
                )
            ],
            payload_kind="ReportDraft",
        )
        assert event is not None
        assert event.event_type == DiagnosticEventType.VALIDATION_ERROR
        assert event.validation_errors[0].field == "markdown"
        assert event.validation_errors[0].expected == "str"
        assert event.validation_errors[0].actual == "missing"


class TestEventStableOrder:
    def test_sequence_stable_increasing(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        for i in range(5):
            buf.record_payload(
                stage="stage", component="comp", payload_kind="pack",
                data={"i": i},
            )
        sequences = [e.sequence for e in buf.events()]
        assert sequences == [1, 2, 3, 4, 5]

    def test_trace_span_id_recorded(self) -> None:
        buf = BoundedDiagnosticBuffer(
            job_id=_job_id(), policy=_policy(DiagnosticCaptureMode.ALL_PAYLOAD)
        )
        event = buf.record_payload(
            stage="research",
            component="agent",
            payload_kind="pack",
            data={"a": 1},
            trace_id="trace-abc",
            span_id="span-def",
        )
        assert event is not None
        assert event.trace_id == "trace-abc"
        assert event.span_id == "span-def"
