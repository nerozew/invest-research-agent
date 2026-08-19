"""12-factor 配置对象（P00-05）。

配置来源优先级（pydantic-settings 默认）：
环境变量 > .env 文件 > 代码内默认值。

安全要求：
- 密钥字段使用 SecretStr，str()/repr() 均不泄露明文；
- .env.example 只含占位符，真实 .env 已被 .gitignore 忽略。
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# P06-04：占位符密钥识别（production 环境 fail-fast 用）。
# .env.example 只允许占位符；这些标记出现即视为"未配置真实密钥"。
_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "your-",
    "placeholder",
    "change-me",
    "replace-me",
    "xxx",
)


def is_placeholder_secret(value: str) -> bool:
    """判断字符串是否仍是占位符（空、纯空白、占位标记或 'secret'）。"""
    lowered = value.strip().lower()
    if not lowered or lowered == "secret":
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class ModelProfile(BaseModel):
    """P06-11G：注册表中的"命名模型"完整配置条目。

    - ``name``：注册表索引名（如 ``deepseek-flash``、``deepseek-flash-thinking``、
      ``qwen-max``）；角色通过 ``LLM_ROLE_<ROLE>=<name>`` 引用；
    - ``purpose_tags``：用途标签（search/analysis/writer），仅供分类建议，
      不参与运行时逻辑（运行时只按角色引用解析）；
    - 全部供应商字段必填，便于注册表条目可独立复用。
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    vendor: Literal["qwen", "deepseek", "generic"]
    base_url: str
    api_key: SecretStr
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout: float = Field(default=60.0, gt=0.0)
    enable_thinking: bool | None = None
    purpose_tags: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url 必须是有效的 http/https URL")
        return value.rstrip("/")


class RoleLLMOverride(BaseModel):
    """P06-11G：单个角色的完整 LLM 覆盖配置（配置层纯数据，无跨层依赖）。

    由 ``Settings.build_role_llm_config`` 解析：
    1) 角色引用的注册表条目（``LLM_ROLE_<ROLE>=<name>`` → ``LLM_MODELS``）；
    2) 或 ``LLM_<ROLE>_*`` 直接覆盖块；
    3) 都未配置 → 返回 None，由 ``LLMConfig.config_for`` 回退全局默认。

    - 全部字段可选；None 表示未覆盖（合并时继承全局默认）；
    - ``enabled``：该角色是否显式配置了（注册表引用或覆盖块）。
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    vendor: Literal["qwen", "deepseek", "generic"] | None = None
    base_url: str | None = None
    api_key: SecretStr | None = None
    model: str | None = None
    temperature: float | None = None
    timeout: float | None = None
    enable_thinking: bool | None = None
    # 注册表条目名（仅用于审计/日志；不是运行时字段）
    registry_name: str | None = None


class ResearchProfile(BaseModel):
    """fast/deep 研究档位（P05.5）：集中管理三 Agent 的迭代/超时/重试/工具预算。

    参数集中在单一配置对象中（不散落在 Agent 文件）；Agent 构建时按角色取对应
    预算字段。fast 用于快速低预算运行，deep（默认）保留完整投研能力。
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["fast", "deep"]
    research_max_iter: int = Field(ge=1)
    analysis_max_iter: int = Field(ge=1)
    writer_max_iter: int = Field(ge=1)
    max_retry_limit: int = Field(ge=0)
    max_execution_time: int = Field(gt=0)  # 每 Agent 总执行超时（秒）
    max_rpm: int | None = Field(default=None, gt=0)  # 工具调用预算（请求/分钟）

    @classmethod
    def for_mode(cls, mode: Literal["fast", "deep"]) -> "ResearchProfile":
        """按档位返回预算（fast=低预算，deep=完整能力，默认）。"""
        if mode == "fast":
            return cls(
                mode="fast",
                research_max_iter=8,
                analysis_max_iter=6,
                # P06-11F-live：真实 DeepSeek Writer 常把长正文生成在工具循环中间步骤，
                # 最终 answer 被迫只有 20~30 token（<200 字符被 REPORT_INVALID 拒绝）。
                # 提高迭代预算让模型有足够轮次在 final answer 输出完整正文（P06-11E
                # live smoke 实测复现；不是无限提高掩盖编排问题，5→8 给足重写余量）。
                writer_max_iter=8,
                max_retry_limit=1,
                max_execution_time=240,
                max_rpm=60,
            )
        return cls(
            mode="deep",
            research_max_iter=15,
            analysis_max_iter=10,
            writer_max_iter=8,
            max_retry_limit=2,
            max_execution_time=600,
            max_rpm=None,
        )


