"""P05-12B/P05.5 真实工具注入（composition root：infrastructure → tools）。

把 P02 确定性工具（Pydantic 契约 + execute → ToolResult）包装为 CrewAI 工具白名单，
供三 Agent 生产使用。依赖方向：infrastructure -> tools + agents，domain 不被污染。

分组（least-privilege，对齐任务要求）：
- research：CompanyResolver、SECSubmissions、SECCompanyFacts、FilingDownloader、
  DocumentParser、WebSearch（Serper）；
- analysis：FinancialFactQuery、FinancialCalculator（复用 agents/analysis_task）；
- writer：ArtifactReader、CitationVerifier、TemplateGuide（复用 agents/writer_task）。

实现约定：
- 每个包装函数返回 str（JSON 编码），CrewAI 将其作为工具结果文本喂给 LLM；
- 失败时返回结构化 JSON（含 error_code / message），不抛异常——CrewAI 会把
  异常当作工具调用失败，丢失统一错误语义；我们用 ToolFailure 的 ErrorCode 保留分类；
- SEC Company Facts / 搜索结果体积受控：按 as_of_date 过滤并限制条数，
  防止把整份 XBRL 塞进 LLM context（事实由 Analysis 阶段确定性拉取，这里只给摘要）；
- P05.5：相同「工具名 + 规范化参数」在单次 Job 内只执行一次（ToolCallCache），
  缓存只存成功结果；所有工具不打印/不记录 API Key（key 只存在于 Serper 请求头）。
"""

from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterator

from crewai.tools import tool

from invest_research.domain.errors import ErrorCode
from invest_research.domain.models import ResearchRequest
from invest_research.financial.concept_mapping import CONCEPTS_V1_PATH, load_concept_mapping
from invest_research.infrastructure.performance import PerformanceRecorder
from invest_research.infrastructure.prefetch import PrefetchResult, PrefetchStatus
from invest_research.infrastructure.tool_budget import ToolBudget
from invest_research.infrastructure.tool_cache import ToolCallCache
from invest_research.tools.artifact_store import (
    ArtifactStore,
    ArtifactStoreRequest,
    ArtifactStoreTool,
)
from invest_research.tools.base import ToolError, ToolFailure
from invest_research.tools.company_resolver import CompanyResolverTool, ResolveCompanyRequest
from invest_research.tools.google_search import GoogleSearchTool, SearchQuery
from invest_research.tools.parser_router import DocumentParseError, parse_document
from invest_research.tools.sec_company_facts import FetchFactsRequest, SECCompanyFactsTool
from invest_research.tools.sec_downloader import DownloadRequest, SECDownloaderTool
from invest_research.tools.sec_submissions import FetchSubmissionsRequest, SECSubmissionsTool

__all__ = [
    "ResearchToolkit",
    "build_artifact_store_tool",
    "build_research_prefetcher",
    "build_research_toolkit",
    "build_research_tools",
]

# 每个指标保留最近两个可比期，足够计算同比且避免整份 XBRL
# 进入 LLM context。concept 白名单来自版本化 concepts_v1.json。
_FACTS_PERIODS_PER_METRIC = 2
# 搜索结果条数上限（Serper 分页已限 page_size ≤ 50；P05.5-fix 收紧到 10）
_SEARCH_MAX_ITEMS = 10

_LOGGER = logging.getLogger(__name__)


def _tool_result_json(value: dict[str, Any]) -> str:
    """把包装工具的结果 dict 序列化为 JSON 字符串（LLM 便于消费）。"""
    return json.dumps(value, ensure_ascii=False, default=str)


def _tool_failure_json(error_code: str, message: str) -> str:
    """把 ToolFailure 转为结构化失败 JSON（保留 ErrorCode，不抛异常）。"""
    return _tool_result_json({"ok": False, "error_code": error_code, "message": message})


def _unpack(result: Any) -> str:
    """从 ToolResult（成功/失败）解出可序列化 JSON 字符串。失败时保留 error 字段。"""
    if getattr(result, "kind", None) == "failure":
        err = result.error
        return _tool_failure_json(err.error_code.value, err.message)
    value = result.value
    return _tool_result_json({"ok": True, **value.model_dump(mode="json")})


