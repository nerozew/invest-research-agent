from invest_research.tools.sec_company_search import _parse_company_search, search_company_by_name

_ATOM = (
    """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>EXXON MOBIL CORP</title>
    <category term="cik"/>
    <link rel="self" href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000034088"""
    """&amp;type=10-K"/>
  </entry>
</feed>"""
)


def test_parse_company_search_extracts_candidates() -> None:
    parsed = _parse_company_search(_ATOM)
    assert parsed == [{"cik": "0000034088", "legal_name": "EXXON MOBIL CORP", "ticker": ""}]


def test_search_company_by_name_returns_candidates_with_mock_client() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("User-Agent")  # SEC 合规：必须带 UA
        return httpx.Response(200, text=_ATOM)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidates = search_company_by_name("Exxon Mobil", client, user_agent="test-agent/1.0")
    assert candidates and candidates[0]["cik"] == "0000034088"


def test_search_company_by_name_fails_gracefully() -> None:
    import httpx

    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(500)))
    assert search_company_by_name("X", client, "test-agent/1.0") == []


# 真实 SEC browse-edgar atom 响应把公司放在 <company-info>（<cik-href>/<conformed-name>），
# <entry> 是该公司近期申报而非候选公司；<entry title="ARRAY(...)"> 是 SEC 的 Perl 结构残留。
_REAL_ATOM_SINGLE = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <author><email>webmaster@sec.gov</email></author>
  <company-info>
    <cik-href>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000034088&amp;owner=include&amp;count=10</cik-href>
    <conformed-name>EXXON MOBIL CORP</conformed-name>
  </company-info>
  <entry>
    <title>SCHEDULE 13G  - Statement of Beneficial Ownership by Certain Investors</title>
    <link rel="self" href="https://www.sec.gov/Archives/edgar/data/34088/000009375126000461/0000093751-26-000461-index.htm"/>
  </entry>
</feed>"""

_REAL_ATOM_MULTI = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry title="ARRAY(0x1)">
    <content type="text/xml">
      <company-info name="ARRAY(0x2)">
        <cik-href>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000034088&amp;owner=include&amp;count=10</cik-href>
        <conformed-name>EXXON MOBIL CORP</conformed-name>
      </company-info>
    </content>
  </entry>
  <entry title="ARRAY(0x3)">
    <content type="text/xml">
      <company-info name="ARRAY(0x4)">
        <cik-href>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0002115436&amp;owner=include&amp;count=10</cik-href>
        <conformed-name>ExxonMobil Holdings Corp</conformed-name>
      </company-info>
    </content>
  </entry>
</feed>"""


def test_parse_company_search_extracts_real_single_match_company_info() -> None:
    """真实 SEC 单家精确命中格式：从顶层 <company-info> 提取候选，<entry>（申报）不误当公司。"""
    parsed = _parse_company_search(_REAL_ATOM_SINGLE)
    assert parsed == [{"cik": "0000034088", "legal_name": "EXXON MOBIL CORP", "ticker": ""}]


def test_parse_company_search_extracts_real_multi_match_company_info() -> None:
    """真实 SEC 多家命中格式：<entry><content> 内嵌套 <company-info> 均被提取并去重。"""
    parsed = _parse_company_search(_REAL_ATOM_MULTI)
    assert parsed == [
        {"cik": "0000034088", "legal_name": "EXXON MOBIL CORP", "ticker": ""},
        {"cik": "0002115436", "legal_name": "ExxonMobil Holdings Corp", "ticker": ""},
    ]
