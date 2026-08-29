"""P07-04 补充：年度网页搜索证据管道（确定性 fan-out 第四路）。

把"网页搜索"作为确定性证据获取（不是 LLM ReAct）：本管道用固定查询模板
调用 ``GoogleSearchTool``（Serper），按 as_of 过滤、URL 去重、可信度分级，
为已预留的叙事 EvidenceKind（BUSINESS_OVERVIEW / RISK_FACTORS /
MANAGEMENT_DISCUSSION / MATERIAL_EVENT）各生成一个搜索摘要工件。

设计对齐 ``annual_company_facts_pipeline.py`` 的可恢复工件模式：
- Manifest + 每个 kind 一个 ``WebSearchSectionEvidence`` 工件 + checksum；
- 恢复：Manifest 匹配且工件完整时直接复用，不重复搜索；
- 失败语义：单路搜索失败只影响该 kind（跳过），全部失败才写 FETCH_FAILED
  Manifest（上层 fan-out 据此不产生证据，缺搜索不阻塞核心 SEC 流程）。

依赖边界：仅标准库、Pydantic、domain、tools；禁止导入 CrewAI/SQLAlchemy。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.annual_pipeline import EvidenceKind
from invest_research.domain.errors import ErrorCode
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore
from invest_research.tools.base import Tool, ToolFailure
from invest_research.tools.google_search import SearchQuery, SearchResponse, SearchResult

_MANIFEST_SCHEMA_VERSION = "annual_web_search_manifest_v1"
_SECTION_SCHEMA_VERSION = "annual_web_search_section_v1"
_PREFIX = "annual/web-search"

# 每章节最多保留的搜索结果条数（控制引用与上下文膨胀）。
DEFAULT_MAX_ENTRIES_PER_SECTION = 10
DEFAULT_PAGE_SIZE = 10

# 确定性查询模板（不靠 LLM 生成）：kind → (查询模板, 可信度边界说明)。
_SEARCH_TEMPLATES: tuple[tuple[EvidenceKind, str, str | None], ...] = (
    (
        EvidenceKind.BUSINESS_OVERVIEW,
        "{company} business model products segments",
        None,
    ),
    (
        EvidenceKind.MANAGEMENT_DISCUSSION,
        "{company} earnings call guidance capital allocation",
        "管理层/第三方观点，非 SEC 官方申报",
    ),
    (
        EvidenceKind.MATERIAL_EVENT,
        "{company} recent news acquisition product launch",
        None,
    ),
    (
        EvidenceKind.RISK_FACTORS,
        "{company} risk regulatory investigation",
        None,
    ),
    (
        EvidenceKind.ANALYST_OPINION,
        "{company} analyst rating target price consensus",
        "第三方分析师观点，非 SEC 官方申报",
    ),
    (
        EvidenceKind.RATING_AGENCY,
        "{company} rating agency outlook Moody's S&P Fitch",
        "评级机构观点，非 SEC 官方申报",
    ),
)

# 权威发布方（publisher 域名白名单，去掉 www. 前缀比较）。
_AUTHORITATIVE_PUBLISHERS = frozenset(
    {
        "reuters.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "cnbc.com",
        "marketwatch.com",
        "finance.yahoo.com",
        "businessinsider.com",
        "apnews.com",
        "nytimes.com",
        "forbes.com",
        "sec.gov",
    }
)
# URL 中出现即视为权威（公司 IR / 官方 newsroom 页面）。
_AUTHORITATIVE_HOST_HINTS = ("investor", "ir.", "newsroom", "press", "sec.gov")

# 本管道支持产生证据的叙事 kind（对齐 _SEARCH_TEMPLATES）。
_SUPPORTED_KINDS: frozenset[str] = frozenset(kind.value for kind, _, _ in _SEARCH_TEMPLATES)


class WebSearchEvidenceStatus(StrEnum):
    """网页搜索工件管道状态（对齐 Company Facts 的状态机）。"""

    COMPLETED = "completed"
    FETCH_FAILED = "fetch_failed"
    VALIDATION_FAILED = "validation_failed"


class WebSearchEvidenceFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    error_code: ErrorCode
    message: str = Field(min_length=1)
    is_retryable: bool


class WebSearchEntry(BaseModel):
    """单条网页搜索结果证据（带确定性可信度分级）。"""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    publisher: str | None = None
    published_at: date | None = None
    accessed_at: date
    snippet: str = ""
    credibility: str = "general"  # authoritative / general


class WebSearchSectionEvidence(BaseModel):
    """一个叙事章节的搜索摘要工件（可被 EvidenceArtifact 按 kind 路由）。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _SECTION_SCHEMA_VERSION
    kind: str = Field(min_length=1)  # EvidenceKind value，如 "business_overview"
    as_of_date: str = Field(min_length=10, max_length=10)
    note: str | None = None  # 可信度边界说明（如"管理层观点，非 SEC 官方"）
    entries: tuple[WebSearchEntry, ...] = ()

    @model_validator(mode="after")
    def _validate_kind(self) -> "WebSearchSectionEvidence":
        if self.kind not in _SUPPORTED_KINDS:
            raise ValueError(f"不支持的搜索证据 kind: {self.kind}")
        return self