class Settings(BaseSettings):
    """应用配置。

    必需字段（无默认值，缺失时报可读错误）：
    - llm_api_key：OpenAI-compatible 提供商的 API 密钥（SecretStr）
    - sec_user_agent_contact：SEC EDGAR 合规要求的联系邮箱
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 项目元信息 ----
    project_name: str = "invest-research"
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    # Flow 运行模式（P05-12A）：fake=离线确定性（默认，不调用模型）；live=真实模型（需 API Key）
    flow_mode: Literal["fake", "live"] = "fake"
    # 研究档位（P05.5）：fast=低预算快速运行；deep=完整投研能力（默认，向后兼容）
    research_profile: Literal["fast", "deep"] = "deep"

    # ---- LLM（OpenAI-compatible，供应商无关；默认阿里云百炼 qwen-max，见架构 §8）----
    # provider 当前仅支持 openai_compatible；未来切换供应商只改 env，不改业务代码。
    llm_provider: Literal["openai_compatible"] = "openai_compatible"
    # P06-11：全局默认供应商标识（openai_compatible 协议之下区分具体供应商），
    # 决定 thinking 参数的供应商专用格式；generic=不传任何供应商专用参数。
    # P06-11G 角色解耦：每个角色可通过 LLM_<ROLE>_*（如下）覆盖 vendor/base_url/
    # api_key/model/temperature/timeout/enable_thinking；未覆盖时回退全局默认。
    llm_vendor: Literal["qwen", "deepseek", "generic"] = "qwen"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: SecretStr
    llm_model_research: str = "qwen-max"
    llm_model_analysis: str = "qwen-max"
    llm_model_writer: str = "qwen-max"
    llm_temperature: float = 0.2
    llm_timeout: float = 60.0
    # Qwen3.5 等模型默认开启思考模式（reasoning），响应极慢易超时：
    # None=不传供应商专有参数（deep 模式默认，保持其它 OpenAI-compatible 兼容）；
    # false=显式关闭思考（fast 模式由 flow_wiring 强制）；true=显式开启
    llm_enable_thinking: bool | None = None

    # P06-11G：每角色的完整 LLM 覆盖块（全部可选；未配置的角色回退全局默认）。
    # 约定 LLM_<ROLE>_*（ROLE ∈ research/analysis/writer，未来扩展）：
    #   LLM_RESEARCH_VENDOR / LLM_RESEARCH_BASE_URL / LLM_RESEARCH_API_KEY /
    #   LLM_RESEARCH_MODEL / LLM_RESEARCH_TEMPERATURE / LLM_RESEARCH_TIMEOUT /
    #   LLM_RESEARCH_ENABLE_THINKING（其余角色同理）。
    # 任一角色覆盖块提供"vendor 与 api_key"二者之一即视为启用；此时缺省字段
    # （base_url/model/temperature/timeout/enable_thinking）回退全局默认值。
    llm_research_vendor: Literal["qwen", "deepseek", "generic"] | None = None
    llm_research_base_url: str | None = None
    llm_research_api_key: SecretStr | None = None
    llm_research_model: str | None = None
    llm_research_temperature: float | None = None
    llm_research_timeout: float | None = None
    llm_research_enable_thinking: bool | None = None

    llm_analysis_vendor: Literal["qwen", "deepseek", "generic"] | None = None
    llm_analysis_base_url: str | None = None
    llm_analysis_api_key: SecretStr | None = None
    llm_analysis_model: str | None = None
    llm_analysis_temperature: float | None = None
    llm_analysis_timeout: float | None = None
    llm_analysis_enable_thinking: bool | None = None

    llm_writer_vendor: Literal["qwen", "deepseek", "generic"] | None = None
    llm_writer_base_url: str | None = None
    llm_writer_api_key: SecretStr | None = None
    llm_writer_model: str | None = None
    llm_writer_temperature: float | None = None
    llm_writer_timeout: float | None = None
    llm_writer_enable_thinking: bool | None = None

    # P06-11G：模型注册表（按 name 索引的命名模型，供角色引用复用）。
    # 通过 ``build_model_registry()`` 返回 ``dict[str, ModelProfile]``。
    # 注册表可从代码注入（``Settings(llm_models={...})``），或在 .env 用
    # ``LLM_MODELS_JSON`` 承载一份 JSON（见 build_model_registry）。
    llm_models: dict[str, "ModelProfile"] = Field(default_factory=dict)

    # P06-11G：角色→注册表模型名的引用（优先于 per-role 覆盖块）。
    #   LLM_ROLE_RESEARCH=deepseek-flash
    #   LLM_ROLE_ANALYSIS=deepseek-flash
    #   LLM_ROLE_WRITER=deepseek-flash-thinking
    # 引用名必须存在于 LLM_MODELS 注册表；否则 fail-fast。
    llm_role_research: str | None = None
    llm_role_analysis: str | None = None
    llm_role_writer: str | None = None

    # ---- SEC EDGAR 合规（见可靠性 §5.2：User-Agent 必须含联系邮箱）----
    sec_user_agent_contact: str

    # ---- 基础设施（P01 起启用，本地开发默认值）----
    database_url: str = "postgresql+psycopg://invest:invest@localhost:5432/invest"
    redis_url: str = "redis://localhost:6379/0"
    # Celery broker（P04-10A：API→Worker 投递；生产红 Redis，测试 memory://）
    broker_url: str = "redis://localhost:6379/0"

    # ---- Readiness 探测超时（P04-01）----
    # /readiness 对依赖的探测必须设置显式超时，避免请求被卡在无响应的依赖上；
    # 探测资源（engine/redis 客户端）创建时同步使用这两个值配置 socket/连接超时。
    readiness_db_connect_timeout: float = 2.0
    readiness_redis_connect_timeout: float = 2.0

    # ---- 本地工件目录（P02 起启用）----
    artifact_root: str = "artifacts"

    # ---- HTTP 客户端（P02-02：显式连接/读取超时，见可靠性 §5.1）----
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    # HTTP User-Agent 必须是 ASCII（httpx 拒绝非 ASCII 头；中文说明只能放注释）
    http_user_agent: str = "invest-research/0.1 (+your-email@example.com)"

    # ---- SEC 合规（P02-03：全局限流，项目安全上限 5 req/s，低于官方 10 req/s）----
    sec_rate_limit_per_second: float = 5.0

    # ---- 搜索服务（P02-17/18：Serper provider，P05-12B live 需要）----
    # Serper API Key 用 SecretStr：str()/repr() 不泄露明文；.env.example 只放占位符。
    serper_api_key: SecretStr | None = None
    serper_endpoint: str = "https://google.serper.dev/search"

    # ---- OpenTelemetry（P05-07 起；P06-05 本地链路导出配置）----
    # OTLP HTTP 导出端点（如 http://localhost:4318，对应 deploy/otel-collector.yaml）；
    # 缺省 None = 控制台导出（本地开发直接看 stdout）。
    otel_service_name: str = "invest-research"
    otel_exporter_otlp_endpoint: str | None = None
    # 批量导出间隔（毫秒，仅 OTLP 导出时生效）
    otel_batch_export_interval_ms: int = 5000

    # ---- P06-11K：失败任务 Payload 级诊断包（默认完全关闭）----
    # DIAGNOSTIC_CAPTURE_MODE 可选值：
    #   off            完全关闭（默认，不产生任何诊断事件）
    #   metadata       只保存长度/类型/hash/状态，不保存 Payload
    #   failure_payload 运行中放入 Job-local 内存缓冲区，仅任务失败时把脱敏
    #                  Payload 落盘（成功任务丢弃 Payload）
    #   all_payload    本地调试专用，成功和失败都落盘（生产环境 fail-fast）
    # 注意：诊断包可能包含业务输入，仅供本地调试；不要把完整 Payload 写入
    # Jaeger、Prometheus label 或普通日志。
    diagnostic_capture_mode: Literal[
        "off", "metadata", "failure_payload", "all_payload"
    ] = "off"
    # 单条诊断事件序列化后的字节上限；超出则截断 Payload（或丢弃）并置 truncated=true。
    diagnostic_max_event_bytes: int = Field(default=65_536, ge=1)
    # 整个诊断 Bundle 的总字节上限；超出后拒绝新事件（累计 over_budget_events）。
    diagnostic_max_bundle_bytes: int = Field(default=4 * 1024 * 1024, ge=1)
    # Job-local Ring Buffer 最大事件数；超出丢弃最旧事件（累计 dropped_events）。
    diagnostic_max_events: int = Field(default=200, ge=1)
    # 诊断包保留天数；生命周期清理按此删除过期诊断包。
    diagnostic_retention_days: int = Field(default=7, ge=1)

    # P06-11K：诊断配置边界校验（容量下限 + 生产环境 all_payload fail-fast）。
    @model_validator(mode="after")
    def _diagnostics_config_guard(self) -> "Settings":
        if self.diagnostic_max_event_bytes > self.diagnostic_max_bundle_bytes:
            raise ValueError(
                "DIAGNOSTIC_MAX_EVENT_BYTES 不能大于 DIAGNOSTIC_MAX_BUNDLE_BYTES"
            )
        if (
            self.environment == "production"
            and self.diagnostic_capture_mode == "all_payload"
        ):
            raise ValueError(
                "environment=production 不允许 DIAGNOSTIC_CAPTURE_MODE=all_payload"
                "（all_payload 是本地调试专用，可能包含业务输入，必须显式切回"
                "off/metadata/failure_payload）"
            )
        return self

    # P06-04：production 环境禁止占位符密钥/联系邮箱（fail-fast，见 .env.example 说明）。
    @model_validator(mode="after")
    def _production_secrets_guard(self) -> "Settings":
        if self.environment != "production":
            return self
        problems: list[str] = []
        if is_placeholder_secret(self.llm_api_key.get_secret_value()):
            problems.append("LLM_API_KEY")
        if self.serper_api_key is not None and is_placeholder_secret(
            self.serper_api_key.get_secret_value()
        ):
            problems.append("SERPER_API_KEY")
        if is_placeholder_secret(self.sec_user_agent_contact):
            problems.append("SEC_USER_AGENT_CONTACT")
        if problems:
            raise ValueError(
                "environment=production 不允许占位符密钥/联系邮箱"
                f"（请在 .env 配置真实值）: {', '.join(problems)}"
            )
        return self

    # P06-11G：角色枚举 → 覆盖块字段前缀（避免字符串散落）。
    _ROLE_OVERRIDE_PREFIXES: dict[str, str] = {
        "research": "llm_research_",
        "analysis": "llm_analysis_",
        "writer": "llm_writer_",
    }
    # P06-11G：角色 → LLM_ROLE_* 引用字段名。
    _ROLE_REFERENCE_FIELDS: dict[str, str] = {
        "research": "llm_role_research",
        "analysis": "llm_role_analysis",
        "writer": "llm_role_writer",
    }

    def build_model_registry(self) -> dict[str, ModelProfile]:
        """返回按 name 索引的模型注册表（P06-11G 角色复用的模型目录）。

        来源（优先级从高到低）：
        1. ``llm_models``（代码/Settings 直接注入的 ``dict[str, ModelProfile]``）；
        2. .env 的 ``LLM_MODELS_JSON``（见下方说明）——为保持简单，注册表
           主要面向代码/测试注入；.env 供角色引用名映射时使用。
        """
        return dict(self.llm_models)

    def _role_registry_name(self, role: str) -> str | None:
        """返回角色引用的注册表条目名（LLM_ROLE_<ROLE>）；未配置返回 None。"""
        field = self._ROLE_REFERENCE_FIELDS[role]
        value = getattr(self, field)
        return value if isinstance(value, str) else None

    def build_role_llm_config(self, role: str) -> "RoleLLMOverride | None":
        """按角色返回完整 LLM 覆盖配置（P06-11G 角色解耦）。

        解析优先级：
        1. 角色引用的注册表条目（``LLM_ROLE_<ROLE>=<name>`` → ``LLM_MODELS``）：
           命中即以该条目的 vendor/base_url/api_key/model/temperature/timeout/
           enable_thinking 全覆盖角色（换模型只改一处引用）；
        2. ``LLM_<ROLE>_*`` 直接覆盖块（每角色全家桶）；
        3. 都未配置 → 返回 None，由 ``LLMConfig.config_for`` 回退全局默认。

        返回 ``RoleLLMOverride``：只含显式配置的字段（None=未覆盖）。
        """
        # 1) 注册表引用优先。
        registry = self.build_model_registry()
        registry_name = self._role_registry_name(role)
        if registry_name:
            profile = registry.get(registry_name)
            if profile is None:
                raise ValueError(
                    f"LLM_ROLE_{role.upper()} 引用的注册表模型 "
                    f"{registry_name!r} 不存在（请检查 LLM_MODELS 注册表）"
                )
            return RoleLLMOverride(
                enabled=True,
                vendor=profile.vendor,
                base_url=profile.base_url,
                api_key=profile.api_key,
                model=profile.model,
                temperature=profile.temperature,
                timeout=profile.timeout,
                enable_thinking=profile.enable_thinking,
                registry_name=registry_name,
            )

        # 2) 直接覆盖块。
        prefix = self._ROLE_OVERRIDE_PREFIXES[role]
        vendor = getattr(self, f"{prefix}vendor")
        base_url = getattr(self, f"{prefix}base_url")
        api_key = getattr(self, f"{prefix}api_key")
        model = getattr(self, f"{prefix}model")
        temperature = getattr(self, f"{prefix}temperature")
        timeout = getattr(self, f"{prefix}timeout")
        enable_thinking = getattr(self, f"{prefix}enable_thinking")

        # 未显式配置 vendor 也没有 api_key → 未启用覆盖块。
        if vendor is None and api_key is None:
            return None
        return RoleLLMOverride(
            enabled=True,
            vendor=vendor,
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
            enable_thinking=enable_thinking,
        )

    def build_research_profile(self) -> ResearchProfile:
        """按 research_profile 档位返回集中预算配置（P05.5）。"""
        return ResearchProfile.for_mode(self.research_profile)


@lru_cache
def get_settings() -> Settings:
    """返回全局共享的 Settings 实例（进程内缓存）。"""
    return Settings()
