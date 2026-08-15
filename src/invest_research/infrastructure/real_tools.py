"""P05-12B 真实工具注入（composition root：infrastructure → tools）。

把 P02 确定性工具（Pydantic 契约 + execute → ToolResult）包装为 CrewAI 工具白名单，
供三 Agent 生产使用。依赖方向：infrastructure -> tools + agents，domain 不被污染。

分组（least-privilege，对齐任务要求）：
- research：CompanyResolver、SECSubmissions、SECCompanyFacts、FilingDownloader、
  DocumentParser、WebSearch（Serper）；
- analysis：FinancialFactQuery、FinancialCalculator（复用 agents/analysis_task）；
- writer：ArtifactReader、CitationVerifier、TemplateGuide（复用 agents/writer_task）。

实现约定：
- 每个包装函数返回 ``str``（JSON 编码），CrewAI 将其作为工具结果文本喂给 LLM；
- 失败时返回结构化 JSON（含 error_code / message），**不抛异常**——CrewAI 会把
  异常当作工具调用失败，丢失统一错误语义；我们用 ToolFailure 的 ErrorCode 保留分类；
- SEC Company Facts / 搜索结果体积受控：按 ``as_of_date`` 过滤并限制条数，
  防止把整份 XBRL 塞进 LLM context（事实由 Analysis 阶段确定性拉取，这里只给摘要）；
- 所有工具不打印/不记录 API Key（key 只存在于 Serper 请求头，由适配器持有）。
"""

from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any

from crewai.tools import tool

from invest_research.tools.artifact_store import (
    ArtifactStore,
    ArtifactStoreRequest,
    ArtifactStoreTool,
)
from invest_research.tools.company_resolver import CompanyResolverTool, ResolveCompanyRequest
from invest_research.tools.google_search import GoogleSearchTool, SearchQuery
from invest_research.tools.parser_router import DocumentParseError, parse_document
from invest_research.tools.sec_company_facts import FetchFactsRequest, SECCompanyFactsTool
from invest_research.tools.sec_downloader import DownloadRequest, SECDownloaderTool
from invest_research.tools.sec_submissions import FetchSubmissionsRequest, SECSubmissionsTool

__all__ = [
    "build_artifact_store_tool",
    "build_research_tools",
]

# SEC Company Facts 摘要条数上限（防止把整份 XBRL 塞进 LLM context）
_FACTS_MAX_ITEMS = 60
# 搜索结果条数上限（Serper 分页已限 page_size ≤ 50，这里再收紧）
_SEARCH_MAX_ITEMS = 20


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


def _count(stats: dict[str, int] | None, key: str) -> None:
    """累加调用/失败统计（P05-13 验收：manifest 需含外部调用证据）。"""
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


# ---------------------------------------------------------------------------
# Research 工具白名单（CompanyResolver + SEC + 下载 + 解析 + 搜索）
# ---------------------------------------------------------------------------