class WebSearchEvidenceManifest(BaseModel):
    """网页搜索的恢复事实来源；完成标记最后写入。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _MANIFEST_SCHEMA_VERSION
    company: str = Field(min_length=1)
    cik: str = Field(min_length=10, max_length=10)
    as_of_date: str = Field(min_length=10, max_length=10)
    status: WebSearchEvidenceStatus
    section_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)  # kind → ref
    failure: WebSearchEvidenceFailure | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> "WebSearchEvidenceManifest":
        if self.status is WebSearchEvidenceStatus.COMPLETED:
            if not self.section_artifacts or self.failure is not None:
                raise ValueError("completed Manifest 必须包含至少一个章节工件且无 failure")
        elif self.failure is None:
            raise ValueError("失败 Manifest 必须包含 failure")
        return self


class WebSearchArtifactResult(BaseModel):
    """搜索管道 run() 的产出（供 fan-out 读取与账本判定）。"""

    model_config = ConfigDict(frozen=True)

    status: WebSearchEvidenceStatus
    section_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)  # kind → ref
    manifest_artifact: ArtifactRef
    failure: WebSearchEvidenceFailure | None = None
    reused_completed_artifacts: bool = False


def _credibility(url: str, publisher: str | None) -> str:
    """确定性可信度分级：SEC / 公司 IR / newsroom / 主流媒体 → authoritative。"""
    lowered = url.lower()
    if any(hint in lowered for hint in _AUTHORITATIVE_HOST_HINTS):
        return "authoritative"
    if publisher:
        domain = publisher.lower().strip().removeprefix("www.")
        if domain in _AUTHORITATIVE_PUBLISHERS:
            return "authoritative"
    return "general"


class WebSearchEvidencePipeline:
    """下载或复用网页搜索结果，并按 kind 保存各章节摘要工件。"""

    def __init__(
        self,
        artifact_root: Path,
        search_tool: Tool[SearchQuery, SearchResponse],
        *,
        max_entries_per_section: int = DEFAULT_MAX_ENTRIES_PER_SECTION,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._artifact_root = artifact_root
        self._search_tool = search_tool
        self._max_entries_per_section = max_entries_per_section
        self._page_size = page_size

    def run(
        self,
        *,
        job_id: UUID,
        company: str,
        cik: str,
        as_of_date: str,
    ) -> WebSearchArtifactResult:
        store = ArtifactStore(self._artifact_root / str(job_id))
        manifest_key = f"{_PREFIX}/manifest.json"
        manifest = self._read_manifest(store, manifest_key)

        if manifest is not None and self._matches_request(manifest, company, cik, as_of_date):
            if (
                manifest.status is WebSearchEvidenceStatus.COMPLETED
                and manifest.section_artifacts
                and self._refs_present(store, manifest.section_artifacts)
            ):
                return WebSearchArtifactResult(
                    status=WebSearchEvidenceStatus.COMPLETED,
                    section_artifacts=manifest.section_artifacts,
                    manifest_artifact=self._artifact_ref(store, manifest_key),
                    reused_completed_artifacts=True,
                )
            if manifest.failure is not None:
                return WebSearchArtifactResult(
                    status=manifest.status,
                    section_artifacts={},
                    manifest_artifact=self._artifact_ref(store, manifest_key),
                    failure=manifest.failure,
                )

        as_of = date.fromisoformat(as_of_date)
        section_artifacts: dict[str, ArtifactRef] = {}
        for kind, template, note in _SEARCH_TEMPLATES:
            query = SearchQuery(
                query=template.format(company=company), as_of=as_of, page_size=self._page_size
            )
            result = self._search_tool.execute(query)
            if isinstance(result, ToolFailure):
                # 单路搜索失败只跳过该 kind；其余 kind 不受影响。
                continue
            entries = self._build_entries(result.value.items, as_of)
            if not entries:
                continue
            section = WebSearchSectionEvidence(
                kind=kind.value, as_of_date=as_of_date, note=note, entries=entries
            )
            section_artifacts[kind.value] = store.write(
                f"{_PREFIX}/{kind.value}.json", self._json_bytes(section), overwrite=True
            )

        if not section_artifacts:
            return self._write_failure(
                store,
                manifest_key,
                company,
                cik,
                as_of_date,
                WebSearchEvidenceStatus.FETCH_FAILED,
                WebSearchEvidenceFailure(
                    error_code=ErrorCode.UPSTREAM_5XX,
                    message="网页搜索未返回任何可用结果",
                    is_retryable=True,
                ),
            )

        completed = WebSearchEvidenceManifest(
            company=company,
            cik=cik,
            as_of_date=as_of_date,
            status=WebSearchEvidenceStatus.COMPLETED,
            section_artifacts=section_artifacts,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(completed), overwrite=True)
        return WebSearchArtifactResult(
            status=WebSearchEvidenceStatus.COMPLETED,
            section_artifacts=section_artifacts,
            manifest_artifact=manifest_ref,
        )

    def _build_entries(
        self, items: tuple[SearchResult, ...], as_of: date
    ) -> tuple[WebSearchEntry, ...]:
        """按 as_of 兜底过滤 + URL 去重 + 可信度分级，并限制条数。"""
        seen: set[str] = set()
        entries: list[WebSearchEntry] = []
        for item in items:
            if item.published_at is not None and item.published_at > as_of:
                continue
            if item.url in seen:
                continue
            seen.add(item.url)
            entries.append(
                WebSearchEntry(
                    title=item.title,
                    url=item.url,
                    publisher=item.publisher,
                    published_at=item.published_at,
                    accessed_at=item.accessed_at.date(),
                    snippet=item.snippet or "",
                    credibility=_credibility(item.url, item.publisher),
                )
            )
            if len(entries) >= self._max_entries_per_section:
                break
        return tuple(entries)

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
    def _refs_present(store: ArtifactStore, refs: dict[str, ArtifactRef]) -> bool:
        for ref in refs.values():
            try:
                content = store.read(ref.artifact_key)
            except KeyError:
                return False
            if len(content) != ref.byte_size:
                return False
            if hashlib.sha256(content).hexdigest() != ref.content_checksum:
                return False
        return True

    @staticmethod
    def _matches_request(
        manifest: WebSearchEvidenceManifest, company: str, cik: str, as_of_date: str
    ) -> bool:
        return (
            manifest.company == company
            and manifest.cik == cik
            and manifest.as_of_date == as_of_date
        )

    def _read_manifest(self, store: ArtifactStore, key: str) -> WebSearchEvidenceManifest | None:
        try:
            return WebSearchEvidenceManifest.model_validate_json(store.read(key))
        except (KeyError, ValueError):
            return None

    def _write_failure(
        self,
        store: ArtifactStore,
        manifest_key: str,
        company: str,
        cik: str,
        as_of_date: str,
        status: WebSearchEvidenceStatus,
        failure: WebSearchEvidenceFailure,
    ) -> WebSearchArtifactResult:
        manifest = WebSearchEvidenceManifest(
            company=company,
            cik=cik,
            as_of_date=as_of_date,
            status=status,
            failure=failure,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(manifest), overwrite=True)
        return WebSearchArtifactResult(
            status=status,
            section_artifacts={},
            manifest_artifact=manifest_ref,
            failure=failure,
        )


__all__ = [
    "DEFAULT_MAX_ENTRIES_PER_SECTION",
    "DEFAULT_PAGE_SIZE",
    "WebSearchArtifactResult",
    "WebSearchEntry",
    "WebSearchEvidenceFailure",
    "WebSearchEvidenceManifest",
    "WebSearchEvidencePipeline",
    "WebSearchEvidenceStatus",
    "WebSearchSectionEvidence",
    "_SEARCH_TEMPLATES",
]
