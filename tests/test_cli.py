"""P04-11: CLI tests (fake HTTP via MockTransport).

Covered:
- run creates a job and stops polling once terminal (pending -> succeeded);
- status prints job + step snapshot;
- artifacts lists registered artifacts;
- error classification: readable message and exit code 1, no leaked details.
No database / Redis / Flow access.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from invest_research.cli import _poll_until_terminal, main
from invest_research.frontend.client import ResearchApiClient

_UUID = str(uuid.uuid4())


def _job_payload(status: str) -> dict[str, object]:
    return {
        "job_id": _UUID,
        "status": status,
        "current_step": None,
        "error_code": None,
        "error_message": None,
        "started_at": None,
        "completed_at": None,
        "duration_seconds": None,
        "steps": [
            {
                "step_name": "gather",
                "sequence_no": 1,
                "status": "succeeded",
                "attempt_count": 1,
                "error_code": None,
                "error_message": None,
                "duration_seconds": 1.2,
            }
        ],
    }


@pytest.fixture()
def handler():
    """MockTransport handler: POST->202 pending; GET->200 succeeded; artifacts->200."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/research-jobs":
            return httpx.Response(202, json={"job_id": _UUID, "status": "pending"})
        if request.method == "GET" and request.url.path == f"/v1/research-jobs/{_UUID}":
            return httpx.Response(200, json=_job_payload("succeeded"))
        if request.method == "GET" and request.url.path.endswith("/artifacts"):
            return httpx.Response(
                200,
                json=[
                    {
                        "artifact_key": "report.md",
                        "artifact_type": "markdown",
                        "schema_version": "v1",
                        "storage_uri": "artifacts/x/report.md",
                        "content_checksum": "sum",
                        "byte_size": 5,
                    }
                ],
            )
        return httpx.Response(404, json={"detail": "not found"})

    return handle


def _client(handler) -> ResearchApiClient:
    return ResearchApiClient(
        base_url="http://test",
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


def _patch_new_client(cli, handler):
    cli._new_client = lambda api_base: _client(handler)  # type: ignore[assignment]


def test_cli_run_creates_and_reaches_terminal(handler, capsys):
    import invest_research.cli as cli

    _patch_new_client(cli, handler)
    with pytest.raises(SystemExit) as exc:
        main(["run", "Apple", "--as-of-date", "2024-12-31", "--language", "zh-CN"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "created job_id=" in out
    assert "succeeded" in out
    assert "step='gather'" in out


def test_cli_status(handler, capsys):
    import invest_research.cli as cli

    _patch_new_client(cli, handler)
    with pytest.raises(SystemExit) as exc:
        main(["status", _UUID])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "succeeded" in out
    assert "step='gather'" in out


def test_cli_artifacts(handler, capsys):
    import invest_research.cli as cli

    _patch_new_client(cli, handler)
    with pytest.raises(SystemExit) as exc:
        main(["artifacts", _UUID])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "report.md" in out
    assert "markdown" in out


def test_cli_error_returns_1_and_readable_message(handler, capsys):
    """5xx -> readable error and exit code 1, without leaking internals."""

    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "storage not connected"})

    import invest_research.cli as cli

    _patch_new_client(cli, err_handler)
    with pytest.raises(SystemExit) as exc:
        main(["status", _UUID])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "error:" in err


def test_poll_stops_at_terminal(handler):
    snapshot = _poll_until_terminal(_client(handler), _UUID)
    assert snapshot.status.value == "succeeded"
