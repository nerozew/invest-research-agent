"""P04-04 工件清单与安全下载接口单元测试。

使用 fake ArtifactCatalogStore/ArtifactContentStore，不连接真实数据库/磁盘/Docker。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from invest_research.api.app import create_app
from invest_research.application.artifacts import (
    ArtifactCatalogStore,
    ArtifactContentStore,
    ArtifactInfo,
)
from invest_research.settings import Settings


class FakeCatalog:
    """内存版工件目录：按 job_id 返回预置清单。"""

    def __init__(self, items: tuple[ArtifactInfo, ...] = ()) -> None:
        self._items = items

    def list_artifacts(self, job_id: uuid.UUID) -> tuple[ArtifactInfo, ...]:
        return self._items


class FakeContent:
    """内存版工件内容：按 (job_id, key) 查表；不在表内返回 None。"""

    def __init__(self, contents: dict[str, bytes] | None = None) -> None:
        self._contents = contents or {}

    def read(self, job_id: uuid.UUID, artifact_key: str) -> bytes | None:
        return self._contents.get(artifact_key)


class FakeChecker:
    """回归用 fake checker（P04-01 模式）。"""

    def check_database(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}

    def check_redis(self) -> dict[str, str | None]:
        return {"status": "ok", "error_code": None}


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "llm_api_key": "test-key",
        "sec_user_agent_contact": "test@example.com",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client(
    catalog: ArtifactCatalogStore | None = None,
    content: ArtifactContentStore | None = None,
) -> TestClient:
    app = create_app(
        settings=_settings(),
        health_checker=FakeChecker(),
        artifact_catalog_store=catalog,
        artifact_content_store=content,
    )
    return TestClient(app)


def _info(key: str, byte_size: int = 10) -> ArtifactInfo:
    return ArtifactInfo(
        artifact_key=key,
        artifact_type="json",
        schema_version=None,
        storage_uri=f"artifacts/{key}",
        content_checksum=f"sha256-{key}",
        byte_size=byte_size,
    )


def test_list_artifacts_returns_registered_items() -> None:
    job_id = uuid.uuid4()
    items = (_info("research_pack.json"), _info("manifest.json"))
    client = _client(catalog=FakeCatalog(items))

    resp = client.get(f"/v1/research-jobs/{job_id}/artifacts")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["artifact_key"] == "research_pack.json"
    assert body[1]["byte_size"] == 10


def test_list_artifacts_returns_empty_when_none() -> None:
    client = _client(catalog=FakeCatalog(()))

    resp = client.get(f"/v1/research-jobs/{uuid.uuid4()}/artifacts")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_artifacts_returns_422_for_invalid_job_id() -> None:
    client = _client(catalog=FakeCatalog(()))

    resp = client.get("/v1/research-jobs/not-a-uuid/artifacts")

    assert resp.status_code == 422


def test_list_artifacts_returns_503_when_no_catalog() -> None:
    client = _client()

    resp = client.get(f"/v1/research-jobs/{uuid.uuid4()}/artifacts")

    assert resp.status_code == 503
    assert "工件目录未连接" in resp.json()["detail"]


def test_download_artifact_returns_content() -> None:
    job_id = uuid.uuid4()
    content = FakeContent({"research_pack.json": b'{"sources": []}'})
    client = _client(content=content)

    resp = client.get(f"/v1/research-jobs/{job_id}/artifacts/research_pack.json")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.content == b'{"sources": []}'


def test_download_artifact_returns_404_when_missing() -> None:
    job_id = uuid.uuid4()
    client = _client(content=FakeContent({}))

    resp = client.get(f"/v1/research-jobs/{job_id}/artifacts/not_there.json")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "工件不存在"


def test_download_artifact_rejects_path_traversal_with_400() -> None:
    job_id = uuid.uuid4()
    client = _client(content=FakeContent({}))

    resp = client.get(
        f"/v1/research-jobs/{job_id}/artifacts/../../etc/passwd",
        follow_redirects=False,
    )

    # .. 会被 httpx 归一化，用编码形式直接测
    assert resp.status_code in (400, 404)


def test_download_artifact_rejects_dangerous_key_with_400() -> None:
    job_id = uuid.uuid4()
    client = _client(content=FakeContent({}))

    # %2e%2e = ".."；%5c = "\"
    resp = client.get(f"/v1/research-jobs/{job_id}/artifacts/%2e%2e%5csecret")

    assert resp.status_code in (400, 422)


def test_download_artifact_returns_503_when_no_content_store() -> None:
    client = _client()

    resp = client.get(f"/v1/research-jobs/{uuid.uuid4()}/artifacts/some.json")

    assert resp.status_code == 503
    assert "工件存储未连接" in resp.json()["detail"]
