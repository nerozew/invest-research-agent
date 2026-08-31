"""P03-01 OpenAI-compatible LLM config/factory adapter（供应商无关 + 角色可解耦）。

设计目标（对齐 docs/02-ARCHITECTURE.md §8「LLM 配置策略」）：
- 供应商无关：业务代码只表达 research/analysis/writer 角色；每个角色可独立指定
  vendor / base_url / api_key / model / temperature / timeout / enable_thinking。
- 角色解耦（P06-11G 扩展）：``RoleLLMConfig`` 表示单个角色的完整 LLM 配置；
  ``LLMConfig.config_for(role)`` 把全局默认与角色覆盖合并为 ``RoleLLMConfig``。
  未来新增 Agent/角色只需在 ``LLMConfig`` 增加一个覆盖项，不改业务代码。
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

角色解耦边界（P06-11G）：
- ``structured_output_mode(config, role)``：按**该角色**的 vendor 判定是否允许
  CrewAI 原生 Pydantic parse（不同角色可混用 qwen/deepseek/generic）；
- ``build_real_llm(config, role)``：用该角色合并后的配置构造 LLM；
- ``enable_thinking``（思考模式）也按角色生效：如 Research/Analysis 关闭、
  Writer 开启深度思考可分别配置，互不影响。

依赖边界：
- P03-01 阶段：只导入标准库与 Pydantic，禁止导入 CrewAI/LiteLLM/OpenAI SDK；
- P03-05 起：项目已安装 CrewAI 1.6.1，`FakeLLM` 继承 ``crewai.BaseLLM``
  以被 ``Agent(llm=...)`` 接受（不联网测试替身）；真实 builder 惰性构造。
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from invest_research.settings import RoleLLMOverride, Settings


class LLMRole(StrEnum):
    """Agent 角色对应的模型用途（业务只表达角色，不表达供应商）。"""

    RESEARCH = "research"
    ANALYSIS = "analysis"
    WRITER = "writer"

    @property
    def settings_field(self) -> str:
        """返回 Settings 上对应的模型字段名（llm_model_research 等）。"""
        return f"llm_model_{self.value}"


class StructuredOutputMode(StrEnum):
    """供应商结构化输出能力（P06-11B，Task 输出路径决策）。

    决定 Task 是否绑定 CrewAI 原生 ``output_pydantic``（会触发远程 Pydantic
    parse / OpenAI response_format json_schema）：

    - ``NATIVE_PYDANTIC``：允许使用 CrewAI 原生 output_pydantic 路径
      （由供应商原生支持 response_format schema）；
    - ``JSON_TEXT_LOCAL_VALIDATION``：禁止 CrewAI 原生 Pydantic parse，
      由 Agent 返回普通 JSON 文本，再经本地 PackBoundary/Pydantic 校验。
    """

    NATIVE_PYDANTIC = "native_pydantic"
    JSON_TEXT_LOCAL_VALIDATION = "json_text_local_validation"


def structured_output_mode(
    config: LLMConfig, role: LLMRole = LLMRole.RESEARCH
) -> StructuredOutputMode:
    """按**该角色**的显式 vendor 决定结构化输出路径（P06-11G 角色解耦）。

    - 使用 ``config.config_for(role).vendor``——允许 Research=deepseek、
      Analysis=deepseek、Writer=qwen 等混合组网，各角色互不影响；
    - ``qwen``：允许当前原生 output_pydantic 路径（供应商支持 response_format）；
    - ``deepseek``：禁止 CrewAI 原生 Pydantic parse，改用 JSON 文本 + 本地校验
      （DeepSeek 普通 Chat Completion 不支持 OpenAI json_schema response_format，
      已实测 HTTP 400 "This response_format type is unavailable now"）；
    - ``generic``：默认采用安全的 JSON 文本 + 本地校验，除非未来明确声明支持。

    不根据 base_url 猜测 —— 一律使用显式 ``RoleLLMConfig.vendor``。
    ``role`` 默认 research 仅用于兼容 P06-11G 之前的单供应商调用；生产路径均应
    显式传入角色，混合供应商配置不会依赖此默认值。
    """
    vendor = config.config_for(role).vendor
    if vendor == "qwen":
        return StructuredOutputMode.NATIVE_PYDANTIC
    if vendor == "deepseek":
        return StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION
    return StructuredOutputMode.JSON_TEXT_LOCAL_VALIDATION


class RoleLLMConfig(BaseModel):
    """单个角色（Agent）的完整 LLM 配置（P06-11G 解耦单元）。

    - 每个角色可独立指定 vendor/base_url/api_key/model/temperature/timeout/
      enable_thinking；
    - 由 ``LLMConfig.config_for(role)`` 负责把全局默认与角色覆盖合并；
    - ``api_key`` 使用 SecretStr，str()/repr()/model_dump() 均不泄露明文。
    """

    model_config = ConfigDict(frozen=True)

    provider: str = "openai_compatible"
    # 显式供应商标识（qwen/deepseek/generic），决定 thinking 参数格式；
    # qwen=enable_thinking；deepseek=thinking.type；generic=不传供应商专用参数。
    vendor: Literal["qwen", "deepseek", "generic"] = "qwen"
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout: float = Field(default=60.0, gt=0.0)
    # 供应商专有参数：Qwen3.5 等默认思考模式（reasoning），显式关闭可显著提速；
    # None=不传（保持其它 OpenAI-compatible 供应商兼容）
    enable_thinking: bool | None = None

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

    @field_validator("model")
    @classmethod
    def _non_blank_model(cls, value: str) -> str:
        """模型名不得为空或纯空白（fail-fast：禁止空模型名启动，不降级 fake）。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("模型名不能为空或纯空白")
        return cleaned


