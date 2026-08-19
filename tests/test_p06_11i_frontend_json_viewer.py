"""P06-11I-FRONTEND：前端工件清单 JSON 页内查看契约。

覆盖：
- ``is_viewable_json_artifact``：只有以 ``.json`` 结尾的 00~07 中间工件可页内查看；
  08_report.md / 09_report.pdf / st.txt 不进入 JSON 查看；
- ``decode_artifact_text``：UTF-8 解码 + 非法字节 errors="replace" 容错；
- ``load_viewable_json_artifacts``：只下载 .json 工件、页内展示原始 JSON、
  单个失败收集为错误不中断其他工件、非 JSON 一律跳过。
"""

from __future__ import annotations

from invest_research.frontend.errors import ApiClientError
from invest_research.frontend.models import ArtifactInfo
from invest_research.frontend.render import (
    decode_artifact_text,
    is_viewable_json_artifact,
    load_viewable_json_artifacts,
)


def _artifact(key: str, byte_size: int = 2) -> ArtifactInfo:
    return ArtifactInfo(
        artifact_key=key,
        artifact_type="pack",
        schema_version=None,
        storage_uri=f"artifacts/job/{key}",
        content_checksum="abc123",
        byte_size=byte_size,
    )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: dict[str, bytes] = {}
        self.errors: set[str] = set()

    def download_artifact(self, job_id: str, artifact_key: str) -> bytes:
        self.calls.append((job_id, artifact_key))
        if artifact_key in self.errors:
            raise ApiClientError(f"无法读取 {artifact_key}")
        return self.payloads.get(artifact_key, b"{}")


# ---------------------------------------------------------------------------
# 可查看 JSON 工件判定
# ---------------------------------------------------------------------------


def test_is_viewable_json_artifact_accepts_00_to_07_json() -> None:
    for key in (
        "00_request.json",
        "02_research_pack.json",
        "04_financial_analysis_pack.json",
        "05_report_draft.json",
        "06_quality_report.json",
        "07_manifest.json",
    ):
        assert is_viewable_json_artifact(key) is True


def test_is_viewable_json_artifact_rejects_final_reports_and_txt() -> None:
    for key in ("08_report.md", "09_report.pdf", "st.txt", "01_company_resolve.txt"):
        assert is_viewable_json_artifact(key) is False


def test_is_viewable_json_artifact_case_sensitive_suffix() -> None:
    assert is_viewable_json_artifact("07_manifest.JSON") is False


# ---------------------------------------------------------------------------
# 字节解码容错
# ---------------------------------------------------------------------------


def test_decode_artifact_text_utf8() -> None:
    payload = '{"x": "微软"}'
    assert decode_artifact_text(payload.encode("utf-8")) == payload


def test_decode_artifact_text_invalid_utf8_replace() -> None:
    text = decode_artifact_text(b'{"broken": "\xff\xfe"}')
    assert "\ufffd" in text
    assert "broken" in text


def test_decode_artifact_text_empty() -> None:
    assert decode_artifact_text(b"") == ""


# ---------------------------------------------------------------------------
# 加载可查看 JSON 工件
# ---------------------------------------------------------------------------


def test_load_viewable_json_artifacts_only_downloads_json() -> None:
    client = _FakeClient()
    client.payloads = {
        "02_research_pack.json": b'{"sources": ["sec"]}',
        "05_report_draft.json": '{"markdown": "# 报告"}'.encode("utf-8"),
    }
    artifacts = [
        _artifact("02_research_pack.json"),
        _artifact("05_report_draft.json"),
        _artifact("08_report.md", 5),
        _artifact("09_report.pdf", 5),
        _artifact("st.txt", 3),
    ]

    views, errors = load_viewable_json_artifacts(client, "job-1", artifacts)

    assert errors == []
    assert [v.artifact_key for v in views] == [
        "02_research_pack.json",
        "05_report_draft.json",
    ]
    assert views[0].byte_size == 2
    assert views[0].text == '{"sources": ["sec"]}'
    assert views[1].text == '{"markdown": "# 报告"}'
    assert ("job-1", "08_report.md") not in client.calls
    assert ("job-1", "09_report.pdf") not in client.calls
    assert ("job-1", "st.txt") not in client.calls


def test_load_viewable_json_artifacts_collects_failures_without_crash() -> None:
    client = _FakeClient()
    client.payloads = {"02_research_pack.json": b'{"ok": true}'}
    client.errors = {"07_manifest.json"}
    artifacts = [_artifact("02_research_pack.json"), _artifact("07_manifest.json")]

    views, errors = load_viewable_json_artifacts(client, "job-1", artifacts)

    assert [v.artifact_key for v in views] == ["02_research_pack.json"]
    assert len(errors) == 1
    assert errors[0][0] == "07_manifest.json"
    assert "无法读取" in errors[0][1]


def test_load_viewable_json_artifacts_empty_artifacts() -> None:
    views, errors = load_viewable_json_artifacts(_FakeClient(), "job-1", [])
    assert views == []
    assert errors == []
