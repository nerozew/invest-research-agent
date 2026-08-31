"""P02-01 最小 Tool 契约（依赖倒置、统一成功/错误对象）。

设计目标（对齐 docs/02-ARCHITECTURE.md §4/§6）：
- 每个工具必须有 Pydantic 输入/输出、错误分类与契约测试；
- Agent/Flow 只依赖抽象契约，不依赖 CrewAI 的具体实现（依赖倒置）；
- 调用方必须能明确区分成功与失败，且二者互斥。

本模块提供：
- ``Tool``：泛型 Protocol（结构性子类型，实现方无需显式继承即可满足契约）；
- ``ToolSuccess`` / ``ToolFailure``：统一成功/失败结果，互斥（extra="forbid"）；
- ``ToolError``：统一错误对象，错误码复用 ``domain.errors.ErrorCode``，
  重试语义委托 ``errors.is_retryable``，不建立第二套错误分类。

依赖边界：本模块只允许导入标准库、Pydantic 与 domain 层；
禁止导入 CrewAI、FastAPI、SQLAlchemy、HTTPX 或任何供应商 SDK。
"""

from __future__ import annotations

from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from invest_research.domain.errors import ErrorCode, is_retryable

# 泛型参数：输入/输出都必须是 Pydantic 模型（跨模块对象契约）。
# 变体声明满足 Protocol 方法位置的 mypy 规则：
# - RequestT 出现在 execute 参数位（消费）→ 逆变 contravariant；
# - ResponseT 出现在 execute 返回位（产出）→ 协变 covariant。
RequestT = TypeVar("RequestT", bound=BaseModel, contravariant=True)
ResponseT = TypeVar("ResponseT", bound=BaseModel, covariant=True)


class ToolError(BaseModel):
    """统一工具错误对象（``ToolFailure.error`` 的有效载荷）。

    - ``error_code``：复用 ``domain.errors.ErrorCode``；
    - ``message``：面向日志/用户的安全错误信息（不含密钥、凭据）；
    - ``details``：结构化补充信息（如 attempt、tool_name）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_code: ErrorCode
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)

    @property
    def is_retryable(self) -> bool:
        """重试语义：委托 domain.errors.is_retryable，保持唯一错误分类表。"""
        return is_retryable(self.error_code)


class ToolSuccess(BaseModel, Generic[ResponseT]):
    """工具成功结果：携带类型明确的 Pydantic 数据（非原始 dict）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["success"] = "success"
    value: ResponseT


class ToolFailure(BaseModel):
    """工具失败结果：携带 ErrorCode 与安全错误信息。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["failure"] = "failure"
    error: ToolError


# PEP 695 泛型别名：ToolResult[ResponseT] = 成功 | 失败（互斥联合）
type ToolResult[ResponseT] = ToolSuccess[ResponseT] | ToolFailure


@runtime_checkable
class Tool(Protocol[RequestT, ResponseT]):
    """统一工具契约（依赖倒置）。

    实现方只需在结构上提供：
    - ``name``：工具名（日志/审计/指标用）；
    - ``execute(request) -> ToolResult[ResponseT]``：接收 Pydantic 请求，
      返回统一成功/失败结果。

    Agent 与 Flow 只依赖本协议调用工具，不依赖具体实现类。
    """

    name: str

    def execute(self, request: RequestT) -> ToolResult[ResponseT]: ...
