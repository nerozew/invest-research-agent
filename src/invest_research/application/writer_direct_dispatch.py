"""P06-11I：Writer 无工具单轮直接调度端口（WriterDirectDispatch Protocol）。

背景（根因）：
- 旧 Writer 在 CrewAI 工具循环中反复调用 ``WriterContextReader``，DeepSeek
  把 token 消耗在 tool_calls 参数上，final answer 过短 → REPORT_INVALID；
- 新流程：Python 确定性加载两个 Pack + CitationRegistry → 构建紧凑上下文 →
  **一次无工具 LLM 调用** → Markdown → 本地 ReportDraftAssembler 组装。

本模块定义**应用层端口**（依赖方向 application -> domain），具体供应商实现
（普通 openai-compatible chat.completions.create）位于 infrastructure 层。

有限重试契约（任务要求，最多一次 Writer-only 重试）：
- 第一次响应出现以下任一情况时允许一次重试：
  - ``response.content`` 为空；
  - ``finish_reason=length``；
  - Markdown 明显过短（由调用方判定）；
  - 缺少必需章节（由调用方判定）；
- 第二次请求：复用同一份确定性上下文 + 结构化错误摘要；
- 第二次仍失败：稳定返回 REPORT_INVALID / REPORT_TRUNCATED（不无限重试、
  不自动切换模型、不伪造报告）。

禁止：
- 传 tools / tool_choice / available_functions；
- 使用 WriterContextReader；
- 进入 Agent/Crew max_iter 循环；
- 触发 beta.chat.completions.parse；
- 重新执行 Research/Analysis 或调用任何外部工具。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from invest_research.application.writer_context_builder import BuiltWriterContext
from invest_research.domain.models import ResearchRequest

__all__ = [
    "WriterDispatchError",
    "WriterDispatchResult",
    "WriterDirectDispatch",
]


class WriterDispatchError(RuntimeError):
    """Writer 直接调度的稳定失败（携带稳定 error_code）。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class WriterDispatchResult:
    """无工具 Writer 调用结果（只保存 Markdown 与低基数元数据）。

    - ``markdown``：模型输出的普通 Markdown 正文（可为空/截断，由调用方校验）；
    - ``finish_reason``：供应商返回的 finish_reason（"stop"/"length"/其它）；
    - ``usage``：供应商返回的 usage 摘要（input_tokens/output_tokens，可能为 None）；
    - ``duration_s``：本次模型调用实测耗时（秒，本地测量）。
    """

    markdown: str
    finish_reason: str = "stop"
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_s: float = 0.0


class WriterDirectDispatch(Protocol):
    """执行一次（或至多两次）无工具 Writer LLM 调用。

    调用方（flow_wiring）负责：
    - 构建唯一确定性上下文（``WriterContextBuilder``）；
    - 判断命中重试条件并构造结构化错误摘要；
    - 对最终 Markdown 运行 ReportDraftAssembler 与质量门禁。

    实现方（infrastructure）保证：
    - 绝不传 tools / tool_choice / available_functions；
    - 只读取普通 ``response.content``（不触发 beta.chat.completions.parse）；
    - 重试复用同一份上下文 + 错误摘要，不重新构建上下文；
    - 第二次失败稳定抛 ``WriterDispatchError``。
    """

    def dispatch(
        self,
        request: ResearchRequest,
        context: BuiltWriterContext,
        *,
        error_summary: str | None = None,
    ) -> WriterDispatchResult:
        """执行一次无工具 Writer 调用。

        - ``request``：ResearchRequest（用于 low-cardinality 观测，不进入 prompt）；
        - ``context``：确定性紧凑上下文（已构建，仅一次）；
        - ``error_summary``：第二次调用时携带的结构化错误摘要（可为 None）。
        """
        ...
