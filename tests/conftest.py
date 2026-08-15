"""pytest 全局环境隔离：清空会污染 Settings 默认值的环境变量。

背景：CrewAI 导入时会 load_dotenv() 把项目根 .env 灌进 os.environ；即使测试用
`_env_file=None`，pydantic-settings 仍会读到这些已注入 os.environ 的变量，
导致「断言默认值」的测试随真实 .env 内容时好时坏（例如真实 .env 里
LLM_MODEL_RESEARCH=qwen3.5-flash、LLM_BASE_URL 指向专属 workspace）。

这里在每次测试前清空相关变量；测试内部仍可用 `monkeypatch.setenv` 重新覆盖。
"""

from __future__ import annotations

import pytest

# 会影响 Settings 默认值/必需字段的环境变量（真实 .env 可能注入）
_POLLUTING_ENV: tuple[str, ...] = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL_RESEARCH",
    "LLM_MODEL_ANALYSIS",
    "LLM_MODEL_WRITER",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT",
    "SEC_USER_AGENT_CONTACT",
    "SERPER_API_KEY",
    "SERPER_ENDPOINT",
    "FLOW_MODE",
    "RESEARCH_PROFILE",
    "PROJECT_NAME",
    "ENVIRONMENT",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def _clear_polluting_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次测试前清空会污染 Settings 的环境变量（测试结束由 monkeypatch 自动恢复）。"""
    for name in _POLLUTING_ENV:
        monkeypatch.delenv(name, raising=False)
