"""12-factor 配置对象（P00-05）。

配置来源优先级（pydantic-settings 默认）：
环境变量 > .env 文件 > 代码内默认值。

安全要求：
- 密钥字段使用 SecretStr，str()/repr() 均不泄露明文；
- .env.example 只含占位符，真实 .env 已被 .gitignore 忽略。
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
                research_max_iter=5,
                analysis_max_iter=2,
                writer_max_iter=1,
                max_retry_limit=1,
                max_execution_time=300,
                max_rpm=60,
            )
        return cls(
            mode="deep",
            research_max_iter=15,
            analysis_max_iter=10,
            writer_max_iter=5,
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
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: SecretStr
    llm_model_research: str = "qwen-max"
    llm_model_analysis: str = "qwen-max"
    llm_model_writer: str = "qwen-max"
    llm_temperature: float = 0.2
    llm_timeout: float = 120.0

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
    http_user_agent: str = "invest-research/0.1 (+your-email@example.com 由 .env 覆盖)"

    # ---- SEC 合规（P02-03：全局限流，项目安全上限 5 req/s，低于官方 10 req/s）----
    sec_rate_limit_per_second: float = 5.0

    # ---- 搜索服务（P02-17/18：Serper provider，P05-12B live 需要）----
    # Serper API Key 用 SecretStr：str()/repr() 不泄露明文；.env.example 只放占位符。
    serper_api_key: SecretStr | None = None
    serper_endpoint: str = "https://google.serper.dev/search"

    def build_research_profile(self) -> ResearchProfile:
        """按 research_profile 档位返回集中预算配置（P05.5）。"""
        return ResearchProfile.for_mode(self.research_profile)


@lru_cache
def get_settings() -> Settings:
    """返回全局共享的 Settings 实例（进程内缓存）。"""
    return Settings()
