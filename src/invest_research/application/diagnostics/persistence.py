"""P06-11K-2：诊断包持久化（应用层纯逻辑）。

职责：
- ``DiagnosticManifest``：manifest.json 的确定性内容（含 capture_mode/计数/
  统计/trace_id/failure_stage/error_code/created_at/expires_at）；
- ``DiagnosticBundleFiles``：5 个文件的字节内容 + 过期时间；
- ``build_bundle_files(...)``：按**落盘语义**过滤 Buffer 事件并构建 5 文件内容。

落盘语义（任务文档“六、失败时持久化”）：
- 任务成功 + capture_mode=failure_payload：**丢弃 Payload 事件**（只保留
  metadata/validation_error/exception/finalize），manifest 计数按保留事件计；
- 任务成功 + metadata：只保留元数据事件（payload 事件被丢弃）；
- 任务成功 + all_payload：全量保留；
- 任务失败（任何模式）：全量保留（失败前的所有诊断事件落盘）；
- failure.json 在失败路径记录稳定错误信息，成功路径写 {"finalized":"success"}。

原子写与目录解析不在本层（本层零文件系统依赖），由 infrastructure
``DiagnosticsBundleStore`` 负责。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from invest_research.application.diagnostics.models import (
    DiagnosticCaptureMode,
    DiagnosticEvent,
    DiagnosticEventType,
    now_utc,
)
from invest_research.application.diagnostics.sink import BoundedDiagnosticBuffer

__all__ = [
    "DiagnosticManifest",
    "DiagnosticBundleFiles",
    "build_bundle_files",
    "BUNDLE_FILE_NAMES",
]

BUNDLE_FILE_NAMES = (
    "manifest.json",
    "execution_timeline.jsonl",
    "stage_payloads.jsonl",
    "validation_errors.json",
    "failure.json",
)


class DiagnosticManifest(BaseModel):
    """诊断包 manifest.json 内容（K-2，字段与任务文档第六节一致）。"""

    model_config = ConfigDict(frozen=True)

    job_id: str
    capture_mode: str
    event_count: int = Field(ge=0)
    original_bytes: int = Field(ge=0)
    stored_bytes: int = Field(ge=0)
    truncated_count: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    dropped_events: int = Field(ge=0)
    over_budget_events: int = Field(ge=0)
    trace_id: str | None = None
    failure_stage: str | None = None
    error_code: str | None = None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class DiagnosticBundleFiles:
    """5 个文件的字节内容 + manifest + 过期时间。"""

    manifest: bytes
    execution_timeline: bytes
    stage_payloads: bytes
    validation_errors: bytes
    failure: bytes
    manifest_model: DiagnosticManifest

    def as_dict(self) -> dict[str, bytes]:
        return {
            "manifest.json": self.manifest,
            "execution_timeline.jsonl": self.execution_timeline,
            "stage_payloads.jsonl": self.stage_payloads,
            "validation_errors.json": self.validation_errors,
            "failure.json": self.failure,
        }


def _event_as_dict(event: DiagnosticEvent) -> dict[str, Any]:
    return event.model_dump(mode="json")


def _retain_event(
    event: DiagnosticEvent,
    *,
    failed: bool,
    capture_mode: DiagnosticCaptureMode,
) -> bool:
    """按落盘语义决定是否保留 Payload 事件。

    - failed：全量保留；
    - capture_mode=FAILURE_PAYLOAD：成功时丢弃 Payload 事件；
    - capture_mode=METADATA：成功时丢弃 Payload 事件（只保留元数据/校验/异常）；
    - capture_mode=ALL_PAYLOAD：全量保留。
    metadata 事件（event_type=METADATA）始终保留（即使失败也丢不了）。
    """
    if failed or capture_mode == DiagnosticCaptureMode.ALL_PAYLOAD:
        return True
    if event.event_type == DiagnosticEventType.PAYLOAD:
        return False
    return True


def build_bundle_files(
    buffer: BoundedDiagnosticBuffer,
    *,
    failed: bool,
    error_code: str,
    failure_stage: str | None = None,
    trace_id: str | None = None,
    retention_days: int = 7,
) -> DiagnosticBundleFiles:
    """按落盘语义过滤事件并构建 5 文件字节内容（应用层纯逻辑）。

    ``error_code``/``failure_stage`` 只在失败路径写入 failure.json 与 manifest。
    """
    policy = buffer.policy
    mode = policy.capture_mode
    events = buffer.events()
    retained = [
        ev for ev in events if _retain_event(ev, failed=failed, capture_mode=mode)
    ]
    stats = buffer.stats

    created_at = now_utc()
    expires_at = created_at + timedelta(days=max(1, retention_days))

    # manifest 计数按保留事件统计；字节数从保留事件求和（截断后真实值）。
    original_bytes = sum(ev.original_size for ev in retained)
    stored_bytes = sum(ev.stored_size for ev in retained)
    truncated = sum(1 for ev in retained if ev.truncated)
    redacted = sum(len(ev.redacted_fields) for ev in retained)

    manifest = DiagnosticManifest(
        job_id=buffer.job_id,
        capture_mode=mode.value,
        event_count=len(retained),
        original_bytes=original_bytes,
        stored_bytes=stored_bytes,
        truncated_count=truncated,
        redaction_count=redacted,
        dropped_events=stats.dropped_events,
        over_budget_events=stats.over_budget_events,
        trace_id=trace_id,
        failure_stage=failure_stage if failed else None,
        error_code=error_code if failed else None,
        created_at=created_at,
        expires_at=expires_at,
    )

    timeline_lines = [json.dumps(_event_as_dict(ev), ensure_ascii=False) for ev in retained]
    payload_lines = [
        json.dumps(_event_as_dict(ev), ensure_ascii=False)
        for ev in retained
        if ev.event_type == DiagnosticEventType.PAYLOAD
    ]

    validation_entries: list[dict[str, Any]] = []
    for ev in retained:
        for err in ev.validation_errors:
            validation_entries.append(
                {
                    "sequence": ev.sequence,
                    "stage": ev.stage,
                    "component": ev.component,
                    "field": err.field,
                    "expected": err.expected,
                    "actual": err.actual,
                    "error_type": err.error_type,
                    "message": err.message,
                }
            )

    if failed:
        failure_doc: dict[str, Any] = {
            "finalized": "failure",
            "error_code": error_code,
            "failure_stage": failure_stage,
            "trace_id": trace_id,
            "created_at": created_at.isoformat(),
            "message": "诊断快照（仅记录稳定错误信息；失败详情见各事件/异常事件）",
        }
    else:
        failure_doc = {"finalized": "success"}

    # 统一 UTF-8 编码（含 ensure_ascii=False 的 JSON 序列化）
    return DiagnosticBundleFiles(
        manifest=json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False).encode(
            "utf-8"
        ),
        execution_timeline=("\n".join(timeline_lines) + ("\n" if timeline_lines else "")).encode(
            "utf-8"
        ),
        stage_payloads=(
            "\n".join(payload_lines) + ("\n" if payload_lines else "")
        ).encode("utf-8"),
        validation_errors=json.dumps(
            {"errors": validation_entries}, ensure_ascii=False
        ).encode("utf-8"),
        failure=json.dumps(failure_doc, ensure_ascii=False).encode("utf-8"),
        manifest_model=manifest,
    )
