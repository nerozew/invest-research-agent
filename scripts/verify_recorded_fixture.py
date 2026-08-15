"""P05-12 offline fixture verification (no network)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from invest_research.infrastructure.fixture import replay  # type: ignore[import-untyped]

FIXTURE = Path("tests/fixtures/sec_recorded_aapl.json")
SENSITIVE = [
    r"authorization",
    r"cookie",
    r"api[_-]?key",
    r"apikey",
    r"password",
    r"client[_-]?secret",
]
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def main() -> None:
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    s = json.dumps(d, ensure_ascii=False)
    bad = [p for p in SENSITIVE if re.search(p, s, re.IGNORECASE)]
    emails = set(EMAIL_RE.findall(s))
    r = replay(FIXTURE)
    assert r["payload"]["ticker"] == "AAPL"
    assert r["payload"]["cik"] == "0000320193"
    assert r["payload"]["as_of_date"] == "2025-10-31"
    print("meta:", d["meta"])
    print("sensitive found:", bad if bad else "none")
    print("emails found:", sorted(emails) if emails else "none")
    print("forms:", list(r["payload"]["submissions"]["filings"]["recent"]["form"][:5]))
    print("REPLAY OK" if not bad and not emails else "REPLAY FAILED")


if __name__ == "__main__":
    main()
