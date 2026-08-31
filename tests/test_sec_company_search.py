from invest_research.tools.sec_company_search import (
    _fetch_name_by_cik,
    _parse_company_search,
    search_company_by_name,
)

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


_REAL_ATOM_ARRAY_NAME = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <company-info>
    <cik-href>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000034088&amp;owner=include&amp;count=10</cik-href>
    <conformed-name>ARRAY(0x2)</conformed-name>
  </company-info>
</feed>"""


def test_parse_company_search_keeps_array_residue_candidate() -> None:
    """SEC 端缺陷：<conformed-name>ARRAY(...)</conformed-name> 是 Perl 结构残留，
    但 CIK 真实——解析保留候选，交由 search_company_by_name 用 submissions API 补名。"""
    parsed = _parse_company_search(_REAL_ATOM_ARRAY_NAME)
    assert parsed == [{"cik": "0000034088", "legal_name": "ARRAY(0x2)", "ticker": ""}]


# 真实 SEC 多命中：feed 用 atom: 前缀命名空间，而 <content type="text/xml"> 内的
# <company-info> 是无命名空间裸标签（ElementTree 下 tag 为纯 "company-info"）——
# 解析器必须命名空间无关，否则 {atom}company-info 匹配不到 → 0 候选。
_REAL_ATOM_MULTI_NAMESPACELESS = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns:atom="http://www.w3.org/2005/Atom">
  <atom:entry>
    <atom:content type="text/xml">
      <company-info name="ARRAY(0x1)">
        <cik>0001472373</cik>
        <conformed-name>ARRAY(0x1)</conformed-name>
      </company-info>
    </atom:content>
  </atom:entry>
</feed>"""


def test_parse_company_search_handles_namespaceless_company_info() -> None:
    """多命中真实结构：<company-info> 无命名空间裸标签，解析器须命名空间无关识别并保留候选。"""
    parsed = _parse_company_search(_REAL_ATOM_MULTI_NAMESPACELESS)
    assert parsed == [{"cik": "0001472373", "legal_name": "ARRAY(0x1)", "ticker": ""}]


def test_search_multi_hit_namespaceless_enriches_name_from_submissions() -> None:
    """命名空间无关解析后补名链路跑通：无命名空间 company-info 候选 + submissions API 补名。"""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-edgar" in str(request.url):
            return httpx.Response(200, text=_REAL_ATOM_MULTI_NAMESPACELESS)
        assert "submissions/CIK0001472373.json" in str(request.url)
        return httpx.Response(200, json={"name": "NESTLE SA"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidates = search_company_by_name("Nestle", client, "test-agent/1.0")
    assert candidates == [{"cik": "0001472373", "legal_name": "NESTLE SA", "ticker": ""}]


# 真实 SEC 多命中：<company-info> 块内只有 <cik> 与 name 属性，**没有**
# <conformed-name> 元素（该元素只在单命中场景出现）——legal_name 须回退到 name 属性。
_REAL_ATOM_MULTI_NAME_ATTR = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns:atom="http://www.w3.org/2005/Atom">
  <atom:entry>
    <atom:content type="text/xml">
      <company-info name="ARRAY(0x1)">
        <cik>0001472373</cik>
      </company-info>
    </atom:content>
  </atom:entry>
</feed>"""


def test_parse_company_search_falls_back_to_name_attr() -> None:
    """多命中真实结构：无 <conformed-name> 元素时 legal_name 回退到 company-info 的 name 属性。"""
    parsed = _parse_company_search(_REAL_ATOM_MULTI_NAME_ATTR)
    assert parsed == [{"cik": "0001472373", "legal_name": "ARRAY(0x1)", "ticker": ""}]


def test_search_multi_hit_name_attr_enriches_from_submissions() -> None:
    """name 属性回退后补名链路跑通：ARRAY(...) 属性候选 + submissions API 补名。"""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-edgar" in str(request.url):
            return httpx.Response(200, text=_REAL_ATOM_MULTI_NAME_ATTR)
        assert "submissions/CIK0001472373.json" in str(request.url)
        return httpx.Response(200, json={"name": "NESTLE SA"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidates = search_company_by_name("Nestle", client, "test-agent/1.0")
    assert candidates == [{"cik": "0001472373", "legal_name": "NESTLE SA", "ticker": ""}]


# ---------------------------------------------------------------------------
# 多命中补名：SEC 多命中时 <conformed-name> 会渲染成 ARRAY(...) 残留（CIK 真实），
# search_company_by_name 应改用 submissions API 补真实名称；补名失败则跳过。
# ---------------------------------------------------------------------------


def test_fetch_name_by_cik_uses_submissions_api() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert "data.sec.gov/submissions/CIK0001472373.json" in str(request.url)
        return httpx.Response(200, json={"name": "NESTLE SA"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert _fetch_name_by_cik("0001472373", client, "test-agent/1.0") == "NESTLE SA"


def test_search_multi_hit_enriches_name_from_submissions() -> None:
    import httpx

    # browse-edgar 多命中：conformed-name 为 ARRAY 残留，cik 真实
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <company-info name="ARRAY(0x1)">
          <cik>0001472373</cik>
          <conformed-name>ARRAY(0x1)</conformed-name>
        </company-info>
      </entry>
    </feed>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-edgar" in str(request.url):
            return httpx.Response(200, text=atom)
        assert "submissions/CIK0001472373.json" in str(request.url)
        return httpx.Response(200, json={"name": "NESTLE SA"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidates = search_company_by_name("Nestle", client, "test-agent/1.0")
    assert candidates == [{"cik": "0001472373", "legal_name": "NESTLE SA", "ticker": ""}]


def test_search_multi_hit_skips_when_enrich_fails() -> None:
    import httpx

    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <company-info name="ARRAY(0x1)">
          <cik>0001472373</cik>
          <conformed-name>ARRAY(0x1)</conformed-name>
        </company-info>
      </entry>
    </feed>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-edgar" in str(request.url):
            return httpx.Response(200, text=atom)
        return httpx.Response(500)  # submissions 失败

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert search_company_by_name("Nestle", client, "test-agent/1.0") == []
