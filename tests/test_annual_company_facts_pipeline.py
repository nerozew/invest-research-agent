"""P07-04 Company Facts 工件流水线测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from invest_research.infrastructure.annual_company_facts_pipeline import (
    AnnualCompanyFactsArtifactPipeline,
    AnnualCompanyFactsManifest,
    AnnualCompanyFactsSelection,
    AnnualCompanyFactsStatus,
)
from invest_research.tools.base import ToolSuccess
from invest_research.tools.sec_company_facts import FetchFactsResponse

JOB_ID = UUID("00000000-0000-0000-0000-000000000704")
CIK = "0000789019"


def _payload(*, include_comparator: bool = True) -> bytes:
    entries = [
        {
            "start": "2024-01-01",
            "end": "2024-12-31",
            "filed": "2025-02-01",
            "val": 100,
            "form": "10-K",
            "fy": 2024,
            "fp": "FY",
            "accn": "target",
        }
    ]
    if include_comparator:
        entries.append(
            {
                "start": "2023-01-01",
                "end": "2023-12-31",
                "filed": "2024-02-01",
                "val": 90,
                "form": "10-K",
                "fy": 2023,
                "fp": "FY",
                "accn": "comparator",
            }
        )
    return json.dumps({"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}).encode()


class RecordingFactsTool:
    name = "recording_facts"

    def __init__(self, source: bytes) -> None:
        self.source = source
        self.calls = 0

    def execute(self, _request: object) -> ToolSuccess[FetchFactsResponse]:
        self.calls += 1
        return ToolSuccess(
            value=FetchFactsResponse(
                facts=[],
                source_content=self.source,
                source_checksum=hashlib.sha256(self.source).hexdigest(),
            )
        )


def test_persists_raw_selected_and_manifest_then_reuses_completed_artifacts(tmp_path: Path) -> None:
    tool = RecordingFactsTool(_payload())
    pipeline = AnnualCompanyFactsArtifactPipeline(tmp_path, tool)

    first = pipeline.run(
        job_id=JOB_ID,
        cik=CIK,
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
    )
    second = pipeline.run(
        job_id=JOB_ID,
        cik=CIK,
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
    )

    assert first.status is AnnualCompanyFactsStatus.COMPLETED
    assert first.available_fiscal_years == (2024, 2023)
    assert second.reused_completed_artifacts is True
    assert tool.calls == 1
    root = tmp_path / str(JOB_ID) / "annual" / "company-facts"
    assert (root / "source.json").read_bytes() == _payload()
    selected = AnnualCompanyFactsSelection.model_validate_json(
        (root / "selected.json").read_bytes()
    )
    manifest = AnnualCompanyFactsManifest.model_validate_json((root / "manifest.json").read_bytes())
    assert len(selected.facts) == 2
    assert manifest.selected_artifact == first.selected_artifact


def test_bad_selected_reselects_without_network_and_bad_source_refetches(tmp_path: Path) -> None:
    tool = RecordingFactsTool(_payload())
    pipeline = AnnualCompanyFactsArtifactPipeline(tmp_path, tool)
    pipeline.run(
        job_id=JOB_ID,
        cik=CIK,
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
    )
    root = tmp_path / str(JOB_ID) / "annual" / "company-facts"
    (root / "selected.json").write_bytes(b"bad selected")

    reselected = pipeline.run(
        job_id=JOB_ID,
        cik=CIK,
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
    )
    assert reselected.status is AnnualCompanyFactsStatus.COMPLETED
    assert reselected.reused_completed_artifacts is False
    assert tool.calls == 1

    (root / "source.json").write_bytes(b"bad source")
    refetched = pipeline.run(
        job_id=JOB_ID,
        cik=CIK,
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
    )
    assert refetched.status is AnnualCompanyFactsStatus.COMPLETED
    assert tool.calls == 2


def test_valid_source_with_one_missing_year_is_completed_but_reports_availability(
    tmp_path: Path,
) -> None:
    pipeline = AnnualCompanyFactsArtifactPipeline(
        tmp_path, RecordingFactsTool(_payload(include_comparator=False))
    )

    result = pipeline.run(
        job_id=JOB_ID,
        cik=CIK,
        as_of_date="2025-02-15",
        target_fiscal_year=2024,
        comparator_fiscal_year=2023,
    )

    assert result.status is AnnualCompanyFactsStatus.COMPLETED
    assert result.available_fiscal_years == (2024,)
