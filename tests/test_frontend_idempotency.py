"""P04-UI-02：前端 Idempotency-Key 管理器测试。

验收要点：
- 同一次网络重试（请求体一致）复用同一个 Key；
- 真正的新任务（请求体变化）生成新 Key；
- reset 后强制生成新 Key。
"""

from __future__ import annotations

from datetime import date

from invest_research.domain.annual_pipeline import ResearchMode
from invest_research.domain.models import ResearchRequest
from invest_research.frontend.idempotency import IdempotencyKeyManager, request_fingerprint


def _make_request(company: str = "Microsoft", language: str = "zh-CN") -> ResearchRequest:
    return ResearchRequest(
        input_company=company,
        as_of_date=date(2026, 7, 31),
        language=language,
        requested_forms=("10-K", "10-Q"),
    )


def test_same_request_reuses_same_key() -> None:
    """同一次网络重试（请求体一致）复用同一个 Key。"""
    manager = IdempotencyKeyManager()
    req = _make_request()
    first = manager.key_for(req)
    second = manager.key_for(req)
    assert first == second
    assert first.startswith("ui-")


def test_different_request_generates_new_key() -> None:
    """真正的新任务（请求体变化）生成新 Key。"""
    manager = IdempotencyKeyManager()
    key_a = manager.key_for(_make_request(company="Microsoft"))
    key_b = manager.key_for(_make_request(company="Apple"))
    assert key_a != key_b


def test_different_language_is_new_request() -> None:
    """语言变化属于新请求，应生成新 Key。"""
    manager = IdempotencyKeyManager()
    key_zh = manager.key_for(_make_request(language="zh-CN"))
    key_en = manager.key_for(_make_request(language="en"))
    assert key_zh != key_en


def test_reset_forces_new_key() -> None:
    """reset 后即使请求体一致也生成新 Key（用户显式新建任务）。"""
    manager = IdempotencyKeyManager()
    req = _make_request()
    first = manager.key_for(req)
    manager.reset()
    second = manager.key_for(req)
    assert first != second


def test_fingerprint_stable_across_equal_requests() -> None:
    """规范化指纹：同输入必同指纹（与 P04-05 后端口径一致）。"""
    a = _make_request()
    b = _make_request()
    assert request_fingerprint(a) == request_fingerprint(b)


def test_legacy_fingerprint_omits_new_default_mode_but_annual_deep_isolated() -> None:
    """P07-01：旧请求指纹兼容；新模式不会与旧模式共用重试键。"""
    legacy = _make_request()
    annual = legacy.model_copy(update={"research_mode": ResearchMode.ANNUAL_DEEP})

    assert '"research_mode"' not in request_fingerprint(legacy)
    assert '"research_mode":"annual_deep"' in request_fingerprint(annual)
    assert request_fingerprint(legacy) != request_fingerprint(annual)