class LLMConfig(BaseModel):
    """供应商无关的 LLM 配置契约（纯数据，无外部依赖）。

    角色解耦（P06-11G）：
    - 全局默认字段（vendor/base_url/api_key/temperature/timeout/enable_thinking）
      仍是向后兼容的"默认值"；每个角色可通过 ``role_overrides`` 独立覆盖；
    - ``config_for(role)`` 返回该角色**合并后**的 ``RoleLLMConfig``；
    - ``model_research/model_analysis/model_writer`` 是三个角色的模型名快捷字段
      （等价于给 role_overrides 配 model），保留旧用法。
    """

    model_config = ConfigDict(frozen=True)

    provider: str = "openai_compatible"
    # 全局默认供应商标识（qwen/deepseek/generic）；角色未覆盖时使用。
    vendor: Literal["qwen", "deepseek", "generic"] = "qwen"
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    model_research: str = Field(min_length=1)
    model_analysis: str = Field(min_length=1)
    model_writer: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout: float = Field(default=60.0, gt=0.0)
    # 供应商专有参数（全局默认）：Qwen3.5 等默认思考模式（reasoning），
    # 显式关闭可显著提速；None=不传（保持其它 OpenAI-compatible 供应商兼容）
    # 角色可通过 role_overrides[role].enable_thinking 独立覆盖。
    enable_thinking: bool | None = None

    # P06-11G：按角色覆盖的完整配置（vendor/base_url/api_key/model/... 均可覆盖）。
    # 键必须是 LLMRole.value（research/analysis/writer）；未来新增角色在此扩展。
    role_overrides: dict[str, "RoleLLMOverride"] = Field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMConfig":
        """从项目 Settings 构造配置（唯一允许触碰 SecretStr 的入口）。

        P06-11G：解析 Settings 中每角色的"完整覆盖块"（LLM_<ROLE>_VENDOR /
        LLM_<ROLE>_BASE_URL / LLM_<ROLE>_API_KEY / LLM_<ROLE>_MODEL /
        LLM_<ROLE>_TEMPERATURE / LLM_<ROLE>_TIMEOUT / LLM_<ROLE>_ENABLE_THINKING）。
        角色未配置覆盖块时回退全局字段（向后兼容）。
        """
        role_overrides: dict[str, RoleLLMOverride] = {}
        for role in LLMRole:
            override = settings.build_role_llm_config(role)
            if override is not None:
                role_overrides[role.value] = override
        return cls(
            provider=settings.llm_provider,
            vendor=settings.llm_vendor,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_research=settings.llm_model_research,
            model_analysis=settings.llm_model_analysis,
            model_writer=settings.llm_model_writer,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout,
            enable_thinking=settings.llm_enable_thinking,
            role_overrides=role_overrides,
        )

    def config_for(self, role: LLMRole) -> RoleLLMConfig:
        """返回该角色合并后的完整 LLM 配置（全局默认 + 角色覆盖）。

        - 角色未配置 ``role_overrides`` 时回退全局默认 + 该角色模型名；
        - 已配置时用角色覆盖块替换对应字段（未覆盖的字段仍继承全局默认）。
        """
        override = self.role_overrides.get(role.value)
        base = RoleLLMConfig(
            provider=self.provider,
            vendor=self.vendor,
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model_for(role),
            temperature=self.temperature,
            timeout=self.timeout,
            enable_thinking=self.enable_thinking,
        )
        if override is None:
            return base
        # RoleLLMOverride 不携带 provider（始终 openai_compatible，全局唯一）。
        # 只替换显式配置的非 None 字段，其余继续继承全局默认；布尔 False 会保留。
        values = {
            "vendor": override.vendor,
            "base_url": override.base_url,
            "api_key": override.api_key,
            "model": override.model,
            "temperature": override.temperature,
            "timeout": override.timeout,
            "enable_thinking": override.enable_thinking,
        }
        updates = {key: value for key, value in values.items() if value is not None}
        return base.model_copy(update=updates)

    def model_for(self, role: LLMRole) -> str:
        """按角色返回模型名（业务只表达角色，不写死供应商/模型名）。

        角色覆盖了 model 时返回覆盖值；否则返回全局 model_research/analysis/writer。
        """
        override = self.role_overrides.get(role.value)
        if override is not None and isinstance(override.model, str):
            return override.model
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
        # P06-11G：fake 也按角色解析（确保测试断言角色模型/温度与真实路径一致）。
        role_cfg = config.config_for(role)
        super().__init__(model=role_cfg.model, temperature=role_cfg.temperature)
        self._config = config
        self._role = role
        self._responses: list[Any] = list(responses or [])
        self._index = 0
        self.invoked_prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return self._config.config_for(self._role).model

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


