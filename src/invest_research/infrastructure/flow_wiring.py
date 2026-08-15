"""P05-12A FLOW_MODE=fake/live 生产 Flow wiring（composition root 增量）。

职责：
- ``build_flow_runner(settings)``：按 ``settings.flow_mode`` 返回 FlowRunner 端口实现；
  - ``fake``（默认）：``ResearchFlowRunner``（P03 纯 fake 00-07 全链，不联网），
    普通测试/CI 不产生任何模型费用；
  - ``live``：fail-fast 校验真实 LLM API Key（缺失/为空即抛可读错误），
    返回 ``LiveResearchFlowRunner``（持有 LLMConfig，负责真实 Crew 组装契约）。
- ``LiveResearchFlowRunner``：
  - ``assemble_crew(fakes)``：用注入的 fake LLM 组装三 Agent sequential Crew
    （仅验证 wiring 契约，不触发任何真实模型调用）；
  - ``run()``：尚不允许执行（真实受控 live run 属 P05-13，等待授权）。

授权边界（对齐用户指令）：本任务只做 fake/mock 离线 wiring 测试；
live 分支不发起真实模型/网络调用，缺 key 时 fail-fast。
"""

from __future__ import annotations

from typing import Any

from crewai.crew import Crew

from invest_research.agents.crew_factory import build_research_crew
from invest_research.agents.llm_factory import FakeLLM, LLMConfig
from invest_research.domain.models import ResearchRequest
from invest_research.infrastructure.queue.flow_adapter import ResearchFlowRunner
from invest_research.settings import Settings

__all__ = [
    "FlowModeError",
    "LiveResearchFlowRunner",
    "build_flow_runner",
]


class FlowModeError(RuntimeError):
    """live 模式启动校验失败（API Key 缺失/为空）时的可读错误。"""


class LiveResearchFlowRunner:
    """live 模式的 FlowRunner 契约实现（真实 Crew wiring，暂不允许执行）。

    构造时只保存 LLMConfig（不发请求）；``assemble_crew`` 接受注入的 fake LLM
    字典组装 Crew，用于离线验证 wiring 契约；``run`` 当前抛 ``NotImplementedError``，
    等待 P05-13 受控 live run 授权后接入真实执行路径。
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    @property
    def config(self) -> LLMConfig:
        """暴露 LLMConfig 供审计/测试断言（api_key 为 SecretStr，不泄露明文）。"""
        return self._config

    def assemble_crew(self, fakes: dict[str, FakeLLM]) -> Crew:
        """用注入的 fake LLM 组装三 Agent 顺序 Crew（离线契约验证，不联网）。"""
        return build_research_crew(self._config, fakes)

    def run(self, request: ResearchRequest) -> None:  # pragma: no cover - 等待 P05-13
        raise NotImplementedError(
            "live flow run 尚未启用：真实受控 run 属于 P05-13，需要用户授权后接入。"
        )


def _ensure_live_api_key(settings: Settings) -> str:
    """live 模式 fail-fast：API Key 缺失或为空时抛出可读错误。"""
    key = settings.llm_api_key.get_secret_value()
    if not key or not key.strip():
        raise FlowModeError(
            "FLOW_MODE=live 需要配置 LLM_API_KEY（仅从环境变量/.env 读取，"
            "缺失或为空时禁止启动真实模型运行）。"
        )
    return key


def build_flow_runner(settings: Settings) -> Any:
    """按 settings.flow_mode 返回 FlowRunner 端口实现（P05-12A 入口）。

    - fake：返回 ``ResearchFlowRunner``（默认，离线确定性，普通测试/CI 用）；
    - live：校验 API Key 后返回 ``LiveResearchFlowRunner``（真实 wiring 契约）。
    """
    if settings.flow_mode == "fake":
        return ResearchFlowRunner()

    _ensure_live_api_key(settings)
    config = LLMConfig.from_settings(settings)
    return LiveResearchFlowRunner(config=config)
