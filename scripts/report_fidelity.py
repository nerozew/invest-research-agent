"""评测：报告内容保真（数字对账）+ 引用可解析（纯函数，零副作用）。

- ``extract_numbers``：从报告正文提取数字（跳过 citation marker / URL / 年份 /
  locator 注释 / 确定性章节）。
- ``evaluate_content_fidelity``：正文每个数字须能被 pack 的 fact.value /
  metric.value 匹配（含百分号近似）。**只进评测记录，不进生产门禁。**
- ``evaluate_citation_resolvability``：每个 citation key 能映射到真实 source；
  SEC 来源要求 locator 非空；WEB 来源要求有快照（可证明引用真实存在于该 URL）。

不调用 LLM/DB/网络。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from invest_research.application.report_draft_assembler import build_source_citation_key

# 跳过"确定性/直取"章节：其内容已是 SEC 原文，不应作为"LLM 数字保真"的检查对象。
DEFAULT_SKIP_SECTIONS: tuple[str, ...] = (
    "来源清单",
    "非投资建议声明",
    "数据限制",
    "管理层讨论与分析",
    "官方声明摘录",
    "财务报表",
)
# locator 注释（SEC 的 offset:/page: 与网页的 snapshot:）不属于报告数字。
_LOCATOR_RE = re.compile(r"locator=(?:offset|page|snapshot):\S+")
# 数字 + 可选中文单位/百分号（避免匹配 citation key / FY 年份 / URL）。
_NUMBER_RE = re.compile(
    r"(?<![\w])(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(\s*(?:千亿|百亿|十亿|亿|千万|百万|万|%)?)"
)
_UNIT_MULTIPLIERS: dict[str, Decimal] = {
    "千亿": Decimal(10) ** 11,
    "百亿": Decimal(10) ** 10,
    "十亿": Decimal(10) ** 9,
    "亿": Decimal(10) ** 8,
    "千万": Decimal(10) ** 7,
    "百万": Decimal(10) ** 6,
    "万": Decimal(10) ** 4,
}
# 百分号与比率匹配容忍度（0.5 个百分点，覆盖保留两位小数的展示误差）。
_PERCENT_EPSILON = Decimal("0.005")


@dataclass(frozen=True)
class ExtractedNumber:
    """正文中提取的一个数字。"""

    raw: str  # 原始文本（含单位/%）
    value: Decimal  # 已应用单位倍数的数值；百分号不除以 100（由 is_percent 标记）
    is_percent: bool


def _strip_noise(markdown: str) -> str:
    """去掉 locator 注释与 markdown 链接，避免其数字被误提取。"""
    text = _LOCATOR_RE.sub(" ", markdown)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", text)  # [label](url) → 空格
    return text


def _is_year(raw: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", raw))


def extract_numbers(
    markdown: str,
    *,
    skip_sections: Iterable[str] = DEFAULT_SKIP_SECTIONS,
) -> list[ExtractedNumber]:
    """从报告正文提取数字（跳过确定性章节、年份、locator、链接）。"""
    text = _strip_noise(markdown)
    for heading in skip_sections:
        text = re.sub(rf"(?s)##? ?{re.escape(heading)}.*?(?=##? |\Z)", " ", text)
    numbers: list[ExtractedNumber] = []
    for match in _NUMBER_RE.finditer(text):
        raw_number = match.group(1).replace(",", "")
        unit = (match.group(2) or "").strip()
        if _is_year(raw_number) and not unit:
            continue  # 纯年份（如 2024）不是财务事实
        try:
            value = Decimal(raw_number)
        except InvalidOperation:
            continue
        multiplier = _UNIT_MULTIPLIERS.get(unit)
        if multiplier is not None:
            value *= multiplier
        numbers.append(
            ExtractedNumber(raw=match.group(0).strip(), value=value, is_percent=(unit == "%"))
        )
    return numbers


def _decimal_of(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _item_value(item: object) -> object:
    if isinstance(item, dict):
        return item.get("value")
    return getattr(item, "value", None)


def _collect_values(items: Iterable[object]) -> list[Decimal]:
    return [d for item in items if (d := _decimal_of(_item_value(item))) is not None]


def evaluate_content_fidelity(
    report_md: str,
    facts: Iterable[object],
    metrics: Iterable[object],
    *,
    tolerance_ratio: float = 0.0,
) -> tuple[bool, list[str], dict]:
    """正文每个数字须能被 pack 的 fact.value / metric.value 匹配。

    - ``facts``：含 ``value`` 键的对象（FinancialFact 或 comparison facts）；
    - ``metrics``：含 ``value`` 键的对象（MetricResult，value 已是归一化比率）；
    - 百分号 ``X%`` 允许与某个 metric 比率近似（``|X/100 - m| <= 0.005``）；
    - 返回 ``(pass, failures, detail)``；``pass = unsupported/total <= tolerance_ratio``。
    """
    fact_values = _collect_values(facts)
    metric_values = _collect_values(metrics)
    supported: list[str] = []
    unsupported: list[str] = []
    for number in extract_numbers(report_md):
        matched = number.value in fact_values or number.value in metric_values
        if not matched and number.is_percent:
            target = number.value / 100
            matched = any(abs(target - mv) <= _PERCENT_EPSILON for mv in metric_values)
        (supported if matched else unsupported).append(number.raw)
    total = len(supported) + len(unsupported)
    rate = (len(supported) / total) if total else 1.0
    ok = rate >= (1.0 - tolerance_ratio)
    detail = {
        "total": total,
        "supported": len(supported),
        "unsupported": unsupported,
        "rate": round(rate, 4),
    }
    failures = []
    if unsupported:
        failures.append(
            "content_fidelity: "
            f"{len(unsupported)}/{total} 数字未匹配 pack（示例: {unsupported[:5]}）"
        )
    return ok, failures, detail


def _source_citation_key(url: str) -> str:
    """复用唯一 src_ 算法（report_draft_assembler.build_source_citation_key）。"""
    shim = type("CitationSourceShim", (), {"canonical_url": url})()
    return build_source_citation_key(shim)


def _is_sec_source(source: dict) -> bool:
    source_type = str(source.get("source_type") or "").lower()
    return "sec" in source_type or source_type in {"filing", "xbrl"}


def evaluate_citation_resolvability(
    research_pack: dict | None,
    report_draft: dict | None,
    *,
    web_snapshot_by_url: dict[str, str] | None = None,
) -> tuple[bool, list[str], dict]:
    """每个 citation key 都能映射到真实 source。

    - 复用 ``build_source_citation_key`` 算法（src_ + url sha256 前 12 位）；
    - SEC 来源要求 locator 非空（硬门槛）；
    - WEB 来源有快照则计 verified，否则标 not_content_verified（不判死，只统计）；
    - 返回 ``(pass, failures, detail)``。
    """
    sources = (research_pack or {}).get("sources") or []
    keys = (report_draft or {}).get("citation_keys") or []
    key_to_source: dict[str, dict] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("canonical_url") or ""
        if not url:
            continue
        key_to_source.setdefault(_source_citation_key(url), source)

    unresolved = [k for k in keys if k not in key_to_source]
    sec_missing_locator = []
    web_verified: list[str] = []
    web_unverified: list[str] = []
    for key in keys:
        source = key_to_source.get(key)
        if source is None:
            continue
        if _is_sec_source(source):
            if not source.get("locator"):
                sec_missing_locator.append(key)
        else:
            url = source.get("canonical_url") or ""
            snapshot = (web_snapshot_by_url or {}).get(url)
            if snapshot:
                web_verified.append(url)
            else:
                web_unverified.append(url)

    failures = []
    if unresolved:
        failures.append(f"citation_resolvability: 未解析引用 {unresolved}")
    if sec_missing_locator:
        failures.append(f"citation_resolvability: SEC 来源缺 locator {sec_missing_locator}")
    detail = {
        "citation_total": len(keys),
        "resolvable": len(keys) - len(unresolved),
        "unresolved_keys": unresolved,
        "sec_missing_locator": sec_missing_locator,
        "web_verified": len(web_verified),
        "web_unverified": web_unverified,
    }
    return (not unresolved and not sec_missing_locator), failures, detail


__all__ = [
    "DEFAULT_SKIP_SECTIONS",
    "ExtractedNumber",
    "evaluate_citation_resolvability",
    "evaluate_content_fidelity",
    "extract_numbers",
]
