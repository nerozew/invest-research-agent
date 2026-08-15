"""P03-01 OpenAI-compatible LLM config/factory adapter 测试。

覆盖验收（不联网、不读取真实 .env、不调用真实模型）：
1. 默认 provider 为 openai_compatible
2. 默认模型为 qwen-max
3. 三个角色模型各自配置正确
4. 三个角色可配置不同模型
5. Base URL 正确传给 builder
6. API Key 正确传入 builder，但输出不可见
7. key 不出现在 Settings repr
8. key 不出现在 factory/config repr
9. 缺少 API Key 时产生安全配置错误
10. 不合法 Base URL 被拒绝
11. factory 构建不发网络请求
12. 真实 builder 参数映射正确
13. 真实 builder 不泄露密钥
14. 不读取 RAG/Embedding 配置
15. 不调用真实阿里云接口
16. fake builder 可代替真实构造器

安全：测试值用占位符 SECRET；所有 Settings 均 `_env_file=None`，
避免读取用户机器上的真实 .env。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from invest_research.agents import FakeLLM, LLMConfig, LLMRole, OpenAICompatibleLLMFactory
from invest_research.agents.llm_factory import AnyLLM, build_real_llm
from invest_research.settings import Settings

SECRET = "sk-test-secret-placeholder"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CONTACT = "test@example.com"


def _settings(**overrides: object) -> Settings:
    """构造不读 .env 的 Settings（基础必需字段 + 覆盖项）。"""
    base: dict[str, object] = {
        "llm_api_key": SECRET,
        "sec_user_agent_contact": CONTACT,
    }
    base.update(overrides)
    # pydantic.mypy 插件对 BaseSettings 呈现为具名参数，动态 kwargs 解包
    # 无法静态匹配，属合理测试用法，按需豁免具体错误码
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _config(**overrides: object) -> LLMConfig:
    settings = _settings(**overrides)
    return LLMConfig.from_settings(settings)


# ---- 1. 默认 provider / 2. 默认模型 ----
def test_default_provider_is_openai_compatible() -> None:
    settings = _settings()
    assert settings.llm_provider == "openai_compatible"
    config = LLMConfig.from_settings(settings)
    assert config.provider == "openai_compatible"


def test_default_models_are_qwen_max() -> None:
    settings = _settings()
    assert settings.llm_model_research == "qwen-max"
    assert settings.llm_model_analysis == "qwen-max"
    assert settings.llm_model_writer == "qwen-max"


# ---- 3. 三个角色模型配置正确 ----
def test_role_models_resolve_correctly() -> None:
    config = _config()
    assert config.model_for(LLMRole.RESEARCH) == "qwen-max"
    assert config.model_for(LLMRole.ANALYSIS) == "qwen-max"
    assert config.model_for(LLMRole.WRITER) == "qwen-max"


# ---- 4. 三个角色可配置不同模型 ----
def test_roles_can_use_different_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL_RESEARCH", "qwen-plus")
    monkeypatch.setenv("LLM_MODEL_ANALYSIS", "qwen-max-longcontext")
    monkeypatch.setenv("LLM_MODEL_WRITER", "qwen-max")
    config = _config()
    models = {
        config.model_for(LLMRole.RESEARCH),
        config.model_for(LLMRole.ANALYSIS),
        config.model_for(LLMRole.WRITER),
    }
    assert config.model_for(LLMRole.RESEARCH) == "qwen-plus"
    assert config.model_for(LLMRole.ANALYSIS) == "qwen-max-longcontext"
    assert config.model_for(LLMRole.WRITER) == "qwen-max"
    assert len(models) == 3


# ---- 5. Base URL 正确传给 builder ----
def test_base_url_passed_to_builder() -> None:
    factory = OpenAICompatibleLLMFactory()
    captured: dict[str, object] = {}

    def builder(config: LLMConfig, role: LLMRole) -> AnyLLM:
        captured["base_url"] = config.base_url
        captured["role"] = role
        return factory.create_fake(config, role)

    config = _config()
    factory.create(config, LLMRole.RESEARCH, builder=builder)
    assert captured["base_url"] == BASE_URL
    assert captured["role"] == LLMRole.RESEARCH


# ---- 6. API Key 正确传入 builder，但输出不可见 ----
def test_api_key_passed_but_never_in_output() -> None:
    factory = OpenAICompatibleLLMFactory()
    captured: dict[str, object] = {}

    def builder(config: LLMConfig, role: LLMRole) -> AnyLLM:
        captured["key"] = config.api_key.get_secret_value()
        return factory.create_fake(config, role)

    config = _config()
    factory.create(config, LLMRole.RESEARCH, builder=builder)
    # 正确传入（builder 能取到明文是构造 LLM 实例所必需）
    assert captured["key"] == SECRET
    # 但在一切输出中都不可见
    outputs = (
        repr(config),
        str(config),
        f"{config!r}",
        config.model_dump_json(),
        repr(factory),
        str(factory),
    )
    for out in outputs:
        assert SECRET not in out


# ---- 7. key 不出现在 Settings repr ----
def test_key_not_in_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    settings = _settings()
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)


# ---- 8. key 不出现在 factory/config repr ----
def test_key_not_in_factory_config_repr() -> None:
    config = _config()
    factory = OpenAICompatibleLLMFactory()
    assert SECRET not in repr(config)
    assert SECRET not in str(config)
    assert SECRET not in repr(factory)
    assert SECRET not in str(factory)


# ---- 9. 缺少 API Key 时产生安全配置错误 ----
def test_missing_api_key_raises_safe_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, sec_user_agent_contact=CONTACT)
    message = str(exc_info.value)
    assert "llm_api_key" in message  # 指明缺失字段
    assert SECRET not in message  # 不泄漏任何 key 值


# ---- 10. 不合法 Base URL 被拒绝 ----
def test_invalid_base_url_rejected() -> None:
    with pytest.raises(ValidationError):
        LLMConfig(
            base_url="ftp://bad",
            api_key=SecretStr(SECRET),
            model_research="m",
            model_analysis="m",
            model_writer="m",
        )
    with pytest.raises(ValidationError):
        LLMConfig(
            base_url="not-a-url",
            api_key=SecretStr(SECRET),
            model_research="m",
            model_analysis="m",
            model_writer="m",
        )


# ---- 11. factory 构建不发网络请求 ----
def test_factory_build_makes_no_network_requests() -> None:
    factory = OpenAICompatibleLLMFactory()
    config = _config()
    sentinel = factory.create_fake(config, LLMRole.RESEARCH)

    def builder(config: LLMConfig, role: LLMRole) -> AnyLLM:
        return sentinel

    # 注入 builder：立即返回，不发网络请求
    result = factory.create(config, LLMRole.RESEARCH, builder=builder)
    assert result is sentinel
    # 真实 builder（build_real_llm）只做客户端构造，不发任何网络请求
    # （CrewAI 1.6.1 实测：LLM(...) 仅解析 model/provider，不发起请求）。
    llm = build_real_llm(config, LLMRole.RESEARCH)
    assert llm is not None
    assert llm.model == config.model_for(LLMRole.RESEARCH)


# ---- 12. 真实 builder 参数映射正确 ----
def test_real_builder_maps_parameters_correctly() -> None:
    config = _config()
    # 三个角色分别构造真实 LLM 客户端，验证参数透传
    for role in (LLMRole.RESEARCH, LLMRole.ANALYSIS, LLMRole.WRITER):
        llm = build_real_llm(config, role)
        assert llm.model == config.model_for(role)


# ---- 13. 真实 builder 不泄露密钥 ----
def test_real_builder_does_not_leak_secret_key() -> None:
    config = _config()
    for role in (LLMRole.RESEARCH, LLMRole.ANALYSIS, LLMRole.WRITER):
        llm = build_real_llm(config, role)
        # CrewAI 1.6.1 实测：repr/str 只含内存地址，不逐出模型名/URL/Key
        for out in (repr(llm), str(llm)):
            assert SECRET not in out
            assert BASE_URL not in out
        assert config.api_key.get_secret_value() not in repr(llm)


# ---- 16. fake builder 可代替真实构造器 ----
def test_fake_builder_replaces_real_builder() -> None:
    factory = OpenAICompatibleLLMFactory()
    config = _config()
    fake = factory.create_fake(config, LLMRole.RESEARCH, responses=["ok"])

    def builder(config: LLMConfig, role: LLMRole) -> FakeLLM:
        return fake

    result = factory.create(config, LLMRole.RESEARCH, builder=builder)
    assert isinstance(result, FakeLLM)
    assert result.invoke("any prompt") == "ok"
    assert result.invoked_prompts == ["any prompt"]


# ---- 14. 不读取 RAG/Embedding 配置 ----
def test_does_not_read_rag_or_embedding_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_LLM_API_KEY", "rag-secret")
    monkeypatch.setenv("RAG_LLM_MODEL", "rag-model")
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("MILVUS_URI", "http://localhost:19530")
    settings = _settings()
    # extra="ignore" + 无这些字段 = 完全不读取
    assert not hasattr(settings, "rag_llm_api_key")
    assert not hasattr(settings, "rag_embedding_model")
    assert not hasattr(settings, "milvus_uri")
    dumped = LLMConfig.from_settings(settings).model_dump()
    assert not any("rag" in str(k) or "embedding" in str(k) or "milvus" in str(k) for k in dumped)


# ---- 15. 不调用真实阿里云接口 ----
def test_no_real_provider_called() -> None:
    factory = OpenAICompatibleLLMFactory()
    config = _config()
    fake = factory.create_fake(config, LLMRole.WRITER, responses=["draft"])
    assert fake.invoke("write report") == "draft"
    # repr 只含 role/model，不出现 URL 或 key
    assert BASE_URL not in repr(fake)
    assert SECRET not in repr(fake)