def build_research_tools(
    *,
    client: Any,
    serper: Any,
    stats: dict[str, int] | None = None,
) -> list[Any]:
    """构造 Research Agent 的真实工具白名单。

    参数：
    - ``client``：共享 httpx.Client（SEC 请求用）；
    - ``serper``：SerperAdapter（GoogleSearchTool 的 provider）；
    - ``stats``：可选调用统计 dict（P05-13 验收：外部调用证据写入 manifest；
      None 时不记录，行为与之前完全一致）。

    返回给 CrewAI 使用的工具函数列表（@tool 包装）。
    """
    resolver_tool = CompanyResolverTool()
    submissions_tool = SECSubmissionsTool(client)
    facts_tool = SECCompanyFactsTool(client)
    downloader_tool = SECDownloaderTool(client)
    search_tool = GoogleSearchTool(provider=serper)

    @tool("CompanyResolver")
    def company_resolver(input_company: str) -> str:
        """按公司名/ticker 解析 10 位 CIK；歧义时返回候选列表（不猜测）。"""
        _count(stats, "company_resolver_calls")
        try:
            return _unpack(
                resolver_tool.execute(ResolveCompanyRequest(input_company=input_company))
            )
        except Exception as exc:  # 应用边界：统一记录，不抛给 CrewAI
            _count(stats, "company_resolver_failures")
            return _tool_failure_json("INTERNAL_BUG", f"CompanyResolver 异常: {type(exc).__name__}")

    @tool("SECSubmissions")
    def sec_submissions(cik: str, as_of_date: str, requested_forms: str = "10-K,10-Q") -> str:
        """拉取 SEC 申报历史并按 as_of_date 过滤，返回 10-K/10-Q 的 Filing 摘要。

        - ``cik``：10 位数字 CIK；
        - ``as_of_date``：ISO 日期（YYYY-MM-DD），只返回 filing_date <= as_of 的申报；
        - ``requested_forms``：逗号分隔的表单（默认 "10-K,10-Q"）。
        """
        _count(stats, "sec_submissions_calls")
        try:
            forms = tuple(f.strip() for f in requested_forms.split(",") if f.strip())
            req = FetchSubmissionsRequest(
                cik=cik,
                as_of_date=date.fromisoformat(as_of_date),
                requested_forms=forms or ("10-K", "10-Q"),
            )
            return _unpack(submissions_tool.execute(req))
        except ValueError as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效入参: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "sec_submissions_failures")
            return _tool_failure_json("INTERNAL_BUG", f"SECSubmissions 异常: {type(exc).__name__}")

    @tool("SECCompanyFacts")
    def sec_company_facts(cik: str, as_of_date: str | None = None) -> str:
        """拉取 XBRL Company Facts 摘要（按 as_of_date 过滤 + 条数上限）。

        - 返回每个 concept 在最近期间的值摘要（不包含完整历史）；
        - as_of_date 过滤在工具内部执行（不存在未来数据）；
        - 供 Analysis Agent 后续经 FinancialFactQuery 精确取数。
        """
        _count(stats, "sec_company_facts_calls")
        try:
            req = FetchFactsRequest(cik=cik)
            result = facts_tool.execute(req)
            if result.kind == "failure":
                return _unpack(result)
            value = result.value
            facts = value.facts
            if as_of_date is not None:
                cutoff = date.fromisoformat(as_of_date)
                kept: list[Any] = []
                for f in facts:
                    effective = f.period_end if f.period_end is not None else f.instant_date
                    if effective is not None and effective <= cutoff:
                        kept.append(f)
                facts = kept
            summary = [
                {
                    "concept": f.concept,
                    "value": str(f.value),
                    "unit": f.unit,
                    "period_end": f.period_end.isoformat() if f.period_end else None,
                    "instant_date": f.instant_date.isoformat() if f.instant_date else None,
                    "form_type": f.form_type,
                }
                for f in facts[-_FACTS_MAX_ITEMS:]
            ]
            return _tool_result_json({"ok": True, "count": len(summary), "facts": summary})
        except ValueError as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效入参: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "sec_company_facts_failures")
            return _tool_failure_json("INTERNAL_BUG", f"SECCompanyFacts 异常: {type(exc).__name__}")

    @tool("FilingDownloader")
    def filing_downloader(url: str, max_bytes: int = 50 * 1024 * 1024) -> str:
        """安全下载 SEC 申报正文文件（校验大小/媒体类型/checksum）。

        返回媒体类型、字节大小和 sha256 checksum（不直接返回文件内容）。
        """
        _count(stats, "filing_downloader_calls")
        try:
            return _unpack(
                downloader_tool.execute(DownloadRequest(url=url, max_bytes=max_bytes))
            )
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "filing_downloader_failures")
            return _tool_failure_json(
                "INTERNAL_BUG", f"FilingDownloader 异常: {type(exc).__name__}"
            )

    @tool("DocumentParser")
    def document_parser(content_base64: str, media_type: str | None = None) -> str:
        """解析文档（HTML/PDF，PDF 失败最多降级一次）并返回文本块摘要。

        - ``content_base64``：文档内容的 base64 编码；
        - ``media_type``：如 text/html 或 application/pdf（可选，缺失自动探测）。
        """
        _count(stats, "document_parser_calls")
        try:
            raw = base64.b64decode(content_base64, validate=True)
            outcome = parse_document(raw, media_type)
        except DocumentParseError as exc:
            return _tool_failure_json("DOCUMENT_UNSUPPORTED", str(exc))
        except (ValueError, TypeError) as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效 base64: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "document_parser_failures")
            return _tool_failure_json("INTERNAL_BUG", f"DocumentParser 异常: {type(exc).__name__}")
        doc = outcome.document
        blocks = getattr(doc, "blocks", None) or getattr(doc, "pages", None) or []
        snippet = blocks[:200]
        return _tool_result_json(
            {
                "ok": True,
                "kind": outcome.kind,
                "parser_name": outcome.parser_name,
                "degraded_from": outcome.degraded_from,
                "block_count": len(blocks),
                "snippet": str(snippet),
            }
        )

    @tool("WebSearch")
    def web_search(query: str, as_of: str | None = None) -> str:
        """搜索公开网络信息（Serper），按 as_of 过滤 + 去重，返回结果摘要。"""
        try:
            as_of_date = date.fromisoformat(as_of) if as_of else date.today()
        except ValueError as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效 as_of: {exc}")
        _count(stats, "web_search_calls")
        try:
            result = search_tool.execute(SearchQuery(query=query, as_of=as_of_date, page_size=10))
            if result.kind == "failure":
                return _unpack(result)
            items = result.value.items[:_SEARCH_MAX_ITEMS]
            return _tool_result_json(
                {
                    "ok": True,
                    "count": len(items),
                    "results": [
                        {"title": r.title, "url": r.url, "publisher": r.publisher}
                        for r in items
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            _count(stats, "web_search_failures")
            return _tool_failure_json("INTERNAL_BUG", f"WebSearch 异常: {type(exc).__name__}")

    return [
        company_resolver,
        sec_submissions,
        sec_company_facts,
        filing_downloader,
        document_parser,
        web_search,
    ]


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
            return _unpack(result)
        except (ValueError, TypeError) as exc:
            return _tool_failure_json("INPUT_INVALID", f"无效 base64: {exc}")
        except Exception as exc:  # noqa: BLE001 - 应用边界统一失败语义
            return _tool_failure_json("INTERNAL_BUG", f"ArtifactWriter 异常: {type(exc).__name__}")

    return [artifact_writer]
