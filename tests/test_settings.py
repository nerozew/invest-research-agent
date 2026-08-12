"""P00-05 配置对象测试：缺失必需变量报可读错误、正常构建、密钥不泄露。"""

import pytest
from pydantic import SecretStr, ValidationError

from invest_research.settings import Settings

_MISSING_FIELDS_MSG = "llm_api_key"


def _clear_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除会影响测试结果的必需环境变量。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT_CONTACT", raising=False)


def test_missing_required_variables_raise_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺失必需变量（llm_api_key）时必须抛出可读的 ValidationError。"""
    _clear_required_env(monkeypatch)

    with pytest.raises(ValidationError) as exc_info:
        # _env_file=None 避免读取真实 .env，保证测试确定性
        Settings(_env_file=None)

    error_text = str(exc_info.value)
    assert _MISSING_FIELDS_MSG in error_text
    # 可读错误应出现在第一条错误信息中
    assert "Field required" in error_text


def test_settings_builds_from_explicit_values() -> None:
    """显式传入完整必需字段应能正常构建配置对象。"""
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test-value",
        sec_user_agent_contact="test@example.com",
    )

    assert settings.project_name == "invest-research"
    assert settings.environment == "development"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.sec_user_agent_contact == "test@example.com"


def test_secret_value_not_leaked() -> None:
    """SecretStr 的 str()/repr() 不得泄露明文密钥。"""
    settings = Settings(
        _env_file=None,
        llm_api_key="super-secret-key",
        sec_user_agent_contact="test@example.com",
    )

    assert isinstance(settings.llm_api_key, SecretStr)
    assert "super-secret-key" not in str(settings.llm_api_key)
    assert "super-secret-key" not in repr(settings.llm_api_key)
    assert "super-secret-key" not in str(settings)
    assert "super-secret-key" not in repr(settings)
    # 通过 get_secret_value() 可正确取出明文
    assert settings.llm_api_key.get_secret_value() == "super-secret-key"
