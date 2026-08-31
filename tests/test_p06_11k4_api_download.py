"""P06-11K-4 测试 15：API 下载脱敏诊断包 — 路径穿越防护与明确 404。

验收点（任务文档第九节测试 15）：
- 非 UUID job_id / ``..`` / 绝对路径 / 反斜杠路径均拒绝（FastAPI uuid.UUID 参数
  天然对非 UUID 返回 422；``..`` 等注入无法到达 UUID 解析，因此被挡在路由层）；
- 诊断包不存在（合法 UUID 但无 manifest.json）→ 返回 404 明确提示，不 500；
- 已存在的诊断包 → 返回 application/gzip 的 tar.gz，内含 5 个固定文件名。
"""

from __future__ import annotations

import io
import json
import tarfile
import uuid

import pytest
from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.diagnostics.persistence import BUNDLE_FILE_NAMES
from invest_research.infrastructure.diagnostics_bundle_store import DiagnosticsBundleStore
from invest_research.settings import Settings


class _FakeDiagnosticsStore:
    """极简 fake：只实现 read_bundle（manifest 缺失返回 None）。"""

    def __init__(self) -> None:
        self.bundles: dict[str, dict[str, bytes]] = {}

    def read_bundle(self, job_id: str | uuid.UUID) -> dict[str, bytes] | None:
        return self.bundles.get(str(job_id))


def _test_settings() -> Settings:
    """返回不依赖开发机 ``.env`` 或 CI secrets 的最小测试配置。"""
    return Settings(
        _env_file=None,
        llm_api_key="test-only-key",
        sec_user_agent_contact="tests@example.com",
        flow_mode="fake",
    )


@pytest.fixture()
def client(tmp_path) -> TestClient:
    real_store = DiagnosticsBundleStore(tmp_path)
    app = create_app(
        settings=_test_settings(),
        diagnostics_bundle_store=real_store,
    )
    return TestClient(app)


def _write_bundle(store_path, job_id: uuid.UUID) -> None:
    store = DiagnosticsBundleStore(store_path)
    manifest = {
        "job_id": str(job_id),
        "capture_mode": "failure_payload",
        "event_count": 0,
        "original_bytes": 0,
        "stored_bytes": 0,
        "truncated_count": 0,
        "redaction_count": 0,
        "dropped_events": 0,
        "over_budget_events": 0,
        "trace_id": None,
        "failure_stage": "05_writer",
        "error_code": "REPORT_INVALID",
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-01-08T00:00:00+00:00",
    }
    files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        "execution_timeline.jsonl": b'{"sequence":1}\n',
        "stage_payloads.jsonl": b"",
        "validation_errors.json": b'{"errors":[]}',
        "failure.json": b'{"finalized":"failure"}',
    }
    # 复用 store.write 原子写语义构造完整包
    from invest_research.application.diagnostics.persistence import DiagnosticBundleFiles

    bundle = DiagnosticBundleFiles(
        manifest=files["manifest.json"],
        execution_timeline=files["execution_timeline.jsonl"],
        stage_payloads=files["stage_payloads.jsonl"],
        validation_errors=files["validation_errors.json"],
        failure=files["failure.json"],
        manifest_model=__import__(
            "invest_research.application.diagnostics.persistence", fromlist=["DiagnosticManifest"]
        ).DiagnosticManifest.model_validate(manifest),
    )
    store.write(job_id, bundle)


# ---------------------------------------------------------------------------
# 测试 15a：诊断包不存在 → 404 明确提示（不 500）
# ---------------------------------------------------------------------------


def test_download_missing_bundle_returns_404_not_500(client: TestClient) -> None:
    job_id = uuid.uuid4()
    resp = client.get(f"/v1/research-jobs/{job_id}/diagnostics")
    assert resp.status_code == 404
    body = resp.json()
    assert "不存在" in body["detail"]
    assert "诊断" in body["detail"]


# ---------------------------------------------------------------------------
# 测试 15b：非 UUID job_id → 422（路由层 UUID 校验，防注入）
# ---------------------------------------------------------------------------


def test_download_non_uuid_job_rejected(client: TestClient) -> None:
    # 纯非 UUID（32 位 hex 之外）在路由层被 uuid.UUID 校验拒绝 → 422。
    for bad in ("not-a-uuid", "0000", "abc"):
        resp = client.get(f"/v1/research-jobs/{bad}/diagnostics")
        assert resp.status_code == 422, f"job_id={bad!r} 应被拒绝"


# ---------------------------------------------------------------------------
# 测试 15c：路径穿越形态（../、绝对路径）即使拼进 URL 也无法到达 manifest
# ---------------------------------------------------------------------------

# 说明：``..`` / 绝对路径 / 反斜杠会被 httpx/Starlette 的 URL 规范化与路由匹配
# 消化——``/v1/research-jobs/../etc/passwd/diagnostics`` 被规范化为
# ``/v1/etc/passwd/diagnostics``（不存在路由 → 404），因此已存在的诊断包
# 永远读不出来。断言重点是：绝不返回 200 且绝不读出 bundle 内容。
_TRAVERSAL_INPUTS = (
    "../etc/passwd",
    "..",
    "/absolute/path",
    "..\\..\\secret",
)


def test_download_path_traversal_never_reaches_bundle(client: TestClient) -> None:
    # 已存在一个合法诊断包；../ 无法绕过 UUID 校验去读它。
    job_id = uuid.uuid4()
    _write_bundle(client.app.state.diagnostics_bundle_store._artifact_root, job_id)

    bad_candidates = [
        f"{job_id}/../",
        f"../{job_id}",
        f"/{job_id}",
    ] + [f"{job_id}/{t}" for t in _TRAVERSAL_INPUTS]
    for bad in bad_candidates:
        resp = client.get(f"/v1/research-jobs/{bad}/diagnostics")
        assert resp.status_code in (400, 404, 422), f"path={bad!r} 应返回 4xx"
        # 绝不 200：已存在的诊断包必须读不出来（防路径穿越核心语义）。
        assert resp.status_code != 200
        assert resp.content != b""


# ---------------------------------------------------------------------------
# 测试 15d：存在的诊断包 → 200 tar.gz 且内含固定 5 文件
# ---------------------------------------------------------------------------


def test_download_existing_bundle_returns_targz(client: TestClient) -> None:
    job_id = uuid.uuid4()
    _write_bundle(client.app.state.diagnostics_bundle_store._artifact_root, job_id)
    resp = client.get(f"/v1/research-jobs/{job_id}/diagnostics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gzip")
    assert f"diagnostics-{job_id}.tar.gz" in resp.headers["content-disposition"]

    tar = tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz")
    names = sorted(tar.getnames())
    assert names == sorted(f"diagnostics/{name}" for name in BUNDLE_FILE_NAMES)
    tar.close()


# ---------------------------------------------------------------------------
# 测试 15e：注入 fake store 时（manifest 缺失）同样 404 不 500
# ---------------------------------------------------------------------------


def test_fake_store_missing_bundle_404() -> None:
    app = create_app(
        settings=_test_settings(),
        diagnostics_bundle_store=_FakeDiagnosticsStore(),
    )
    with TestClient(app) as c:
        resp = c.get(f"/v1/research-jobs/{uuid.uuid4()}/diagnostics")
        assert resp.status_code == 404
        assert "不存在" in resp.json()["detail"]
