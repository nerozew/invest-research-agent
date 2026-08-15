"""P03-01 OpenAI-compatible LLM config/factory adapter（供应商无关）。

设计目标（对齐 docs/02-ARCHITECTURE.md §8「LLM 配置策略」）：
- 供应商无关：业务代码只表达 research/analysis/writer 模型、base_url、
  api_key、timeout、temperature；不出现具体供应商类名（无 DeepSeek/Qwen Factory）。
- 默认适配阿里云百炼（Model Studio）qwen-max，通过 OpenAI-compatible 接口调用；
  未来切换供应商只改环境变量，不改 Agent/Task/Flow 代码。
- 构建 factory 期间不发任何网络请求；真实 builder 采用惰性 import，
  只有真正构造 LLM 实例的一瞬间才导入 CrewAI 并解包 SecretStr。
- API Key 只在真正构造 LLM 实例的一瞬间取出，且不进 repr/str/异常/日志。

统一 LLM 接口（P05-12B）：
- ``AnyLLM``：``FakeLLM | crewai.BaseLLM``——三个 Agent 都接受该联合类型，
  FakeLLM 用于测试/CI，``crewai.LLM`` 用于真实运行；业务层不感知具体实现。
- ``_build_real_llm``：按当前安装的 CrewAI 版本实现 OpenAI-compatible LLM
  构造（CrewAI 1.6.1：``LLM(model, base_url, api_key, temperature, timeout)``，
  provider 自动识别为 openai；构造阶段不发起网络请求）。

依赖边界：
- P03-01 阶段：只导入标准库与 Pydantic，禁止导入 CrewAI/LiteLLM/OpenAI SDK；
- P03-05 起：项目已安装 CrewAI 1.6.1，`FakeLLM` 继承 ``crewai.BaseLLM``
  以被 ``Agent(llm=...)`` 接受（不联网测试替身）；真实 builder 惰性构造。
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
    # 供应商专有参数：Qwen3.5 等默认思考模式（reasoning），显式关闭可显著提速；
    # None=不传（保持其它 OpenAI-compatible 供应商兼容）
    enable_thinking: bool | None = None

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
            enable_thinking=settings.llm_enable_thinking,
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

    @field_validator("model_research", "model_analysis", "model_writer")
    @classmethod
    def _non_blank_model(cls, value: str) -> str:
        """模型名不得为空或纯空白（fail-fast：禁止空模型名启动，不降级 fake）。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("模型名不能为空或纯空白")
        return cleaned


# CrewAI 1.6.1 已安装；惰性导入 BaseLLM/LLM，避免在未安装环境（如纯配置测试）导入失败。
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


# 统一 LLM 接口（P05-12B）：三个 Agent 接受 FakeLLM 或 crewai.BaseLLM（含真实
# crewai.LLM，其继承自 BaseLLM —— 已在 CrewAI 1.6.1 实测 issubclass(LLM, BaseLLM)=True）。
# 业务层只依赖该联合类型，不感知具体实现；普通测试/CI 一律注入 FakeLLM，不联网。
AnyLLM = FakeLLM | BaseLLM

LLMBuilder = Callable[[LLMConfig, LLMRole], AnyLLM]


def build_real_llm(config: LLMConfig, role: LLMRole) -> AnyLLM:
    """惰性构造真实 OpenAI-compatible LLM 实例（P05-12B 实现）。

    依据当前安装的 CrewAI 1.6.1 官方 API：
    ``crewai.LLM(model=..., base_url=..., api_key=..., temperature=..., timeout=...)``。
    - API Key 只在构造真实客户端的这一刻解包（``SecretStr.get_secret_value()``），
      之后由 CrewAI 内部持有，不进入本模块的 repr/日志/异常；
    - 构造阶段不发起任何网络请求（CrewAI 1.6.1 实测：仅 model/provider 解析）；
    - 供应商无关：不做任何 Qwen/DeepSeek 专属业务类，仅透传配置。
    """
    from crewai import LLM as CrewAILLM

    kwargs: dict[str, Any] = {}
    if config.enable_thinking is not None:
        # 仅当显式配置时才传供应商专有参数：Qwen3.5 思考模式默认开启导致响应极慢，
        # enable_thinking=false 显著提速；None 时不传，保持其它 OpenAI-compatible 兼容。
        kwargs["extra_body"] = {"enable_thinking": config.enable_thinking}
    return CrewAILLM(
        model=config.model_for(role),
        base_url=config.base_url,
        api_key=config.api_key.get_secret_value(),
        temperature=config.temperature,
        timeout=config.timeout,
        **kwargs,
    )


class OpenAICompatibleLLMFactory:
    """供应商无关的 LLM factory。

    - ``create(config, role, builder=None)``：按角色返回 LLM 实例；
    - builder 可注入：测试传入 fake builder 验证配置传递与 key 安全，不联网；
    - 默认 builder 为惰性真实构造（``build_real_llm``，仅构造真正需要时）。
    """

    def create(
        self,
        config: LLMConfig,
        role: LLMRole,
        builder: LLMBuilder | None = None,
    ) -> AnyLLM:
        if builder is not None:
            return builder(config, role)
        return build_real_llm(config, role)

    def create_fake(
        self,
        config: LLMConfig,
        role: LLMRole,
        responses: list[Any] | None = None,
    ) -> FakeLLM:
        """返回不联网的 fake LLM（测试专用，不经过真实 builder）。"""
        return FakeLLM(config=config, role=role, responses=responses)
