"""SEC EDGAR 在线公司搜索（第三层兜底）：本地快照解析不到时按名称实时搜索拿 CIK。

best-effort：无候选/网络失败返回 []（不阻塞调用方）。
SEC 合规：请求带显式 User-Agent；httpx client 由 infrastructure 注入（复用 build_http_client）。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

_EDGAR_COMPANY_SEARCH = "https://www.sec.gov/cgi-bin/browse-edgar"


def _localname(tag: str) -> str:
    """取 XML tag 的本地名（剥掉命名空间前缀），用于命名空间无关的匹配。

    单家命中时 ``<company-info>`` 继承 feed 的 Atom 命名空间（``{atom}company-info``），
    而多命中时它是 ``<content type="text/xml">`` 内无命名空间的裸标签
    （``company-info``）——只按 ``{atom}`` 前缀匹配会漏掉后者。
    """
    return tag.split("}")[-1]


def _parse_company_search(atom_xml: str) -> list[dict[str, str]]:
    """解析 browse-edgar getcompany atom 响应，提取 {cik, legal_name, ticker} 候选。

    真实 SEC 响应把公司放在 ``<company-info>`` 块（``<cik-href>`` 含 10 位 CIK、
    ``<conformed-name>`` 为法定名称）；单家精确命中时它在 feed 顶层（带 Atom
    命名空间），多家命中时嵌套在 ``<entry><content type="text/xml">`` 内且
    ``<company-info>`` 是无命名空间裸标签。解析对命名空间无关，两种都识别。
    ``<entry>`` 其余场景是该公司近期申报（form 标题），不是候选公司，不能当作
    搜索结果。保留旧 ``<entry><title>`` + ``<link href="...CIK=...">`` 格式兜底，
    兼容历史 mock 与其它变体；按 CIK 去重。
    """
    try:
        root = ET.fromstring(atom_xml)
    except ET.ParseError:
        return []

    def _findtext_local(parent: ET.Element, localname: str) -> str:
        """取直接子元素中本地名为 localname 的文本（命名空间无关）。"""
        for child in parent:
            if _localname(child.tag) == localname:
                return (child.text or "").strip()
        return ""

    def _company_info_to_candidate(company_info: ET.Element) -> dict[str, str] | None:
        cik_href = _findtext_local(company_info, "cik-href")
        match = re.search(r"CIK=(\d{10})", cik_href)
        cik = match.group(1) if match else _findtext_local(company_info, "cik")
        # 单命中时名称在 <conformed-name> 元素；多命中时该元素缺失，名称在
        # <company-info name="..."> 属性里（通常是 ARRAY(...) 残留）——回退到属性。
        legal_name = (
            _findtext_local(company_info, "conformed-name")
            or (company_info.get("name") or "").strip()
        )
        # 必须同时拿到 10 位 CIK 与名称才构成候选。多命中时名称可能是 ARRAY(...)
        # 残留（SEC 端把 Perl 结构残留进 atom，但 CIK 真实），保留给上层用
        # submissions API 补名；空名称无法补名，不构成候选。
        if legal_name and len(cik) == 10 and cik.isdigit():
            return {"cik": cik, "legal_name": legal_name, "ticker": ""}
        return None

    candidates: list[dict[str, str]] = []
    seen_ciks: set[str] = set()
    for element in root.iter():
        if _localname(element.tag) != "company-info":
            continue
        candidate = _company_info_to_candidate(element)
        if candidate is not None and candidate["cik"] not in seen_ciks:
            candidates.append(candidate)
            seen_ciks.add(candidate["cik"])
    if candidates:
        return candidates

    # 旧格式兜底：<entry><title> + <link href="...CIK=...">（命名空间无关）
    for entry in root.iter():
        if _localname(entry.tag) != "entry":
            continue
        title = _findtext_local(entry, "title")
        cik = ""
        for link in entry:
            if _localname(link.tag) != "link":
                continue
            href = link.get("href") or ""
            match = re.search(r"CIK=(\d{10})", href)
            if match:
                cik = match.group(1)
        if title and cik:
            candidates.append({"cik": cik, "legal_name": title, "ticker": ""})
    return candidates


def _looks_like_array_residue(name: str) -> bool:
    """判断名称是否为 SEC browse-edgar 多命中时的 Perl 结构残留（ARRAY(...)）。"""
    return name.startswith("ARRAY(")


def _fetch_name_by_cik(cik: str, client: Any, user_agent: str) -> str:
    """按 CIK 调 SEC submissions API 拿真实公司名；失败返回空串。"""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        response = client.get(url, headers={"User-Agent": user_agent}, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        return str(data.get("name", "")).strip()
    except Exception:  # noqa: BLE001 - best-effort
        return ""


def search_company_by_name(name: str, client: Any, user_agent: str) -> list[dict[str, str]]:
    """按公司名调 SEC browse-edgar 搜索；best-effort，失败/无候选返回 []。

    多命中场景 SEC 会把 <conformed-name> 渲染成 ARRAY(...) 残留（CIK 仍真实），
    这里对这类候选逐个调 submissions API 补真实名称；补名失败则跳过（不引入垃圾名）。
    """
    params = f"?action=getcompany&company={quote(name)}&output=atom&count=10"
    try:
        response = client.get(
            _EDGAR_COMPANY_SEARCH + params,
            headers={"User-Agent": user_agent, "Accept": "application/atom+xml"},
            timeout=30.0,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - 在线兜底 best-effort
        return []

    candidates = _parse_company_search(response.text)
    enriched: list[dict[str, str]] = []
    for cand in candidates:
        legal_name = cand["legal_name"]
        if not legal_name or _looks_like_array_residue(legal_name):
            real = _fetch_name_by_cik(cand["cik"], client, user_agent)
            if not real:
                continue  # 补名失败 → 跳过（不引入垃圾名）
            legal_name = real
        enriched.append(
            {"cik": cand["cik"], "legal_name": legal_name, "ticker": cand.get("ticker", "")}
        )
    return enriched
