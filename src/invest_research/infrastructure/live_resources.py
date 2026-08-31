"""live 模式 I/O 资源构建（轻量：不依赖 Celery/CrewAI）。

把 FLOW_MODE=live 下「校验 Serper Key + 构建共享 httpx.Client + SerperAdapter」
从 queue/worker.py 中提取出来，使测试无需导入 Celery/CrewAI 即可验证 fail-fast。

依赖边界：仅 httpx、tools、settings；禁止导入 Celery/CrewAI/flow_wiring。
"""

from __future__ import annotations

import httpx

from invest_research.infrastructure.http.client import build_http_client
from invest_research.settings import Settings
from invest_research.tools.serper_adapter import SerperAdapter, SerperConfig


class FlowModeError(RuntimeError):
    """live 模式启动校验失败（API Key 缺失/为空）时的可读错误。"""


def build_live_client_and_serper(settings: Settings) -> tuple[httpx.Client, SerperAdapter]:
    """校验 Serper Key 并构建共享 httpx.Client + SerperAdapter。

    - Serper API Key 缺失/为空时 fail-fast（禁止缺配置启动真实搜索）；
    - 返回 (client, serper)，供 build_research_tools（CrewAI 包装层）使用；
    - 本函数不导入 CrewAI/Celery，可被轻量测试直接调用。
    """
    serper_key = settings.serper_api_key
    if serper_key is None:
        raise FlowModeError(
            "FLOW_MODE=live 需要配置 SERPER_API_KEY（仅从环境变量/.env 读取，"
            "缺失时禁止启动真实搜索）。"
        )
    key_value = serper_key.get_secret_value()
    if not key_value or not key_value.strip():
        raise FlowModeError("FLOW_MODE=live 需要配置 SERPER_API_KEY（不能为空值）。")

    client = build_http_client(
        connect_timeout=settings.http_connect_timeout,
        read_timeout=settings.http_read_timeout,
        user_agent=settings.http_user_agent
        or f"invest-research/0.1 (+{settings.sec_user_agent_contact})",
    )
    serper = SerperAdapter(
        client=client,
        config=SerperConfig(api_key=serper_key, endpoint=settings.serper_endpoint),
    )
    return client, serper
