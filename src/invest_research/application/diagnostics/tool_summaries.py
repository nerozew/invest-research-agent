"""P06-11K-4：工具 / SEC / Serper / LLM 调用摘要构造器（纯函数）。

设计目标：
- 每个构造器都是纯函数：输入工具名/参数/结果 → 输出**小体积、有界**的
  摘要 dict；摘要只含白名单字段（天然脱敏，不引入敏感键）。
- 白名单原则：工具参数只允许 cik / as_of_date / requested_forms / query /
  metric_name / period_end / concept 等确定性查询字段；token / api_key /
  authorization / cookie / 认证头一律不进摘要（即使 capture 会做纵深脱敏，
  摘要构造阶段也不收集敏感字段）。
- 输出摘要从工具结果 JSON 中提取结构化信息：来源 URL / accession number /
  locator / 标题 / 结果数量 / 有界文本预览。原始结果正文绝不进摘要。
- 本模块零文件系统 / 零网络依赖（应用层纯逻辑），与 ``persistence.py``
  同层级，可被 infrastructure 接线层直接调用。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "build_tool_request_summary",
    "build_tool_response_summary",
    "build_llm_response_summary",
]

# 工具参数白名单：只有这些字段会进入摘要（其余一律丢弃）。
# 键统一为真实工具入参名；值类型保持原样（CIK/日期/查询关键词等）。
_TOOL_PARAM_WHITELIST: dict[str, frozenset[str]] = {
    "company_resolver": frozenset({"input_company"}),
    "sec_submissions": frozenset({"cik", "as_of_date", "requested_forms"}),
    "sec_company_facts": frozenset({"cik", "as_of_date"}),
    "filing_downloader": frozenset(),  # url 可能含签名；摘要不保留
    "document_parser": frozenset(),  # content_base64 是业务正文，不进摘要
    "web_search": frozenset({"query", "as_of"}),
    "financial_fact_query": frozenset({"concept", "available_concepts", "prefer_annual"}),
    "financial_calculator": frozenset({"metric_name", "period_end"}),
}

# 有界文本预览字节上限（防止把大段正文塞进诊断包）。
_MAX_PREVIEW_CHARS = 300


def _preview_text(value: Any, limit: int = _MAX_PREVIEW_CHARS) -> str:
    """把任意值压缩为有界文本预览（截断 + 省略号）。"""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_tool_request_summary(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """构造工具调用参数摘要（白名单字段，天然脱敏）。

    - 只保留 ``_TOOL_PARAM_WHITELIST[tool_name]`` 内的参数；
    - 未知工具一律返回空 dict（不猜测字段）；
    - ``available_concepts`` 等 tuple 会被截断为有界预览。
    """
    allowed = _TOOL_PARAM_WHITELIST.get(tool_name)
    if allowed is None:
        return {}
    summary: dict[str, Any] = {}
    for key in sorted(allowed):
        if key not in params:
            continue
        value = params[key]
        if isinstance(value, (dict, list, tuple)):
            # 结构化白名单字段（如 available_concepts）只留第一层 + 有界预览
            summary[key] = _preview_text(value)
        else:
            summary[key] = value
    return summary


def _extract_ok(result: dict[str, Any]) -> bool:
    return bool(result.get("ok")) and not bool(result.get("error_code"))


def build_tool_response_summary(
    tool_name: str,
    output_text: str | None,
) -> dict[str, Any]:
    """从工具输出 JSON 构造结构化摘要（SEC/Serper/Calculator 通用）。

    - 输出必须是 JSON 字符串（real_tools 统一 ``_tool_result_json``）；
    - 失败：只保留 ``error_code`` 与 ``message`` 的有界预览（message 由 capture
      再做一次纵深脱敏）；
    - 成功：按工具提取结构化字段（filings/accession/locator/结果数/标题/URL）：
      - ``sec_submissions``：filings 数量 + 每条的 form/accession/locator；
      - ``sec_company_facts``：facts 数 + 每条的 concept/period/locator；
      - ``web_search``：count + 每条 title/url（url 保留域名与路径，query 已不含密钥）；
      - ``financial_calculator`` / ``financial_fact_query``：整个结果有界预览。
    """
    if not output_text:
        return {"ok": False, "error_code": "EMPTY_RESULT"}
    try:
        parsed = json.loads(output_text)
    except (ValueError, TypeError):
        return {"ok": False, "error_code": "INVALID_JSON"}
    if not isinstance(parsed, dict):
        return {"ok": False, "error_code": "INVALID_JSON"}
    if not _extract_ok(parsed):
        return {
            "ok": False,
            "error_code": str(parsed.get("error_code") or "TOOL_FAILURE"),
            "message": _preview_text(parsed.get("message") or ""),
        }

    summary: dict[str, Any] = {"ok": True}

    if tool_name == "sec_submissions":
        filings = parsed.get("filings")
        if isinstance(filings, list):
            entries = []
            for f in filings[:20]:
                if not isinstance(f, dict):
                    continue
                form = f.get("form") or f.get("form_type")
                locator = {
                    "form": form,
                    "accession": f.get("accession_number"),
                    "filing_date": str(f.get("filing_date") or ""),
                    "report_date": str(f.get("report_date") or ""),
                    "locator": f"accn={f.get('accession_number') or 'unknown'}; "
                    f"form={form or 'unknown'}",
                }
                entries.append(locator)
            summary["count"] = len(filings)
            summary["filings"] = entries
        return summary

    if tool_name == "sec_company_facts":
        facts = parsed.get("facts")
        if isinstance(facts, list):
            entries = []
            for fact in facts[:20]:
                if not isinstance(fact, dict):
                    continue
                entries.append(
                    {
                        "concept": fact.get("concept"),
                        "period": str(
                            fact.get("period_end") or fact.get("instant_date") or ""
                        ),
                        "unit": fact.get("unit"),
                        "locator": "accn="
                        + str(fact.get("accession_number") or "unknown")
                        + "; concept="
                        + str(fact.get("concept") or "unknown"),
                    }
                )
            summary["count"] = len(facts)
            summary["facts"] = entries
        return summary

    if tool_name == "web_search":
        raw_results = parsed.get("results")
        if isinstance(raw_results, list):
            entries = []
            for item in raw_results[:10]:
                if not isinstance(item, dict):
                    continue
                # url 保留完整字符串（不含 query 密钥；Serper 返回无密钥参数）
                entries.append(
                    {
                        "title": _preview_text(item.get("title") or ""),
                        "url": _preview_text(item.get("url") or ""),
                        "publisher": item.get("publisher"),
                    }
                )
            summary["count"] = len(raw_results)
            summary["results"] = entries
        else:
            summary["count"] = parsed.get("count") if isinstance(parsed.get("count"), int) else None
        return summary

    # Calculator / FactQuery / 其他工具：只保留有界预览（不逐字段猜测）。
    summary["preview"] = _preview_text(json.dumps(parsed, ensure_ascii=False, default=str))
    return summary


def build_llm_response_summary(
    kind: str,
    *,
    role: str | None = None,
    content_chars: int = 0,
    finish_reason: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    tool_call_count: int = 0,
    error_code: str | None = None,
) -> dict[str, Any]:
    """构造 LLM 调用结果摘要（content / tool_calls / empty 三态）。

    - ``kind`` 取值：``content`` / ``tool_calls`` / ``empty``；
    - 只含计数/长度/角色/模型等低基数字段，绝不包含回复正文或工具参数；
    - 本摘要直接传给 ``DiagnosticCapture.capture``（内部仍会做纵深脱敏）。
    """
    return {
        "kind": kind,
        "role": role,
        "content_chars": int(content_chars),
        "finish_reason": finish_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_call_count": int(tool_call_count),
        "error_code": error_code,
    }
