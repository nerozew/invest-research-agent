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
