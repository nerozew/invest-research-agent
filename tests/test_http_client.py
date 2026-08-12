"""P02-02 共享 httpx client 与 HTTP 异常/状态码映射测试。

验证目标（对齐 docs/04-WORKFLOW-RELIABILITY.md §4/§5.1）：
- 共享 client 显式设置连接/读取超时、跟随重定向、携带 User-Agent；
- 非法超时（<=0）被拒绝；
- 状态码映射：2xx/3xx→None、401/403→AUTH_ERROR、408/超时→NETWORK_TRANSIENT、
  429→RATE_LIMITED、5xx→UPSTREAM_5XX、其它 4xx→INPUT_INVALID；
- 异常映射：超时/连接错误→NETWORK_TRANSIENT、HTTPStatusError 按状态码、
  未知异常→INTERNAL_BUG；
- 错误码复用 domain.errors.ErrorCode，可重试性委托 domain。

不发起真实网络请求；httpx 异常对象直接构造，外部服务使用 mock/fixture。
"""

from __future__ import annotations

import httpx
import pytest

from invest_research.domain.errors import ErrorCode, is_retryable
from invest_research.infrastructure.http.client import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    build_http_client,
    classify_http_exception,
    classify_status_code,
)
from invest_research.settings import Settings

_UA = "invest-research-test/0.1 (+test@example.com)"


def _status_response(status_code: int) -> httpx.Response:
    """构造一个携带指定状态码的 httpx.Response（需绑定 request）。"""
    request = httpx.Request("GET", "https://example.test/")
    return httpx.Response(status_code, request=request)


# ---------------------------------------------------------------------------
# build_http_client
# ---------------------------------------------------------------------------


def test_build_http_client_sets_explicit_timeouts() -> None:
    """共享 client 必须显式设置连接与读取超时（不允许 timeout=None）。"""
    client = build_http_client(user_agent=_UA)
    try:
        timeout = client.timeout
        assert timeout is not None
        assert timeout.connect == DEFAULT_CONNECT_TIMEOUT_SECONDS
        assert timeout.read == DEFAULT_READ_TIMEOUT_SECONDS
        assert timeout.write is not None
        assert timeout.pool is not None
    finally:
        client.close()


def test_build_http_client_follows_redirects_and_sets_ua() -> None:
    """client 跟随重定向，并携带显式 User-Agent。"""
    client = build_http_client(user_agent=_UA)
    try:
        assert client.follow_redirects is True
        assert client.headers["User-Agent"] == _UA
    finally:
        client.close()


def test_build_http_client_custom_timeouts() -> None:
    """可按需覆盖连接/读取超时。"""
    client = build_http_client(
        connect_timeout=7.5,
        read_timeout=60.0,
        user_agent=_UA,
    )
    try:
        assert client.timeout.connect == 7.5
        assert client.timeout.read == 60.0
    finally:
        client.close()


@pytest.mark.parametrize("bad_timeout", [0.0, -1.0])
def test_build_http_client_rejects_non_positive_timeout(bad_timeout: float) -> None:
    """连接/读取超时必须为正数。"""
    with pytest.raises(ValueError):
        build_http_client(connect_timeout=bad_timeout, user_agent=_UA)
    with pytest.raises(ValueError):
        build_http_client(read_timeout=bad_timeout, user_agent=_UA)


# ---------------------------------------------------------------------------
# classify_status_code
# ---------------------------------------------------------------------------


def test_status_success_and_redirect_map_to_none() -> None:
    """2xx/3xx 视为成功，返回 None（不判定为错误）。"""
    assert classify_status_code(200) is None
    assert classify_status_code(204) is None
    assert classify_status_code(301) is None
    assert classify_status_code(304) is None


@pytest.mark.parametrize("status", [401, 403])
def test_status_auth_error(status: int) -> None:
    """401/403 → AUTH_ERROR（不可重试）。"""
    code = classify_status_code(status)
    assert code == ErrorCode.AUTH_ERROR
    assert code is not None and is_retryable(code) is False


def test_status_rate_limited() -> None:
    """429 → RATE_LIMITED（可重试）。"""
    code = classify_status_code(429)
    assert code == ErrorCode.RATE_LIMITED
    assert code is not None and is_retryable(code) is True


def test_status_request_timeout() -> None:
    """408 → NETWORK_TRANSIENT（可重试）。"""
    code = classify_status_code(408)
    assert code == ErrorCode.NETWORK_TRANSIENT
    assert code is not None and is_retryable(code) is True


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_status_upstream_5xx(status: int) -> None:
    """5xx → UPSTREAM_5XX（可重试）。"""
    code = classify_status_code(status)
    assert code == ErrorCode.UPSTREAM_5XX
    assert code is not None and is_retryable(code) is True


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_status_other_4xx_input_invalid(status: int) -> None:
    """其它 4xx → INPUT_INVALID（不可重试）。"""
    code = classify_status_code(status)
    assert code == ErrorCode.INPUT_INVALID
    assert code is not None and is_retryable(code) is False


# ---------------------------------------------------------------------------
# classify_http_exception
# ---------------------------------------------------------------------------


def test_exception_timeout_maps_to_network_transient() -> None:
    """超时异常 → NETWORK_TRANSIENT（可重试）。"""
    code = classify_http_exception(httpx.TimeoutException("read timeout"))
    assert code == ErrorCode.NETWORK_TRANSIENT
    assert is_retryable(code) is True


def test_exception_connect_error_maps_to_network_transient() -> None:
    """连接错误（NetworkError 子类）→ NETWORK_TRANSIENT（可重试）。"""
    code = classify_http_exception(httpx.ConnectError("connection refused"))
    assert code == ErrorCode.NETWORK_TRANSIENT
    assert is_retryable(code) is True


def test_exception_http_status_error_uses_status_map() -> None:
    """HTTPStatusError 按响应状态码映射。"""
    rate = httpx.HTTPStatusError(
        "rate limited",
        request=_status_response(429).request,
        response=_status_response(429),
    )
    assert classify_http_exception(rate) == ErrorCode.RATE_LIMITED

    auth = httpx.HTTPStatusError(
        "forbidden",
        request=_status_response(403).request,
        response=_status_response(403),
    )
    assert classify_http_exception(auth) == ErrorCode.AUTH_ERROR


def test_exception_unknown_maps_to_internal_bug() -> None:
    """未知异常 → INTERNAL_BUG（不可重试，显式暴露缺陷）。"""
    code = classify_http_exception(RuntimeError("unexpected"))
    assert code == ErrorCode.INTERNAL_BUG
    assert is_retryable(code) is False


# ---------------------------------------------------------------------------
# Settings 集成：HTTP 配置默认值
# ---------------------------------------------------------------------------


def test_settings_http_defaults() -> None:
    """settings 提供 HTTP 超时与 User-Agent 默认值。"""
    settings = Settings(
        _env_file=None,
        llm_api_key="sk-test",
        sec_user_agent_contact="test@example.com",
    )
    assert settings.http_connect_timeout == 5.0
    assert settings.http_read_timeout == 30.0
    assert "invest-research" in settings.http_user_agent
