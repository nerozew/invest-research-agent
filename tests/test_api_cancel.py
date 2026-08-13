"""P04-08：取消任务 API 端点测试。

- 注入 fake CancelStatusWriter（pending/running 可取消）到 create_app；
- 验证 DELETE /v1/research-jobs/{job_id}：
  - pending/running → 200 + 已取消；
  - 终态（writer 双 False）→ 200 + 幂等（already_cancelled=True）；
  - 未注入取消存储 → 503。
使用 TestClient 全程 fake，不连接真实数据库/Redis。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.cancellation import CancelStatusWriter
from invest_research.settings import Settings


def _test_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="sk-test-placeholder",
        sec_user_agent_contact="test@example.com",
    )


class FakeCancelWriter:
    """fake 取消端口：可配置 pending/running 是否可被取消。"""

    def __init__(self, *, pending_cancellable: bool, running_cancellable: bool) -> None:
        self._pending = pending_cancellable
        self._running = running_cancellable

    def cancel_from_pending(self, job_id: uuid.UUID) -> bool:
        return self._pending

    def cancel_from_running(self, job_id: uuid.UUID) -> bool:
        return self._running


def _make_client(writer: CancelStatusWriter | None) -> TestClient:
    app = create_app(settings=_test_settings(), cancel_status_writer=writer)
    return TestClient(app)


def test_cancel_pending_returns_200() -> None:
    writer = FakeCancelWriter(pending_cancellable=True, running_cancellable=False)
    client = _make_client(writer)
    job_id = str(uuid.uuid4())

    resp = client.delete(f"/v1/research-jobs/{job_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["did_cancel"] is True


def test_cancel_running_returns_200() -> None:
    writer = FakeCancelWriter(pending_cancellable=False, running_cancellable=True)
    client = _make_client(writer)
    job_id = str(uuid.uuid4())

    resp = client.delete(f"/v1/research-jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["did_cancel"] is True


def test_cancel_terminal_is_idempotent_noop() -> None:
    """终态任务：取消返回 200 + already_cancelled=True（幂等）。"""
    writer = FakeCancelWriter(pending_cancellable=False, running_cancellable=False)
    client = _make_client(writer)
    job_id = str(uuid.uuid4())

    resp = client.delete(f"/v1/research-jobs/{job_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["did_cancel"] is False
    assert body["already_cancelled"] is True


def test_cancel_without_store_returns_503() -> None:
    """未注入取消存储：返回 503。"""
    client = _make_client(None)
    job_id = str(uuid.uuid4())

    resp = client.delete(f"/v1/research-jobs/{job_id}")

    assert resp.status_code == 503


def test_cancel_invalid_job_id_returns_422() -> None:
    """非法 job_id：返回 422。"""
    writer = FakeCancelWriter(pending_cancellable=True, running_cancellable=False)
    client = _make_client(writer)

    resp = client.delete("/v1/research-jobs/not-a-uuid")

    assert resp.status_code == 422
