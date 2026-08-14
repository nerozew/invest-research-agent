"""输入 hash 与下游失效（P05-04）。

依据 `docs/04-WORKFLOW-RELIABILITY.md` §6「幂等与断点续跑」：
> 步骤幂等键为 `job_id + step_name + input_hash + schema_version`；
> 提示词、模型、公式或 schema 版本变化后，input hash 改变，
> 相关下游步骤必须失效重算。

设计：
- ``compute_input_hash``：把"阶段标识 + 上游版本(公式/模型/schema) + 提示词 hash"
  组成确定性 sha256。同一组输入必得同一 hash；任一输入变化 hash 即变。
- ``should_recompute``：当前计算出的 hash 与缓存 hash 不一致、或 schema 版本
  与期望不一致 → 需要重算；否则可复用。
- ``VersioningService``：组合以上两个函数，给调用方一个统一入口。

依赖边界：本层只允许导入标准库、domain（字段类型）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


def compute_input_hash(
    stage: str,
    *,
    forward_inputs: Mapping[str, str] | None = None,
    upstream_hashes: Mapping[str, str] | None = None,
    formula_versions: Mapping[str, str] | None = None,
    prompt_hashes: Mapping[str, str] | None = None,
    schema_versions: Mapping[str, str] | None = None,
) -> str:
    """计算某步骤的输入指纹（sha256 hex）。

    参与 hash 的组件（任一变化 → hash 变 → 下游失效）：
    - ``stage``：步骤名（如 "04-analysis"）——防不同步骤同 hash；
    - ``forward_inputs``：请求/上游非结构化输入（如公司名、as-of）；
    - ``upstream_hashes``：上游 pack 的 hash（如 research_pack hash）；
    - ``formula_versions``：指标公式版本（P02-15/16）；
    - ``prompt_hashes``：提示词 hash（P03-14 / loader.prompt_sha256）；
    - ``schema_versions``：pack schema 版本（P01-05）。
    """
    payload: dict[str, object] = {
        "stage": stage,
        "forward_inputs": dict(forward_inputs or {}),
        "upstream_hashes": dict(upstream_hashes or {}),
        "formula_versions": dict(formula_versions or {}),
        "prompt_hashes": dict(prompt_hashes or {}),
        "schema_versions": dict(schema_versions or {}),
    }
    # sort_keys=True 保证字典键顺序不影响 hash（确定性）。
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def should_recompute(
    current_hash: str,
    stored_hash: str | None,
    *,
    current_schema_versions: Mapping[str, str] | None = None,
    expected_schema_versions: Mapping[str, str] | None = None,
) -> bool:
    """判断是否必须重算。

    - 无缓存 hash（从未算过）→ 必须重算；
    - hash 不一致（任一输入/版本变化）→ 重算；
    - 当前 schema 版本与期望不一致 → 重算（schema 升级后旧结果作废）；
    - 其余情况 → 可复用（返回 False）。
    """
    if stored_hash is None or stored_hash != current_hash:
        return True

    current_versions = dict(current_schema_versions or {})
    expected_versions = dict(expected_schema_versions or {})
    # 期望版本集合必须完全匹配当前版本集合；任一 key 或值不同 → 重算。
    return current_versions != expected_versions


@dataclass(frozen=True)
class VersionDecision:
    """一个步骤的版本化决策结果。"""

    step_name: str
    input_hash: str
    recompute_needed: bool


class VersioningService:
    """输入 hash + 失效判断的统一入口。"""

    def recompute_decision(
        self,
        stage: str,
        stored_hash: str | None,
        *,
        forward_inputs: Mapping[str, str] | None = None,
        upstream_hashes: Mapping[str, str] | None = None,
        formula_versions: Mapping[str, str] | None = None,
        prompt_hashes: Mapping[str, str] | None = None,
        schema_versions: Mapping[str, str] | None = None,
        expected_schema_versions: Mapping[str, str] | None = None,
    ) -> VersionDecision:
        """计算当前 hash，并判断该步骤是否需要重算。"""
        current_hash = compute_input_hash(
            stage,
            forward_inputs=forward_inputs,
            upstream_hashes=upstream_hashes,
            formula_versions=formula_versions,
            prompt_hashes=prompt_hashes,
            schema_versions=schema_versions,
        )
        need = should_recompute(
            current_hash,
            stored_hash,
            current_schema_versions=schema_versions,
            expected_schema_versions=expected_schema_versions,
        )
        return VersionDecision(step_name=stage, input_hash=current_hash, recompute_needed=need)
