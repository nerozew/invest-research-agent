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
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_company_search(atom_xml: str) -> list[dict[str, str]]:
    """解析 browse-edgar getcompany atom 响应，提取 {cik, legal_name, ticker} 候选。"""
    try:
        root = ET.fromstring(atom_xml)
    except ET.ParseError:
        return []
    candidates: list[dict[str, str]] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        cik = ""
        for link in entry.findall(f"{_ATOM_NS}link"):
            href = link.get("href") or ""
            match = re.search(r"CIK=(\d{10})", href)
            if match:
                cik = match.group(1)
        if title and cik:
            candidates.append({"cik": cik, "legal_name": title, "ticker": ""})
    return candidates


def search_company_by_name(name: str, client: Any, user_agent: str) -> list[dict[str, str]]:
    """按公司名调 SEC browse-edgar 搜索；best-effort，失败/无候选返回 []。"""
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
    return _parse_company_search(response.text)
