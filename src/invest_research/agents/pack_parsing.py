"""P06-09 统一结构化 Agent 输出边界解析（pack 契约）。

职责：
- 从 CrewAI 输出对象 / dict / JSON 文本 / 带代码围栏文本统一解析为
  对应 Pydantic pack（ResearchPack / FinancialAnalysisPack / ReportDraft）；
- 任何无法解析为合法 pack 的输入统一抛 ``PackParseError``（带稳定
  error_code=SCHEMA_INVALID），供上层把任务标记为 failed 并保存错误码；
- 不再依赖"提示词保证 schema"：解析是确定性的，与 LLM 输出格式解耦。

依赖方向：本模块只依赖 domain（Pydantic 模型）+ 标准库，属于 agents/flows
共用的纯函数；infrastructure 的 flow_wiring 与 runner 复用本模块，
避免两处各自实现 JSON 提取与校验（收口 P06-09）。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

_PackModel = TypeVar("_PackModel", bound=BaseModel)

# 从 LLM 原始文本中提取 JSON 对象（贪婪匹配第一个 { 到最后一个 }，兼容围栏/前后缀）
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class PackParseError(RuntimeError):
    """结构化 pack 解析失败（稳定错误码 SCHEMA_INVALID，供失败分类读取）。"""

    error_code: str = "SCHEMA_INVALID"

    def __init__(self, model_name: str, message: str) -> None:
        super().__init__(f"无法解析 {model_name} 输出：{message}")
        self.model_name = model_name


def _validate_json_text(raw: str, model: type[_PackModel]) -> _PackModel:
    """解析 JSON 文本为模型；失败时尝试从文本中提取首个 JSON 对象（兼容围栏）。"""
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        match = _JSON_OBJECT_RE.search(raw)
        if match is not None:
            return model.model_validate_json(match.group())
        raise


def _reject_extra_fields(data: dict[str, Any], model: type[_PackModel]) -> None:
    """Pydantic 默认 extra=ignore 会静默忽略多余字段；契约要求明确拒绝。"""
    declared = set(model.model_fields.keys())
    extra = set(data.keys()) - declared
    if extra:
        raise PackParseError(
            model.__name__,
            f"输出包含未声明字段: {', '.join(sorted(extra))}",
        )


def parse_pack_output(obj: Any, model: type[_PackModel]) -> _PackModel:
    """从 Crew 输出对象解析为对应 pack（成功对象 / 字典 / JSON 文本）。

    - 已是该模型的实例直接返回；
    - CrewAI TaskOutput 优先取 ``pydantic`` / ``json_dict`` / ``exported_output``；
    - 字符串按 JSON 文本解析（兼容代码围栏/前后缀）；
    - 任何校验失败统一转 PackParseError（SCHEMA_INVALID），不吞错、不改写。
    """
    if isinstance(obj, model):
        return obj
    if isinstance(obj, str):
        try:
            return _validate_json_text(obj, model)
        except ValidationError as exc:
            raise PackParseError(
                model.__name__,
                "输出不是合法 JSON 文本（无法解析为结构化对象）",
            ) from exc
    json_dict = getattr(obj, "json_dict", None)
    if json_dict is None:
        json_dict = getattr(obj, "exported_output", None)
    try:
        if isinstance(json_dict, dict):
            _reject_extra_fields(json_dict, model)
            return model.model_validate(json_dict)
        raw = getattr(obj, "raw", None)
        if isinstance(raw, str):
            return _validate_json_text(raw, model)
        if isinstance(obj, dict):
            _reject_extra_fields(obj, model)
            return model.model_validate(obj)
    except ValidationError as exc:
        raise PackParseError(
            model.__name__,
            "输出不是合法结构化对象（Action/Action Input 是工具调用过程，不是最终答案）",
        ) from exc
    raise PackParseError(model.__name__, "无法识别的输出类型")


def dump_task_output(output: Any) -> dict[str, Any] | None:
    """把 CrewAI Task 的 ``output``（TaskOutput 或 dict/桩）转为可序列化 dict。

    - 供 ArtifactReader 的运行时 loader 读取上游 pack 真实内容；
    - 与 parse_pack_output 共用同一读取顺序（pydantic → json_dict/exported → raw），
      保证"解析侧读到的内容"与"Writer 工具侧读到的内容"一致。
    """
    if output is None:
        return None
    if isinstance(output, dict):
        return output
    pydantic = getattr(output, "pydantic", None)
    if pydantic is not None:
        try:
            dumped = pydantic.model_dump(mode="json")
        except AttributeError:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    for attr in ("json_dict", "exported_output"):
        value = getattr(output, attr, None)
        if isinstance(value, dict):
            return value
    raw = getattr(output, "raw", None)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None
