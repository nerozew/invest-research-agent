"""P05-14 evals 数据集校验器（离线，不调用模型）。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

DATASET_PATH = Path(__file__).resolve().parents[1] / "evals" / "dataset.json"

_SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9]{10,}",
    r"SERPER[_-]?API[_-]?KEY\s*[:=]\s*\S+",
    r"LLM[_-]?API[_-]?KEY\s*[:=]\s*\S+",
    r"Authorization\s*[:=]\s*\S+",
)


@pytest.fixture(scope="module")
def dataset() -> dict:
    if not DATASET_PATH.exists():
        pytest.skip("evals/dataset.json 未生成：请先运行 evals/build_dataset.py")
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def test_exactly_100_cases(dataset: dict) -> None:
    assert len(dataset["cases"]) == 100
    assert dataset["schema_version"] == "evals_dataset_v1"


def test_case_ids_unique(dataset: dict) -> None:
    ids = [c["case_id"] for c in dataset["cases"]]
    assert len(ids) == len(set(ids)) == 100


def test_cik_10_digit(dataset: dict) -> None:
    for c in dataset["cases"]:
        assert re.fullmatch(r"\d{10}", c["cik"]), f"CIK 非 10 位: {c['cik']}"


def test_20_companies_each_5_scenarios(dataset: dict) -> None:
    companies = Counter(c["ticker"] for c in dataset["cases"])
    assert len(companies) == 20
    for ticker, count in companies.items():
        assert count == 5, f"{ticker} 应有 5 场景，实际 {count}"


def test_scenario_distribution(dataset: dict) -> None:
    scenarios = Counter(c["scenario"] for c in dataset["cases"])
    assert scenarios == {
        "annual_10k_zh": 20,
        "quarterly_10q_zh": 20,
        "combined_zh": 20,
        "historical_asof": 20,
        "annual_10k_en": 20,
    }


def test_dates_fixed_no_today(dataset: dict) -> None:
    today = date.today()
    valid_dates = ("2025-10-31", "2024-06-30")
    for c in dataset["cases"]:
        as_of = date.fromisoformat(c["as_of_date"])
        assert as_of != today, "不得使用动态 today"
        assert c["as_of_date"] in valid_dates, "as_of 必须固定历史日期"


def test_language_valid(dataset: dict) -> None:
    for c in dataset["cases"]:
        assert c["language"] in ("zh-CN", "en")


def test_requested_forms_valid(dataset: dict) -> None:
    for c in dataset["cases"]:
        assert set(c["requested_forms"]) <= {"10-K", "10-Q"}
        assert c["requested_forms"]


def test_source_types_have_sec(dataset: dict) -> None:
    for c in dataset["cases"]:
        assert "sec_filing" in c["expected_source_types"]


def test_no_secrets(dataset: dict) -> None:
    raw = json.dumps(dataset, ensure_ascii=False)
    for pattern in _SECRET_PATTERNS:
        assert not re.search(pattern, raw), f"数据集疑似含密钥: {pattern}"