def _serialize_search_result(result: Any) -> str:
    """把搜索 ToolResult 序列化为摘要 JSON（失败走 _unpack；成功限制条数）。"""
    if getattr(result, "kind", None) == "failure":
        return _unpack(result)
    items = result.value.items[:_SEARCH_MAX_ITEMS]
    return _tool_result_json(
        {
            "ok": True,
            "count": len(items),
            "results": [{"title": r.title, "url": r.url, "publisher": r.publisher} for r in items],
        }
    )


def _serialize_facts(result: Any, as_of_date: str | None) -> str:
    """序列化有限、可追溯的 SEC XBRL 事实集。

    只选 concepts_v1 中的指标候选，每个指标选实际存在的最高
    优先级 concept，再保留最近两个期间。值、单位、期间和 SEC
    accession locator 一起交给 Analysis Agent，禁止只给一串裸数字。
    """
    if getattr(result, "kind", None) == "failure":
        return _unpack(result)
    facts = result.value.facts
    if as_of_date is not None:
        cutoff = date.fromisoformat(as_of_date)
        kept: list[Any] = []
        for f in facts:
            effective = f.period_end if f.period_end is not None else f.instant_date
            if effective is not None and effective <= cutoff:
                kept.append(f)
        facts = kept

    mapping = load_concept_mapping(CONCEPTS_V1_PATH)
    available = {fact.concept for fact in facts}
    selected: list[tuple[str, Any]] = []
    for entry in mapping.entries:
        concept = next((name for name in entry.candidates if name in available), None)
        if concept is None:
            continue
        candidates = [fact for fact in facts if fact.concept == concept]
        candidates.sort(
            key=lambda fact: fact.period_end or fact.instant_date or date.min,
            reverse=True,
        )
        seen_periods: set[tuple[object, ...]] = set()
        for fact in candidates:
            period_key = (
                fact.period_start,
                fact.period_end,
                fact.instant_date,
                fact.unit,
            )
            if period_key in seen_periods:
                continue
            seen_periods.add(period_key)
            selected.append((entry.metric_name, fact))
            if len(seen_periods) >= _FACTS_PERIODS_PER_METRIC:
                break

    summary = []
    for metric_name, fact in selected:
        effective = fact.period_end or fact.instant_date
        locator = (
            f"accn={fact.accession_number}; concept={fact.concept}; "
            f"period={effective.isoformat() if effective else 'unknown'}"
        )
        summary.append(
            {
                "company_id": fact.company_id,
                "source_id": f"sec-companyfacts-{fact.company_id}",
                "source_url": (
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{fact.company_id}.json"
                ),
                "metric_name": metric_name,
                "taxonomy": fact.taxonomy,
                "concept": fact.concept,
                "label": fact.label,
                "value": str(fact.value),
                "unit": fact.unit,
                "period_start": fact.period_start.isoformat() if fact.period_start else None,
                "period_end": fact.period_end.isoformat() if fact.period_end else None,
                "instant_date": fact.instant_date.isoformat() if fact.instant_date else None,
                "fiscal_year": fact.fiscal_year,
                "fiscal_period": fact.fiscal_period,
                "form_type": fact.form_type,
                "accession_number": fact.accession_number,
                "locator": locator,
            }
        )
    return _tool_result_json(
        {
            "ok": True,
            "mapping_version": mapping.version,
            "count": len(summary),
            "facts": summary,
        }
    )


def _count(stats: dict[str, int] | None, key: str) -> None:
    """累加调用/失败统计（P05-13 验收：manifest 需含外部调用证据）。"""
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


@contextmanager
def _timed(recorder: PerformanceRecorder | None, tool_name: str) -> Iterator[None]:
    """工具执行上下文：性能计时（可选）+ OTel span（P06-05）。

    - recorder 为 None 时跳过计时（零额外开销）；
    - OTel span 始终开启（未 setup_tracing 时是 no-op provider，零开销）；
    - span 属性只放工具名（低基数），不放参数/结果/密钥。
    """
    from invest_research.infrastructure.observability.tracing import span as _otel_span

    with ExitStack() as stack:
        if recorder is not None:
            stack.enter_context(recorder.timed_tool(tool_name))
        stack.enter_context(_otel_span(f"tool.{tool_name}", {"tool.name": tool_name}))
        yield


