"""12-factor 配置对象（P00-05）。

配置来源优先级（pydantic-settings 默认）：
环境变量 > .env 文件 > 代码内默认值。

安全要求：
- 密钥字段使用 SecretStr，str()/repr() 均不泄露明文；
- .env.example 只含占位符，真实 .env 已被 .gitignore 忽略。
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。

    必需字段（无默认值，缺失时报可读错误）：
    - llm_api_key：DeepSeek API 密钥（SecretStr）
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

    # ---- LLM（DeepSeek，OpenAI-compatible endpoint，见架构 §8）----
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr
    llm_model_research: str = "deepseek-chat"
    llm_model_analysis: str = "deepseek-chat"
    llm_model_writer: str = "deepseek-chat"

    # ---- SEC EDGAR 合规（见可靠性 §5.2：User-Agent 必须含联系邮箱）----
    sec_user_agent_contact: str

    # ---- 基础设施（P01 起启用，本地开发默认值）----
    database_url: str = "postgresql+psycopg://invest:invest@localhost:5432/invest"
    redis_url: str = "redis://localhost:6379/0"

    # ---- 本地工件目录（P02 起启用）----
    artifact_root: str = "artifacts"

    # ---- HTTP 客户端（P02-02：显式连接/读取超时，见可靠性 §5.1）----
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 30.0
    http_user_agent: str = "invest-research/0.1 (+your-email@example.com 由 .env 覆盖)"

    # ---- SEC 合规（P02-03：全局限流，项目安全上限 5 req/s，低于官方 10 req/s）----
    sec_rate_limit_per_second: float = 5.0


@lru_cache
def get_settings() -> Settings:
    """返回全局共享的 Settings 实例（进程内缓存）。"""
    return Settings()
