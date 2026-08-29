"""P07-03：单份年度 filing 的可恢复工件流水线。

本模块刻意不接入数据库、Flow 或调度器。每次调用只处理一个已由 P07-02
选定的 filing，并把恢复事实完整地保存在该 job 的 ArtifactStore 中。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import Filing
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore
from invest_research.tools.base import Tool, ToolFailure
from invest_research.tools.parser_router import DocumentParseError, ParseOutcome, parse_document
from invest_research.tools.pdf_parser import ParsedPDF
from invest_research.tools.sec_downloader import DownloadedDocument, DownloadRequest
from invest_research.tools.sec_html_parser import ParsedDocument

_MANIFEST_SCHEMA_VERSION = "annual_document_manifest_v1"
_PARSED_SCHEMA_VERSION = "annual_parsed_document_v1"
_MEDIA_TYPE_TO_EXTENSION = {"text/html": "html", "application/pdf": "pdf"}
_EXTENSION_TO_MEDIA_TYPE = {value: key for key, value in _MEDIA_TYPE_TO_EXTENSION.items()}


class AnnualDocumentPipelineStatus(StrEnum):
    """单个 filing 工件流水线的终态。"""

    COMPLETED = "completed"
    DOWNLOAD_FAILED = "download_failed"
    PARSE_FAILED = "parse_failed"
    VALIDATION_FAILED = "validation_failed"


class AnnualDocumentFailure(BaseModel):
    """可解释失败；重试策略由后续调度层决定。"""

    model_config = ConfigDict(frozen=True)

    error_code: ErrorCode
    message: str = Field(min_length=1)
    is_retryable: bool


class AnnualParsedTextBlock(BaseModel):
    """统一的可定位文本块：HTML 用 offset，PDF 用页码。"""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)


class AnnualParsedDocument(BaseModel):
    """版本化结构化解析结果，不把解析器的内部对象直接持久化。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _PARSED_SCHEMA_VERSION
    source_checksum: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    parser_name: str = Field(min_length=1)
    degraded_from: str | None = None
    blocks: tuple[AnnualParsedTextBlock, ...] = Field(min_length=1)


