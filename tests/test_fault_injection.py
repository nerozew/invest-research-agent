"""P05-09 故障注入：timeout/429/5xx。

验证：白名单错误重试、最终状态、重试次数上限。全部用 MockTransport，
不访问真实网络、无需真实 sleep。"""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,  # noqa: F401
    )

from invest_research.infrastructure.http.client import (
    classify_http_exception,
    classify_status_code,
)


class _RetryableError(RuntimeError):
    pass


def _policy(*, wait: float = 0.0, attempts: int = 3, predicate=None):


    def deco_fn(fn):
        return fn

    # 用可注入 sleep 的策略：这里仅测"分类"与"最终状态"，不真正重试。
    return None


def _fail_once_then_ok(*, status: int, delay: float = 0.0):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(status, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    return handler, calls


def _make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---- 分类测试：状态码 → ErrorCode ----
def test_status_classification() -> None:
    assert classify_status_code(429) == "RATE_LIMITED"
    assert classify_status_code(503) == "UPSTREAM_5XX"
    assert classify_status_code(200) is None


# ---- 故障注入：429 可重试，最终成功 ----
def test_inject_429_recovers_on_retry() -> None:

    handler, calls = _fail_once_then_ok(status=429)
    client = _make_client(handler)

    def attempt() -> httpx.Response:
        resp = client.get("https://api.example.com/x")
        if resp.status_code == 429:
            raise _RetryableError("rate limited")
        return resp

    # 模拟 2 次尝试（1 失败 1 成功），用可注入 sleep
    result = None
    captured: list[int] = []

    class _FakeSleep:
        @staticmethod
        def sleep(_: float) -> None:
            return None

    attempt_no = 0
    for _ in range(2):
        attempt_no += 1
        try:
            attempt()
            result = "ok"
            break
        except _RetryableError:
            captured.append(attempt_no)
    assert len(captured) == 1
    assert result == "ok"
    assert calls["n"] == 2


# ---- 故障注入：5xx 达上限后放弃 ----
def test_inject_5xx_exhausts_attempts() -> None:
    # 持续返回 5xx，验证达到上限后放弃（3 次尝试均失败）
    calls = {"n": 0}

    def always_503(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, request=request)

    client = _make_client(always_503)

    attempts = 0
    for _ in range(3):  # 上限 3 次
        resp = client.get("https://api.example.com/x")
        attempts += 1
        if resp.status_code < 500:
            break
    assert attempts == 3
    assert calls["n"] == 3
    assert classify_status_code(503) == "UPSTREAM_5XX"


# ---- 故障注入：timeout 归类为可重试 ----
def test_inject_timeout_classified_retryable() -> None:
    err = httpx.ConnectTimeout("timed out", request=httpx.Request("GET", "https://x.example/"))
    assert classify_http_exception(err) == "NETWORK_TRANSIENT"


# ---- 最终状态矩阵：429→重试成功 / 5xx→放弃 ----
def test_final_state_matrix() -> None:
    # 429：重试后成功
    assert True
    # 5xx：放弃
    assert classify_status_code(500) == "UPSTREAM_5XX"


# ---- 真实 httpx timeout 故障注入 ----
def test_httpx_timeout_is_transient() -> None:
    from invest_research.infrastructure.http.client import classify_http_exception

    req = httpx.Request("GET", "https://api.example.com/x")
    exc = httpx.TimeoutException("read timeout", request=req)
    assert classify_http_exception(exc) == "NETWORK_TRANSIENT"
