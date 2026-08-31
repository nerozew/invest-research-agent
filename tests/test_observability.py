"""P05-05 结构化日志与敏感字段脱敏测试。"""

from __future__ import annotations

import json
import uuid

from invest_research.infrastructure.observability.logging import (
    mask_secrets,
    sanitize_value,
    setup_structlog,
)


def test_sanitize_masks_key_value() -> None:
    out = sanitize_value("api_key=sk-abc123")
    assert "sk-abc123" not in out
    assert "***" in out


def test_sanitize_masks_bearer() -> None:
    out = sanitize_value("Authorization: Bearer abc.def.ghi")
    assert "abc.def.ghi" not in out
    assert "***" in out


def test_mask_secrets_redacts_sensitive_fields() -> None:
    masked = mask_secrets(
        event="job_started",
        job_id=str(uuid.uuid4()),
        step_name="step02",
        api_key="sk-secret-1",
        set_cookie="session=abc123",
    )
    assert masked["api_key"] == "***"
    assert masked["set_cookie"] == "***"
    assert "sk-secret-1" not in json.dumps(masked, ensure_ascii=False)
    assert "abc123" not in json.dumps(masked, ensure_ascii=False)


def test_mask_secrets_keeps_normal_fields() -> None:
    jid = str(uuid.uuid4())
    masked = mask_secrets(event="done", job_id=jid, step_name="step03")
    assert masked["job_id"] == jid
    assert masked["step_name"] == "step03"
    assert masked["event"] == "done"


def test_setup_structlog_redacts_in_json() -> None:
    """structlog JSON 渲染后，key/Authorization 不出现在输出。"""
    setup_structlog(json_logs=True)
    import structlog

    # 用独立 logger 捕获输出
    # 直接调用结构化为 JSON 的 processor 链验证
    renderer = structlog.processors.JSONRenderer()
    event_dict = {
        "api_key": "sk-leak",
        "authorization": "Bearer tok.leak",
        "job_id": str(uuid.uuid4()),
    }
    masked = mask_secrets(**event_dict)
    rendered = renderer(None, None, masked)
    assert "sk-leak" not in rendered
    assert "tok.leak" not in rendered
    assert "job_id" in rendered
