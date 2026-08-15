"""P05-14 生成 20 公司 × 5 场景基准数据集（确定性，无动态 today）。

用法：`uv run python evals/build_dataset.py` -> 生成 `evals/dataset.json`。

设计：
- 20 家美国上市公司，覆盖不同行业、不同规模、不同财年结束月份、
  不同申报复杂度（CIK 均为真实 10 位数字）；
- 每家公司固定 5 个场景：
    1. annual_10k_zh       10-K 中文年报
    2. quarterly_10q_zh    10-Q 中文季报
    3. combined_zh         10-K + 10-Q 综合中文
    4. historical_asof     历史截止测试（更早固定日期，验证"无未来数据"）
    5. annual_10k_en       10-K 英文年报
- 所有 as_of_date 均为固定日期（不做 date.today()），保证可复现；
- 语言仅使用 zh-CN / en（对齐 domain.ResearchRequest 合法值）。

每家公司数据格式：
(company, ticker, cik, industry, fiscal_year_end)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

# (company, ticker, cik, industry, fiscal_year_end)
# fiscal_year_end 表达公司财年结束月份（用于数据集纪律；as_of 仍为固定全局日期）
_COMPANIES: tuple[tuple[str, str, str, str, int], ...] = (
    ("Apple Inc.", "AAPL", "0000320193", "consumer_electronics", 9),
    ("Microsoft Corporation", "MSFT", "0000789019", "software", 6),
    ("Amazon.com Inc.", "AMZN", "0001018724", "ecommerce", 12),
    ("Alphabet Inc.", "GOOGL", "0001652044", "technology", 12),
    ("Meta Platforms Inc.", "META", "0001326801", "social_media", 12),
    ("JPMorgan Chase & Co.", "JPM", "0000019617", "banking", 12),
    ("Johnson & Johnson", "JNJ", "0000200406", "healthcare", 12),
    ("Walmart Inc.", "WMT", "0000104169", "retail", 1),
    ("Exxon Mobil Corporation", "XOM", "0000034088", "energy", 12),
    ("The Procter & Gamble Company", "PG", "0000080424", "consumer_goods", 6),
    ("Tesla Inc.", "TSLA", "0001318605", "automotive", 12),
    ("NVIDIA Corporation", "NVDA", "0001045810", "semiconductors", 1),
    ("Netflix Inc.", "NFLX", "0001065280", "streaming", 12),
    ("The Coca-Cola Company", "KO", "0000021344", "beverages", 12),
    ("The Boeing Company", "BA", "0000012927", "aerospace", 12),
    ("The Walt Disney Company", "DIS", "0001744489", "entertainment", 9),
    ("Caterpillar Inc.", "CAT", "0000018230", "industrial", 12),
    ("Visa Inc.", "V", "0001403161", "payments", 9),
    ("UnitedHealth Group Inc.", "UNH", "0000731766", "health_insurance", 12),
    ("The Home Depot Inc.", "HD", "0000354950", "retail", 1),
)

# (scenario_id, language, requested_forms, as_of_date, description)
_SCENARIOS: tuple[tuple[str, str, tuple[str, ...], date, str], ...] = (
    ("annual_10k_zh", "zh-CN", ("10-K",), date(2025, 10, 31), "10-K 中文年报"),
    ("quarterly_10q_zh", "zh-CN", ("10-Q",), date(2025, 10, 31), "10-Q 中文季报"),
    ("combined_zh", "zh-CN", ("10-K", "10-Q"), date(2025, 10, 31), "10-K + 10-Q 综合中文"),
    ("historical_asof", "zh-CN", ("10-K", "10-Q"), date(2024, 6, 30), "历史截止测试"),
    ("annual_10k_en", "en", ("10-K",), date(2025, 10, 31), "10-K 英文年报"),
)


def build_dataset() -> list[dict[str, Any]]:
    """构造 100 条 case（20 公司 × 5 场景，确定性顺序）。"""
    cases: list[dict[str, Any]] = []
    for company, ticker, cik, industry, fye in _COMPANIES:
        for scenario_id, language, requested_forms, as_of, description in _SCENARIOS:
            case_id = f"{ticker.lower()}-{scenario_id}"
            cases.append(
                {
                    "case_id": case_id,
                    "company": company,
                    "ticker": ticker,
                    "cik": cik,
                    "industry": industry,
                    "scenario": scenario_id,
                    "as_of_date": as_of.isoformat(),
                    "language": language,
                    "requested_forms": list(requested_forms),
                    "expected_source_types": ["sec_filing"],
                    "fiscal_year_end": fye,
                    "description": description,
                }
            )
    return cases


def write_dataset(dest: Path) -> Path:
    """把数据集写入 JSON（固定缩进、确定性键序）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "evals_dataset_v1",
        "generated_on": "fixed",  # 不写真实日期，保证 Git 可复现
        "note": "20 公司 × 5 场景 = 100 条；所有 as_of_date 固定，无动态 today；无密钥。",
        "cases": build_dataset(),
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


if __name__ == "__main__":
    out = write_dataset(Path(__file__).resolve().parent / "dataset.json")
    print(f"生成 {len(build_dataset())} 条 case -> {out}")
