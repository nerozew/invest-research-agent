"""前端 API 错误分类（P04-UI-01）。

将 HTTP 4xx / 5xx 与网络错误分类为 typed 异常，使 UI 层可以精确展示：
- 4xx（409、422、404 等）：请求本身的问题，通常是用户可修正的（如输入无效、
  幂等冲突、任务不存在）。不重试。
- 5xx（503、500 等）：后端/依赖暂不可用。UI 可提示稍后重试。
- 网络/超时错误：后端无法到达。UI 可提示检查 API 地址与网络。

安全：错误消息只透出后端返回的 ``detail`` 字段（FastAPI 的标准错误结构），
不显示任何连接串、密钥或内部路径。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApiClientError",
    "ApiError",
    "ApiNetworkError",
    "ApiNotFoundError",
    "ApiTimeoutError",
    "HttpStatusError",
]


class ApiClientError(Exception):
    """API client 错误基类。UI 层捕获后统一展示 message。"""


class ApiError(ApiClientError):
    """后端返回的 4xx / 5xx 错误（带 HTTP 状态码与 detail）。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")

    @property
    def is_client_error(self) -> bool:
        """是否为 4xx 客户端错误（用户可修正，不重试）。"""
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        """是否为 5xx 服务端错误（后端/依赖不可用，可稍后重试）。"""
        return 500 <= self.status_code < 600


class HttpStatusError(ApiError):
    """HTTP 状态码非 2xx 时抛出的错误（保留状态码分类）。"""


class ApiNotFoundError(ApiError):
    """404：任务/工件不存在。"""


class ApiTimeoutError(ApiClientError):
    """请求超时（连接或读取超时显式触发）。"""


class ApiNetworkError(ApiClientError):
    """网络错误（连接被拒、DNS 失败、断连等）。"""


def classify_response_error(status_code: int, payload: Any = None) -> ApiError:
    """把后端错误响应分类为 typed 异常（P04-UI-01 验收）。

    - 从标准错误结构中提取 ``detail``（字符串或可读表示）。
    - 404 归为 ``ApiNotFoundError``（UI 显示"不存在"）。
    - 其余 4xx/5xx 归为 ``HttpStatusError``（保留状态码与 detail）。
    """
    detail = _extract_detail(payload)
    if status_code == 404:
        return ApiNotFoundError(status_code, detail)
    return HttpStatusError(status_code, detail)


def _extract_detail(payload: Any) -> str:
    """从 FastAPI 标准错误 JSON 提取 detail；无法解析时给默认文案。"""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return "请求失败（无详细信息）"
