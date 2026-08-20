"""P06-11K-4 测试 16：前端失败任务诊断入口（fake HTTP API 测试）。

验收点（任务文档第九节测试 16）：
- 失败任务能看到诊断入口：错误阶段（failure_stage）、稳定错误码（error_code）、
  最近 10 条执行事件（只含展示白名单字段）、「下载脱敏诊断包」按钮语义
  （前端只调 FastAPI，不在 session state 存密钥）。
- 数据契约抽取为纯函数 ``build_diagnostics_event_rows``：
  - 只取最近 10 条；
  - 绝不暴露 payload 正文/密钥/内部路径（行只有时间/阶段/类型/摘要类型/状态）。
- ``ResearchApiClient.download_diagnostics_bundle`` 用 httpx.MockTransport
  验证：请求发往 ``/v1/research-jobs/{id}/diagnostics``、字节原样返回、
  服务端 404 时抛 ``ApiNotFoundError``。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from invest_research.frontend.client import ResearchApiClient
from invest_research.frontend.errors import ApiClientError, ApiNotFoundError
from invest_research.frontend.render import build_diagnostics_event_rows

# ---------------------------------------------------------------------------
# 最近 10 条事件表格行（只含白名单字段）
# ---------------------------------------------------------------------------


def test_build_rows_keeps_last_10() -> None:
    events = [
        {"sequence": i, "stage": "02_research", "event_type": "payload",
         "payload_kind": "tool_response", "timestamp": f"2026-01-01T00:{i:02d}:00"}
        for i in range(1, 15)
    ]
    rows = build_diagnostics_event_rows(events)
    assert len(rows) == 10
    # 保留最后 10 条（sequence 5..14）
    assert rows[0]["时间"] == "2026-01-01T00:05:00"
    assert rows[-1]["时间"] == "2026-01-01T00:14:00"


def test_build_rows_empty_and_none() -> None:
    assert build_diagnostics_event_rows(None) == []
    assert build_diagnostics_event_rows([]) == []


def test_build_rows_only_whitelist_columns() -> None:
    events = [
        {
            "timestamp": "2026-01-01T00:00:00",
            "stage": "05_writer",
            "event_type": "exception",
            "payload_kind": "writer_response_empty",
            "status": "failure",
            "error_code": "REPORT_INVALID",
            # 纵深防御：即使事件意外携带 payload / reasoning，也不进入展示行。
            "payload": "secret-content",
            "reasoning_content": "secret-cot",
            "headers": {"authorization": "Bearer sk-xyz"},
        }
    ]
    rows = build_diagnostics_event_rows(events)
    assert len(rows) == 1
    row = rows[0]
    assert row["阶段"] == "05_writer"
    assert row["类型"] == "exception"
    assert row["摘要类型"] == "writer_response_empty"
    assert row["状态/错误码"] == "REPORT_INVALID"
    # 白名单之外字段绝不进入行（payload/密钥/内部路径）。
    assert "secret" not in str(row).lower()


# ---------------------------------------------------------------------------
# ResearchApiClient.download_diagnostics_bundle（fake HTTP）
# ---------------------------------------------------------------------------


def test_download_diagnostics_bundle_hits_endpoint_and_returns_bytes() -> None:
    job_id = str(uuid.uuid4())
    archive = b"\x1f\x8b" + b"fake-tar-gz"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/research-jobs/{job_id}/diagnostics"
        return httpx.Response(200, content=archive, headers={"content-type": "application/gzip"})

    client = ResearchApiClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    result = client.download_diagnostics_bundle(job_id)
    assert result == archive


def test_download_diagnostics_bundle_missing_raises_not_found() -> None:
    job_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": "该任务不存在脱敏诊断包（可能未启用诊断捕获或任务未收口）"},
        )

    client = ResearchApiClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ApiNotFoundError):
        client.download_diagnostics_bundle(job_id)


def test_download_diagnostics_bundle_server_error_raises() -> None:
    job_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = ResearchApiClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ApiClientError):
        client.download_diagnostics_bundle(job_id)
