"""P06-11G: writer message helpers unify dict/object content extraction."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from pydantic import SecretStr

from invest_research.agents.llm_factory import LLMConfig
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.flow_wiring import (
    LiveResearchFlowRunner,
    _message_role,
    _message_text,
)


def _config() -> LLMConfig:
    return LLMConfig(
        vendor="qwen",
        api_key=SecretStr("sk-test"),
        base_url="https://example.com",
        model_research="qwen-test",
        model_analysis="qwen-test",
        model_writer="qwen-test",
    )


def _runner() -> LiveResearchFlowRunner:
    return LiveResearchFlowRunner(config=_config())


def _request() -> ResearchRequest:
    return ResearchRequest(input_company="MSFT", as_of_date=date(2025, 10, 31))


# ---------------------------------------------------------------------------
# _message_role / _message_text 单元测试
# ---------------------------------------------------------------------------


def test_message_role_dict() -> None:
    assert _message_role({"role": "assistant", "content": "x"}) == "assistant"


def test_message_role_object() -> None:
    assert _message_role(SimpleNamespace(role="user", content="x")) == "user"


def test_message_role_empty() -> None:
    assert _message_role({}) is None
    assert _message_role(SimpleNamespace()) is None
    assert _message_role(None) is None


def test_message_text_str() -> None:
    assert _message_text({"role": "assistant", "content": "hello"}) == "hello"


def test_message_text_str_object() -> None:
    assert _message_text(SimpleNamespace(role="assistant", content="hello")) == "hello"


def test_message_text_list_text_blocks() -> None:
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "第一章"},
            {"type": "text", "text": "第二章"},
        ],
    }
    assert _message_text(msg) == "第一章第二章"


def test_message_text_ignores_non_text_blocks() -> None:
    msg = {
        "role": "assistant",
        "content": [
            {"type": "tool_call", "id": "x", "function": {"name": "f", "arguments": "{}"}},
            {"type": "text", "text": "只有这段是正文"},
            {"type": "image", "image_url": "http://example.com/a.png"},
        ],
    }
    assert _message_text(msg) == "只有这段是正文"


def test_message_text_never_str_dict() -> None:
    msg = {
        "role": "assistant",
        "content": [
            {"type": "tool_call", "function": {"name": "WriterContextReader", "arguments": "{}"}},
        ],
    }
    assert _message_text(msg) == ""


# ---------------------------------------------------------------------------
# _longest_writer_history 集成测试
# ---------------------------------------------------------------------------


def test_longest_writer_history_dict_str_content() -> None:
    runner = _runner()
    runner._writer_agent = SimpleNamespace(
        last_messages=[
            {"role": "user", "content": "请撰写报告"},
            {"role": "assistant", "content": "短正文"},
            {"role": "assistant", "content": "这是一个很长很长的报告正文。" * 50},
        ]
    )
    assert runner._longest_writer_history() == "这是一个很长很长的报告正文。" * 50


def test_longest_writer_history_object_messages() -> None:
    runner = _runner()
    runner._writer_agent = SimpleNamespace(
        last_messages=[
            SimpleNamespace(role="user", content="请撰写报告"),
            SimpleNamespace(role="assistant", content="对象消息正文"),
        ]
    )
    assert runner._longest_writer_history() == "对象消息正文"


def test_longest_writer_history_list_text_blocks() -> None:
    runner = _runner()
    runner._writer_agent = SimpleNamespace(
        last_messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "第一部分。"},
                    {"type": "text", "text": "第二部分。"},
                    {"type": "tool_call", "id": "x", "function": {"name": "f", "arguments": "{}"}},
                ],
            }
        ]
    )
    assert runner._longest_writer_history() == "第一部分。第二部分。"


def test_longest_writer_history_ignores_tool_params() -> None:
    runner = _runner()
    tool_msg = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_call",
                "id": "x",
                "function": {
                    "name": "WriterContextReader",
                    "arguments": '{"artifact_key": "research_pack"}',
                },
            },
        ],
    }
    runner._writer_agent = SimpleNamespace(last_messages=[tool_msg])
    assert runner._longest_writer_history() is None


def test_longest_writer_history_no_candidate() -> None:
    runner = _runner()
    runner._writer_agent = SimpleNamespace(
        last_messages=[
            {"role": "user", "content": "请撰写报告"},
            {"role": "system", "content": "系统提示"},
        ]
    )
    assert runner._longest_writer_history() is None

    runner._writer_agent = None
    assert runner._longest_writer_history() is None

    runner._writer_agent = SimpleNamespace(last_messages=[])
    assert runner._longest_writer_history() is None


def test_longest_writer_history_long_over_short() -> None:
    """875 tokens 长正文候选优先于 114 字符 final answer。"""
    long_text = "微软公司 2025 财年业务概览。" * 30  # 模拟 875 tokens 的中间长文
    final_text = "I need to read the context packs first."
    runner = _runner()
    runner._writer_agent = SimpleNamespace(
        last_messages=[
            {"role": "assistant", "content": final_text},
            {"role": "assistant", "content": long_text},
        ]
    )
    best = runner._longest_writer_history()
    assert best is not None
    assert best == long_text
    assert len(best) > len(final_text)