def _budget_exhausted(budget: ToolBudget | None, tool_name: str) -> str | None:
    """尝试占用一次工具执行额度；超限返回 BUDGET_EXHAUSTED 失败 JSON（typed）。"""
    if budget is None:
        return None
    if budget.try_acquire(tool_name):
        return None
    cap = budget.cap(tool_name)
    return _tool_failure_json(
        "BUDGET_EXHAUSTED", f"{tool_name} 调用预算已耗尽（每 Job 上限 {cap} 次）"
    )


def _cached_lookup(
    cache: ToolCallCache | None,
    recorder: PerformanceRecorder | None,
    tool_name: str,
    params: dict[str, Any],
) -> tuple[str | None, str | None]:
    """查缓存：返回 (cached_text, key)；未命中时 cached_text 为 None。"""
    if cache is None:
        return None, None
    key = cache.key(tool_name, params)
    cached: str | None = cache.get(key)
    if cached is not None and recorder is not None:
        recorder.record_cache_hit(tool_name)
    return cached, key


def _cached_execute(
    *,
    cache: ToolCallCache | None,
    recorder: PerformanceRecorder | None,
    tool_name: str,
    params: dict[str, Any],
    serialize_fn: Callable[[Any], str],
    execute_fn: Callable[[], Any],
    budget: ToolBudget | None = None,
) -> str:
    """带缓存执行：命中→预算→执行→序列化→（成功）写缓存；预算耗尽返回 BUDGET_EXHAUSTED。

    P06-06C：真实执行完成后按成功/失败计数 tool_calls_total；
    失败且错误可重试时按 tool/error_code 计数 tool_retries_total。
    """
    cached, key = _cached_lookup(cache, recorder, tool_name, params)
    if cached is not None:
        return cached
    exhausted = _budget_exhausted(budget, tool_name)
    if exhausted is not None:
        return exhausted
    with _timed(recorder, tool_name):
        result = execute_fn()
    text = serialize_fn(result)
    if cache is not None and key is not None and getattr(result, "kind", None) == "success":
        cache.put(key, text)
    _record_tool_metrics(tool_name, result)
    return text


def _record_tool_metrics(tool_name: str, result: Any) -> None:
    """工具调用完成指标：成功/失败计数 + 可重试失败重试计数（尽力而为）。

    失败时 result 是 ToolFailure（kind="failure"）；可重试判定委托
    ToolError.is_retryable（domain 错误分类白名单）。计数失败不影响业务。
    """
    from invest_research.infrastructure.observability.metrics_events import (
        count_tool_call,
        count_tool_retry,
    )

    is_success = getattr(result, "kind", None) == "success"
    status = "success" if is_success else "failure"
    count_tool_call(tool_name, status)
    if is_success:
        return
    err = getattr(result, "error", None)
    code = getattr(err, "error_code", None)
    code_value = code.value if code is not None and hasattr(code, "value") else str(code)
    if bool(getattr(err, "is_retryable", False)):
        count_tool_retry(tool_name, code_value)


def _record_tool_failure_metrics(tool_name: str, error_code: str) -> None:
    """非缓存工具路径失败指标：按 tool/status 计数 + 可重试时计数重试。

    用于异常路径直接返回失败 JSON 的工具（无 ToolFailure 对象可复用）。
    """
    from invest_research.domain.errors import is_retryable
    from invest_research.infrastructure.observability.metrics_events import (
        count_tool_call,
        count_tool_retry,
    )

    count_tool_call(tool_name, "failure")
    if is_retryable(error_code):
        count_tool_retry(tool_name, error_code)


@dataclass(frozen=True)
class ResearchToolkit:
    """Research 阶段 P02 确定性工具集（供 CrewAI 包装与 prefetch 共享同一实例）。"""

    resolver: CompanyResolverTool
    submissions: SECSubmissionsTool
    facts: SECCompanyFactsTool
    downloader: SECDownloaderTool
    search: GoogleSearchTool


def build_research_toolkit(client: Any, serper: Any) -> ResearchToolkit:
    """构造 Research 工具集（共享 client/serper；包装层与 prefetch 复用）。"""
    return ResearchToolkit(
        resolver=CompanyResolverTool(),
        submissions=SECSubmissionsTool(client),
        facts=SECCompanyFactsTool(client),
        downloader=SECDownloaderTool(client),
        search=GoogleSearchTool(provider=serper),
    )


