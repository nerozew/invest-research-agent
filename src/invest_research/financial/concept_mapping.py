"""P02-13 财务 concept 映射配置（versioned mapping）。

把 PRD 指标（revenue、gross_profit 等）映射到 SEC XBRL 的 concept 候选优先级表；
对同义 concept（us-gaap 标准、旧标准别名、公司扩展）按配置顺序选择 available
中首个命中：
- 无匹配返回 None：禁止臆造，交给计算层返回 not_computable；
- version 字段用于可追溯与 P05-04 输入失效重算；
- 数据文件为版本化 JSON，用标准库 json 加载（零新增依赖，满足 "versioned mapping"）。

依赖边界：本层只依赖标准库与 Pydantic；禁止导入 CrewAI/FastAPI/SQLAlchemy/httpx。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONCEPTS_V1_PATH = Path(__file__).parent / "mappings" / "concepts_v1.json"


class ConceptMappingEntry(BaseModel):
    """单个指标的 concept 候选优先级表（按列表顺序降权）。"""

    model_config = ConfigDict(frozen=True)

    metric_name: str = Field(min_length=1)
    candidates: tuple[str, ...]

    @field_validator("metric_name")
    @classmethod
    def _strip_metric(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("metric_name 不能为空")
        return cleaned

    @field_validator("candidates")
    @classmethod
    def _candidates_nonempty_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("candidates 至少需要一个 concept")
        if len(set(value)) != len(value):
            raise ValueError("candidates 不能包含重复 concept")
        return value


class ConceptMapping(BaseModel):
    """版本化映射集合（version 必填，缺失即视为配置非法）。"""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    entries: tuple[ConceptMappingEntry, ...] = ()


def load_concept_mapping(path: Path) -> ConceptMapping:
    """从 JSON 数据文件加载并校验映射（fail-fast：缺 version/非法条目即失败）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ConceptMapping.model_validate(raw)


def select_concept(
    mapping: ConceptMapping, metric_name: str, available_concepts: tuple[str, ...]
) -> str | None:
    """按优先级选择 available 中第一个候选 concept；无匹配返回 None。"""
    available = set(available_concepts)
    for entry in mapping.entries:
        if entry.metric_name == metric_name:
            for candidate in entry.candidates:
                if candidate in available:
                    return candidate
            return None
    return None
