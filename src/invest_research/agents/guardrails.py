"""P03-09 schema guardrail（确定性校验 + 修复反馈 + 限定重试语义）。

用途（docs/05 P03-09、docs/04 §4 SCHEMA_INVALID）：
- Agent 输出必须能通过对应 `output_pydantic`（ResearchPack / FinancialAnalysisPack /
  ReportDraft）校验；
- 不合法时用**确定性代码**（Pydantic ValidationError）提取错误、生成修复指令，
  而不是靠 LLM 自觉；
- 修复/重试次数必须**有上限**（尊重 docs/04 §5.1：LLM schema 修复最多限定次数）。

本模块为纯函数，不依赖 CrewAI；可完全离线单测。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

# 结构化输出契约：对应三个 Task 的 output_pydantic
Schema = TypeVar("Schema", bound=BaseModel)


def validate_against_schema(
    payload: str | dict[str, object], schema: type[Schema]
) -> tuple[bool, list[str]]:
    """用 Pydantic 校验 Agent 输出。

    - ``payload``：LLM 原始输出（JSON 字符串或 dict）；
    - 返回 ``(valid, errors)``：valid=False 时 errors 为可读的错误列表
      （含字段路径与原因），可用于生成修复指令。

    确定性：同一输入必然返回同一结果（不联网）。
    """
    if isinstance(payload, str):
        try:
            schema.model_validate_json(payload)
        except ValidationError as exc:
            return False, _format_validation_errors(exc)
        except ValueError as exc:
            return False, [f"输出不是合法 JSON: {exc}"]
        return True, []

    try:
        schema.model_validate(payload)
    except ValidationError as exc:
        return False, _format_validation_errors(exc)
    except ValueError as exc:
        return False, [f"输出格式不合法: {exc}"]
    return True, []


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """把 Pydantic ValidationError 转成可读、可喂回 LLM 的错误列表。"""
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}" if loc else msg)
    return lines


def build_fix_prompt(errors: list[str], schema_prompt_hint: str) -> str:
    """基于校验错误生成给 LLM 的修复指令（限定重试时反复使用）。"""
    error_block = "\n".join(f"- {e}" for e in errors)
    return (
        "上一次输出未通过结构化校验，请修正后重新输出。"
        f"\n{schema_prompt_hint}\n错误如下：\n{error_block}"
    )


class GuardrailOutcome(BaseModel):
    """guardrail 重试结果（可读、可断言）。"""

    valid: bool
    attempts_used: int
    errors: list[str] = []


def run_with_guardrail(
    producer: Callable[[str | None], str | dict[str, object]],
    schema: type[Schema],
    schema_prompt_hint: str,
    max_attempts: int = 3,
) -> GuardrailOutcome:
    """带修复反馈的重试循环（有上限）。

    - 每次 producer 产出 payload；
    - 合法 → 返回 valid=True；
    - 非法 → 用 build_fix_prompt 生成修复指令作为 hint，再让 producer 重产出；
    - 达到 max_attempts 仍非法 → 返回 valid=False（尊重 docs/04 §5.1 上限）。
    """
    hint: str | None = None
    last_errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        payload = producer(hint)
        valid, errors = validate_against_schema(payload, schema)
        if valid:
            return GuardrailOutcome(valid=True, attempts_used=attempt)
        last_errors = errors
        hint = build_fix_prompt(errors, schema_prompt_hint)
    return GuardrailOutcome(valid=False, attempts_used=max_attempts, errors=last_errors)
