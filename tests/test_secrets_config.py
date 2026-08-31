"""P06-04 生产配置与密钥管理测试。

验证目标（docs/05 P06-04）：
- environment=production 时占位符密钥/联系邮箱 fail-fast（可读错误）；
- development 环境允许占位符（本地开发/测试便利）；
- .env.example 只含占位符、不含真实密钥样式；
- .env 被 .gitignore 忽略；compose.yml 使用 ${VAR:-占位符} 注入、无真实密钥。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from invest_research.settings import Settings, is_placeholder_secret

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 真实密钥的常见样式（用于"文件中不得出现"扫描）
_REAL_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),  # OpenAI 风格
    re.compile(r"[A-Za-z0-9]{32,}"),  # 32+ 位无分隔长串（token 风格）
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),  # GitHub PAT
)


def _settings(
    *,
    environment: str = "development",
    llm_key: str = "sk-test-value",
    serper_key: str | None = None,
    contact: str = "test@example.com",
) -> Settings:
    return Settings(
        _env_file=None,
        environment=environment,
        llm_api_key=llm_key,
        serper_api_key=serper_key,
        sec_user_agent_contact=contact,
    )


# ---- 1. 占位符识别 ----

def test_is_placeholder_secret_detects_placeholders() -> None:
    for value in (
        "",
        "   ",
        "secret",
        "your-alibaba-model-studio-api-key-here",
        "your-serper-api-key-placeholder",
        "CHANGE-ME-TO-REAL",
        "xxx-xxxx-xxxx",
        "replace-me-with-real",
    ):
        assert is_placeholder_secret(value), f"应识别为占位符: {value!r}"


def test_is_placeholder_secret_accepts_real_values() -> None:
    for value in (
        "sk-test-value",
        "sk-proj-abc123def456",
        "real-key-2026",
        "invest@example.com",
    ):
        assert not is_placeholder_secret(value), f"不应识别为占位符: {value!r}"


# ---- 2. production 环境 fail-fast ----

def test_production_rejects_placeholder_llm_key() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(environment="production", llm_key="your-llm-api-key-placeholder")
    assert "LLM_API_KEY" in str(exc_info.value)
    assert "production" in str(exc_info.value)


def test_production_rejects_placeholder_serper_key() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(environment="production", serper_key="your-serper-api-key-placeholder")
    assert "SERPER_API_KEY" in str(exc_info.value)


def test_production_rejects_placeholder_contact() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(environment="production", contact="your-email@example.com")
    assert "SEC_USER_AGENT_CONTACT" in str(exc_info.value)


def test_production_rejects_empty_llm_key() -> None:
    with pytest.raises(ValidationError):
        _settings(environment="production", llm_key="")


def test_production_allows_real_looking_keys() -> None:
    settings = _settings(
        environment="production",
        llm_key="sk-prod-real-2026-key",
        serper_key="serper-real-key-2026",
        contact="ops@invest-research.example.com",
    )
    assert settings.environment == "production"
    assert settings.llm_api_key.get_secret_value() == "sk-prod-real-2026-key"


def test_development_allows_placeholders() -> None:
    settings = _settings(
        environment="development",
        llm_key="your-llm-api-key-placeholder",
        contact="your-email@example.com",
    )
    assert settings.environment == "development"


# ---- 3. 仓库文件密钥卫生 ----

def test_env_example_contains_only_placeholders() -> None:
    """.env.example 只能含占位符，不得出现真实密钥样式。"""
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped and any(
            key in stripped for key in ("KEY=", "TOKEN=", "SECRET=")
        ):
            value = stripped.split("=", 1)[1]
            assert is_placeholder_secret(value), f".env.example 出现非占位符值: {line}"
    for pattern in _REAL_SECRET_PATTERNS:
        assert not pattern.search(text), f".env.example 疑似包含真实密钥样式: {pattern.pattern}"


def test_env_is_gitignored() -> None:
    """真实 .env 必须被 .gitignore 忽略（且 .env.example 被放行）。"""
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", text, re.MULTILINE)
    assert re.search(r"^\.env\.\*$", text, re.MULTILINE)
    assert re.search(r"^!\.env\.example$", text, re.MULTILINE)


def test_compose_uses_env_placeholders_not_real_secrets() -> None:
    """compose.yml 通过 ${VAR:-占位符} 注入密钥，不得含真实密钥样式。"""
    text = (PROJECT_ROOT / "compose.yml").read_text(encoding="utf-8")
    for pattern in _REAL_SECRET_PATTERNS:
        assert not pattern.search(text), f"compose.yml 疑似包含真实密钥样式: {pattern.pattern}"
    # 密钥注入使用带占位符默认值的环境变量形式
    assert "${LLM_API_KEY:-" in text
    assert "${SERPER_API_KEY:-" in text
