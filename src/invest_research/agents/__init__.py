"""CrewAI Agent 层（P03）。

- P03-01：OpenAI-compatible LLM config/factory adapter（供应商无关）。
- P03-02~04：三个 Agent 版本化提示词。
- P03-05~08：三个 fake LLM Task 与 sequential Crew。

依赖方向：本层朝下依赖 ``domain``（结构化 schema）与 ``settings``，
不导入具体供应商 SDK。
"""

from invest_research.agents.llm_factory import (
    FakeLLM,
    LLMConfig,
    LLMRole,
    OpenAICompatibleLLMFactory,
)

__all__ = [
    "FakeLLM",
    "LLMConfig",
    "LLMRole",
    "OpenAICompatibleLLMFactory",
]
