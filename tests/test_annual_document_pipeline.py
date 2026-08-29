"""P07-03 单份 filing 工件流水线的恢复与失败契约测试。"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from uuid import UUID

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import Filing
from invest_research.infrastructure.annual_document_pipeline import (
    AnnualDocumentArtifactPipeline,
    AnnualDocumentManifest,
    AnnualDocumentPipelineStatus,
    AnnualParsedDocument,
)
from invest_research.tools.base import ToolError, ToolFailure, ToolSuccess
from invest_research.tools.parser_router import DocumentParseError, ParseOutcome
from invest_research.tools.sec_downloader import DownloadedDocument
from invest_research.tools.sec_html_parser import ParsedDocument

JOB_ID = UUID("00000000-0000-0000-0000-000000000007")


class StubDownloader:
    name = "stub_downloader"

    def __init__(self, result: ToolSuccess[DownloadedDocument] | ToolFailure) -> None:
        self._result = result
        self.calls = 0

    def execute(self, _request: object) -> ToolSuccess[DownloadedDocument] | ToolFailure:
        self.calls += 1
        return self._result


def _filing(accession: str = "0000000000-24-000001") -> Filing:
    return Filing(
        accession_number=accession,
        form_type="10-K",
        filing_date=date(2025, 2, 15),
        report_period=date(2024, 12, 31),
        primary_document_url=f"https://www.sec.gov/Archives/{accession}.htm",
    )


def _download(
    content: bytes = b"<html><body><h1>Annual report</h1></body></html>",
) -> StubDownloader:
    return StubDownloader(
        ToolSuccess(
            value=DownloadedDocument(
                content=content,
                media_type="text/html",
                byte_size=len(content),
                content_checksum=hashlib.sha256(content).hexdigest(),
            )
        )
    )


def test_html_download_parse_and_manifest_are_atomically_persisted(tmp_path: Path) -> None:
    downloader = _download()
    pipeline = AnnualDocumentArtifactPipeline(tmp_path, downloader)

    result = pipeline.run(job_id=JOB_ID, filing=_filing())

    assert result.status is AnnualDocumentPipelineStatus.COMPLETED
    assert result.source_artifact is not None
    assert result.source_artifact.artifact_key.endswith("/source.html")
    assert result.parsed_artifact is not None
    root = tmp_path / str(JOB_ID) / "annual" / _filing().accession_number
    manifest = AnnualDocumentManifest.model_validate_json((root / "manifest.json").read_bytes())
    parsed = AnnualParsedDocument.model_validate_json((root / "parsed.json").read_bytes())
    assert manifest.status is AnnualDocumentPipelineStatus.COMPLETED
    assert manifest.parsed_artifact == result.parsed_artifact
    assert parsed.blocks[0].locator.startswith("offset:")


def test_completed_accession_reuses_valid_artifacts_without_download(tmp_path: Path) -> None:
    downloader = _download()
    pipeline = AnnualDocumentArtifactPipeline(tmp_path, downloader)
    pipeline.run(job_id=JOB_ID, filing=_filing())

    result = pipeline.run(job_id=JOB_ID, filing=_filing())

    assert downloader.calls == 1
    assert result.reused_completed_artifacts is True


def test_parse_failure_keeps_source_and_next_run_only_reparses(tmp_path: Path) -> None:
    downloader = _download()
    failing = AnnualDocumentArtifactPipeline(
        tmp_path,
        downloader,
        parser=lambda _content, _media: (_ for _ in ()).throw(
            DocumentParseError(("html",), "fixture parse failure")
        ),
    )
    failed = failing.run(job_id=JOB_ID, filing=_filing())

    recovered = AnnualDocumentArtifactPipeline(tmp_path, downloader).run(
        job_id=JOB_ID, filing=_filing()
    )

    assert failed.status is AnnualDocumentPipelineStatus.PARSE_FAILED
    assert failed.source_artifact is not None
    assert recovered.status is AnnualDocumentPipelineStatus.COMPLETED
    assert downloader.calls == 1


def test_two_accessions_are_independent_when_one_download_fails(tmp_path: Path) -> None:
    failed_downloader = StubDownloader(
        ToolFailure(
            error=ToolError(error_code=ErrorCode.NETWORK_TRANSIENT, message="SEC unavailable")
        )
    )
    failed = AnnualDocumentArtifactPipeline(tmp_path, failed_downloader).run(
        job_id=JOB_ID, filing=_filing("0000000000-24-000001")
    )
    succeeded = AnnualDocumentArtifactPipeline(tmp_path, _download()).run(
        job_id=JOB_ID, filing=_filing("0000000000-23-000001")
    )

    assert failed.status is AnnualDocumentPipelineStatus.DOWNLOAD_FAILED
    assert failed.failure is not None and failed.failure.error_code is ErrorCode.NETWORK_TRANSIENT
    assert succeeded.status is AnnualDocumentPipelineStatus.COMPLETED


def test_checksum_damage_triggers_redownload(tmp_path: Path) -> None:
    downloader = _download()
    pipeline = AnnualDocumentArtifactPipeline(tmp_path, downloader)
    pipeline.run(job_id=JOB_ID, filing=_filing())
    source = tmp_path / str(JOB_ID) / "annual" / _filing().accession_number / "source.html"
    source.write_bytes(b"damaged")

    result = pipeline.run(job_id=JOB_ID, filing=_filing())

    assert result.status is AnnualDocumentPipelineStatus.COMPLETED
    assert downloader.calls == 2
    assert source.read_bytes().startswith(b"<html")


def test_unsupported_media_and_empty_parse_are_explainable_failures(tmp_path: Path) -> None:
    unsupported = StubDownloader(
        ToolFailure(
            error=ToolError(error_code=ErrorCode.DOCUMENT_UNSUPPORTED, message="unsupported type")
        )
    )
    unsupported_result = AnnualDocumentArtifactPipeline(tmp_path, unsupported).run(
        job_id=JOB_ID, filing=_filing()
    )
    empty_result = AnnualDocumentArtifactPipeline(
        tmp_path,
        _download(),
        parser=lambda _content, _media: ParseOutcome(
            kind="html", document=ParsedDocument(blocks=()), parser_name="fixture"
        ),
    ).run(job_id=UUID("00000000-0000-0000-0000-000000000008"), filing=_filing())

    assert unsupported_result.status is AnnualDocumentPipelineStatus.DOWNLOAD_FAILED
    assert unsupported_result.failure is not None
    assert unsupported_result.failure.error_code is ErrorCode.DOCUMENT_UNSUPPORTED
    assert empty_result.status is AnnualDocumentPipelineStatus.VALIDATION_FAILED
    assert empty_result.failure is not None
    assert "非空文本块" in empty_result.failure.message
