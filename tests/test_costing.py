"""application/costing 共享计价单测（纯函数，无网络/DB）。"""

import json

from invest_research.application.costing import (
    estimate_job_cost,
    load_pricing,
    pricing_entry_for,
)

_PRICING = {
    "as_of": "2026-08-01",
    "models": {
        "deep": {"input_per_1m": 0.5, "output_per_1m": 1.5},
        "default": {"input_per_1m": 0.3, "output_per_1m": 0.9},
    },
}


def test_load_pricing_valid(tmp_path):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(_PRICING), encoding="utf-8")
    assert load_pricing(path) == _PRICING


def test_load_pricing_missing_or_invalid(tmp_path):
    assert load_pricing(None) is None
    assert load_pricing(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_pricing(bad) is None
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"as_of": "x"}), encoding="utf-8")
    assert load_pricing(empty) is None  # 缺 models


def test_pricing_entry_for_profile_and_default():
    assert pricing_entry_for(_PRICING, "deep") == _PRICING["models"]["deep"]
    assert pricing_entry_for(_PRICING, "fast") == _PRICING["models"]["default"]  # 回退 default
    assert pricing_entry_for(None, "deep") is None


def test_estimate_job_cost_math():
    cost = estimate_job_cost(
        input_tokens=1_000_000, output_tokens=1_000_000, pricing_entry=_PRICING["models"]["deep"]
    )
    assert cost == 2.0  # 1M*0.5 + 1M*1.5 = 2.0 USD


def test_estimate_job_cost_missing_tokens_or_pricing():
    assert estimate_job_cost(input_tokens=None, output_tokens=None, pricing_entry={}) is None
    assert estimate_job_cost(input_tokens=10, output_tokens=10, pricing_entry=None) is None
