"""LLM-as-judge 软评分单测（注入 fake completion，不联网）。"""

import json

from scripts.llm_judge import LLMJudge, _parse_judge_json


class _FakeCompletion:
    def __init__(self, markdown: str, *, raise_error: bool = False) -> None:
        self._markdown = markdown
        self._raise_error = raise_error
        self.calls = 0
        self.last_prompt = ""

    def complete(self, *, role, system_prompt, user_prompt, max_tokens=500):
        self.calls += 1
        self.last_prompt = user_prompt
        if self._raise_error:
            raise RuntimeError("boom")
        return type("Result", (), {"markdown": self._markdown})()


def _valid_json() -> str:
    return json.dumps(
        {"clarity": 8.5, "coherence": 9.0, "information_density": 7.5, "overall": 8.2,
         "rationale": "结构清晰，段落连贯。"}
    )


def test_judge_parses_score_from_fake():
    fake = _FakeCompletion(_valid_json())
    score = LLMJudge(fake).score("## 报告\n正文。")
    assert score is not None
    assert score.clarity == 8.5
    assert score.coherence == 9.0
    assert score.information_density == 7.5
    assert score.overall == 8.2
    assert fake.calls == 1


def test_judge_tolerates_code_fence_wrapped_json():
    fake = _FakeCompletion("```json\n" + _valid_json() + "\n```")
    score = LLMJudge(fake).score("正文")
    assert score is not None
    assert score.overall == 8.2


def test_judge_returns_none_on_malformed_output():
    fake = _FakeCompletion("这不是 JSON")
    assert LLMJudge(fake).score("正文") is None


def test_judge_returns_none_on_out_of_range():
    payload = {"clarity": 99, "coherence": 1, "information_density": 1, "overall": 1}
    fake = _FakeCompletion(json.dumps(payload))
    assert LLMJudge(fake).score("正文") is None


def test_judge_returns_none_on_completion_error():
    fake = _FakeCompletion("", raise_error=True)
    assert LLMJudge(fake).score("正文") is None


def test_judge_no_completion_returns_none():
    assert LLMJudge(None).score("正文") is None


def test_parse_judge_json_handles_empty():
    assert _parse_judge_json("") is None
    assert _parse_judge_json("text {broken") is None
