"""P07-10B：年度章节的受限 LLM 执行器。

该模块刻意不复用 legacy WriterContextBuilder：年度章节只能看到
``SectionInputPack`` 授权的证据。模型调用是普通 OpenAI-compatible chat
completion，不提供 tools，也不允许模型检索或计算财务指标。
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from invest_research.agents.llm_factory import LLMConfig, LLMRole
from invest_research.application.analysis_assembler import build_fact_ref
from invest_research.application.report_draft_assembler import build_source_citation_key
from invest_research.domain.annual_finalization import AnnualFinalizationPack
from invest_research.domain.annual_pipeline import EvidenceArtifact
from invest_research.domain.annual_sections import (
    AnnualSectionKind,
    SectionAnalysisPack,
    SectionDraft,
    SectionInputPack,
    SectionWorkStatus,
)
from invest_research.domain.models import CompanyIdentity
from invest_research.infrastructure.annual_document_pipeline import AnnualParsedDocument
from invest_research.infrastructure.annual_web_search_pipeline import WebSearchSectionEvidence
from invest_research.infrastructure.observability.metrics_events import (
    count_llm_request,
    count_llm_tokens,
    count_llm_usage_missing,
    observe_llm_duration,
)
from invest_research.reporting.annual_report_renderer import (
    AnnualOfficialStatements,
    OfficialStatement,
    extract_document_title,
    extract_mda,
)
from invest_research.tools.artifact_store import ArtifactStore

__all__ = [
    "AnnualLlmCallError",
    "AnnualLlmDispatcher",
    "AnnualLlmResult",
    "AnnualSectionExecutor",
]

_PROMPT_VERSION = "annual_llm_writing_v1"
# 与 annual_evidence_fanout._web_evidence 的 parser_version 对齐：用于识别
# 网页搜索工件（区别于 SEC 10-K 解析工件），走独立的逐条 [src_] 引用分支。
_WEB_SEARCH_PARSER_VERSION = "annual_web_search_section_v1"
_MAX_CONTEXT_CHARS = 12_000
# 官方声明翻译：每段原文输入上限（超出按节选截断，best-effort）。
_OFFICIAL_TRANSLATE_MAX_CHARS = 1_500
# MD&A 摘译：喂给 LLM 的原文上限（原文可能几千字，取有界前段挑重点）。
_MDA_SUMMARIZE_MAX_CHARS = 4_000
# MD&A 摘译最低字符量：低于此值视为模型只给了元说明/偷懒，回退原文直取。
_MDA_SUMMARY_MIN_CHARS = 100
# Item/MD&A 标题块命中后连带纳入的后续正文块数量（捕获长段落如管理层讨论）。
_BLOCK_SPAN = 5
_MAX_OUTPUT_TOKENS = 8_000
_CITATION_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9_]*)\]")
_FORBIDDEN_ADVICE_RE = re.compile(
    r"(?:建议买入|建议卖出|目标价|建仓|加仓|减仓|持仓比例|guaranteed return)", re.I
)
_TRUNCATED_TAILS = ("...", "……", "（未完", "待续", "to be continued")


def _usage_from_response(usage: Any) -> dict[str, int | None]:
    """提取 OpenAI-compatible 的真实 usage；缺失字段保持 ``None``。"""

    def value(name: str, *aliases: str) -> int | None:
        raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if raw is None:
            for alias in aliases:
                raw = usage.get(alias) if isinstance(usage, dict) else getattr(usage, alias, None)
                if raw is not None:
                    break
        return raw if isinstance(raw, int) and raw >= 0 else None

    details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    )
    cached = (
        details.get("cached_tokens")
        if isinstance(details, dict)
        else getattr(details, "cached_tokens", None)
    )
    return {
        "prompt_tokens": value("prompt_tokens", "input_tokens"),
        "completion_tokens": value("completion_tokens", "output_tokens"),
        "cached_prompt_tokens": cached if isinstance(cached, int) and cached >= 0 else None,
        "total_tokens": value("total_tokens"),
    }


class AnnualLlmCallError(RuntimeError):
    """年度 LLM 输出不满足受控写作边界。"""

    failure_stage = "annual_writing"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AnnualLlmResult:
    markdown: str
    finish_reason: str = "stop"
    duration_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    total_tokens: int | None = None


class AnnualCompletion(Protocol):
    def complete(
        self,
        *,
        role: LLMRole,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = _MAX_OUTPUT_TOKENS,
    ) -> AnnualLlmResult: ...


class AnnualLlmDispatcher:
    """年度专用、无工具的 OpenAI-compatible 调用边界。

    ``requests`` 只记录脱敏元数据，便于测试证明没有 tools 或原始正文被记录。
    """

    def __init__(
        self, config: LLMConfig, *, client_factory: Callable[[LLMRole], Any] | None = None
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._clients: dict[LLMRole, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self._performance: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def complete(
        self,
        *,
        role: LLMRole,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = _MAX_OUTPUT_TOKENS,
    ) -> AnnualLlmResult:
        role_cfg = self._config.config_for(role)
        kwargs: dict[str, Any] = {
            "model": role_cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": role_cfg.temperature,
        }
        if role_cfg.enable_thinking is not None and role_cfg.vendor == "deepseek":
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if role_cfg.enable_thinking else "disabled"}
            }
        request_record = {
            "role": role.value,
            "model": role_cfg.model,
            "tools": None,
            "tool_choice": None,
            "messages": [
                {"role": "system", "chars": len(system_prompt)},
                {"role": "user", "chars": len(user_prompt)},
            ],
            "max_tokens": max_tokens,
        }
        with self._lock:
            self.requests.append(request_record)
        started = time.monotonic()
        try:
            response = self._client_for(role).chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - stable boundary error
            duration = time.monotonic() - started
            self._record_performance(
                role=role,
                model=role_cfg.model,
                provider=role_cfg.vendor,
                duration_s=duration,
                status="failure",
            )
            raise AnnualLlmCallError(
                "ANNUAL_LLM_CALL_FAILED", f"年度 LLM 调用失败: {type(exc).__name__}: {exc}"
            ) from exc
        duration = time.monotonic() - started
        choice = response.choices[0] if getattr(response, "choices", None) else None
        content = getattr(getattr(choice, "message", None), "content", "") if choice else ""
        finish = str(getattr(choice, "finish_reason", "stop") or "stop") if choice else "stop"
        text = content if isinstance(content, str) else ""
        usage = _usage_from_response(getattr(response, "usage", None))
        with self._lock:
            request_record.update({"finish_reason": finish, "output_chars": len(text)})
        self._record_performance(
            role=role,
            model=role_cfg.model,
            provider=role_cfg.vendor,
            duration_s=duration,
            status="success",
            usage=usage,
        )
        return AnnualLlmResult(
            markdown=text,
            finish_reason=finish,
            duration_s=duration,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            cached_prompt_tokens=usage["cached_prompt_tokens"],
            total_tokens=usage["total_tokens"],
        )

    def performance_summary(self) -> dict[str, object]:
        """返回可写入年度 Manifest 的脱敏调用汇总，不保存 Prompt 或正文。"""
        with self._lock:
            calls = list(self._performance)
        successful = [item for item in calls if item["status"] == "success"]
        complete_usage = bool(successful) and all(
            isinstance(item.get("prompt_tokens"), int)
            and isinstance(item.get("completion_tokens"), int)
            and isinstance(item.get("total_tokens"), int)
            for item in successful
        )
        usage: dict[str, int | None] = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "cached_prompt_tokens": None,
            "total_tokens": None,
        }
        if complete_usage:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] = sum(int(item[key]) for item in successful)
            cached = [item.get("cached_prompt_tokens") for item in successful]
            usage["cached_prompt_tokens"] = (
                sum(int(value) for value in cached if isinstance(value, int))
                if all(isinstance(value, int) for value in cached)
                else None
            )
        return {
            "llm_calls": len(calls),
            "llm_duration_seconds": round(sum(float(item["duration_s"]) for item in calls), 6),
            "models": {
                role.value: self.model_for(role)
                for role in (LLMRole.ANALYSIS, LLMRole.WRITER)
            },
            "token_usage": usage,
            "token_usage_complete": complete_usage,
            "usage_missing_calls": sum(
                1
                for item in successful
                if not isinstance(item.get("total_tokens"), int)
            ),
        }

    def _record_performance(
        self,
        *,
        role: LLMRole,
        model: str,
        provider: str,
        duration_s: float,
        status: str,
        usage: dict[str, int | None] | None = None,
    ) -> None:
        count_llm_request(provider, model, role.value, status)
        observe_llm_duration(provider, model, role.value, status, duration_s)
        item: dict[str, Any] = {
            "role": role.value,
            "model": model,
            "status": status,
            "duration_s": round(duration_s, 6),
        }
        if usage is not None:
            item.update(usage)
            if usage["total_tokens"] is None:
                count_llm_usage_missing(provider, model, role.value)
            else:
                for token_type, field in (
                    ("input", "prompt_tokens"),
                    ("output", "completion_tokens"),
                    ("cached_input", "cached_prompt_tokens"),
                ):
                    value = usage[field]
                    if isinstance(value, int):
                        count_llm_tokens(provider, model, role.value, token_type, value)
        with self._lock:
            self._performance.append(item)

    def model_for(self, role: LLMRole) -> str:
        """仅供工件 Manifest 记录模型名；不暴露密钥或 prompt。"""
        return self._config.config_for(role).model

    def _client_for(self, role: LLMRole) -> Any:
        if role in self._clients:
            return self._clients[role]
        if self._client_factory is not None:
            client = self._client_factory(role)
        else:
            from openai import OpenAI

            cfg = self._config.config_for(role)
            client = OpenAI(
                base_url=cfg.base_url,
                api_key=cfg.api_key.get_secret_value(),
                timeout=cfg.timeout,
            )
        self._clients[role] = client
        return client


@dataclass(frozen=True)
class _CitationMap:
    allowed_keys: frozenset[str]
    artifacts_by_key: dict[str, frozenset[str]]
    source_entries: tuple[tuple[str, str], ...] = ()

    def artifact_keys_for(self, citation_keys: Iterable[str]) -> tuple[str, ...]:
        out: list[str] = []
        for key in citation_keys:
            for artifact_key in self.artifacts_by_key.get(key, frozenset()):
                if artifact_key not in out:
                    out.append(artifact_key)
        return tuple(out)


class AnnualSectionExecutor:
    """构造最小上下文并执行财务/叙事章节与受限最终编辑。"""

    def __init__(
        self,
        artifact_root: Path,
        completion: AnnualCompletion,
        *,
        max_context_chars: int = _MAX_CONTEXT_CHARS,
    ) -> None:
        self._artifact_root = artifact_root
        self._completion = completion
        self._max_context_chars = max_context_chars

    def analyze_financial(self, pack: SectionInputPack) -> SectionAnalysisPack:
        if pack.section is not AnnualSectionKind.FINANCIAL_PERFORMANCE:
            raise ValueError("财务分析只能消费财务章节输入")
        if pack.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
            return SectionAnalysisPack(
                section_input=pack, status=SectionWorkStatus.BLOCKED, limitations=pack.limitations
            )
        assert pack.comparison_pack is not None
        citations = self._financial_citations(pack)
        result = self._completion.complete(
            role=LLMRole.ANALYSIS,
            system_prompt=(
                "你是财务分析助手。只解释给定的 SEC/XBRL 确定性指标，不得重新计算、"
                "改写数值、补充外部信息或给出投资建议。每个结论必须使用给定 [fr_] 引用。"
                "只输出中文 Markdown 段落。"
            ),
            user_prompt=self._financial_analysis_prompt(pack, citations),
        )
        keys = self._validate_output(
            result,
            citations.allowed_keys,
            require_heading=False,
            allow_missing_citations=True,
        )
        return SectionAnalysisPack(
            section_input=pack,
            status=pack.status,
            analysis_notes=result.markdown.strip(),
            citation_artifact_keys=citations.artifact_keys_for(keys),
            limitations=pack.limitations,
        )

    def write_financial(
        self, pack: SectionInputPack, analysis: SectionAnalysisPack
    ) -> SectionDraft:
        citations = self._financial_citations(pack)
        result = self._completion.complete(
            role=LLMRole.WRITER,
            system_prompt=(
                "你是年度投研报告撰写者。只依据输入的确定性指标和分析说明写‘财务表现’"
                "章节；数值、公式状态不得改写或计算。必须使用已给出的 [fr_] 引用，"
                "不得给投资建议。只输出 Markdown。"
            ),
            user_prompt=(
                "# 章节\n财务表现\n\n# 财务分析说明\n"
                f"{analysis.analysis_notes or ''}\n\n# 指标与引用\n"
                f"{self._financial_analysis_prompt(pack, citations)}\n\n"
                "必须以 `## 财务表现` 开头。"
            ),
        )
        keys = self._validate_output(
            result,
            citations.allowed_keys,
            require_heading=True,
            allow_missing_citations=True,
        )
        return SectionDraft(
            section=pack.section,
            status=pack.status,
            markdown=result.markdown.strip(),
            citation_artifact_keys=citations.artifact_keys_for(keys),
            financial_analysis=analysis,
            limitations=pack.limitations,
        )

    def write_narrative(self, *, job_id: str, pack: SectionInputPack) -> SectionDraft:
        if pack.section is AnnualSectionKind.FINANCIAL_PERFORMANCE:
            raise ValueError("叙事 Writer 不得消费财务章节")
        if pack.status in {SectionWorkStatus.BLOCKED, SectionWorkStatus.NOT_APPLICABLE}:
            return SectionDraft(
                section=pack.section,
                status=pack.status,
                section_input=pack,
                limitations=pack.limitations,
            )
        citations, excerpts = self._narrative_context(job_id, pack)
        heading = _heading_for(pack.section)
        result = self._completion.complete(
            role=LLMRole.WRITER,
            system_prompt=(
                "你是年度投研报告撰写者。只使用用户提供的已验证 SEC 文本块，"
                "不可调用工具、不可补充常识或外部资料。每个事实性段落必须附合法 [src_] 引用；"
                "不得进行投资建议。只输出中文 Markdown。"
            ),
            user_prompt=(
                f"# 章节\n{heading}\n\n# 限制\n{_lines(pack.limitations)}\n\n"
                f"# 已授权证据（不得使用列表外资料）\n{excerpts}\n\n"
                f"必须以 `## {heading}` 开头。"
            ),
        )
        keys = self._validate_output(
            result,
            citations.allowed_keys,
            require_heading=True,
            allow_missing_citations=True,
        )
        return SectionDraft(
            section=pack.section,
            status=pack.status,
            markdown=result.markdown.strip(),
            citation_artifact_keys=citations.artifact_keys_for(keys),
            section_input=pack,
            limitations=pack.limitations,
        )

    def edit_final(
        self,
        *,
        finalization: AnnualFinalizationPack,
        identity: CompanyIdentity,
    ) -> str:
        """Final Writer 只接受已通过门禁的章节文本，不再读取工件。"""
        allowed = frozenset(
            key
            for section in finalization.sections
            for key in _extract_citations(section.markdown or "")
        )
        if not allowed:
            raise AnnualLlmCallError("ANNUAL_FINAL_NO_CITATIONS", "最终编辑输入没有章节引用")
        sections = "\n\n".join(
            section.markdown or "" for section in finalization.sections if section.markdown
        )
        result = self._completion.complete(
            role=LLMRole.WRITER,
            system_prompt=(
                "你是报告编辑。只能重组、润色和衔接已提供章节，不能引入新事实、数字、"
                "引用或投资建议。必须保留全部已有引用和每个可交付章节。只输出 Markdown。"
            ),
            user_prompt=(
                f"# 公司\n{identity.legal_name}\n\n# 已通过门禁的章节\n{sections}\n\n"
                f"# 数据限制\n{_lines(finalization.limitations)}\n\n"
                "输出必须包含 `## 执行摘要`，并保留原有章节标题与全部引用。"
            ),
            max_tokens=10_000,
        )
        keys = self._validate_output(result, allowed, require_heading=False)
        if set(keys) != set(allowed):
            raise AnnualLlmCallError(
                "ANNUAL_FINAL_CITATION_LOSS", "Final Writer 删除或新增了章节引用"
            )
        if "执行摘要" not in result.markdown:
            raise AnnualLlmCallError("ANNUAL_FINAL_STRUCTURE_INVALID", "Final Writer 缺少执行摘要")
        for section in finalization.sections:
            if section.markdown and _heading_for(section.section) not in result.markdown:
                raise AnnualLlmCallError(
                    "ANNUAL_FINAL_STRUCTURE_INVALID", "Final Writer 缺少章节标题"
                )
        return result.markdown.strip()

    def source_entries(self, artifacts: Iterable[EvidenceArtifact]) -> tuple[tuple[str, str], ...]:
        entries: list[tuple[str, str]] = []
        for artifact in artifacts:
            key = build_source_citation_key(_source_for_artifact(artifact))
            entry = (key, artifact.source_url)
            if entry not in entries:
                entries.append(entry)
        return tuple(entries)

    def source_entries_with_titles(
        self,
        artifacts: Iterable[EvidenceArtifact],
        *,
        store: ArtifactStore | None = None,
    ) -> tuple[tuple[str, str | None, str], ...]:
        """返回 ``(src_key, title, url)`` 三元组，供来源清单带标题+链接渲染。

        - key 复用 ``build_source_citation_key``（唯一算法，与旧 ``source_entries`` 一致）；
        - title 为 best-effort：网页工件取 ``WebSearchEntry.title``，SEC 解析工件走
          ``extract_document_title`` 启发式；读取失败或类型不匹配返回 None（回退短标签）；
        - ``store is None`` 时标题一律 None，不抛异常。
        """
        entries: list[tuple[str, str | None, str]] = []
        seen: set[tuple[str, str]] = set()
        for artifact in artifacts:
            key = build_source_citation_key(_source_for_artifact(artifact))
            pair = (key, artifact.source_url)
            if pair in seen:
                continue
            seen.add(pair)
            title = self._artifact_title(artifact, store)
            entries.append((key, title, artifact.source_url))
        return tuple(entries)

    def translate_official_statements(
        self, statements: AnnualOfficialStatements
    ) -> AnnualOfficialStatements:
        """best-effort 把官方声明原文翻译成中文（中英对照）。

        - 一次 ``complete`` 调用合并封面页/审计意见/302 原文，要求按 ``【label】``
          分段输出中文；解析失败或调用失败时原样返回（保留英文，不阻塞发布）；
        - 输入截断到有界长度，避免超出模型上下文。
        """
        entries: list[tuple[str, OfficialStatement]] = [
            (label, getattr(statements, attr))
            for label, attr in (
                ("封面页", "cover_page"),
                ("独立审计意见", "audit_opinion"),
                ("302 认证", "certification_302"),
            )
            if getattr(statements, attr) is not None
        ]
        if not entries:
            return statements
        labeled_source = "\n\n".join(
            f"【{label}】\n{_truncate(stmt.text, _OFFICIAL_TRANSLATE_MAX_CHARS)}"
            for label, stmt in entries
        )
        try:
            result = self._completion.complete(
                role=LLMRole.WRITER,
                system_prompt=(
                    "你是证券申报文件翻译。把给定的英文官方声明逐字忠实翻译成中文，"
                    "保留公司名、数字、法律术语；不得增删内容、不得改写成自己的话、"
                    "不得输出英文原文。按输入顺序用【封面页】/【独立审计意见】/"
                    "【302 认证】标记分段，只输出中文翻译。"
                ),
                user_prompt=f"# 需翻译的英文官方声明\n{labeled_source}",
                max_tokens=4_000,
            )
        except AnnualLlmCallError:
            return statements
        translated = _parse_translated_statements(
            result.markdown, [label for label, _ in entries]
        )
        if not translated:
            return statements
        by_label = dict(zip([label for label, _ in entries], translated))
        return AnnualOfficialStatements(
            cover_page=_with_translation(statements.cover_page, by_label.get("封面页")),
            audit_opinion=_with_translation(
                statements.audit_opinion, by_label.get("独立审计意见")
            ),
            certification_302=_with_translation(
                statements.certification_302, by_label.get("302 认证")
            ),
        )

    def summarize_mda(self, blocks: Iterable[Any]) -> str:
        """LLM 分析 10-K Item 7 管理层讨论挑重点，输出 300-500 字中文摘译。

        - 复用 ``extract_mda`` 取有界原文，输入再截到有界长度；
        - prompt 明确要求**实质要点**（禁止元叙述/开场白）并给**下限字数**，防止模型偷懒；
        - 输出过短（<100 字，模型只给元说明）返回空串，由调用方回退原文直取节选。
        """
        mda = extract_mda(blocks)
        if mda is None:
            return ""
        source_text = mda.text[:_MDA_SUMMARIZE_MAX_CHARS]
        result = self._completion.complete(
            role=LLMRole.ANALYSIS,
            system_prompt=(
                "你是财报分析师。阅读管理层讨论与分析（MD&A）原文节选，提炼最重要的"
                "3-5 个业务与财务要点，用中文写出 300-500 字的要点说明。直接给出要点"
                "内容（可用 `- ` 列表），不要解释“本节是管理层讨论与分析”、不要"
                "开场白或元叙述。只使用原文中出现的事实和数字，不得编造、不得补充"
                "外部信息、不得给出投资建议。只输出中文要点。"
            ),
            user_prompt=f"# 管理层讨论与分析原文节选\n{source_text}",
            max_tokens=2_000,
        )
        summary = (result.markdown or "").strip()
        if len(summary) < _MDA_SUMMARY_MIN_CHARS:
            return ""
        return summary

    def _artifact_title(
        self, artifact: EvidenceArtifact, store: ArtifactStore | None
    ) -> str | None:
        """从工件字节解析来源名；任何失败（KeyError/校验失败）返回 None（best-effort）。"""
        if store is None:
            return None
        try:
            content = store.read(artifact.artifact_key)
        except KeyError:
            return None
        if artifact.parser_version == _WEB_SEARCH_PARSER_VERSION:
            try:
                web = WebSearchSectionEvidence.model_validate_json(content)
            except ValueError:
                return None
            return web.entries[0].title if web.entries else None
        try:
            parsed = AnnualParsedDocument.model_validate_json(content)
        except ValueError:
            return None
        return extract_document_title(parsed.blocks)

    def manifest_metadata(self, pack: SectionInputPack) -> dict[str, object]:
        """章节工件可审计的非敏感输入摘要。"""
        model_for = getattr(self._completion, "model_for", None)
        if callable(model_for):
            roles = (
                (LLMRole.ANALYSIS, LLMRole.WRITER)
                if pack.section is AnnualSectionKind.FINANCIAL_PERFORMANCE
                else (LLMRole.WRITER,)
            )
            models = {role.value: str(model_for(role)) for role in roles}
        else:
            models = {"mode": "injected_test_or_adapter"}
        return {
            "prompt_version": _PROMPT_VERSION,
            "models": models,
            "allowed_artifact_keys": sorted(pack.allowed_artifact_keys),
            "section_status": pack.status.value,
        }

    def performance_summary(self) -> dict[str, object]:
        """委托给支持观测的 completion；测试替身保持空、非伪造语义。"""
        summary = getattr(self._completion, "performance_summary", None)
        if callable(summary):
            value = summary()
            if isinstance(value, dict):
                return value
        return {
            "llm_calls": 0,
            "llm_duration_seconds": 0.0,
            "models": {},
            "token_usage": {
                "prompt_tokens": None,
                "completion_tokens": None,
                "cached_prompt_tokens": None,
                "total_tokens": None,
            },
            "token_usage_complete": False,
            "usage_missing_calls": 0,
        }

    def _financial_citations(self, pack: SectionInputPack) -> _CitationMap:
        assert pack.comparison_pack is not None
        artifacts = tuple(dict.fromkeys(item.artifact_key for item in pack.evidence_artifacts))
        if not artifacts:
            raise AnnualLlmCallError("ANNUAL_FINANCIAL_EVIDENCE_MISSING", "财务章节没有事实集工件")
        mapping: dict[str, frozenset[str]] = {}
        for fact in pack.comparison_pack.facts:
            mapping[build_fact_ref(fact)] = frozenset(artifacts)
        if not mapping:
            raise AnnualLlmCallError("ANNUAL_FINANCIAL_FACTS_MISSING", "财务章节没有可引用事实")
        return _CitationMap(allowed_keys=frozenset(mapping), artifacts_by_key=mapping)

    def _financial_analysis_prompt(self, pack: SectionInputPack, citations: _CitationMap) -> str:
        assert pack.comparison_pack is not None
        facts = []
        for fact in pack.comparison_pack.facts:
            key = build_fact_ref(fact)
            period = fact.period_end or fact.instant_date
            facts.append(
                f"[{key}] concept={fact.concept}; value={fact.value}; unit={fact.unit}; "
                f"period={period}"
            )
        metrics = []
        for metric in pack.comparison_pack.metrics:
            metrics.append(
                f"metric={metric.metric_name}; value={metric.value}; unit={metric.unit}; "
                f"status={metric.status.value}; formula={metric.formula_version}; "
                f"reason={metric.explanation or ''}"
            )
        return (
            "# 限制\n"
            f"{_lines(pack.limitations)}\n\n# 确定性指标（不得修改）\n{_lines(metrics)}\n\n"
            f"# 可引用原始事实\n{_lines(facts)}\n\n"
            f"# 允许引用\n{', '.join(sorted(citations.allowed_keys))}"
        )

    def _narrative_context(self, job_id: str, pack: SectionInputPack) -> tuple[_CitationMap, str]:
        store = ArtifactStore(self._artifact_root / job_id)
        mapping: dict[str, frozenset[str]] = {}
        excerpts: list[str] = []
        remaining = self._max_context_chars
        for artifact in pack.evidence_artifacts:
            content = store.read(artifact.artifact_key)
            if hashlib.sha256(content).hexdigest() != artifact.content_checksum:
                raise AnnualLlmCallError(
                    "ANNUAL_ARTIFACT_CHECKSUM_MISMATCH", "章节证据 checksum 不匹配"
                )
            if artifact.parser_version == _WEB_SEARCH_PARSER_VERSION:
                remaining = self._web_search_context(
                    artifact, content, excerpts, remaining, mapping
                )
                if remaining <= 0:
                    break
                continue
            parsed = AnnualParsedDocument.model_validate_json(content)
            citation = build_source_citation_key(_source_for_artifact(artifact))
            mapping[citation] = frozenset({artifact.artifact_key})
            for block in self._select_blocks(pack.section, parsed):
                if remaining <= 0:
                    break
                text = block.text[: min(len(block.text), remaining)]
                excerpts.append(f"[{citation}] locator={block.locator}\n{text}")
                remaining -= len(text)
            if remaining <= 0:
                break
        if not excerpts:
            raise AnnualLlmCallError("ANNUAL_NARRATIVE_CONTEXT_EMPTY", "已授权叙事证据没有可用文本")
        return _CitationMap(frozenset(mapping), mapping), "\n\n".join(excerpts)

    def _web_search_context(
        self,
        artifact: EvidenceArtifact,
        content: bytes,
        excerpts: list[str],
        remaining: int,
        mapping: dict[str, frozenset[str]],
    ) -> int:
        """把授权给本章节的网页搜索摘要渲染为带独立 [src_] 引用的文本块。

        每条搜索结果生成独立 ``src_<hash>``（基于其 URL）；搜索工件损坏时
        降级跳过（不阻塞，10-K 原文仍在）。
        """
        try:
            section = WebSearchSectionEvidence.model_validate_json(content)
        except ValueError:
            return remaining
        for entry in section.entries:
            if remaining <= 0:
                break
            citation = build_source_citation_key(_source_for_entry(entry))
            mapping[citation] = frozenset({artifact.artifact_key})
            note = f"（{section.note}）" if section.note else ""
            text = f"{entry.title}。{entry.snippet}{note}"
            excerpts.append(f"[{citation}] locator={entry.url}\n{text}")
            remaining -= len(text)
        return remaining

    @staticmethod
    def _select_blocks(section: AnnualSectionKind, parsed: AnnualParsedDocument) -> tuple[Any, ...]:
        # 全文级选择：业务概览纳入 MD&A（Item 7/7A）与展望段落；Item/MD&A 标题块
        # 命中后连同紧随的正文块一起纳入，捕获长段落（如管理层讨论）。
        keywords = {
            AnnualSectionKind.BUSINESS_OVERVIEW: (
                "item 1",
                "item 7",
                "item 7a",
                "management's discussion",
                "mda",
                "business",
                "业务",
                "product",
                "customer",
                "strategy",
                "outlook",
                "展望",
            ),
            AnnualSectionKind.RISK_FACTORS: (
                "item 1a",
                "item 7",
                "risk",
                "风险",
                "cyber",
                "competition",
                "litigation",
            ),
            AnnualSectionKind.MATERIAL_EVENTS: (
                "material",
                "重大",
                "item 1.05",
                "item 2.0",
                "event",
            ),
        }[section]
        blocks = parsed.blocks
        selected: list[Any] = []
        seen: set[int] = set()
        for index, block in enumerate(blocks):
            if index in seen:
                continue
            lowered = block.text.lower()
            if not any(word in lowered for word in keywords):
                continue
            selected.append(block)
            seen.add(index)
            if lowered.lstrip().startswith(("item", "mda", "management")):
                # Item/MD&A 标题块：把紧随其后的正文块一并纳入。
                for j in range(index + 1, min(index + 1 + _BLOCK_SPAN, len(blocks))):
                    if j in seen:
                        continue
                    selected.append(blocks[j])
                    seen.add(j)
        return tuple(selected) or blocks[: min(12, len(blocks))]

    @staticmethod
    def _validate_output(
        result: AnnualLlmResult,
        allowed_keys: frozenset[str],
        *,
        require_heading: bool,
        allow_missing_citations: bool = False,
    ) -> tuple[str, ...]:
        text = result.markdown.strip()
        if result.finish_reason.lower() == "length" or text.lower().endswith(_TRUNCATED_TAILS):
            raise AnnualLlmCallError("ANNUAL_LLM_TRUNCATED", "年度 LLM 输出疑似截断")
        if len(text) < 80:
            raise AnnualLlmCallError("ANNUAL_LLM_OUTPUT_INVALID", "年度 LLM 输出为空或过短")
        if _FORBIDDEN_ADVICE_RE.search(text):
            raise AnnualLlmCallError("ANNUAL_LLM_FORBIDDEN_ADVICE", "年度 LLM 输出包含投资建议")
        keys = _extract_citations(text)
        if not keys and not allow_missing_citations:
            raise AnnualLlmCallError("ANNUAL_LLM_CITATION_MISSING", "年度 LLM 输出缺少引用")
        unknown = set(keys) - set(allowed_keys)
        if unknown:
            raise AnnualLlmCallError(
                "ANNUAL_LLM_CITATION_UNAUTHORIZED", "年度 LLM 输出引用了未授权证据"
            )
        if require_heading and not text.startswith("## "):
            raise AnnualLlmCallError("ANNUAL_LLM_STRUCTURE_INVALID", "年度章节未以二级标题开始")
        return keys


def _source_for_artifact(artifact: EvidenceArtifact) -> Any:
    """以最小对象复用唯一的 src_ citation-key 算法。"""

    return type("AnnualCitationSource", (), {"canonical_url": artifact.source_url})()


def _source_for_entry(entry: Any) -> Any:
    """以最小对象复用唯一的 src_ citation-key 算法（针对网页搜索条目）。"""

    return type("AnnualWebEntrySource", (), {"canonical_url": entry.url})()


def _extract_citations(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_CITATION_RE.findall(text)))


def _heading_for(section: AnnualSectionKind) -> str:
    return {
        AnnualSectionKind.FINANCIAL_PERFORMANCE: "财务表现",
        AnnualSectionKind.BUSINESS_OVERVIEW: "业务概览",
        AnnualSectionKind.RISK_FACTORS: "风险因素",
        AnnualSectionKind.MATERIAL_EVENTS: "重大事件",
    }[section]


def _lines(values: Iterable[object]) -> str:
    rendered = [str(value) for value in values if str(value)]
    return "\n".join(f"- {value}" for value in rendered) or "- 无"


def _truncate(text: str, limit: int) -> str:
    """截断到 limit 字符并在末尾加省略号（若超长）。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _with_translation(
    statement: OfficialStatement | None, translation: str | None
) -> OfficialStatement | None:
    """给一条官方声明附加中文翻译；翻译为空则原样返回（best-effort 保留英文）。"""
    if statement is None or not translation:
        return statement
    return replace(statement, translation=translation.strip())


_TRANSLATION_LABEL_RE = re.compile(r"【([^】]+)】")


def _parse_translated_statements(output: str, labels: list[str]) -> list[str]:
    """解析 LLM 翻译输出为按 ``labels`` 顺序的中文翻译列表（best-effort）。

    - 优先按 ``【封面页】`` 等标记分段；标记齐全时按标记匹配；
    - 无标记时按空行块顺序分配（数量一致才采纳）；
    - 解析不到任何段返回空列表（调用方保留英文，不阻塞发布）。
    """
    parts = _TRANSLATION_LABEL_RE.split(output)
    if len(parts) >= 3:
        mapping: dict[str, str] = {}
        for index in range(1, len(parts), 2):
            label = parts[index].strip()
            text = parts[index + 1].strip() if index + 1 < len(parts) else ""
            if label and text:
                mapping[label] = text
        if any(mapping.get(label) for label in labels):
            return [mapping.get(label, "") for label in labels]
    blocks = [block.strip() for block in re.split(r"\n{2,}", output) if block.strip()]
    if len(blocks) == len(labels) and all(block for block in blocks):
        return blocks
    if len(blocks) > len(labels):
        return blocks[: len(labels)]
    return []
