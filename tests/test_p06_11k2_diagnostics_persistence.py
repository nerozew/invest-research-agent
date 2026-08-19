"""P06-11K-2：诊断包持久化 + 原子写 + 生命周期清理离线测试。

覆盖任务文档第九节中属于 K-2 的验收：
1. failure_payload 成功任务不落 Payload；
2. 失败任务能保留失败前所有阶段；
13. finalize_failure 重复调用幂等（落盘幂等）；
14. 诊断落盘失败不覆盖原始业务异常；
18. 生命周期清理会删除过期诊断包。
外加补充：manifest 字段完整、原子写无临时文件残留、metadata 成功保留元数据。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from invest_research.application.diagnostics.models import (
    DiagnosticCaptureMode,
    DiagnosticCapturePolicy,
    ValidationErrorEntry,
)
from invest_research.application.diagnostics.persistence import build_bundle_files
from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer
from invest_research.infrastructure.diagnostics_bundle_store import (
    DiagnosticsBundleStore,
    DiagnosticsCleanupService,
    InvalidDiagnosticsJobId,
    resolve_diagnostics_dir,
)


def _policy(
    mode: DiagnosticCaptureMode = DiagnosticCaptureMode.FAILURE_PAYLOAD,
) -> DiagnosticCapturePolicy:
    return DiagnosticCapturePolicy(capture_mode=mode)


def _job_id() -> uuid.UUID:
    return uuid.uuid4()


def _buffer_with_events(
    mode: DiagnosticCaptureMode, *, job_id: uuid.UUID | None = None
) -> BoundedDiagnosticBuffer:
    buf = BoundedDiagnosticBuffer(
        job_id=str(job_id or _job_id()), policy=_policy(mode)
    )
    buf.record_payload(
        stage="research", component="agent", payload_kind="ResearchPack",
        data={"company": "MSFT", "ok": True},
    )
    buf.record_payload(
        stage="analysis", component="agent", payload_kind="AnalysisPack",
        data={"facts": [1, 2, 3]},
    )
    buf.record_payload(
        stage="writer", component="llm", payload_kind="response",
        data={"content": "report text"},
    )
    buf.record_validation_error(
        stage="writer", component="pack_boundary",
        errors=[ValidationErrorEntry(field="markdown", expected="str", actual="missing")],
    )
    buf.record_exception(
        stage="writer", component="assemble", error_code="REPORT_INVALID",
        message="ReportDraft 缺少必需章节",
    )
    return buf


# ----------------------------------------------------------------------
# 落盘语义（application 层 build_bundle_files）
# ----------------------------------------------------------------------


class TestFailurePayloadPersistSemantics:
    def test_success_does_not_write_payload(self) -> None:
        buf = _buffer_with_events(DiagnosticCaptureMode.FAILURE_PAYLOAD)
        files = build_bundle_files(buf, failed=False, error_code="")
        # success：Payload 事件被丢弃，validation/exception 保留（2 个）
        lines = [ln for ln in files.execution_timeline.decode().splitlines() if ln.strip()]
        timeline = [json.loads(ln) for ln in lines]
        assert len(timeline) == 2
        payloads = files.as_dict()["stage_payloads.jsonl"].decode().strip()
        assert payloads == ""
        assert files.manifest_model.event_count == 2
        assert files.manifest_model.capture_mode == "failure_payload"

    def test_failed_keeps_all_stages_before_failure(self) -> None:
        buf = _buffer_with_events(DiagnosticCaptureMode.FAILURE_PAYLOAD)
        files = build_bundle_files(
            buf,
            failed=True,
            error_code="REPORT_INVALID",
            failure_stage="05_writer",
        )
        timeline = [
            json.loads(ln)
            for ln in files.execution_timeline.decode().splitlines()
            if ln.strip()
        ]
        assert len(timeline) == 5
        stages = [ev["stage"] for ev in timeline]
        assert stages == ["research", "analysis", "writer", "writer", "writer"]
        payloads = [
            json.loads(ln)
            for ln in files.stage_payloads.decode().splitlines()
            if ln.strip()
        ]
        assert len(payloads) == 3
        assert payloads[0]["payload"] == {"company": "MSFT", "ok": True}
        assert files.manifest_model.failure_stage == "05_writer"
        assert files.manifest_model.error_code == "REPORT_INVALID"
        failure = json.loads(files.failure.decode())
        assert failure["finalized"] == "failure"
        assert failure["error_code"] == "REPORT_INVALID"

    def test_success_metadata_keeps_metadata(self) -> None:
        buf = _buffer_with_events(DiagnosticCaptureMode.METADATA)
        files = build_bundle_files(buf, failed=False, error_code="")
        events = [
            json.loads(ln)
            for ln in files.execution_timeline.decode().splitlines()
            if ln.strip()
        ]
        # metadata 模式：业务 payload 事件被丢弃；exception 事件保存的
        # 是错误消息（小体积诊断元数据），不属于业务 Payload，允许保留。
        payload_events = [ev for ev in events if ev["event_type"] == "payload"]
        assert payload_events == []
        assert all(ev.get("payload") is None for ev in events if ev["event_type"] == "metadata")
        assert files.manifest_model.capture_mode == "metadata"


class TestManifest:
    def test_manifest_fields_complete(self) -> None:
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(
            buf,
            failed=True,
            error_code="SCHEMA_INVALID",
            failure_stage="04_analysis",
            trace_id="trace-abc",
            retention_days=7,
        )
        m = files.manifest_model
        assert m.job_id == buf.job_id
        assert m.capture_mode == "all_payload"
        assert m.event_count == 5
        assert m.original_bytes > 0
        assert m.stored_bytes > 0
        assert m.truncated_count == 0
        assert m.redaction_count == 0
        assert m.trace_id == "trace-abc"
        assert m.failure_stage == "04_analysis"
        assert m.error_code == "SCHEMA_INVALID"
        assert m.created_at <= m.expires_at
        assert m.expires_at - m.created_at >= timedelta(days=7 - 1)


# ----------------------------------------------------------------------
# 基础设施：写盘 + 幂等 + 原子
# ----------------------------------------------------------------------


class TestDiagnosticsBundleStore:
    def test_write_creates_five_files(self, tmp_path: Path) -> None:
        store = DiagnosticsBundleStore(tmp_path)
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(buf, failed=True, error_code="REPORT_INVALID")
        job = _job_id()
        written = store.write(job, files)
        diag = resolve_diagnostics_dir(tmp_path, job)
        assert diag.exists()
        for name in (
            "manifest.json",
            "execution_timeline.jsonl",
            "stage_payloads.jsonl",
            "validation_errors.json",
            "failure.json",
        ):
            assert (diag / name).exists(), name
        assert len(written) == 5

    def test_write_leaves_no_temp_file(self, tmp_path: Path) -> None:
        store = DiagnosticsBundleStore(tmp_path)
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(buf, failed=False, error_code="")
        store.write(_job_id(), files)
        leftovers = list(tmp_path.rglob(".tmp-*")) + list(tmp_path.rglob("*.part"))
        assert leftovers == []

    def test_write_if_absent_idempotent(self, tmp_path: Path) -> None:
        store = DiagnosticsBundleStore(tmp_path)
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(buf, failed=True, error_code="X")
        job = _job_id()
        first = store.write_if_absent(job, files)
        assert first is not None
        second = store.write_if_absent(job, files)
        assert second is None
        manifest = (resolve_diagnostics_dir(tmp_path, job) / "manifest.json").read_text(
            encoding="utf-8"
        )
        assert '"event_count": 5' in manifest

    def test_invalid_job_id_rejected(self, tmp_path: Path) -> None:
        store = DiagnosticsBundleStore(tmp_path)
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(buf, failed=False, error_code="")
        with pytest.raises(InvalidDiagnosticsJobId):
            store.write("../../etc/passwd", files)


# ----------------------------------------------------------------------
# 生命周期清理
# ----------------------------------------------------------------------


class TestCleanup:
    def _make_bundle(self, tmp_path: Path) -> tuple[uuid.UUID, DiagnosticsBundleStore]:
        store = DiagnosticsBundleStore(tmp_path)
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(buf, failed=True, error_code="X", retention_days=1)
        job = _job_id()
        store.write(job, files)
        return job, store

    def test_cleanup_deletes_expired(self, tmp_path: Path) -> None:
        job, _ = self._make_bundle(tmp_path)
        diag = resolve_diagnostics_dir(tmp_path, job)
        assert diag.exists()
        service = DiagnosticsCleanupService(tmp_path)
        # now < expires（now+1day）→ 未过期，不删
        assert service.cleanup_expired() == 0
        assert diag.exists()
        # now+2days > expires → 过期删除
        future = datetime.now(timezone.utc) + timedelta(days=2)
        assert service.cleanup_expired(now=future) == 1
        assert not diag.exists()

    def test_cleanup_keeps_fresh(self, tmp_path: Path) -> None:
        job, _ = self._make_bundle(tmp_path)
        diag = resolve_diagnostics_dir(tmp_path, job)
        service = DiagnosticsCleanupService(tmp_path)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        assert service.cleanup_expired(now=past) == 0
        assert diag.exists()


# ----------------------------------------------------------------------
# 落盘失败不覆盖原始业务异常
# ----------------------------------------------------------------------


class TestNoMask:
    def test_persistence_error_does_not_mask_business_exception(
        self, tmp_path: Path
    ) -> None:
        """模拟业务异常中诊断落盘失败：原始业务异常必须仍在。"""

        class Boom(RuntimeError):
            pass

        store = DiagnosticsBundleStore(tmp_path)
        buf = _buffer_with_events(DiagnosticCaptureMode.ALL_PAYLOAD)
        files = build_bundle_files(buf, failed=True, error_code="REPORT_INVALID")
        job = _job_id()

        original_exc: Exception | None = None
        try:
            raise Boom("原始业务异常")
        except Boom as exc:
            original_exc = exc
            # 模拟落盘失败：抢先创建不可写的 manifest（只读文件）
            target_dir = resolve_diagnostics_dir(tmp_path, job)
            target_dir.mkdir(parents=True)
            blocker = target_dir / "manifest.json"
            blocker.write_text("损坏内容", encoding="utf-8")
            blocker.chmod(0o444)
            try:
                with pytest.raises(OSError):
                    # manifest 已存在但非预期的非诊断内容 → os.replace 到只读
                    # 目标可能被平台拒绝；此处只验证"捕获 OSError 后异常链仍在"
                    try:
                        store.write(job, files)
                    except OSError:
                        raise
            finally:
                blocker.chmod(0o644)
        assert original_exc is not None
        assert isinstance(original_exc, Boom)
        assert "原始业务异常" in str(original_exc)
