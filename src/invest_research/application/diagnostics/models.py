"""P06-11K：诊断事件模型（失败任务 Payload 级诊断包）。

事件模型的字段与任务文档“三、事件模型”保持一致：每条诊断事件至少包含
sequence/timestamp/job_id/trace_id/span_id/stage/component/event_type/
direction/payload_kind/content_type/original_size/stored_size/truncated/
redacted_fields/sha256/payload/error_code/validation_errors。

安全边界：
- ``payload`` 只允许保存**脱敏后**的内容；调用方必须先经过
  ``diagnostics.redaction`` 处理，禁止把原始业务输入直接塞入事件。
- ``reasoning_content`` / Chain-of-Thought 永远不允许进入事件（由脱敏器丢弃）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DiagnosticCaptureMode",
    "DiagnosticEventType",
    "DiagnosticDirection",
    "DiagnosticEvent",
    "ValidationErrorEntry",
    "DiagnosticCapturePolicy",
    "now_utc",
    "calculate_sha256",
    "measure_bytes",
]


class DiagnosticCaptureMode(str, Enum):
    """诊断捕获模式（对应 DIAGNOSTIC_CAPTURE_MODE 环境变量）。"""

    OFF = "off"
    METADATA = "metadata"
    FAILURE_PAYLOAD = "failure_payload"
    ALL_PAYLOAD = "all_payload"


class DiagnosticEventType(str, Enum):
    """诊断事件类型。"""

    METADATA = "metadata"
    PAYLOAD = "payload"
    VALIDATION_ERROR = "validation_error"
    EXCEPTION = "exception"
    FINALIZE_SUCCESS = "finalize_success"
    FINALIZE_FAILURE = "finalize_failure"


class DiagnosticDirection(str, Enum):
    """数据方向（input/output）。"""

    INPUT = "input"
    OUTPUT = "output"


def now_utc() -> datetime:
    """返回当前 UTC 时间（ISO 带时区）。"""
    return datetime.now(timezone.utc)


def calculate_sha256(data: Any) -> str:
    """返回 JSON 序列化后的稳定 SHA-256 摘要。

    序列化固定使用 ``sort_keys=True, ensure_ascii=False``，保证同一对象
    在不同进程中产生相同 hash（顺序稳定的诊断事件可复现）。
    """
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def measure_bytes(data: Any) -> int:
    """按 ``model_dump(mode=\"json\")`` JSON 序列化的 UTF-8 字节口径度量。

    与 ``BoundedDiagnosticBuffer`` 的容量判定使用同一序列化口径
    （不用 ``str(len(data))`` 等不稳定的近似度量）。
    """
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return len(raw.encode("utf-8"))


class ValidationErrorEntry(BaseModel):
    """Schema 校验错误条目（K-3 接线时由 Pydantic ValidationError 转换）。

    对应任务文档测试 8：Schema 错误包含 ``field`` / ``expected`` / ``actual``。
    K-1 只定义模型；K-3 负责把 Pydantic 错误转换为本模型。
    """

    model_config = ConfigDict(frozen=True)

    field: str = Field(min_length=1)
    expected: str | None = None
    actual: str | None = None
    # Pydantic 的错误类型（如 missing/string_type 等），无则 None
    error_type: str | None = None
    # 错误消息（已脱敏；禁止包含任何敏感字段值）
    message: str | None = None


class DiagnosticCapturePolicy(BaseModel):
    """诊断捕获容量策略（K-1 由 Settings 构建，运行期不可变）。

    字段直接对应 5 个诊断配置项：
    - ``capture_mode``：off / metadata / failure_payload / all_payload；
    - ``max_event_bytes``：单条事件序列化字节上限；
    - ``max_bundle_bytes``：整个 Bundle 总字节上限；
    - ``max_events``：Job-local Ring Buffer 最大事件数；
    - ``retention_days``：诊断包保留天数（K-2 生命周期清理使用）。
    """

    model_config = ConfigDict(frozen=True)

    capture_mode: DiagnosticCaptureMode = DiagnosticCaptureMode.OFF
    max_event_bytes: int = 65_536
    max_bundle_bytes: int = 4 * 1024 * 1024
    max_events: int = 200
    retention_days: int = 7

    @property
    def is_enabled(self) -> bool:
        """是否产生任何诊断事件（off 为完全关闭）。"""
        return self.capture_mode != DiagnosticCaptureMode.OFF

    @property
    def keeps_payload(self) -> bool:
        """当前模式是否保存 Payload 内容（metadata 模式不保存）。"""
        return self.capture_mode in (
            DiagnosticCaptureMode.FAILURE_PAYLOAD,
            DiagnosticCaptureMode.ALL_PAYLOAD,
        )


class DiagnosticEvent(BaseModel):
    """单条诊断事件。

    - ``sequence``：Job 内稳定递增的事件序号（第一个记录为 1）；
    - ``payload``：**只允许脱敏后**的内容。metadata / off 模式下为 ``None``；
    - ``redacted_fields``：被脱敏器命中的字段路径（如 ``headers.authorization``）；
    - ``truncated``：单事件超过 ``max_event_bytes`` 时，Payload 被截断为 True；
    - ``validation_errors``：Schema 校验错误列表（仅 validation_error 事件非空）。
    """

    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    timestamp: datetime
    job_id: str
    trace_id: str | None = None
    span_id: str | None = None
    stage: str | None = None
    component: str | None = None
    event_type: DiagnosticEventType
    direction: DiagnosticDirection | None = None
    payload_kind: str | None = None
    content_type: str | None = None
    original_size: int = 0
    stored_size: int = 0
    truncated: bool = False
    redacted_fields: list[str] = Field(default_factory=list)
    dropped_fields: list[str] = Field(default_factory=list)
    sha256: str | None = None
    payload: Any = None
    error_code: str | None = None
    validation_errors: list[ValidationErrorEntry] = Field(default_factory=list)

    @classmethod
    def metadata_only(
        cls,
        *,
        sequence: int,
        job_id: str,
        data: Any,
        original_size: int,
        content_type: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        stage: str | None = None,
        component: str | None = None,
        payload_kind: str | None = None,
        direction: DiagnosticDirection | None = None,
    ) -> "DiagnosticEvent":
        """metadata 模式事件：只保存长度/类型/hash/状态，不保存 Payload 内容。

        ``payload`` 固定为 ``None``（禁止把业务输入写入 metadata 事件）。
        """
        return cls(
            sequence=sequence,
            timestamp=now_utc(),
            job_id=job_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            component=component,
            event_type=DiagnosticEventType.METADATA,
            direction=direction,
            payload_kind=payload_kind,
            content_type=content_type,
            original_size=original_size,
            stored_size=0,
            truncated=False,
            sha256=calculate_sha256(data),
        )
