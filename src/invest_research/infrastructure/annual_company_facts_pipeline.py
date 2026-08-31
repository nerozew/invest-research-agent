"""P07-04：年度 Company Facts 的可恢复工件流水线。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import FinancialFact
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore
from invest_research.tools.base import Tool, ToolFailure
from invest_research.tools.sec_company_facts import (
    FetchFactsRequest,
    FetchFactsResponse,
    parse_company_facts,
)

_MANIFEST_SCHEMA_VERSION = "annual_company_facts_manifest_v1"
_SELECTION_SCHEMA_VERSION = "annual_company_facts_selection_v1"
_ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})


class AnnualCompanyFactsStatus(StrEnum):
    COMPLETED = "completed"
    FETCH_FAILED = "fetch_failed"
    VALIDATION_FAILED = "validation_failed"


class AnnualCompanyFactsFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    error_code: ErrorCode
    message: str = Field(min_length=1)
    is_retryable: bool


class AnnualCompanyFactsSelection(BaseModel):
    """经过截止日、财年和年报表单筛选后的事实集合。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _SELECTION_SCHEMA_VERSION
    cik: str = Field(min_length=10, max_length=10)
    as_of_date: str = Field(min_length=10, max_length=10)
    target_fiscal_year: int = Field(ge=1900, le=9999)
    comparator_fiscal_year: int = Field(ge=1900, le=9999)
    available_fiscal_years: tuple[int, ...] = ()
    facts: tuple[FinancialFact, ...] = ()

    @model_validator(mode="after")
    def _validate_available_years(self) -> "AnnualCompanyFactsSelection":
        expected = {self.target_fiscal_year, self.comparator_fiscal_year}
        if not set(self.available_fiscal_years).issubset(expected):
            raise ValueError("available_fiscal_years 只能包含目标或上一财年")
        return self


