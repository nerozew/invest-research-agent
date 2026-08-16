"""P06-06B 收口：前端中国时区（Asia/Shanghai）与结束时间/耗时展示测试。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from invest_research.domain.status import JobStatus
from invest_research.frontend.models import JobListEntry, JobSnapshot
from invest_research.frontend.render import format_cn_time, job_list_row


def test_format_cn_time_converts_utc_to_shanghai() -> None:
    """后端 UTC 时间转中国时区（+8）且格式简化（两位年份）。"""
    utc = datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc)
    assert format_cn_time(utc) == "25-11-01 08:00"


def test_format_cn_time_none_returns_dash() -> None:
    assert format_cn_time(None) == "—"


def test_job_list_row_shows_start_and_end_times() -> None:
    entry = JobListEntry(
        job_id=uuid.uuid4(),
        input_company="AAPL",
        as_of_date=date(2025, 10, 31),
        language="zh-CN",
        status=JobStatus.SUCCEEDED,
        research_profile="fast",
        created_at=datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc),
        started_at=datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2025, 11, 1, 1, 30, tzinfo=timezone.utc),
    )
    row = job_list_row(entry)
    assert row["创建时间"] == "25-11-01 08:00"
    assert row["开始时间"] == "25-11-01 08:00"
    assert row["结束时间"] == "25-11-01 09:30"


def test_render_job_snapshot_shows_duration_and_times(monkeypatch) -> None:
    import streamlit as st

    from invest_research.frontend.render import render_job_snapshot

    metrics: list[tuple[str, str]] = []

    def fake_metric(label: str, value: str) -> None:
        metrics.append((label, str(value)))

    class _FakeColumn:
        def __enter__(self) -> "_FakeColumn":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _FakeColumns:
        def __init__(self, n: int) -> None:
            self._cols = [_FakeColumn() for _ in range(n)]

        def __getitem__(self, idx: int) -> _FakeColumn:
            return self._cols[idx]

    monkeypatch.setattr(st, "subheader", lambda *a, **k: None)
    monkeypatch.setattr(st, "columns", lambda n: _FakeColumns(n))
    monkeypatch.setattr(st, "metric", fake_metric)
    monkeypatch.setattr(st, "info", lambda *a, **k: None)
    monkeypatch.setattr(st, "table", lambda *a, **k: None)
    monkeypatch.setattr(st, "markdown", lambda *a, **k: None)

    snapshot = JobSnapshot(
        job_id=uuid.uuid4(),
        status=JobStatus.SUCCEEDED,
        research_profile="deep",
        started_at=datetime(2025, 11, 1, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2025, 11, 1, 0, 2, 5, tzinfo=timezone.utc),
        duration_seconds=125.0,
        steps=(),
    )
    render_job_snapshot(snapshot)

    label_to_value = dict(metrics)
    assert label_to_value["开始时间"] == "25-11-01 08:00"
    assert label_to_value["结束时间"] == "25-11-01 08:02"
    assert label_to_value["总耗时"] == "2分05秒"
