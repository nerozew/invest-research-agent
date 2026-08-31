"""P06-11E：供应商无关的结构化收尾端口（StructuredFinalizer）。

职责（应用层端口，供应商无关）：
- 输入原始 Agent 输出（``raw_output``）；
- 输入目标草稿类型（``model``）；
- 输出经本地 Pydantic 校验的草稿；
- 不执行工具；
- 不修改原始事实；
- 最多允许一次格式修复；
- 所有状态必须限定在当前 Job（由调用方保证每次 run 持有独立 Finalizer）。

数据流：

    普通 Agent 工具循环
    → 独立 JSON Finalizer
    → BoundaryCanonicalizer
    → Pydantic
    → 确定性 PackAssembler

禁止：
- ``beta.chat.completions.parse``；
- ``response_format=json_schema``；
- CrewAI 任务级 ``output_pydantic`` / ``output_json``；
- 全局给所有 Agent 请求添加 json_object。

依赖方向：application → domain（Pydantic 模型）+ 标准库。
禁止导入 CrewAI/httpx/openai SDK（具体供应商实现放 infrastructure）。
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

# 目标草稿/包模型（Pydantic BaseModel 子类）
T = TypeVar("T", bound=BaseModel)

# 稳定角色名（research / analysis / writer）；具体实现负责映射到模型。
# 不使用 agents 层 LLMRole，保持 application → domain 依赖方向。
RoleName = str


@runtime_checkable
class StructuredFinalizer(Protocol[T]):
    """把原始 Agent 输出确定为合法草稿的供应商无关端口（P06-11E）。

    契约：
    - 输入任意 Agent 原始输出（文本 / dict / CrewAI 对象）；
    - 输出经本地 Pydantic 校验的目标模型实例；
    - 至多执行一次格式修复；第二次失败立即终止（禁止重跑整个 Agent）；
    - 不执行工具、不修改原始事实、不携带 tools。
    """

    def finalize(
        self,
        raw_output: Any,
        model: type[T],
        *,
        role: RoleName,
    ) -> T:
        """把原始 Agent 输出收尾为合法草稿。

        参数：
        - ``raw_output``：Agent 工具循环完成后的原始输出；
        - ``model``：目标草稿类型（如 AnalysisSelectionDraft）；
        - ``role``：当前角色（research / analysis / writer，决定使用哪个模型）。
        """
        ...


class FinalizerError(RuntimeError):
    """Finalizer 确定性失败（携带稳定错误码与失败阶段）。

    语义：Finalizer 失败不是网络瞬态；重试整个 Agent 不会修复。
    调用方应把它转成 ``LiveFlowExecutionError`` 对应的稳定错误码。
    """

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        failure_stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.failure_stage = failure_stage
