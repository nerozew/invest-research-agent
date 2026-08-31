"""评测模块 report_fidelity 的单测（纯函数，无 LLM/DB/网络）。"""

from decimal import Decimal

from invest_research.application.report_draft_assembler import build_source_citation_key
from scripts.report_fidelity import (
    evaluate_citation_resolvability,
    evaluate_content_fidelity,
    extract_numbers,
)


def test_extract_numbers_normalizes_units_and_percent():
    md = "收入 2.45亿元，增长 35%，净利 1,234,567 美元。"
    nums = extract_numbers(md)
    values = {n.raw: (n.value, n.is_percent) for n in nums}
    assert values["2.45亿"][0] == Decimal("245000000")  # "元"不是单位，不进 raw
    assert values["35%"][1] is True
    assert values["1,234,567"][0] == Decimal("1234567")  # "美元"不是单位，不进 raw


def test_extract_numbers_skips_year_locator_and_links():
    md = (
        "公司于 2024 财年推出产品 [来源](https://www.sec.gov/x) locator=offset:29249\n"
        "## 管理层讨论与分析\n> Revenue increased 65% to $215.9 billion.\n"
    )
    nums = extract_numbers(md)
    raws = [n.raw for n in nums]
    assert "2024 财年" not in raws  # 纯年份跳过
    assert "2024" not in raws
    assert not any("locator" in raw or "offset" in raw for raw in raws)
    assert all("管理层讨论与分析" not in raw for raw in raws)


def test_content_fidelity_matches_facts_and_metrics():
    md = (
        "## 执行摘要\n"
        "营业收入 215,938,000,000 美元，毛利率为 0.7106808435754707369707971733（即 71.07%）。"
    )
    facts = [{"value": "215938000000"}, {"value": "130497000000"}]
    metrics = [
        {"value": "0.7106808435754707369707971733"},
        {"value": "0.6547353579009479145114447075"},
    ]
    ok, failures, detail = evaluate_content_fidelity(md, facts, metrics)
    assert ok
    assert detail["unsupported"] == []
    assert detail["supported"] == 3  # 原始美元值 + 比率 + 百分号


def test_content_fidelity_reports_unsupported_number():
    md = "## 执行摘要\n营业收入 999,999 美元。"
    facts = [{"value": "215938000000"}]
    ok, failures, detail = evaluate_content_fidelity(md, facts, [])
    assert not ok
    assert any("999,999" in raw for raw in detail["unsupported"])
    assert failures


def test_content_fidelity_percent_approx_within_epsilon():
    md = "## 执行摘要\n毛利率为 71.07%。"
    metrics = [{"value": "0.7106808435754707369707971733"}]
    ok, _, _ = evaluate_content_fidelity(md, [], metrics)
    assert ok


def test_citation_resolvability_sec_with_locator():
    url = "https://www.sec.gov/archives/x.htm"
    key = build_source_citation_key(type("S", (), {"canonical_url": url})())
    pack = {"sources": [{"canonical_url": url, "source_type": "sec_filing", "locator": "offset:1"}]}
    draft = {"citation_keys": [key]}
    ok, failures, detail = evaluate_citation_resolvability(pack, draft)
    assert ok
    assert detail["resolvable"] == 1


def test_citation_resolvability_sec_missing_locator_fails():
    url = "https://www.sec.gov/archives/x.htm"
    key = build_source_citation_key(type("S", (), {"canonical_url": url})())
    pack = {"sources": [{"canonical_url": url, "source_type": "sec_filing", "locator": None}]}
    draft = {"citation_keys": [key]}
    ok, failures, detail = evaluate_citation_resolvability(pack, draft)
    assert not ok
    assert detail["sec_missing_locator"] == [key]


def test_citation_resolvability_web_verified_with_snapshot():
    url = "https://news.example.com/a"
    key = build_source_citation_key(type("S", (), {"canonical_url": url})())
    pack = {"sources": [{"canonical_url": url, "source_type": "web", "locator": None}]}
    draft = {"citation_keys": [key]}
    ok, _, detail = evaluate_citation_resolvability(
        pack, draft, web_snapshot_by_url={url: "snapshot:abc"}
    )
    assert ok  # web 无 locator 不判死；有快照计 verified
    assert detail["web_verified"] == 1


def test_citation_resolvability_unresolved_key_fails():
    pack = {"sources": [{"canonical_url": "https://a.com", "source_type": "web"}]}
    draft = {"citation_keys": ["src_000000000000"]}
    ok, failures, detail = evaluate_citation_resolvability(pack, draft)
    assert not ok
    assert detail["unresolved_keys"] == ["src_000000000000"]


def test_fidelity_empty_report_rates_full():
    ok, failures, detail = evaluate_content_fidelity("## 执行摘要\n（无数字）", [], [])
    assert ok
    assert detail["rate"] == 1.0


def test_extract_numbers_rejects_invalid_decimal():
    # 无数字的正文不抛异常
    assert extract_numbers("纯文字说明。") == []
