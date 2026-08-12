"""P02-13 财务 concept 映射配置契约测试（versioned mapping）。

验证目标（docs/05 P02-13 验收）：
- versioned mapping：配置带显式 version 字段，可校验、可失效重算；
- 同义 concept 选择：同一指标（如 revenue）可配置多个 XBRL concept 候选，
  按优先级返回 available 中第一个匹配；
- 扩展 concept：公司自定义扩展 concept 可配置优先级，与 us-gaap 标准共存；
- 无匹配：available 中没有候选 → 返回 None（禁止臆造，交给计算层 not_computable）；
- 空/重复候选校验：配置非法时在加载阶段即失败（fail-fast）；
- 不修改数据库、不联网、不依赖外部服务。

数据文件：src/invest_research/financial/mappings/concepts_v1.json（标准库 json）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from invest_research.financial.concept_mapping import (
    CONCEPTS_V1_PATH,
    ConceptMapping,
    ConceptMappingEntry,
    load_concept_mapping,
    select_concept,
)

# 与 companyfacts_msft.json fixture 中实际存在的 concept 保持一致
_MSFT_REVENUE_CONCEPTS = ("Assets", "EarningsPerShareBasic")


def test_mapping_exposes_version() -> None:
    """映射必须携带显式 version（可追溯/可失效重算）。"""
    mapping = ConceptMapping(version="concept_mapping_v1", entries=[])
    assert mapping.version == "concept_mapping_v1"
    assert mapping.version.startswith("concept_mapping_v")


def test_select_first_priority_available() -> None:
    """同义选择：优先返回候选列表中最靠前且 available 含有的 concept。"""
    entry = ConceptMappingEntry(
        metric_name="revenue",
        candidates=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",  # 首选（us-gaap 标准）
            "Revenues",  # 次选（旧标准）
            "Revenue",  # 三选（常见别名）
        ),
    )
    mapping = ConceptMapping(version="v1", entries=(entry,))

    # available 同时含首选与次选 → 选首选
    assert (
        select_concept(
            mapping,
            "revenue",
            available_concepts=(
                "Revenues",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
            ),
        )
        == "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    # available 只含次选 → 选次选
    assert select_concept(mapping, "revenue", available_concepts=("Revenues",)) == "Revenues"


def test_select_extension_concept() -> None:
    """扩展 concept（公司自定义）可在优先级表末尾配置，available 命中时选中。"""
    entry = ConceptMappingEntry(
        metric_name="revenue",
        candidates=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "msft:RevenueCustom",  # 公司扩展
        ),
    )
    mapping = ConceptMapping(version="v1", entries=(entry,))

    assert (
        select_concept(
            mapping,
            "revenue",
            available_concepts=(
                "msft:RevenueCustom",
                "RevenueFromContractWithCustomerExcludingAssessedTax",
            ),
        )
        == "RevenueFromContractWithCustomerExcludingAssessedTax"  # 标准优先
    )
    assert (
        select_concept(mapping, "revenue", available_concepts=("msft:RevenueCustom",))
        == "msft:RevenueCustom"
    )


def test_no_match_returns_none() -> None:
    """available 无匹配 → None（禁止臆造 concept）。"""
    entry = ConceptMappingEntry(
        metric_name="revenue", candidates=("RevenueFromContractWithCustomerExcludingAssessedTax",)
    )
    mapping = ConceptMapping(version="v1", entries=(entry,))

    assert select_concept(mapping, "revenue", available_concepts=("Assets",)) is None
    # 未配置的指标 → None
    assert (
        select_concept(mapping, "gross_profit", available_concepts=_MSFT_REVENUE_CONCEPTS) is None
    )


def test_empty_candidates_rejected() -> None:
    """空候选列表 → 配置非法（fail-fast）。"""
    with pytest.raises(ValidationError):
        ConceptMappingEntry(metric_name="revenue", candidates=())


def test_empty_metric_name_rejected() -> None:
    """空指标名 → 配置非法。"""
    with pytest.raises(ValidationError):
        ConceptMappingEntry(metric_name=" ", candidates=("Revenues",))


def test_duplicate_candidates_rejected() -> None:
    """同一 entry 内重复候选 → 配置非法（优先级表必须无歧义）。"""
    with pytest.raises(ValidationError):
        ConceptMappingEntry(
            metric_name="revenue",
            candidates=("Revenues", "Revenues"),
        )


def test_load_builtin_mapping_v1() -> None:
    """加载内置 concepts_v1.json：version 正确、revenue 存在候选且可被选择。"""
    mapping = load_concept_mapping(CONCEPTS_V1_PATH)

    assert mapping.version == "concept_mapping_v1"
    assert any(e.metric_name == "revenue" for e in mapping.entries)

    revenue = next(e for e in mapping.entries if e.metric_name == "revenue")
    # 内置映射至少含 us-gaap 标准 + 常见别名（覆盖 P02-06 fixture 的 concept）
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in revenue.candidates

    # 对 companyfacts_msft.json 中能出现的 revenue concept，能选出一个具体 concept
    selected = select_concept(
        mapping,
        "revenue",
        available_concepts=(
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ),
    )
    assert selected == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_load_invalid_version_rejected(tmp_path: Path) -> None:
    """数据文件 version 缺失/非法 → 加载失败（fail-fast，防止使用者依赖无版本映射）。"""
    bad = tmp_path / "bad_concepts.json"
    bad.write_text(
        json.dumps({"entries": [{"metric_name": "revenue", "candidates": ["Revenues"]}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_concept_mapping(bad)


def test_roundtrip_serialization() -> None:
    """映射可 JSON 序列化/反序列化（不可变契约 round-trip）。"""
    entry = ConceptMappingEntry(
        metric_name="revenue",
        candidates=("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    )
    mapping = ConceptMapping(version="concept_mapping_v1", entries=(entry,))

    dumped = mapping.model_dump_json()
    restored = ConceptMapping.model_validate_json(dumped)
    assert restored == mapping
    assert restored.version == "concept_mapping_v1"
