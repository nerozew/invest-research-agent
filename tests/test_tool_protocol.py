"""P02-01 Tool 契约测试（fake tool）。

验证目标：
- fake tool 满足统一 `Tool` 契约（依赖倒置：Agent/Flow 只依赖抽象协议）；
- 成功结果携带类型明确的数据（Pydantic 模型）；
- 失败结果携带 `ErrorCode` 与安全错误信息；
- 成功/失败结果可被调用方明确区分；
- 成功与失败互斥，不允许出现"既成功又失败"的矛盾结果；
- 错误分类复用 `domain.errors.ErrorCode`，不建立第二套错误码。

本测试不依赖真实外部服务、CrewAI、HTTPX、数据库。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from invest_research.domain.errors import ErrorCode, is_retryable
from invest_research.tools.base import Tool, ToolError, ToolFailure, ToolResult, ToolSuccess

# ---------------------------------------------------------------------------
# fake 输入/输出数据类型（契约层面只需要 Pydantic 模型，与具体服务无关）
# ---------------------------------------------------------------------------


class PingRequest(BaseModel):
    """fake 工具输入：非空字符串消息。"""

    model_config = {"frozen": True}

    message: str = Field(min_length=1)


class PingResponse(BaseModel):
    """fake 工具成功输出：回显消息。"""

    model_config = {"frozen": True}

    echo: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# fake tool：结构上满足 Tool[PingRequest, PingResponse] 契约
# ---------------------------------------------------------------------------


class FakePingTool:
    """最小 fake 工具（不改继承，验证纯结构性依赖倒置）。"""

    name = "fake_ping"

    def execute(self, request: PingRequest) -> ToolResult[PingResponse]:
        if request.message == "boom":
            # 可重试错误：网络瞬态
            return ToolFailure(
                error=ToolError(
                    error_code=ErrorCode.NETWORK_TRANSIENT,
                    message="上游服务临时不可用",
                    details={"attempt": 1},
                )
            )
        if request.message == "invalid":
            # 不可重试错误：非法输入
            return ToolFailure(
                error=ToolError(error_code=ErrorCode.INPUT_INVALID, message="非法输入")
            )
        return ToolSuccess(value=PingResponse(echo=request.message))


def _execute_via_contract(
    tool: Tool[PingRequest, PingResponse], request: PingRequest
) -> ToolResult[PingResponse]:
    """通过协议类型调用工具：mypy 在类型层验证 fake tool 满足契约。"""
    return tool.execute(request)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_fake_tool_satisfies_tool_contract() -> None:
    """fake tool 满足统一 Tool 契约（运行时 + 类型层）。"""
    fake = FakePingTool()
    # 运行时：具备 Tool 协议要求的成员（name / execute）
    assert isinstance(fake, Tool)
    # 类型层：能作为 Tool[PingRequest, PingResponse] 传入并调用
    result = _execute_via_contract(fake, PingRequest(message="hello"))
    assert isinstance(result, ToolSuccess)


def test_success_result_carries_typed_value() -> None:
    """成功结果携带类型明确的数据（PingResponse 模型，而非原始 dict）。"""
    fake = FakePingTool()
    result = fake.execute(PingRequest(message="hello"))

    assert isinstance(result, ToolSuccess)
    assert not isinstance(result, ToolFailure)
    # value 是类型明确的 Pydantic 模型（mypy 已收窄为 PingResponse）
    assert result.value == PingResponse(echo="hello")
    assert result.value.echo == "hello"
    assert result.kind == "success"


def test_success_and_failure_are_distinguishable() -> None:
    """调用方可明确区分成功与失败两种结果。"""
    fake = FakePingTool()
    ok = fake.execute(PingRequest(message="hello"))
    err = fake.execute(PingRequest(message="boom"))

    assert isinstance(ok, ToolSuccess) and not isinstance(ok, ToolFailure)
    assert isinstance(err, ToolFailure) and not isinstance(err, ToolSuccess)
    assert ok.kind == "success"
    assert err.kind == "failure"


def test_failure_carries_error_code_and_safe_message() -> None:
    """失败结果携带 ErrorCode 与安全的错误信息。"""
    fake = FakePingTool()
    result = fake.execute(PingRequest(message="invalid"))

    assert isinstance(result, ToolFailure)
    # 错误码复用 domain.errors.ErrorCode
    assert result.error.error_code == ErrorCode.INPUT_INVALID
    assert isinstance(result.error.error_code, ErrorCode)
    # message 为普通文本（工具层不承载密钥/凭据；安全脱敏在各工具实现,P05-05）
    assert "key" not in result.error.message.lower()
    assert result.kind == "failure"

    # 可重试性委托 domain.errors
    assert result.error.is_retryable is False
    assert is_retryable(result.error.error_code) is False


def test_retryable_error_delegates_to_domain_classification() -> None:
    """错误重试语义复用 domain.errors，不重复定义分类表。"""
    transient = ToolError(error_code=ErrorCode.NETWORK_TRANSIENT, message="瞬时故障")
    assert transient.is_retryable is True
    assert is_retryable(ErrorCode.NETWORK_TRANSIENT) is True

    rate_limited = ToolError(error_code=ErrorCode.RATE_LIMITED, message="限流")
    assert rate_limited.is_retryable is True

    invalid = ToolError(error_code=ErrorCode.INPUT_INVALID, message="非法输入")
    assert invalid.is_retryable is False
    assert is_retryable(ErrorCode.INPUT_INVALID) is False


def test_success_and_failure_are_mutually_exclusive() -> None:
    """不允许出现"既成功又失败"的矛盾结果。

    - ToolSuccess 不含 error 字段；ToolFailure 不含 value 字段；
    - extra="forbid"：向成功结果塞错误字段会被拒绝。
    """
    ok: ToolSuccess[PingResponse] = ToolSuccess(value=PingResponse(echo="x"))
    err = ToolFailure(error=ToolError(error_code=ErrorCode.INTERNAL_BUG, message="内部错误"))

    assert not hasattr(ok, "error")
    assert not hasattr(err, "value")

    with pytest.raises(ValidationError):
        # 尝试构造"既成功又失败"的模型 → 未知字段被拒绝；
        # error 关键字在类型上不存在，属故意构造非法对象，按需豁免
        ToolSuccess(  # type: ignore[call-arg]
            value=PingResponse(echo="x"),
            error=ToolError(error_code=ErrorCode.INTERNAL_BUG, message="x"),
        )