# ---------------------------------------------------------------------------
# Research 工具白名单（CompanyResolver + SEC + 下载 + 解析 + 搜索）
# ---------------------------------------------------------------------------


def build_research_tools(
    *,
    toolkit: ResearchToolkit,
    stats: dict[str, int] | None = None,
    recorder: PerformanceRecorder | None = None,
    cache: ToolCallCache | None = None,
    budget: ToolBudget | None = None,
) -> list[Any]:
    """构造 Research Agent 的真实工具白名单。

    参数：
    - toolkit：共享 P02 工具集（build_research_toolkit 构造，prefetch 复用）；
    - stats：可选调用统计 dict（P05-13 验收：外部调用证据写入 manifest；
      None 时不记录，行为与之前完全一致）；
    - recorder：可选 PerformanceRecorder（P05.5 工具耗时/次数统计；None 时不计时）；
    - cache：可选 ToolCallCache（P05.5 相同工具名+规范化参数单 Job 只执行一次）。

    返回给 CrewAI 使用的工具函数列表（@tool 包装）。
    """
    resolver_tool = toolkit.resolver
    submissions_tool = toolkit.submissions
    facts_tool = toolkit.facts
    downloader_tool = toolkit.downloader
    search_tool = toolkit.search

    @tool("CompanyResolver")
    def company_resolver(input_company: str) -> str:
        """按公司名/ticker 解析 10 位 CIK；歧义时返回候选列表（不猜测）。"""
        _count(stats, "company_resolver_calls")

        def _run() -> Any:
            try:
                return resolver_tool.execute(ResolveCompanyRequest(input_company=input_company))
            except Exception as exc:  # 应用边界：统一记录，不抛给 CrewAI
                _count(stats, "company_resolver_failures")
                return ToolFailure(
                    error=ToolError(
                        error_code=ErrorCode.INTERNAL_BUG,
                        message=f"CompanyResolver 异常: {type(exc).__name__}",
                    )
                )

        return _cached_execute(
            cache=cache,
            recorder=recorder,
            tool_name="company_resolver",
            params={"input_company": input_company},
            serialize_fn=_unpack,
            execute_fn=_run,
            budget=budget,
        )

    @tool("SECSubmissions")
    def sec_submissions(cik: str, as_of_date: str, requested_forms: str = "10-K,10-Q") -> str:
        """拉取 SEC 申报历史并按 as_of_date 过滤，返回 10-K/10-Q 的 Filing 摘要。

        - cik：10 位数字 CIK；
        - as_of_date：ISO 日期（YYYY-MM-DD），只返回 filing_date <= as_of 的申报；
        - requested_forms：逗号分隔的表单（默认 "10-K,10-Q"）。
        """
        _count(stats, "sec_submissions_calls")
        try:
            forms = tuple(f.strip() for f in requested_forms.split(",") if f.strip())
            req = FetchSubmissionsRequest(
                cik=cik,
                as_of_date=date.fromisoformat(as_of_date),
                requested_forms=forms or ("10-K", "10-Q"),
            )
        except ValueError as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效入参: {exc}")

        def _run() -> Any:
            try:
                return submissions_tool.execute(req)
            except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
                _count(stats, "sec_submissions_failures")
                return ToolFailure(
                    error=ToolError(
                        error_code=ErrorCode.INTERNAL_BUG,
                        message=f"SECSubmissions 异常: {type(exc).__name__}",
                    )
                )

        return _cached_execute(
            cache=cache,
            recorder=recorder,
            tool_name="sec_submissions",
            params={"cik": cik, "as_of_date": as_of_date, "requested_forms": requested_forms},
            serialize_fn=_unpack,
            execute_fn=_run,
            budget=budget,
        )

    @tool("SECCompanyFacts")
    def sec_company_facts(cik: str, as_of_date: str | None = None) -> str:
        """拉取 XBRL Company Facts 摘要（按 as_of_date 过滤 + 条数上限）。"""
        _count(stats, "sec_company_facts_calls")
        cached, key = _cached_lookup(
            cache, recorder, "sec_company_facts", {"cik": cik, "as_of_date": as_of_date}
        )
        if cached is not None:
            return cached
        exhausted = _budget_exhausted(budget, "sec_company_facts")
        if exhausted is not None:
            return exhausted
        try:
            req = FetchFactsRequest(
                cik=cik,
                as_of_date=date.fromisoformat(as_of_date) if as_of_date else None,
            )
            with _timed(recorder, "sec_company_facts"):
                result = facts_tool.execute(req)
            text = _serialize_facts(result, as_of_date)
            if cache is not None and key is not None and result.kind == "success":
                cache.put(key, text)
            _record_tool_metrics("sec_company_facts", result)
            return text
        except ValueError as exc:
            _record_tool_failure_metrics("sec_company_facts", "INPUT_INVALID")
            return _tool_failure_json("INPUT_INVALID", f"无效入参: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "sec_company_facts_failures")
            _record_tool_failure_metrics("sec_company_facts", "INTERNAL_BUG")
            return _tool_failure_json("INTERNAL_BUG", f"SECCompanyFacts 异常: {type(exc).__name__}")

    @tool("FilingDownloader")
    def filing_downloader(url: str, max_bytes: int = 50 * 1024 * 1024) -> str:
        """安全下载 SEC 申报正文文件（校验大小/媒体类型/checksum）。"""
        _count(stats, "filing_downloader_calls")

        def _run() -> Any:
            try:
                return downloader_tool.execute(DownloadRequest(url=url, max_bytes=max_bytes))
            except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
                _count(stats, "filing_downloader_failures")
                return ToolFailure(
                    error=ToolError(
                        error_code=ErrorCode.INTERNAL_BUG,
                        message=f"FilingDownloader 异常: {type(exc).__name__}",
                    )
                )

        return _cached_execute(
            cache=cache,
            recorder=recorder,
            tool_name="filing_downloader",
            params={"url": url, "max_bytes": max_bytes},
            serialize_fn=_unpack,
            execute_fn=_run,
            budget=budget,
        )

    @tool("DocumentParser")
    def document_parser(content_base64: str, media_type: str | None = None) -> str:
        """解析文档（HTML/PDF，PDF 失败最多降级一次）并返回文本块摘要。"""
        _count(stats, "document_parser_calls")
        cached, key = _cached_lookup(
            cache,
            recorder,
            "document_parser",
            {"content_base64": content_base64, "media_type": media_type},
        )
        if cached is not None:
            return cached
        exhausted = _budget_exhausted(budget, "document_parser")
        if exhausted is not None:
            return exhausted
        try:
            raw = base64.b64decode(content_base64, validate=True)
            with _timed(recorder, "document_parser"):
                outcome = parse_document(raw, media_type)
        except DocumentParseError as exc:
            _record_tool_failure_metrics("document_parser", "DOCUMENT_UNSUPPORTED")
            return _tool_failure_json("DOCUMENT_UNSUPPORTED", str(exc))
        except (ValueError, TypeError) as exc:
            _record_tool_failure_metrics("document_parser", "INPUT_INVALID")
            return _tool_failure_json("INPUT_INVALID", f"无效 base64: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "document_parser_failures")
            _record_tool_failure_metrics("document_parser", "INTERNAL_BUG")
            return _tool_failure_json("INTERNAL_BUG", f"DocumentParser 异常: {type(exc).__name__}")
        from invest_research.infrastructure.observability.metrics_events import (
            count_tool_call,
        )

        count_tool_call("document_parser", "success")
        doc = outcome.document
        blocks = getattr(doc, "blocks", None) or getattr(doc, "pages", None) or []
        snippet = blocks[:60]
        text = _tool_result_json(
            {
                "ok": True,
                "kind": outcome.kind,
                "parser_name": outcome.parser_name,
                "degraded_from": outcome.degraded_from,
                "block_count": len(blocks),
                "snippet": str(snippet),
            }
        )
        if cache is not None and key is not None:
            cache.put(key, text)
        return text

    @tool("WebSearch")
    def web_search(query: str, as_of: str | None = None) -> str:
        """搜索公开网络信息（Serper），按 as_of 过滤 + 去重，返回结果摘要。"""
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效 as_of: {exc}")
        _count(stats, "web_search_calls")

        def _run() -> Any:
            try:
                return search_tool.execute(SearchQuery(query=query, as_of=as_of_date, page_size=10))
            except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
                _count(stats, "web_search_failures")
                return ToolFailure(
                    error=ToolError(
                        error_code=ErrorCode.INTERNAL_BUG,
                        message=f"WebSearch 异常: {type(exc).__name__}",
                    )
                )

        return _cached_execute(
            cache=cache,
            recorder=recorder,
            tool_name="web_search",
            params={"query": query, "as_of": as_of},
            serialize_fn=_serialize_search_result,
            execute_fn=_run,
            budget=budget,
        )

    return [
        company_resolver,
        sec_submissions,
        sec_company_facts,
        filing_downloader,
        document_parser,
        web_search,
    ]


# ---------------------------------------------------------------------------
# 公司身份确认后的并行预取（P05.5：并行独立 I/O + 缓存预热，不新增 Agent）
# ---------------------------------------------------------------------------


def _forms_key(request: ResearchRequest) -> str:
    """把 request.requested_forms 规范化为逗号分隔字符串（与包装层默认一致）。"""
    forms = request.requested_forms
    return ",".join(forms) if forms else "10-K,10-Q"


def resolve_and_prefetch(
    request: ResearchRequest,
    toolkit: ResearchToolkit,
    cache: ToolCallCache,
    recorder: PerformanceRecorder | None = None,
    budget: ToolBudget | None = None,
    stats: dict[str, int] | None = None,
) -> PrefetchResult:
    """公司身份确认后并行预取 SEC submissions/facts + Serper。

    - 解析公司（确定性）；歧义/失败返回 status=failed 的 PrefetchResult（不猜测）；
    - 并行执行三个独立 I/O（SEC submissions + Company Facts + Serper），
      各自先查缓存，
      未命中按预算执行并按成功结果写缓存（键与 build_research_tools 一致）；
    - 摘要随 PrefetchResult 返回，供 runner 注入 Research Task（不只预热缓存）；
    - 仅并行独立 I/O；Analysis 仍依赖 Research、Writer 仍依赖 Research+Analysis。
    """
    resolve_result = toolkit.resolver.execute(
        ResolveCompanyRequest(input_company=request.input_company)
    )
    if resolve_result.kind == "failure" or not resolve_result.value.resolved:
        return PrefetchResult(
            company_identity=None,
            submissions_summary=None,
            search_summary=None,
            status="failed",
            financial_facts_summary=None,
        )
    identity = resolve_result.value.candidates[0]
    cik = identity.cik
    as_of = request.as_of_date.isoformat()
    forms_str = _forms_key(request)
    submissions_summary: str | None = None
    search_summary: str | None = None
    financial_facts_summary: str | None = None

    def fetch_submissions() -> None:
        nonlocal submissions_summary
        key = cache.key(
            "sec_submissions",
            {"cik": cik, "as_of_date": as_of, "requested_forms": forms_str},
        )
        cached = cache.get(key)
        if cached is not None:
            submissions_summary = cached
            if recorder is not None:
                recorder.record_cache_hit("sec_submissions")
            return
        if budget is not None and not budget.try_acquire("sec_submissions"):
            return
        _count(stats, "sec_submissions_calls")
        with _timed(recorder, "sec_submissions"):
            result = toolkit.submissions.execute(
                FetchSubmissionsRequest(
                    cik=cik,
                    as_of_date=request.as_of_date,
                    requested_forms=request.requested_forms or ("10-K", "10-Q"),
                )
            )
        if result.kind == "success":
            cache.put(key, _unpack(result))
            filings = result.value.filings
            submissions_summary = (
                "\n".join(
                    f"- {f.form_type} | filed {f.filing_date.isoformat()} | "
                    f"{f.primary_document_url}"
                    for f in filings[:_SEARCH_MAX_ITEMS]
                )
                or "（无 10-K/10-Q 申报记录）"
            )
        else:
            _LOGGER.warning("prefetch sec_submissions 失败: %s", result.error.message)
            _count(stats, "sec_submissions_failures")

    def fetch_search() -> None:
        nonlocal search_summary
        key = cache.key("web_search", {"query": request.input_company, "as_of": as_of})
        cached = cache.get(key)
        if cached is not None:
            search_summary = cached
            if recorder is not None:
                recorder.record_cache_hit("web_search")
            return
        if budget is not None and not budget.try_acquire("web_search"):
            return
        _count(stats, "web_search_calls")
        with _timed(recorder, "web_search"):
            result = toolkit.search.execute(
                SearchQuery(query=request.input_company, as_of=request.as_of_date, page_size=10)
            )
        if result.kind == "success":
            cache.put(key, _serialize_search_result(result))
            items = result.value.items[:_SEARCH_MAX_ITEMS]
            search_summary = (
                "\n".join(f"- {r.title} | {r.url} | {r.publisher or ''}" for r in items)
                or "（无搜索结果）"
            )
        else:
            _LOGGER.warning("prefetch web_search 失败: %s", result.error.message)
            _count(stats, "web_search_failures")

    def fetch_facts() -> None:
        nonlocal financial_facts_summary
        key = cache.key("sec_company_facts", {"cik": cik, "as_of_date": as_of})
        cached = cache.get(key)
        if cached is not None:
            financial_facts_summary = cached
            if recorder is not None:
                recorder.record_cache_hit("sec_company_facts")
            return
        if budget is not None and not budget.try_acquire("sec_company_facts"):
            return
        _count(stats, "sec_company_facts_calls")
        with _timed(recorder, "sec_company_facts"):
            result = toolkit.facts.execute(
                FetchFactsRequest(cik=cik, as_of_date=request.as_of_date)
            )
        if result.kind == "success":
            financial_facts_summary = _serialize_facts(result, as_of)
            cache.put(key, financial_facts_summary)
        else:
            _LOGGER.warning("prefetch sec_company_facts 失败: %s", result.error.message)
            _count(stats, "sec_company_facts_failures")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(fetch_submissions),
            executor.submit(fetch_facts),
            executor.submit(fetch_search),
        ]
        for future in futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001 - prefetch 是优化，失败不影响 Agent 兜底
                continue

    status: PrefetchStatus = (
        "ok"
        if (
            submissions_summary is not None
            and financial_facts_summary is not None
            and search_summary is not None
        )
        else "partial"
    )
    return PrefetchResult(
        company_identity=identity,
        submissions_summary=submissions_summary,
        search_summary=search_summary,
        status=status,
        financial_facts_summary=financial_facts_summary,
    )


def build_research_prefetcher(
    *,
    toolkit: ResearchToolkit,
    cache: ToolCallCache,
    recorder: PerformanceRecorder | None = None,
    budget: ToolBudget | None = None,
    stats: dict[str, int] | None = None,
) -> Callable[[ResearchRequest], PrefetchResult]:
    """返回 prefetch 可调用对象（公司解析 + 并行 SEC/Serper + 缓存预热）。"""

    def prefetch(request: ResearchRequest) -> PrefetchResult:
        return resolve_and_prefetch(request, toolkit, cache, recorder, budget=budget, stats=stats)

    return prefetch


def build_artifact_store_tool(artifact_store: ArtifactStore) -> list[Any]:
    """构造 Writer 的工件写入工具（ArtifactStoreTool 包装为 CrewAI 工具）。"""
    store_tool = ArtifactStoreTool(store=artifact_store)

    @tool("ArtifactWriter")
    def artifact_writer(artifact_key: str, content_base64: str) -> str:
        """把内容原子写入本地工件存储（返回 checksum；不覆盖已存在内容）。"""
        try:
            content = base64.b64decode(content_base64, validate=True)
            result = store_tool.execute(
                ArtifactStoreRequest(operation="write", artifact_key=artifact_key, content=content)
            )
            _record_tool_metrics("artifact_writer", result)
            return _unpack(result)
        except (ValueError, TypeError) as exc:
            _record_tool_failure_metrics("artifact_writer", "INPUT_INVALID")
            return _tool_failure_json("INPUT_INVALID", f"无效 base64: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _record_tool_failure_metrics("artifact_writer", "INTERNAL_BUG")
            return _tool_failure_json("INTERNAL_BUG", f"ArtifactWriter 异常: {type(exc).__name__}")

    return [artifact_writer]
