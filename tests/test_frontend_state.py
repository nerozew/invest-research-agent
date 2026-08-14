"""P04-UI-08：前端 job_id URL/session 持久化纯逻辑测试。

覆盖：
- UUID 校验（合法/非法/空白）
- resolve_job_id 优先级：URL > session > manual
- save_job_id 写入 session + 校验非法值抛错
（load_job_id / save_job_id 依赖 Streamlit 运行时，这里测试可独立运行的纯逻辑。）
"""

from __future__ import annotations

import uuid

import pytest

from invest_research.frontend.state import (
    is_valid_job_id,
    resolve_job_id,
    save_job_id,
)


def _valid_uuid() -> str:
    return str(uuid.uuid4())


def test_is_valid_job_id_accepts_valid_uuid() -> None:
    assert is_valid_job_id(_valid_uuid()) is True


def test_is_valid_job_id_rejects_invalid() -> None:
    assert is_valid_job_id("not-a-uuid") is False
    assert is_valid_job_id("") is False
    assert is_valid_job_id(None) is False
    assert is_valid_job_id("   ") is False


def test_resolve_job_id_prefers_url_over_session_and_manual() -> None:
    url = _valid_uuid()
    session = _valid_uuid()
    manual = _valid_uuid()
    job_id, valid = resolve_job_id(url_value=url, session_value=session, manual_value=manual)
    assert valid is True
    assert job_id == url


def test_resolve_job_id_falls_back_to_session_when_url_invalid() -> None:
    session = _valid_uuid()
    job_id, valid = resolve_job_id(url_value="bad-uuid", session_value=session, manual_value=None)
    assert valid is True
    assert job_id == session


def test_resolve_job_id_falls_back_to_manual_when_url_and_session_invalid() -> None:
    manual = _valid_uuid()
    job_id, valid = resolve_job_id(url_value=None, session_value="bad", manual_value=manual)
    assert valid is True
    assert job_id == manual


def test_resolve_job_id_returns_none_when_all_invalid() -> None:
    job_id, valid = resolve_job_id(url_value="bad", session_value=None, manual_value="also-bad")
    assert valid is False
    assert job_id is None


def test_save_job_id_rejects_invalid_value() -> None:
    with pytest.raises(ValueError):
        save_job_id("not-a-uuid")
