"""P05-04 输入 hash 与下游失效测试。

验证目标（docs/04-WORKFLOW-RELIABILITY.md §6）：
- 同输入必同 hash（确定性）；不同 stage / 不同输入 hash 不同；
- 改 prompt / 公式 / schema 版本 → hash 变 → 判定需重算；
- 无缓存 hash → 重算；hash 一致且 schema 版本匹配 → 可复用。
"""

from __future__ import annotations

from invest_research.application.versioning import (
    VersioningService,
    compute_input_hash,
    should_recompute,
)

_BASE_INPUTS: dict[str, str] = {"company": "MSFT", "as_of": "2025-12-31"}
_BASE_PROMPTS: dict[str, str] = {"research": "hash-a", "analysis": "hash-b", "writer": "hash-c"}
_BASE_FORMULAS: dict[str, str] = {"gross_margin": "v1", "roa": "v1"}
_BASE_SCHEMAS: dict[str, str] = {"research_pack": "v1", "analysis_pack": "v1"}


# ---------------------------------------------------------------------------
# compute_input_hash：确定性
# ---------------------------------------------------------------------------


def test_same_input_same_hash() -> None:
    """同输入必得同 hash（确定性）。"""
    h1 = compute_input_hash("02-research", forward_inputs=_BASE_INPUTS)
    h2 = compute_input_hash("02-research", forward_inputs=_BASE_INPUTS)
    assert h1 == h2


def test_different_stage_different_hash() -> None:
    """不同 stage 的 hash 不同。"""
    a = compute_input_hash("02-research", forward_inputs=_BASE_INPUTS)
    b = compute_input_hash("04-analysis", forward_inputs=_BASE_INPUTS)
    assert a != b


def test_hash_changes_when_prompt_changes() -> None:
    """提示词 hash 变化 → 输入 hash 变化（下游失效）。"""
    h1 = compute_input_hash("05-writer", prompt_hashes=_BASE_PROMPTS)
    h2 = compute_input_hash("05-writer", prompt_hashes={**_BASE_PROMPTS, "writer": "hash-d"})
    assert h1 != h2


def test_hash_changes_when_formula_version_changes() -> None:
    """公式版本变化 → 输入 hash 变化。"""
    h1 = compute_input_hash("04-analysis", formula_versions=_BASE_FORMULAS)
    h2 = compute_input_hash("04-analysis", formula_versions={**_BASE_FORMULAS, "roa": "v2"})
    assert h1 != h2


def test_dict_key_order_does_not_affect_hash() -> None:
    """字典键顺序不影响 hash（sort_keys）。"""
    h1 = compute_input_hash("04-analysis", forward_inputs={"a": "1", "b": "2"})
    h2 = compute_input_hash("04-analysis", forward_inputs={"b": "2", "a": "1"})
    assert h1 == h2


# ---------------------------------------------------------------------------
# should_recompute
# ---------------------------------------------------------------------------


def test_recompute_when_no_stored_hash() -> None:
    """无缓存 hash → 必须重算。"""
    assert should_recompute("hash-x", None) is True


def test_recompute_when_hash_differs() -> None:
    """hash 不一致 → 必须重算。"""
    assert should_recompute("hash-current", "hash-stale") is True


def test_reuse_when_hash_same_and_schema_match() -> None:
    """hash 一致且 schema 版本匹配 → 可复用。"""
    assert should_recompute("hash-x", "hash-x") is False


def test_recompute_when_schema_version_mismatch() -> None:
    """schema 版本不匹配 → 必须重算（升级作废旧结果）。"""
    current_schema = {"research_pack": "v2"}
    expected_schema = {"research_pack": "v1"}
    assert (
        should_recompute(
            "hash-x",
            "hash-x",
            current_schema_versions=current_schema,
            expected_schema_versions=expected_schema,
        )
        is True
    )


# ---------------------------------------------------------------------------
# VersioningService
# ---------------------------------------------------------------------------


def test_service_returns_decision_and_hash() -> None:
    """VersioningService 输出决策 + hash。"""
    service = VersioningService()
    decision = service.recompute_decision(
        "04-analysis",
        None,  # 无缓存 → 应重算
        forward_inputs=_BASE_INPUTS,
        formula_versions=_BASE_FORMULAS,
        prompt_hashes=_BASE_PROMPTS,
        schema_versions=_BASE_SCHEMAS,
    )
    assert decision.step_name == "04-analysis"
    assert decision.recompute_needed is True
    assert len(decision.input_hash) == 64  # sha256 hex


def test_service_reuse_when_everything_matches() -> None:
    """缓存 hash 一致 + schema 匹配 → 可复用（False）。"""
    service = VersioningService()
    first = service.recompute_decision(
        "05-writer",
        None,
        forward_inputs=_BASE_INPUTS,
        prompt_hashes=_BASE_PROMPTS,
        schema_versions=_BASE_SCHEMAS,
        expected_schema_versions=_BASE_SCHEMAS,
    )
    second = service.recompute_decision(
        "05-writer",
        first.input_hash,  # 用第一次的 hash 作为缓存
        forward_inputs=_BASE_INPUTS,
        prompt_hashes=_BASE_PROMPTS,
        schema_versions=_BASE_SCHEMAS,
        expected_schema_versions=_BASE_SCHEMAS,
    )
    assert first.input_hash == second.input_hash
    assert second.recompute_needed is False