class AnnualDocumentManifest(BaseModel):
    """P07-03 恢复事实来源；完成 Manifest 总在 parsed.json 之后写入。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _MANIFEST_SCHEMA_VERSION
    accession_number: str = Field(min_length=1)
    primary_document_url: str = Field(min_length=1)
    form_type: str = Field(min_length=1)
    status: AnnualDocumentPipelineStatus
    source_artifact: ArtifactRef | None = None
    parsed_artifact: ArtifactRef | None = None
    media_type: str | None = None
    parser_name: str | None = None
    degraded_from: str | None = None
    failure: AnnualDocumentFailure | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> "AnnualDocumentManifest":
        if self.status is AnnualDocumentPipelineStatus.COMPLETED:
            if (
                self.source_artifact is None
                or self.parsed_artifact is None
                or self.media_type is None
                or self.parser_name is None
                or self.failure is not None
            ):
                raise ValueError("completed Manifest 必须包含原始、解析、媒体类型和解析器信息")
        elif self.failure is None:
            raise ValueError("失败 Manifest 必须包含 failure")
        return self


class AnnualDocumentArtifactResult(BaseModel):
    """单份 filing 的工件引用与明确状态。"""

    model_config = ConfigDict(frozen=True)

    status: AnnualDocumentPipelineStatus
    accession_number: str = Field(min_length=1)
    source_artifact: ArtifactRef | None = None
    parsed_artifact: ArtifactRef | None = None
    manifest_artifact: ArtifactRef
    failure: AnnualDocumentFailure | None = None
    reused_completed_artifacts: bool = False


type DocumentParser = Callable[[bytes, str | None], ParseOutcome]


class AnnualDocumentArtifactPipeline:
    """下载、解析并原子保存一份 canonical annual filing。

    ``downloader`` 和 ``parser`` 均可注入，令本模块可在不联网、不依赖真实
    解析器的单元测试中验证恢复逻辑。
    """

    def __init__(
        self,
        artifact_root: Path,
        downloader: Tool[DownloadRequest, DownloadedDocument],
        *,
        parser: DocumentParser = parse_document,
    ) -> None:
        self._artifact_root = artifact_root
        self._downloader = downloader
        self._parser = parser

    def run(self, *, job_id: UUID, filing: Filing) -> AnnualDocumentArtifactResult:
        """处理一份 filing；每次调用都是该 accession 的独立恢复尝试。"""
        store = ArtifactStore(self._artifact_root / str(job_id))
        prefix = f"annual/{filing.accession_number}"
        manifest_key = f"{prefix}/manifest.json"
        parsed_key = f"{prefix}/parsed.json"
        manifest = self._read_manifest(store, manifest_key)
        source = self._recover_source(store, prefix, manifest)

        if manifest is not None and manifest.status is AnnualDocumentPipelineStatus.COMPLETED:
            completed = self._recover_completed(store, manifest, source)
            if completed is not None:
                manifest_ref = self._artifact_ref(store, manifest_key)
                return AnnualDocumentArtifactResult(
                    status=manifest.status,
                    accession_number=filing.accession_number,
                    source_artifact=manifest.source_artifact,
                    parsed_artifact=manifest.parsed_artifact,
                    manifest_artifact=manifest_ref,
                    reused_completed_artifacts=True,
                )

        if source is None:
            download_result = self._downloader.execute(
                DownloadRequest(url=filing.primary_document_url)
            )
            if isinstance(download_result, ToolFailure):
                failure = AnnualDocumentFailure(
                    error_code=download_result.error.error_code,
                    message=download_result.error.message,
                    is_retryable=download_result.error.is_retryable,
                )
                return self._write_failure(
                    store,
                    manifest_key,
                    filing,
                    AnnualDocumentPipelineStatus.DOWNLOAD_FAILED,
                    failure,
                )

            downloaded = download_result.value
            source = self._persist_download(store, prefix, downloaded)
            if source is None:
                failure = AnnualDocumentFailure(
                    error_code=ErrorCode.SCHEMA_INVALID,
                    message="下载器返回的 checksum 与原始内容不一致",
                    is_retryable=True,
                )
                return self._write_failure(
                    store,
                    manifest_key,
                    filing,
                    AnnualDocumentPipelineStatus.VALIDATION_FAILED,
                    failure,
                )

        source_ref, source_content, media_type = source
        try:
            outcome = self._parser(source_content, media_type)
        except DocumentParseError as exc:
            failure = AnnualDocumentFailure(
                error_code=ErrorCode.DOCUMENT_UNSUPPORTED,
                message=str(exc),
                is_retryable=False,
            )
            return self._write_failure(
                store,
                manifest_key,
                filing,
                AnnualDocumentPipelineStatus.PARSE_FAILED,
                failure,
                source_artifact=source_ref,
                media_type=media_type,
            )
        except Exception as exc:
            failure = AnnualDocumentFailure(
                error_code=ErrorCode.INTERNAL_BUG,
                message=f"解析器发生未预期错误: {exc}",
                is_retryable=False,
            )
            return self._write_failure(
                store,
                manifest_key,
                filing,
                AnnualDocumentPipelineStatus.PARSE_FAILED,
                failure,
                source_artifact=source_ref,
                media_type=media_type,
            )

        try:
            parsed = self._to_parsed_document(outcome, source_ref.content_checksum, media_type)
        except ValueError as exc:
            failure = AnnualDocumentFailure(
                error_code=ErrorCode.DOCUMENT_UNSUPPORTED,
                message=str(exc),
                is_retryable=False,
            )
            return self._write_failure(
                store,
                manifest_key,
                filing,
                AnnualDocumentPipelineStatus.VALIDATION_FAILED,
                failure,
                source_artifact=source_ref,
                media_type=media_type,
                parser_name=outcome.parser_name,
                degraded_from=outcome.degraded_from,
            )

        parsed_ref = store.write(parsed_key, self._json_bytes(parsed), overwrite=True)
        completed_manifest = AnnualDocumentManifest(
            accession_number=filing.accession_number,
            primary_document_url=filing.primary_document_url,
            form_type=filing.form_type,
            status=AnnualDocumentPipelineStatus.COMPLETED,
            source_artifact=source_ref,
            parsed_artifact=parsed_ref,
            media_type=media_type,
            parser_name=outcome.parser_name,
            degraded_from=outcome.degraded_from,
        )
        manifest_ref = store.write(
            manifest_key, self._json_bytes(completed_manifest), overwrite=True
        )
        return AnnualDocumentArtifactResult(
            status=AnnualDocumentPipelineStatus.COMPLETED,
            accession_number=filing.accession_number,
            source_artifact=source_ref,
            parsed_artifact=parsed_ref,
            manifest_artifact=manifest_ref,
        )

    @staticmethod
    def _json_bytes(model: BaseModel) -> bytes:
        return json.dumps(
            model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _artifact_ref(store: ArtifactStore, key: str) -> ArtifactRef:
        content = store.read(key)
        return ArtifactRef(
            artifact_key=key,
            byte_size=len(content),
            content_checksum=hashlib.sha256(content).hexdigest(),
        )

    @staticmethod
    def _ref_matches(store: ArtifactStore, ref: ArtifactRef) -> bytes | None:
        try:
            content = store.read(ref.artifact_key)
        except KeyError:
            return None
        if len(content) != ref.byte_size:
            return None
        if hashlib.sha256(content).hexdigest() != ref.content_checksum:
            return None
        return content

    def _read_manifest(
        self, store: ArtifactStore, manifest_key: str
    ) -> AnnualDocumentManifest | None:
        try:
            return AnnualDocumentManifest.model_validate_json(store.read(manifest_key))
        except (KeyError, ValueError):
            return None

    def _recover_source(
        self,
        store: ArtifactStore,
        prefix: str,
        manifest: AnnualDocumentManifest | None,
    ) -> tuple[ArtifactRef, bytes, str] | None:
        if manifest is not None and manifest.source_artifact is not None:
            if manifest.media_type is None:
                return None
            content = self._ref_matches(store, manifest.source_artifact)
            if content is None:
                # Manifest 已声明该 source 的 checksum。不能把同一路径上被篡改的
                # 内容当成“未登记原始文件”继续解析，必须重新下载。
                return None
            return manifest.source_artifact, content, manifest.media_type

        for extension, media_type in _EXTENSION_TO_MEDIA_TYPE.items():
            key = f"{prefix}/source.{extension}"
            try:
                content = store.read(key)
            except KeyError:
                continue
            if content:
                return (
                    ArtifactRef(
                        artifact_key=key,
                        byte_size=len(content),
                        content_checksum=hashlib.sha256(content).hexdigest(),
                    ),
                    content,
                    media_type,
                )
        return None

    def _recover_completed(
        self,
        store: ArtifactStore,
        manifest: AnnualDocumentManifest,
        source: tuple[ArtifactRef, bytes, str] | None,
    ) -> AnnualParsedDocument | None:
        if source is None or manifest.parsed_artifact is None:
            return None
        parsed_content = self._ref_matches(store, manifest.parsed_artifact)
        if parsed_content is None:
            return None
        try:
            parsed = AnnualParsedDocument.model_validate_json(parsed_content)
        except ValueError:
            return None
        if parsed.source_checksum != source[0].content_checksum:
            return None
        return parsed

    def _persist_download(
        self,
        store: ArtifactStore,
        prefix: str,
        downloaded: DownloadedDocument,
    ) -> tuple[ArtifactRef, bytes, str] | None:
        actual_checksum = hashlib.sha256(downloaded.content).hexdigest()
        if (
            not downloaded.content
            or len(downloaded.content) != downloaded.byte_size
            or actual_checksum != downloaded.content_checksum
        ):
            return None
        extension = _MEDIA_TYPE_TO_EXTENSION.get(downloaded.media_type)
        if extension is None:
            return None
        ref = store.write(f"{prefix}/source.{extension}", downloaded.content, overwrite=True)
        return ref, downloaded.content, downloaded.media_type

    @staticmethod
    def _to_parsed_document(
        outcome: ParseOutcome,
        source_checksum: str,
        media_type: str,
    ) -> AnnualParsedDocument:
        if outcome.kind == "html":
            if not isinstance(outcome.document, ParsedDocument):
                raise ValueError("HTML 解析路由返回了非 HTML 文档")
            blocks = tuple(
                AnnualParsedTextBlock(
                    text=block.text.strip(),
                    locator=f"offset:{block.location}",
                )
                for block in outcome.document.blocks
                if block.text.strip()
            )
        else:
            if not isinstance(outcome.document, ParsedPDF):
                raise ValueError("PDF 解析路由返回了非 PDF 文档")
            blocks = tuple(
                AnnualParsedTextBlock(
                    text=block.text.strip(),
                    locator=f"page:{block.page_number}",
                    page_number=block.page_number,
                )
                for block in outcome.document.blocks
                if block.text.strip()
            )
        if not blocks:
            raise ValueError("解析结果不含可定位的非空文本块")
        return AnnualParsedDocument(
            source_checksum=source_checksum,
            media_type=media_type,
            parser_name=outcome.parser_name,
            degraded_from=outcome.degraded_from,
            blocks=blocks,
        )

    def _write_failure(
        self,
        store: ArtifactStore,
        manifest_key: str,
        filing: Filing,
        status: AnnualDocumentPipelineStatus,
        failure: AnnualDocumentFailure,
        *,
        source_artifact: ArtifactRef | None = None,
        media_type: str | None = None,
        parser_name: str | None = None,
        degraded_from: str | None = None,
    ) -> AnnualDocumentArtifactResult:
        manifest = AnnualDocumentManifest(
            accession_number=filing.accession_number,
            primary_document_url=filing.primary_document_url,
            form_type=filing.form_type,
            status=status,
            source_artifact=source_artifact,
            media_type=media_type,
            parser_name=parser_name,
            degraded_from=degraded_from,
            failure=failure,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(manifest), overwrite=True)
        return AnnualDocumentArtifactResult(
            status=status,
            accession_number=filing.accession_number,
            source_artifact=source_artifact,
            manifest_artifact=manifest_ref,
            failure=failure,
        )
