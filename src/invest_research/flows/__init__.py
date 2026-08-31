"""CrewAI Flow 层（P03-10 起）。

- P03-10：typed Flow state（ResearchFlowState，跨步骤共享的 Pydantic 状态）；
- P03-11~14：Flow 步骤编排、sequential Crew 接入、质量门禁、发布；
- P03-15：纯 fake 端到端。

依赖方向：本层朝下依赖 domain（pack 模型）与 agents（Task/Crew）。
"""

from invest_research.flows.state import ResearchFlowState

__all__ = ["ResearchFlowState"]
