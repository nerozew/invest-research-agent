"""P06-11K：失败任务诊断子系统。

K-1 范围：配置（Settings）、事件模型（models）、递归脱敏（redaction）、
Job-local 有界缓冲（sink）。
K-2 范围：诊断包持久化（persistence）——manifest + 5 文件内容与落盘语义。
"""

from invest_research.application.diagnostics.models import (
    DiagnosticCaptureMode,
    DiagnosticCapturePolicy,
    DiagnosticDirection,
    DiagnosticEvent,
    DiagnosticEventType,
    ValidationErrorEntry,
    calculate_sha256,
    measure_bytes,
    now_utc,
)
from invest_research.application.diagnostics.persistence import (
    DiagnosticBundleFiles,
    DiagnosticManifest,
    build_bundle_files,
)
from invest_research.application.diagnostics.redaction import (
    FORBIDDEN_KEYS,
    MASK,
    SENSITIVE_KEYS,
    RecursiveRedactor,
    RedactionContext,
    RedactionResult,
    redact_payload,
    scrub_url_query_secrets,
)
from invest_research.application.diagnostics.sink import (
    BoundedDiagnosticBuffer,
    BufferStats,
    DiagnosticCaptureSink,
)

__all__ = [
    "DiagnosticCaptureMode",
    "DiagnosticCapturePolicy",
    "DiagnosticDirection",
    "DiagnosticEvent",
    "DiagnosticEventType",
    "ValidationErrorEntry",
    "calculate_sha256",
    "measure_bytes",
    "now_utc",
    "FORBIDDEN_KEYS",
    "MASK",
    "SENSITIVE_KEYS",
    "RedactionContext",
    "RedactionResult",
    "RecursiveRedactor",
    "redact_payload",
    "scrub_url_query_secrets",
    "BoundedDiagnosticBuffer",
    "BufferStats",
    "DiagnosticCaptureSink",
    "DiagnosticBundleFiles",
    "DiagnosticManifest",
    "build_bundle_files",
]
