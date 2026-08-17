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
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

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


# ---------------------------------------------------------------------------
# P06-09B：统一 PackBoundary（在 P06-09 parse_pack_output 之上扩展，不重复建解析器）
# ---------------------------------------------------------------------------
#
# PackBoundary 的职责：
# 1. 从 Crew 输出提取候选结果（Pydantic / dict / JSON 文本 / ```json 围栏文本）；
# 2. 区分「Agent 最终答案 / 工具调用参数 / Action Input / 普通自然语言说明」——
#    工具调用参数与 Action Input 不是最终 Pack，会被拒绝；
# 3. 分层校验：JSON/结构解析 → Pydantic schema 校验 → 多余字段检查 → 业务跨字段校验；
# 4. 返回结构化错误（error_code / stage / field / expected / actual / 脱敏 detail）；
# 5. 有限修复：仅 JSON/字段结构类错误允许至多一次 LLM 修复；业务事实/网络错误不进入
#    格式修复；数据缺失应转换为 partial/unavailable（由模型层处理），不允许修复器发明
#    财务数据；修复失败返回原始稳定错误分类。
#
# 重复使用 pack_parsing 既有读取顺序（pydantic → json_dict/exported → raw），
# 保证「解析侧」与「ArtifactReader/dump_task_output 侧」读到一致内容。


class PackSourceKind(StrEnum):
    """候选结果来源分类（P06-09B）。"""

    FINAL_ANSWER = "final_answer"  # Agent 最终结构化答案
    TOOL_PARAMS = "tool_params"  # 工具调用参数（不是最终 Pack）
    ACTION_INPUT = "action_input"  # CrewAI Action Input（不是最终 Pack）
    PLAIN_TEXT = "plain_text"  # 普通自然语言说明（没有结构，不是 Pack）


# 常见工具调用/Action 结构键（用于拒绝"工具过程"而非"最终答案"）
_TOOL_FRAME_KEYS: frozenset[str] = frozenset(
    {"action", "action_input", "tool_name", "tool_input", "tool", "arguments"}
)


class BoundaryError(BaseModel):
    """PackBoundary 结构化错误（可脱敏、可喂回修复器）。

    - ``error_code``：稳定短码（JSON_INVALID / SCHEMA_INVALID / EXTRA_FIELD /
      MISSING_FIELD / VALUE_INVALID / NOT_A_PACK）；
    - ``stage``：产生该错误的处理阶段（extract / schema / semantics / repair）；
    - ``field``：字段路径（点分隔，如 "completeness" / "facts.0.value"）；
    - ``expected``：期望值/类型描述；
    - ``actual``：实际值/类型描述（自动脱敏：截断 + 去绝对路径）；
    - ``detail``：人类可读说明（去敏感信息）。
    """

    model_config = ConfigDict(frozen=True)

    error_code: str
    stage: str
    field: str | None = None
    expected: str | None = None
    actual: str | None = None
    detail: str | None = None


def _sanitize(value: object, limit: int = 200) -> str:
    """把值转成可展示字符串并脱敏：
    - 截断过长内容；
    - 移除形如 C:\\... / /home/... 的本地绝对路径；
    - 移除疑似密钥的键值（api_key / token / password / cookie / key 等）。
    """
    text = (
        json.dumps(value, ensure_ascii=False, default=str)
        if not isinstance(value, str)
        else value
    )
    text = re.sub(r"[A-Za-z]:\\[^\s,;\"']+|/[A-Za-z0-9_./-]{3,}", "<PATH>", text)
    for secret_key in (
        "api_key",
        "apikey",
        "token",
        "password",
        "cookie",
        "secret",
        "authorization",
    ):
        text = re.sub(
            rf'"{secret_key}"\s*:\s*"[^"]*"',
            f'"{secret_key}": "<REDACTED>"',
            text,
            flags=re.IGNORECASE,
        )
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _validation_errors_to_boundary(
    exc: ValidationError, stage: str
) -> list[BoundaryError]:
    """把 Pydantic ValidationError 转成结构化 BoundaryError 列表。"""
    out: list[BoundaryError] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        etype = str(err.get("type", ""))
        # P06-09B：业务跨字段矛盾（model_validator value_error，如 completeness 与内容
        # 冲突）标记为 semantics 阶段，**不进入格式修复**（禁止修复器发明/改写业务数据）。
        # 字段结构错误（类型/缺失/多余）标记为 schema 阶段，允许一次确定性修复。
        error_stage = "semantics" if etype == "value_error" else stage
        if "extra" in etype:
            code = "EXTRA_FIELD"
            expected = "字段集合 {declared}"
            actual = "未声明字段"
        elif "missing" in etype:
            code = "MISSING_FIELD"
            expected = "必填字段"
            actual = "缺失"
        elif "json_invalid" in etype or "json" in etype:
            code = "JSON_INVALID"
            expected = "合法 JSON"
            actual = "不可解析文本"
        elif etype == "value_error":
            code = "VALUE_INVALID"  # 业务跨字段矛盾（不可修复，见 error_stage）
            expected = None
            actual = _sanitize(str(err.get("msg", ""))[:200])
        else:
            code = "VALUE_INVALID"
            expected = str(err.get("input", "")) if err.get("input") is not None else "—"
            actual = str(err.get("msg", "")).split("\n")[0][:120]
            expected = _sanitize(expected)
            actual = _sanitize(actual)
        out.append(
            BoundaryError(
                error_code=code,
                stage=error_stage,
                field=loc or None,
                expected=expected,
                actual=actual,
                detail=f"{loc}: {err.get('msg', '')[:200]}".strip(": "),
            )
        )
    return out or [
        BoundaryError(error_code="SCHEMA_INVALID", stage=stage, detail="schema 校验失败")
    ]


