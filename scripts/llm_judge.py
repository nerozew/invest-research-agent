"""评测：LLM-as-judge 软评分（可读性/连贯/信息密度，仅辅助，不进硬门禁）。

- ``LLMJudge.score(report_md)``：0-10 三围 + overall + rationale；
- temperature=0；**不判断数字/事实正确性**（那由确定性校验负责）；
- 调用失败或解析失败返回 None（不伪造分数）；
- completion 鸭子类型注入（具备 ``complete(*, role, system_prompt, user_prompt, max_tokens)``），
  复用 ``AnnualLlmDispatcher`` 的 OpenAI-compatible 接口，便于测试注入 fake。

依赖边界：只依赖 stdlib + LLMRole 枚举；不调用 DB/网络。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from invest_research.agents.llm_factory import LLMRole

_JUDGE_SYSTEM = (
    "你是投资研究报告的文字质量评审员。只评估文字的可读性、连贯性和信息密度，"
    "每项 0-10 分（可含一位小数）。你【不】判断任何数字或事实的正确性——数字正确性"
    "由确定性校验负责。只输出一个 JSON 对象，不要输出其它文字："
    '{"clarity": <0-10>, "coherence": <0-10>, "information_density": <0-10>, '
    '"overall": <0-10>, "rationale": "<一句话理由>"}'
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class LLMJudgeScore:
    """一次软评分的结构化结果。"""

    clarity: float
    coherence: float
    information_density: float
    overall: float
    rationale: str


def _clamp_score(value: object) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if 0.0 <= score <= 10.0 else None


def _parse_judge_json(text: str) -> LLMJudgeScore | None:
    """从 LLM 输出解析分数 JSON；容忍代码块包裹/多余文字；失败返回 None。"""
    match = _JSON_OBJECT_RE.search(text or "")
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    clarity = _clamp_score(payload.get("clarity"))
    coherence = _clamp_score(payload.get("coherence"))
    density = _clamp_score(payload.get("information_density"))
    overall = _clamp_score(payload.get("overall"))
    if clarity is None or coherence is None or density is None or overall is None:
        return None
    return LLMJudgeScore(
        clarity=clarity,
        coherence=coherence,
        information_density=density,
        overall=overall,
        rationale=str(payload.get("rationale") or "")[:500],
    )


class LLMJudge:
    """LLM 软评分器：只评文字质量，失败返回 None（不伪造）。"""

    def __init__(
        self,
        completion: Any | None = None,
        *,
        build_completion: Callable[[], Any] | None = None,
        max_chars: int = 6000,
    ) -> None:
        self._completion = completion
        self._build_completion = build_completion
        self._max_chars = max_chars

    def score(self, report_md: str) -> LLMJudgeScore | None:
        completion = self._completion
        if completion is None and self._build_completion is not None:
            try:
                completion = self._build_completion()
            except Exception:
                return None
        if completion is None:
            return None
        user_prompt = (
            f"请评审以下投资研究报告的文字质量（只评文字，不校数字）：\n\n"
            f"{report_md[: self._max_chars]}"
        )
        try:
            result = completion.complete(
                role=LLMRole.WRITER,
                system_prompt=_JUDGE_SYSTEM,
                user_prompt=user_prompt,
                max_tokens=500,
            )
        except Exception:
            return None
        return _parse_judge_json(getattr(result, "markdown", None) or "")


__all__ = ["LLMJudge", "LLMJudgeScore", "_parse_judge_json"]