class AnnualCompanyFactsManifest(BaseModel):
    """Company Facts 的恢复事实来源，完成标记最后写入。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _MANIFEST_SCHEMA_VERSION
    cik: str = Field(min_length=10, max_length=10)
    as_of_date: str = Field(min_length=10, max_length=10)
    target_fiscal_year: int = Field(ge=1900, le=9999)
    comparator_fiscal_year: int = Field(ge=1900, le=9999)
    status: AnnualCompanyFactsStatus
    source_artifact: ArtifactRef | None = None
    selected_artifact: ArtifactRef | None = None
    available_fiscal_years: tuple[int, ...] = ()
    failure: AnnualCompanyFactsFailure | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> "AnnualCompanyFactsManifest":
        if self.status is AnnualCompanyFactsStatus.COMPLETED:
            if (
                self.source_artifact is None
                or self.selected_artifact is None
                or self.failure is not None
            ):
                raise ValueError("completed Manifest 必须包含原始和筛选工件")
        elif self.failure is None:
            raise ValueError("失败 Manifest 必须包含 failure")
        return self


class AnnualCompanyFactsArtifactResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AnnualCompanyFactsStatus
    source_artifact: ArtifactRef | None = None
    selected_artifact: ArtifactRef | None = None
    manifest_artifact: ArtifactRef
    available_fiscal_years: tuple[int, ...] = ()
    failure: AnnualCompanyFactsFailure | None = None
    reused_completed_artifacts: bool = False


class AnnualCompanyFactsArtifactPipeline:
    """下载或复用 Company Facts，并保存原始 SEC JSON 与两年筛选结果。"""

    def __init__(
        self,
        artifact_root: Path,
        facts_tool: Tool[FetchFactsRequest, FetchFactsResponse],
    ) -> None:
        self._artifact_root = artifact_root
        self._facts_tool = facts_tool

    def run(
        self,
        *,
        job_id: UUID,
        cik: str,
        as_of_date: str,
        target_fiscal_year: int,
        comparator_fiscal_year: int,
    ) -> AnnualCompanyFactsArtifactResult:
        store = ArtifactStore(self._artifact_root / str(job_id))
        prefix = "annual/company-facts"
        manifest_key = f"{prefix}/manifest.json"
        selected_key = f"{prefix}/selected.json"
        manifest = self._read_manifest(store, manifest_key)

        if manifest is not None and not self._matches_request(
            manifest, cik, as_of_date, target_fiscal_year, comparator_fiscal_year
        ):
            manifest = None

        source = self._recover_source(store, prefix, manifest)
        if (
            manifest is not None
            and manifest.status is AnnualCompanyFactsStatus.COMPLETED
            and source
        ):
            selected_content = self._ref_matches(store, manifest.selected_artifact)
            if selected_content is not None:
                try:
                    selected = AnnualCompanyFactsSelection.model_validate_json(selected_content)
                except ValueError:
                    selected = None
                if selected is not None and self._selection_matches(
                    selected, cik, as_of_date, target_fiscal_year, comparator_fiscal_year
                ):
                    return AnnualCompanyFactsArtifactResult(
                        status=AnnualCompanyFactsStatus.COMPLETED,
                        source_artifact=source[0],
                        selected_artifact=manifest.selected_artifact,
                        manifest_artifact=self._artifact_ref(store, manifest_key),
                        available_fiscal_years=selected.available_fiscal_years,
                        reused_completed_artifacts=True,
                    )

        if source is None:
            fetch_result = self._facts_tool.execute(
                FetchFactsRequest(cik=cik, as_of_date=self._date_from_iso(as_of_date))
            )
            if isinstance(fetch_result, ToolFailure):
                return self._write_failure(
                    store,
                    manifest_key,
                    cik,
                    as_of_date,
                    target_fiscal_year,
                    comparator_fiscal_year,
                    AnnualCompanyFactsStatus.FETCH_FAILED,
                    AnnualCompanyFactsFailure(
                        error_code=fetch_result.error.error_code,
                        message=fetch_result.error.message,
                        is_retryable=fetch_result.error.is_retryable,
                    ),
                )
            source = self._persist_response(store, prefix, fetch_result.value)
            if source is None:
                return self._write_failure(
                    store,
                    manifest_key,
                    cik,
                    as_of_date,
                    target_fiscal_year,
                    comparator_fiscal_year,
                    AnnualCompanyFactsStatus.VALIDATION_FAILED,
                    AnnualCompanyFactsFailure(
                        error_code=ErrorCode.SCHEMA_INVALID,
                        message="Company Facts 原始响应为空、非 JSON 或 checksum 不一致",
                        is_retryable=True,
                    ),
                )

        source_ref, source_content = source
        try:
            payload = json.loads(source_content)
            if not isinstance(payload, dict):
                raise ValueError("Company Facts 顶层必须为对象")
            all_facts = parse_company_facts(
                payload, cik, "us-gaap", self._date_from_iso(as_of_date)
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            return self._write_failure(
                store,
                manifest_key,
                cik,
                as_of_date,
                target_fiscal_year,
                comparator_fiscal_year,
                AnnualCompanyFactsStatus.VALIDATION_FAILED,
                AnnualCompanyFactsFailure(
                    error_code=ErrorCode.SCHEMA_INVALID,
                    message=f"Company Facts 原始 JSON 无法解析: {exc}",
                    is_retryable=True,
                ),
                source_artifact=source_ref,
            )

        selected = self._select_facts(
            all_facts, cik, as_of_date, target_fiscal_year, comparator_fiscal_year
        )
        selected_ref = store.write(selected_key, self._json_bytes(selected), overwrite=True)
        completed = AnnualCompanyFactsManifest(
            cik=cik,
            as_of_date=as_of_date,
            target_fiscal_year=target_fiscal_year,
            comparator_fiscal_year=comparator_fiscal_year,
            status=AnnualCompanyFactsStatus.COMPLETED,
            source_artifact=source_ref,
            selected_artifact=selected_ref,
            available_fiscal_years=selected.available_fiscal_years,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(completed), overwrite=True)
        return AnnualCompanyFactsArtifactResult(
            status=AnnualCompanyFactsStatus.COMPLETED,
            source_artifact=source_ref,
            selected_artifact=selected_ref,
            manifest_artifact=manifest_ref,
            available_fiscal_years=selected.available_fiscal_years,
        )

    @staticmethod
    def _date_from_iso(value: str) -> date:
        return date.fromisoformat(value)

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
    def _ref_matches(store: ArtifactStore, ref: ArtifactRef | None) -> bytes | None:
        if ref is None:
            return None
        try:
            content = store.read(ref.artifact_key)
        except KeyError:
            return None
        if len(content) != ref.byte_size:
            return None
        if hashlib.sha256(content).hexdigest() != ref.content_checksum:
            return None
        return content

    @staticmethod
    def _matches_request(
        manifest: AnnualCompanyFactsManifest,
        cik: str,
        as_of_date: str,
        target_fiscal_year: int,
        comparator_fiscal_year: int,
    ) -> bool:
        return (
            manifest.cik == cik
            and manifest.as_of_date == as_of_date
            and manifest.target_fiscal_year == target_fiscal_year
            and manifest.comparator_fiscal_year == comparator_fiscal_year
        )

    @staticmethod
    def _selection_matches(
        selected: AnnualCompanyFactsSelection,
        cik: str,
        as_of_date: str,
        target_fiscal_year: int,
        comparator_fiscal_year: int,
    ) -> bool:
        return (
            selected.cik == cik
            and selected.as_of_date == as_of_date
            and selected.target_fiscal_year == target_fiscal_year
            and selected.comparator_fiscal_year == comparator_fiscal_year
        )

    def _read_manifest(self, store: ArtifactStore, key: str) -> AnnualCompanyFactsManifest | None:
        try:
            return AnnualCompanyFactsManifest.model_validate_json(store.read(key))
        except (KeyError, ValueError):
            return None

    def _recover_source(
        self,
        store: ArtifactStore,
        prefix: str,
        manifest: AnnualCompanyFactsManifest | None,
    ) -> tuple[ArtifactRef, bytes] | None:
        if manifest is not None and manifest.source_artifact is not None:
            content = self._ref_matches(store, manifest.source_artifact)
            if content is None:
                return None
            return manifest.source_artifact, content
        key = f"{prefix}/source.json"
        try:
            content = store.read(key)
        except KeyError:
            return None
        if not content:
            return None
        return (
            ArtifactRef(
                artifact_key=key,
                byte_size=len(content),
                content_checksum=hashlib.sha256(content).hexdigest(),
            ),
            content,
        )

    def _persist_response(
        self,
        store: ArtifactStore,
        prefix: str,
        response: FetchFactsResponse,
    ) -> tuple[ArtifactRef, bytes] | None:
        content = response.source_content
        checksum = hashlib.sha256(content).hexdigest()
        if not content or (
            response.source_checksum is not None and checksum != response.source_checksum
        ):
            return None
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        ref = store.write(f"{prefix}/source.json", content, overwrite=True)
        return ref, content

    @staticmethod
    def _select_facts(
        facts: list[FinancialFact],
        cik: str,
        as_of_date: str,
        target_fiscal_year: int,
        comparator_fiscal_year: int,
    ) -> AnnualCompanyFactsSelection:
        years = {target_fiscal_year, comparator_fiscal_year}
        selected_facts = tuple(
            fact
            for fact in facts
            if fact.fiscal_year in years
            and fact.fiscal_period == "FY"
            and fact.form_type in _ANNUAL_FORMS
        )
        available = tuple(
            year
            for year in (target_fiscal_year, comparator_fiscal_year)
            if any(fact.fiscal_year == year for fact in selected_facts)
        )
        return AnnualCompanyFactsSelection(
            cik=cik,
            as_of_date=as_of_date,
            target_fiscal_year=target_fiscal_year,
            comparator_fiscal_year=comparator_fiscal_year,
            available_fiscal_years=available,
            facts=selected_facts,
        )

    def _write_failure(
        self,
        store: ArtifactStore,
        manifest_key: str,
        cik: str,
        as_of_date: str,
        target_fiscal_year: int,
        comparator_fiscal_year: int,
        status: AnnualCompanyFactsStatus,
        failure: AnnualCompanyFactsFailure,
        *,
        source_artifact: ArtifactRef | None = None,
    ) -> AnnualCompanyFactsArtifactResult:
        manifest = AnnualCompanyFactsManifest(
            cik=cik,
            as_of_date=as_of_date,
            target_fiscal_year=target_fiscal_year,
            comparator_fiscal_year=comparator_fiscal_year,
            status=status,
            source_artifact=source_artifact,
            failure=failure,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(manifest), overwrite=True)
        return AnnualCompanyFactsArtifactResult(
            status=status,
            source_artifact=source_artifact,
            manifest_artifact=manifest_ref,
            failure=failure,
        )
