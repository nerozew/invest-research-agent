"""前端运行配置（P04-UI-01）。

Streamlit 是独立进程，不读取后端 ``Settings``（后者强制要求 LLM 密钥等，
前端环境没有这些变量）。本模块只读取前端需要的两个环境变量：

- ``API_BASE_URL``：FastAPI 服务地址（本地默认 ``http://localhost:8000``，
  Docker Compose 内用服务名覆盖）。
- ``API_TIMEOUT``：请求总超时秒数（默认 30.0）。

安全：本模块不读取、不保存任何密钥；只透出 API 地址（不含凭据）。
"""

from __future__ import annotations

import os

__all__ = ["DEFAULT_API_BASE_URL", "DEFAULT_API_TIMEOUT", "get_api_base_url", "get_api_timeout"]

DEFAULT_API_BASE_URL = "http://localhost:8000"
DEFAULT_API_TIMEOUT = 30.0


def get_api_base_url() -> str:
    """从环境变量读取 API Base URL；未设置时返回本地默认值。"""
    return os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL).strip() or DEFAULT_API_BASE_URL


def get_api_timeout() -> float:
    """从环境变量读取 API 超时秒数；非法/未设置时返回默认值。"""
    raw = os.environ.get("API_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_API_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_API_TIMEOUT
    return value if value > 0 else DEFAULT_API_TIMEOUT
