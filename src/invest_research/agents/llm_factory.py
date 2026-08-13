"""P03-01 OpenAI-compatible LLM config/factory adapter（供应商无关）。

设计目标（对齐 docs/02-ARCHITECTURE.md §8「LLM 配置策略」）：
- 供应商无关：业务代码只表达 research/analysis/writer 模型、base_url、
  api_key、timeout、temperature；不出现具体供应商类名（无 DeepSeek/Qwen Factory）。
- 默认适配阿里云百炼（Model Studio）qwen-max，通过 OpenAI-compatible 接口调用；
  未来切换供应商只改环境变量，不改 Agent/Task/Flow 代码。
- 构建 factory 期间不发任何网络请求；真实 builder 采用惰性 import，
  避免在 P03-01 引入尚未使用的 CrewAI/LiteLLM 重依赖。
- API Key 只在真正构造 LLM 实例的一瞬间取出，且不进 repr/str/异常/日志。

依赖边界：
- P03-01 阶段：只导入标准库与 Pydantic，禁止导入 CrewAI/LiteLLM/OpenAI SDK；
- P03-05 起：项目已安装 CrewAI 1.6.1，`FakeLLM` 继承 ``crewai.BaseLLM``
  以被 ``Agent(llm=...)`` 接受（不联网测试替身）；真实 builder 仍惰性占位。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from invest_research.settings import Settings


class LLMRole(StrEnum):
    """Agent 角色对应的模型用途（业务只表达角色，不表达供应商）。"""

    RESEARCH = "research"
    ANALYSIS = "analysis"
    WRITER = "writer"

    @property
    def settings_field(self) -> str:
        """返回 Settings 上对应的模型字段名（llm_model_research 等）。"""
        return f"llm_model_{self.value}"


class LLMConfig(BaseModel):
    """供应商无关的 LLM 配置契约（纯数据，无外部依赖）。

    - api_key 使用 SecretStr：str()/repr()/model_dump() 均不泄露明文；
    - base_url 必须是 http/https；
    - temperature 限制在 OpenAI-compatible 合法范围 [0, 2]；
    - timeout 必须为正数。
    """

    model_config = ConfigDict(frozen=True)

    provider: str = "openai_compatible"
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    model_research: str = Field(min_length=1)
    model_analysis: str = Field(min_length=1)
    model_writer: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout: float = Field(default=60.0, gt=0.0)

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMConfig":
        """从项目 Settings 构造配置（唯一允许触碰 SecretStr 的入口）。"""
        return cls(
            provider=settings.llm_provider,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_research=settings.llm_model_research,
            model_analysis=settings.llm_model_analysis,
            model_writer=settings.llm_model_writer,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout,
        )

    def model_for(self, role: LLMRole) -> str:
        """按角色返回模型名（业务只表达角色，不写死供应商/模型名）。"""
        match role:
            case LLMRole.RESEARCH:
                return self.model_research
            case LLMRole.ANALYSIS:
                return self.model_analysis
            case LLMRole.WRITER:
                return self.model_writer

    @field_validator("provider")
    @classmethod
    def _provider_supported(cls, value: str) -> str:
        """当前仅支持 openai_compatible；未来增加协议在此扩展。"""
        if value != "openai_compatible":
            raise ValueError("provider 仅支持 openai_compatible")
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        """base_url 必须是 http/https，且不能带多余尾部斜杠。"""
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url 必须是有效的 http/https URL")
        return value.rstrip("/")


# LLM 实例的类型占位：P03-08 安装 CrewAI 后，真实 builder 返回其 LLM 对象。
# P03-01 阶段不 import CrewAI/LiteLLM，因此用 object 表示"外部可调用对象"。
# 测试通过注入 fake builder 验证配置传递，不产生任何网络请求。
LLMInstance = object


# CrewAI 1.6.1 已安装；惰性导入 BaseLLM，避免在未安装环境（如纯配置测试）导入失败。
try:  # pragma: no cover - 惰性导入分支
    from crewai import BaseLLM
except ImportError:  # pragma: no cover - 依赖缺失时降级为普通对象
    BaseLLM = object  # type: ignore[assignment,misc]


class FakeLLM(BaseLLM):
    """不联网的 fake LLM（P03-05~07/15 的测试替身基础）。

    - 继承 ``crewai.BaseLLM``，可被 ``Agent(llm=...)`` 接受；
    - ``call``/``invoke/__call__`` 按顺序返回预构造响应（Pydantic BaseModel 或文本）；
    - ``response_model`` 提供时尝试实例化（返回 BaseModel 或校验后的文本）；
    - repr/str 只暴露模型名，绝不包含 API Key；不发起任何网络请求。
    """

    def __init__(
        self,
        config: LLMConfig,
        role: LLMRole,
        responses: list[Any] | None = None,
    ) -> None:
        # BaseLLM.__init__ 需要 model；temperature 用配置值；api_key/base_url 不传给
        # fake（fake 从不联网，绝不携带真实 key）。
        effective_model = config.model_for(role)
        super().__init__(model=effective_model, temperature=config.temperature)
        self._config = config
        self._role = role
        self._responses: list[Any] = list(responses or [])
        self._index = 0
        self.invoked_prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return self._config.model_for(self._role)

    def invoke(self, prompt: str) -> Any:
        """按顺序返回响应；耗尽后循环复用第一份（CrewAI 多次调用 LLM）。"""
        self.invoked_prompts.append(prompt)
        if not self._responses:
            raise RuntimeError("FakeLLM 没有预配置任何响应")
        if self._index >= len(self._responses):
            self._index = 0
        response = self._responses[self._index]
        self._index += 1
        return response

    def call(
        self,
        messages: Any,
        tools: list[Any] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any = None,
        from_agent: Any = None,
        response_model: type[BaseModel] | None = None,
    ) -> Any:
        """CrewAI BaseLLM 接口：响应预置 pack。

        - 无 ``response_model``（如 CrewAI 的 thought/plan 阶段）：返回预置 BaseModel 的
          JSON 文本（CrewAI 会当作字符串处理，如 .rstrip()）；
        - 有 ``response_model``（结构化输出）：返回对应 BaseModel 实例；
        - 响应耗尽时循环复用（invoke 已处理）。
        """
        prompt = messages if isinstance(messages, str) else str(messages)
        result = self.invoke(prompt)
        if response_model is not None:
            if isinstance(result, response_model):
                return result
            if isinstance(result, dict):
                return response_model.model_validate(result)
            if isinstance(result, str):
                return response_model.model_validate_json(result)
            return result
        # 无 response_model：CrewAI 期望文本，返回预置 BaseModel 的 JSON 串
        if isinstance(result, BaseModel):
            return result.model_dump_json()
        return str(result)

    def supports_function_calling(self) -> bool:
        """fake 不做工具函数调用（保持最小行为）。"""
        return False

    def get_context_window_size(self) -> int:
        """返回固定上下文窗口（fake 不真实推理）。"""
        return 8192

    # 兼容 P03-01 早期测试与懒调用：直接调用 fake 等价于 invoke。
    def __call__(self, prompt: str) -> Any:
        return self.invoke(prompt)

    def __repr__(self) -> str:
        # 只暴露角色+模型名，不暴露 base_url/key。
        return f"FakeLLM(role={self._role.value}, model={self.model_name})"


LLMBuilder = Callable[[LLMConfig, LLMRole], Any]


def _build_real_llm(config: LLMConfig, role: LLMRole) -> Any:
    """惰性构造真实 OpenAI-compatible LLM 实例。

    当前 P03-01 不安装 CrewAI/LiteLLM/OpenAI SDK。当这些依赖在 P03-08 引入时，
    按当时官方文档实现（注意：某些库要求模型名前缀如 ``openai/qwen-max``）。
    在此之前，调用真实 builder 会得到明确的未实现提示，绝不偷偷发起网络请求。
    """
    raise NotImplementedError(
        "真实 LLM builder 将在 P03-08 引入 CrewAI 后按官方文档实现；"
        "P03-01 仅提供 fake builder 与配置契约。"
    )


class OpenAICompatibleLLMFactory:
    """供应商无关的 LLM factory。

    - ``create(config, role, builder=None)``：按角色返回 LLM 实例；
    - builder 可注入：测试传入 fake builder 验证配置传递与 key 安全，不联网；
    - 默认 builder 为惰性真实构造（当前返回 NotImplementedError，不产生请求）。
    """

    def create(
        self,
        config: LLMConfig,
        role: LLMRole,
        builder: LLMBuilder | None = None,
    ) -> Any:
        if builder is not None:
            return builder(config, role)
        return _build_real_llm(config, role)

    def create_fake(
        self,
        config: LLMConfig,
        role: LLMRole,
        responses: list[Any] | None = None,
    ) -> FakeLLM:
        """返回不联网的 fake LLM（测试专用，不经过真实 builder）。"""
        return FakeLLM(config=config, role=role, responses=responses)