def _build_thinking_extra_body(role_cfg: RoleLLMConfig) -> dict[str, Any] | None:
    """按角色的 vendor 把 enable_thinking 翻译为对应的供应商专有参数（P06-11G）。

    - qwen：``{"enable_thinking": bool}``；
    - deepseek：``{"thinking": {"type": "enabled"|"disabled"}}``；
    - generic 与 enable_thinking=None：返回 None（不传任何供应商专用参数）；
      generic 且显式设置 enable_thinking 时给出清晰告警（不静默误传）。
    """
    if role_cfg.enable_thinking is None:
        return None
    if role_cfg.vendor == "qwen":
        return {"enable_thinking": role_cfg.enable_thinking}
    if role_cfg.vendor == "deepseek":
        return {"thinking": {"type": "enabled" if role_cfg.enable_thinking else "disabled"}}
    # generic：不传供应商专用参数；显式设置不支持的参数时给出清晰告警。
    warnings.warn(
        "LLM_VENDOR=generic 不支持 enable_thinking 供应商专有参数，"
        f"已忽略 LLM_ENABLE_THINKING={role_cfg.enable_thinking}（不会传给供应商）。",
        UserWarning,
        stacklevel=2,
    )
    return None


def build_real_llm(config: LLMConfig, role: LLMRole) -> AnyLLM:
    """惰性构造真实 OpenAI-compatible LLM 实例（P05-12B / P06-11 实现 + P06-11G 解耦）。

    - 使用 ``config.config_for(role)`` 的角色合并配置（vendor/base_url/api_key/
      model/temperature/timeout/enable_thinking 均可被 role_overrides 覆盖）；
    - 依据当前安装的 CrewAI 1.6.1 官方 API：
      ``crewai.LLM(model=..., base_url=..., api_key=..., temperature=..., timeout=...)``；
    - API Key 只在构造真实客户端的这一刻解包（``SecretStr.get_secret_value()``），
      之后由 CrewAI 内部持有，不进入本模块的 repr/日志/异常；
    - 构造阶段不发起任何网络请求（CrewAI 1.6.1 实测：仅 model/provider 解析）；
    - 供应商无关：不做任何 Qwen/DeepSeek 专属业务类，仅透传配置；
    - thinking 供应商专有参数按角色 vendor 翻译（qwen=enable_thinking、
      deepseek=thinking.type、generic=不传并在显式设置时告警）。
    """
    from crewai import LLM as CrewAILLM

    role_cfg = config.config_for(role)
    kwargs: dict[str, Any] = {}
    extra_body = _build_thinking_extra_body(role_cfg)
    if extra_body is not None:
        kwargs["extra_body"] = extra_body
    return CrewAILLM(
        model=role_cfg.model,
        base_url=role_cfg.base_url,
        api_key=role_cfg.api_key.get_secret_value(),
        temperature=role_cfg.temperature,
        timeout=role_cfg.timeout,
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
