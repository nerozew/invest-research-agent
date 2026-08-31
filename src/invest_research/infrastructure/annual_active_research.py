"""P07 补充：年度主动研究 Agent（受控工具循环）。

让"搜索 Agent"真正调用工具干活：LLM 决策查什么，代码执行搜索、下载网页、
解析全文，LLM 从网页提炼管理层讨论/电话会议/事件摘要，写入复用
``WebSearchSectionEvidence`` 格式的证据工件，供叙事章节 LLM 消费。

边界（对齐 P07 交接约束）：
- 有界循环：``max_rounds`` 轮、每轮 ``max_queries_per_round`` 条查询、
  ``max_pages_to_read`` 页；到点即停。
- LLM 不碰数字：系统提示词硬性禁止输出财务数值/结论/买卖建议；产出仅为
  文本性摘要。
- 来源可追溯：每条摘要必须引用真实搜索/下载到的 URL，LLM 编造 URL 会被丢弃。
- 复用既有确定性工件格式（``WebSearchSectionEvidence``），下游 ``_narrative_context``
  无需改动即可消费。

依赖边界：仅标准库、Pydantic、domain、tools、annual_llm_writing 的 completion 接口；
禁止导入 CrewAI/SQLAlchemy。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.agents.llm_factory import LLMRole
from invest_research.domain.annual_pipeline import EvidenceKind
from invest_research.domain.errors import ErrorCode
from invest_research.infrastructure.annual_llm_writing import AnnualCompletion
from invest_research.infrastructure.annual_web_search_pipeline import (
    WebSearchEntry,
    WebSearchSectionEvidence,
    _credibility,
)
from invest_research.tools.artifact_store import ArtifactRef, ArtifactStore
from invest_research.tools.base import Tool, ToolFailure
from invest_research.tools.google_search import SearchQuery, SearchResponse
from invest_research.tools.parser_router import ParseOutcome, parse_document
from invest_research.tools.sec_downloader import DownloadedDocument, DownloadRequest

_MANIFEST_SCHEMA_VERSION = "annual_active_research_manifest_v1"
_PREFIX = "annual/active-research"
# 网页快照前缀（内容级溯源：把下载解析后的网页原文留档，可证明"引用真实存在于该 URL"）。
_PAGES_PREFIX = f"{_PREFIX}/pages"
_DEFAULT_MAX_ROUNDS = 2
_DEFAULT_MAX_QUERIES_PER_ROUND = 4
_DEFAULT_MAX_PAGES_TO_READ = 3
_MAX_CONTEXT_CHARS = 12_000
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# 每 kind 的可信度边界说明（对齐 WebSearchSectionEvidence.note）。
_NOTE_BY_KIND: dict[str, str] = {
    "management_discussion": "管理层/第三方观点，非 SEC 官方申报",
}
# 允许主动研究的叙事 kind（排除财务——数字只信 XBRL）。
_ACTIVE_KINDS = frozenset(
    {
        EvidenceKind.BUSINESS_OVERVIEW.value,
        EvidenceKind.MANAGEMENT_DISCUSSION.value,
        EvidenceKind.MATERIAL_EVENT.value,
        EvidenceKind.RISK_FACTORS.value,
        EvidenceKind.ANALYST_OPINION.value,
        EvidenceKind.RATING_AGENCY.value,
    }
)

_PLAN_SYSTEM_PROMPT = (
    "你是投研搜索规划者。只负责决定‘查什么网页’，不输出任何财务数字、结论或投资建议。"
    "基于给定公司与研究方向，给出少量精准的英文搜索查询。只输出 JSON，禁止任何前后缀。"
)
_EXTRACT_SYSTEM_PROMPT = (
    "你是网页信息提炼者。阅读给定网页内容，为每个研究方向提炼不超过 150 字的客观中文摘要。"
    "禁止输出财务指标数值、禁止给出买入/卖出建议、禁止编造网页未提供的内容。"
    "每条摘要必须引用给定的原始 URL。只输出 JSON，禁止任何前后缀。"
)


class ActiveResearchStatus(StrEnum):
    """主动研究工件管道状态。"""

    COMPLETED = "completed"
    FETCH_FAILED = "fetch_failed"
    VALIDATION_FAILED = "validation_failed"


class ActiveResearchFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    error_code: ErrorCode
    message: str = Field(min_length=1)
    is_retryable: bool


class ActiveResearchManifest(BaseModel):
    """主动研究的恢复事实来源；完成标记最后写入。"""

    model_config = ConfigDict(frozen=True)

    schema_version: str = _MANIFEST_SCHEMA_VERSION
    company: str = Field(min_length=1)
    cik: str = Field(min_length=10, max_length=10)
    as_of_date: str = Field(min_length=10, max_length=10)
    status: ActiveResearchStatus
    section_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)  # kind → ref
    rounds: int = 0
    failure: ActiveResearchFailure | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> "ActiveResearchManifest":
        if self.status is ActiveResearchStatus.COMPLETED:
            if not self.section_artifacts or self.failure is not None:
                raise ValueError("completed Manifest 必须包含至少一个章节工件且无 failure")
        elif self.failure is None:
            raise ValueError("失败 Manifest 必须包含 failure")
        return self


class ActiveResearchArtifactResult(BaseModel):
    """主动研究 run() 的产出（供 fan-out / 补证接线读取）。"""

    model_config = ConfigDict(frozen=True)

    status: ActiveResearchStatus
    section_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)  # kind → ref
    manifest_artifact: ArtifactRef
    rounds: int = 0
    failure: ActiveResearchFailure | None = None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从 LLM 文本提取 JSON 对象（兼容围栏/前后缀）；失败返回 None。"""
    if not text:
        return None
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group())
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class ActiveResearchAgent:
    """受控工具循环：LLM 决策 → 代码执行搜索/下载/解析 → LLM 提炼 → 写证据工件。"""

    def __init__(
        self,
        artifact_root: Path,
        completion: AnnualCompletion,
        search_tool: Tool[SearchQuery, SearchResponse],
        downloader: Tool[DownloadRequest, DownloadedDocument],
        *,
        parser: Any = parse_document,
        max_rounds: int = _DEFAULT_MAX_ROUNDS,
        max_queries_per_round: int = _DEFAULT_MAX_QUERIES_PER_ROUND,
        max_pages_to_read: int = _DEFAULT_MAX_PAGES_TO_READ,
        max_context_chars: int = _MAX_CONTEXT_CHARS,
    ) -> None:
        self._artifact_root = artifact_root
        self._completion = completion
        self._search_tool = search_tool
        self._downloader = downloader
        self._parser = parser
        self._max_rounds = max_rounds
        self._max_queries_per_round = max_queries_per_round
        self._max_pages_to_read = max_pages_to_read
        self._max_context_chars = max_context_chars

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        job_id: UUID,
        company: str,
        cik: str,
        as_of_date: str,
        targets: tuple[EvidenceKind, ...],
    ) -> ActiveResearchArtifactResult:
        store = ArtifactStore(self._artifact_root / str(job_id))
        manifest_key = f"{_PREFIX}/manifest.json"
        manifest = self._read_manifest(store, manifest_key)
        if manifest is not None and self._matches_request(manifest, company, cik, as_of_date):
            if (
                manifest.status is ActiveResearchStatus.COMPLETED
                and manifest.section_artifacts
                and self._refs_present(store, manifest.section_artifacts)
            ):
                return ActiveResearchArtifactResult(
                    status=ActiveResearchStatus.COMPLETED,
                    section_artifacts=manifest.section_artifacts,
                    manifest_artifact=self._artifact_ref(store, manifest_key),
                    rounds=manifest.rounds,
                )
            if manifest.failure is not None:
                return ActiveResearchArtifactResult(
                    status=manifest.status,
                    section_artifacts={},
                    manifest_artifact=self._artifact_ref(store, manifest_key),
                    rounds=manifest.rounds,
                    failure=manifest.failure,
                )

        as_of = date.fromisoformat(as_of_date)
        pending: list[str] = [k.value for k in targets if k.value in _ACTIVE_KINDS]
        section_artifacts: dict[str, ArtifactRef] = {}
        rounds = 0
        while pending and rounds < self._max_rounds:
            rounds += 1
            queries = self._plan(company, pending, as_of)
            if not queries:
                break
            pages = self._collect_pages(queries, as_of, store=store)
            if not pages:
                break
            extracts = self._extract(pages)
            if not extracts:
                break
            written = self._assemble_and_write(store, extracts, pages, as_of_date)
            section_artifacts.update(written)
            pending = [k for k in pending if k not in section_artifacts]

        if not section_artifacts:
            return self._write_failure(
                store,
                manifest_key,
                company,
                cik,
                as_of_date,
                ActiveResearchStatus.FETCH_FAILED,
                ActiveResearchFailure(
                    error_code=ErrorCode.UPSTREAM_5XX,
                    message="主动研究未产出可用证据",
                    is_retryable=True,
                ),
                rounds=rounds,
            )

        completed = ActiveResearchManifest(
            company=company,
            cik=cik,
            as_of_date=as_of_date,
            status=ActiveResearchStatus.COMPLETED,
            section_artifacts=section_artifacts,
            rounds=rounds,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(completed), overwrite=True)
        return ActiveResearchArtifactResult(
            status=ActiveResearchStatus.COMPLETED,
            section_artifacts=section_artifacts,
            manifest_artifact=manifest_ref,
            rounds=rounds,
        )

    # ------------------------------------------------------------------
    # 受控工具循环步骤
    # ------------------------------------------------------------------

    def _plan(
        self, company: str, pending: list[str], as_of: date
    ) -> tuple[tuple[str, str], ...]:
        """LLM 决策本轮查询词 → 返回 [(kind, query), ...]。"""
        user = (
            f"公司: {company}\n数据截止日: {as_of.isoformat()}\n"
            f"需要研究的方向: {', '.join(pending)}\n"
            "请给出不超过 4 条精准的英文搜索查询覆盖这些方向。只输出 JSON："
            '{"queries":[{"query":"<英文查询>","kind":"<方向>"}]}'
        )
        result = self._completion.complete(
            role=LLMRole.RESEARCH, system_prompt=_PLAN_SYSTEM_PROMPT, user_prompt=user
        )
        parsed = _extract_json_object(result.markdown)
        if parsed is None:
            return ()
        queries: list[tuple[str, str]] = []
        for raw in (parsed.get("queries") or [])[: self._max_queries_per_round]:
            if not isinstance(raw, dict):
                continue
            query = str(raw.get("query") or "").strip()
            kind = str(raw.get("kind") or "").strip()
            if query and kind in pending:
                queries.append((kind, query))
        return tuple(queries)

    def _collect_pages(
        self,
        queries: tuple[tuple[str, str], ...],
        as_of: date,
        *,
        store: ArtifactStore | None = None,
    ) -> tuple[tuple[str, str, str, str, str, date | None, str], ...]:
        """执行搜索 → 选 URL → 下载 → 解析 →（可选）留快照，返回网页文本。

        返回元素：(kind, url, title, snippet, publisher, published_at, text)。
        """
        pages: list[tuple[str, str, str, str, str, date | None, str]] = []
        seen_urls: set[str] = set()
        for kind, query in queries:
            response = self._search_tool.execute(
                SearchQuery(query=query, as_of=as_of, page_size=10)
            )
            if isinstance(response, ToolFailure):
                continue
            for item in response.value.items:
                if item.url in seen_urls or len(pages) >= self._max_pages_to_read:
                    continue
                if item.published_at is not None and item.published_at > as_of:
                    continue
                seen_urls.add(item.url)
                text = self._read_page(item.url)
                if text:
                    if store is not None:
                        self._write_snapshot(store, item.url, item.title, as_of, text)
                    pages.append(
                        (
                            kind,
                            item.url,
                            item.title,
                            item.snippet or "",
                            item.publisher or "",
                            item.published_at,
                            text,
                        )
                    )
                    if len(pages) >= self._max_pages_to_read:
                        break
        return tuple(pages)

    def _write_snapshot(
        self, store: ArtifactStore, url: str, title: str, accessed_at: date, text: str
    ) -> str | None:
        """把已解析网页原文写成快照工件（内容级溯源证据）；失败返回 None（不阻塞）。"""
        try:
            key = f"{_PAGES_PREFIX}/{hashlib.sha256(url.encode()).hexdigest()}.json"
            store.write(
                key,
                json.dumps(
                    {
                        "url": url,
                        "accessed_at": accessed_at.isoformat(),
                        "title": title or "",
                        "text": text,
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
                overwrite=False,
            )
            return key
        except Exception:  # noqa: BLE001 - 快照落盘尽力而为，不阻塞主动研究
            return None

    def _read_page(self, url: str) -> str | None:
        """下载网页并解析为纯文本（best-effort；失败返回 None）。"""
        downloaded = self._downloader.execute(DownloadRequest(url=url))
        if isinstance(downloaded, ToolFailure):
            return None
        try:
            outcome: ParseOutcome = self._parser(
                downloaded.value.content, downloaded.value.media_type
            )
        except Exception:  # noqa: BLE001 - 单个网页解析失败不阻塞
            return None
        document = outcome.document
        blocks = getattr(document, "blocks", ()) or ()
        text = "\n".join(str(getattr(block, "text", "")) for block in blocks)
        return text[: self._max_context_chars]

    def _extract(
        self, pages: tuple[tuple[str, str, str, str, str, date | None, str], ...]
    ) -> tuple[tuple[str, str, str], ...]:
        """LLM 从网页文本提炼摘要 → 返回 [(kind, url, excerpt), ...]。"""
        blocks = []
        for kind, url, title, snippet, _, _, text in pages:
            blocks.append(f"--- [{kind}] {url}\n{title}\n{snippet}\n{text[:2000]}")
        user = (
            "以下是搜索到的网页内容，请为每个方向提炼不超过 150 字的中文摘要。只输出 JSON：\n"
            '{"extracts":[{"kind":"<方向>","url":"<原始URL>","excerpt":"<中文摘要>"}]}\n\n'
            + "\n\n".join(blocks)
        )
        result = self._completion.complete(
            role=LLMRole.RESEARCH, system_prompt=_EXTRACT_SYSTEM_PROMPT, user_prompt=user
        )
        parsed = _extract_json_object(result.markdown)
        if parsed is None:
            return ()
        extracts: list[tuple[str, str, str]] = []
        for raw in (parsed.get("extracts") or []):
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "").strip()
            url = str(raw.get("url") or "").strip()
            excerpt = str(raw.get("excerpt") or "").strip()
            if kind and url and excerpt:
                extracts.append((kind, url, excerpt))
        return tuple(extracts)

    def _assemble_and_write(
        self,
        store: ArtifactStore,
        extracts: tuple[tuple[str, str, str], ...],
        pages: tuple[tuple[str, str, str, str, str, date | None, str], ...],
        as_of_date: str,
    ) -> dict[str, ArtifactRef]:
        """按 kind 组装 WebSearchSectionEvidence 工件并写盘；丢弃编造 URL 的摘要。"""
        url_info: dict[str, tuple[str, str, str, date | None]] = {
            url: (title, snippet, publisher, published_at)
            for _, url, title, snippet, publisher, published_at, _ in pages
        }
        groups: dict[str, list[WebSearchEntry]] = {}
        for kind, url, excerpt in extracts:
            if url not in url_info:
                continue  # 防编造来源：URL 必须来自真实搜索/下载到的页面
            title, snippet, publisher, published_at = url_info[url]
            groups.setdefault(kind, []).append(
                WebSearchEntry(
                    title=title,
                    url=url,
                    publisher=publisher or None,
                    published_at=published_at,
                    accessed_at=date.today(),
                    snippet=excerpt,
                    credibility=_credibility(url, publisher),
                )
            )
        written: dict[str, ArtifactRef] = {}
        for kind, entries in groups.items():
            if kind not in _ACTIVE_KINDS:
                continue
            section = WebSearchSectionEvidence(
                kind=kind,
                as_of_date=as_of_date,
                note=_NOTE_BY_KIND.get(kind),
                entries=tuple(entries),
            )
            written[kind] = store.write(
                f"{_PREFIX}/{kind}.json", self._json_bytes(section), overwrite=True
            )
        return written

    # ------------------------------------------------------------------
    # 工件辅助（复用 Company Facts / WebSearch 管道模式）
    # ------------------------------------------------------------------

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
        manifest: ActiveResearchManifest, company: str, cik: str, as_of_date: str
    ) -> bool:
        return (
            manifest.company == company
            and manifest.cik == cik
            and manifest.as_of_date == as_of_date
        )

    def _read_manifest(self, store: ArtifactStore, key: str) -> ActiveResearchManifest | None:
        try:
            return ActiveResearchManifest.model_validate_json(store.read(key))
        except (KeyError, ValueError):
            return None

    def _write_failure(
        self,
        store: ArtifactStore,
        manifest_key: str,
        company: str,
        cik: str,
        as_of_date: str,
        status: ActiveResearchStatus,
        failure: ActiveResearchFailure,
        *,
        rounds: int,
    ) -> ActiveResearchArtifactResult:
        manifest = ActiveResearchManifest(
            company=company,
            cik=cik,
            as_of_date=as_of_date,
            status=status,
            rounds=rounds,
            failure=failure,
        )
        manifest_ref = store.write(manifest_key, self._json_bytes(manifest), overwrite=True)
        return ActiveResearchArtifactResult(
            status=status,
            section_artifacts={},
            manifest_artifact=manifest_ref,
            rounds=rounds,
            failure=failure,
        )


__all__ = [
    "ActiveResearchAgent",
    "ActiveResearchArtifactResult",
    "ActiveResearchFailure",
    "ActiveResearchManifest",
    "ActiveResearchStatus",
    "_ACTIVE_KINDS",
]
