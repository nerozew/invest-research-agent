"""共享 httpx client 与 HTTP 异常/状态码 → ErrorCode 统一映射（P02-02）。

依据 docs/04-WORKFLOW-RELIABILITY.md：
- §5.1：连接超时 5s、读取超时 20–90s；所有外部 I/O 显式设置连接/读取超时；
- §4 错误分类表：401/403→AUTH_ERROR、429→RATE_LIMITED、
  408/超时/连接错误→NETWORK_TRANSIENT、5xx→UPSTREAM_5XX、
  其它 4xx→INPUT_INVALID、未知→INTERNAL_BUG。

错误码复用 domain.errors.ErrorCode，不建立第二套错误分类。

依赖边界：本层只允许导入标准库、httpx 与 domain 层；
禁止导入 CrewAI、FastAPI、SQLAlchemy 或任何供应商 SDK。
"""

from __future__ import annotations

import httpx

from invest_research.domain.errors import ErrorCode

# 架构 §5.1 推荐默认值
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0


def build_http_client(
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    user_agent: str,
) -> httpx.Client:
    """构建统一的 httpx 共享 client。

    强制要求：
    - 显式设置连接与读取超时（不允许 timeout=None）；
    - 跟随重定向，用于 SEC/搜索返回的 3xx 跳转；
    - 携带显式 User-Agent（SEC EDGAR 合规，见可靠性 §5.2）。
    """
    if connect_timeout <= 0 or read_timeout <= 0:
        raise ValueError("connect_timeout/read_timeout 必须为正数")

    # httpx.Timeout 要求显式设置全部四个参数（connect/read/write/pool），
    # 否则抛 ValueError: must either include a default, or set all four.
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    )


def classify_status_code(status_code: int) -> ErrorCode | None:
    """把 HTTP 状态码映射到统一 ErrorCode；成功/跳转（2xx/3xx）返回 None。"""
    if 200 <= status_code < 400:
        return None
    if status_code in (401, 403):
        return ErrorCode.AUTH_ERROR
    if status_code == 408:
        return ErrorCode.NETWORK_TRANSIENT
    if status_code == 429:
        return ErrorCode.RATE_LIMITED
    if 500 <= status_code < 600:
        return ErrorCode.UPSTREAM_5XX
    # 其它 4xx（400/404/422 等）：输入/请求问题，不重试
    return ErrorCode.INPUT_INVALID


def classify_http_exception(exc: Exception) -> ErrorCode:
    """把 httpx 运行时异常映射到统一 ErrorCode。

    顺序：带响应状态码的错误（HTTPStatusError）→ 超时 → 网络错误 → 未知兜底。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        classified = classify_status_code(exc.response.status_code)
        # 2xx/3xx 理论上不会触发 HTTPStatusError；若出现视为内部缺陷。
        return classified if classified is not None else ErrorCode.INTERNAL_BUG
    if isinstance(exc, httpx.TimeoutException):
        return ErrorCode.NETWORK_TRANSIENT
    if isinstance(exc, httpx.NetworkError):
        return ErrorCode.NETWORK_TRANSIENT
    return ErrorCode.INTERNAL_BUG