def identify_source_kind(obj: Any) -> PackSourceKind:
    """区分 Candidate 的来源类别（最终答案 / 工具参数 / Action Input / 纯文本）。

    规则（P06-09B）：
    - 已是 Pydantic pack 实例 → FINAL_ANSWER；
    - CrewAI TaskOutput 带 ``pydantic`` → FINAL_ANSWER；
    - dict 若形如工具调用（含 ``action``+``action_input`` 或 ``tool_name``/``tool_input``/
      ``arguments`` 等框架键）→ ACTION_INPUT / TOOL_PARAMS（不是最终 Pack）；
    - JSON 字符串解析后是 dict：同上判断；
    - 其余无法解析为结构对象 → PLAIN_TEXT。
    """
    if isinstance(obj, BaseModel):
        return PackSourceKind.FINAL_ANSWER
    pydantic = getattr(obj, "pydantic", None)
    if pydantic is not None:
        return PackSourceKind.FINAL_ANSWER

    data: Any = obj
    if not isinstance(obj, dict):
        for attr in ("json_dict", "exported_output", "raw"):
            value = getattr(obj, attr, None)
            if isinstance(value, dict):
                data = value
                break
            if isinstance(value, str):
                data = value
                break

    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*|```\s*$", "", stripped, flags=re.DOTALL).strip()
        # 以 { 开头的文本视为"疑似 JSON 输出"（即使 json.loads 失败也归类为最终答案，
        # 由 _validate 层报 JSON_INVALID 并允许一次修复）；其余不可解析文本才算纯文本。
        if stripped.startswith("{"):
            return PackSourceKind.FINAL_ANSWER
        try:
            candidate = json.loads(stripped)
        except (ValueError, TypeError):
            return PackSourceKind.PLAIN_TEXT
        if isinstance(candidate, dict):
            data = candidate
        else:
            # 非对象 JSON（数组/标量）不是 Pack；但仍是"结构化候选"而非自然语言
            return PackSourceKind.FINAL_ANSWER

    if isinstance(data, dict):
        keys = {str(k).lower() for k in data.keys()}
        if "action" in keys and "action_input" in keys:
            return PackSourceKind.ACTION_INPUT
        if _TOOL_FRAME_KEYS & keys:
            return PackSourceKind.TOOL_PARAMS
        return PackSourceKind.FINAL_ANSWER
    return PackSourceKind.PLAIN_TEXT


def extract_candidate(obj: Any) -> dict[str, Any] | str | None:
    """提取候选结构化结果（dict / Pydantic 实例 / JSON 文本），复用既有读取顺序。"""
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, BaseModel):
        try:
            dumped = obj.model_dump(mode="json")
        except AttributeError:
            dumped = None
        return dumped if isinstance(dumped, dict) else None
    dumped = dump_task_output(obj)
    if isinstance(dumped, dict):
        return dumped
    raw = getattr(obj, "raw", None)
    return raw if isinstance(raw, str) else None


class PackBoundary:
    """P06-09B 统一 Pack 边界：提取 → 分类 → 分层校验 → 有限修复。

    ``repair_once``：仅当错误属于 JSON/字段结构类，且调用方注入 ``fixer`` 时，
    才允许一次 LLM 修复（fixer 由调用方提供，生产环境应注入禁止联网工具的实现）。
    每类错误只修复一次，循环修复被明确禁止（``max_repairs=1``）。
    """

    def __init__(self, *, max_repairs: int = 1) -> None:
        self._max_repairs = max_repairs

    # 结构化错误只用于 JSON/字段结构问题（可修复）；业务语义/网络类不进入修复。
    _REPAIRABLE_CODES: frozenset[str] = frozenset(
        {"JSON_INVALID", "SCHEMA_INVALID", "EXTRA_FIELD", "MISSING_FIELD", "VALUE_INVALID"}
    )

    def parse(
        self,
        obj: Any,
        model: type[_PackModel],
        *,
        stage: str,
        fixer: Callable[[str, list[BoundaryError]], Any] | None = None,
    ) -> tuple[_PackModel | None, list[BoundaryError]]:
        """解析对象为 pack；失败返回 (None, errors)。修复上限 1 次。

        - ``stage``：调用方阶段名（research / analysis / writer），写入错误；
        - ``fixer``：可选修复回调（输入 原始文本+错误，输出新的候选字符串/dict）；
          仅结构类错误会触发，且最多调用一次。
        """
        kind = identify_source_kind(obj)
        if kind == PackSourceKind.ACTION_INPUT:
            return None, [
                BoundaryError(
                    error_code="NOT_A_PACK",
                    stage=stage,
                    detail="Action Input 是工具调用过程，不是最终 Pack",
                )
            ]
        if kind == PackSourceKind.TOOL_PARAMS:
            return None, [
                BoundaryError(
                    error_code="NOT_A_PACK",
                    stage=stage,
                    detail="工具调用参数不是最终 Pack（不得把工具调用过程当输出）",
                )
            ]
        if kind == PackSourceKind.PLAIN_TEXT:
            if fixer is None:
                return None, [
                    BoundaryError(
                        error_code="NOT_A_PACK",
                        stage=stage,
                        detail="输出是普通自然语言，不是结构化 Pack",
                    )
                ]
            # 纯文本不允许直接修复（确定性优先）；只有 JSON/结构错误才允许修复。
            return None, [
                BoundaryError(
                    error_code="NOT_A_PACK",
                    stage=stage,
                    detail="输出是普通自然语言，不是结构化 Pack",
                )
            ]

        candidate = extract_candidate(obj)
        first_errors: list[BoundaryError] | None = None
        errors: list[BoundaryError] = []
        normalized: dict[str, Any] | None = None
        for attempt in range(self._max_repairs + 1):
            if candidate is None:
                return None, [
                    BoundaryError(error_code="NOT_A_PACK", stage=stage, detail="无候选输出")
                ]

            errors, normalized = self._validate(candidate, model, stage=stage)
            if not errors and normalized is not None:
                try:
                    return model.model_validate(normalized), []
                except ValidationError as exc:
                    errors = _validation_errors_to_boundary(exc, "semantics")

            if not errors:
                return None, errors  # 不应到达，防御性分支

            if first_errors is None:
                first_errors = errors

            # 仅 schema 阶段的结构错误可修复；semantics（业务跨字段矛盾）不进入格式修复。
            repairable = any(
                e.error_code in self._REPAIRABLE_CODES and e.stage == stage for e in errors
            )
            if not repairable or fixer is None or attempt >= self._max_repairs:
                break

            raw_text = (
                candidate
                if isinstance(candidate, str)
                else json.dumps(candidate, ensure_ascii=False)
            )
            candidate = fixer(raw_text, errors)

        # 修复后仍失败：返回原始的稳定错误分类（不返回修复器引入的新错误）。
        return None, first_errors or errors

    def _validate(
        self, candidate: dict[str, Any] | str, model: type[_PackModel], *, stage: str
    ) -> tuple[list[BoundaryError], dict[str, Any] | None]:
        """分层校验：JSON/结构 → 多余字段 → Pydantic schema → 业务跨字段。

        返回 (errors, normalized_dict)：校验通过时 normalized_dict 为剥离围栏/解析后的
        dict（供外层做最终 model_validate），否则为 None。
        """
        raw: dict[str, Any] | None = None
        if isinstance(candidate, str):
            text = candidate.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|```\s*$", "", text, flags=re.DOTALL).strip()
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError) as exc:
                return [
                    BoundaryError(
                        error_code="JSON_INVALID",
                        stage=stage,
                        field=None,
                        expected="合法 JSON 对象",
                        actual=_sanitize(str(exc), 120),
                        detail="输出不是合法 JSON",
                    )
                ], None
            if not isinstance(parsed, dict):
                return [
                    BoundaryError(
                        error_code="JSON_INVALID",
                        stage=stage,
                        expected="JSON 对象（dict）",
                        actual=f"JSON 数组/标量: {_sanitize(parsed, 60)}",
                    )
                ], None
            raw = parsed
        else:
            raw = candidate

        assert raw is not None
        try:
            _reject_extra_fields(raw, model)
        except PackParseError as exc:
            return [
                BoundaryError(
                    error_code="EXTRA_FIELD",
                    stage=stage,
                    expected=f"字段集合 {sorted(model.model_fields.keys())}",
                    actual="存在未声明字段",
                    detail=str(exc),
                )
            ], None
        try:
            model.model_validate(raw)
        except ValidationError as exc:
            return _validation_errors_to_boundary(exc, "schema"), None
        return [], raw
