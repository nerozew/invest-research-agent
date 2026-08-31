"""成本估算共享计价（纯函数，不硬编码模型价格）。

- ``load_pricing(path)``：读取定价 JSON（``{"as_of": ..., "models": {<profile|default>:
  {"input_per_1m", "output_per_1m"}}}``）；
- ``pricing_entry_for(pricing, profile)``：按 profile 取模型价格条目（缺失回退 default）；
- ``estimate_job_cost(*, input_tokens, output_tokens, pricing_entry)``：估算 USD；
  token 缺失或 pricing 缺失返回 None（不伪造）。

依赖边界：只依赖 stdlib；不调用 DB/网络/LLM。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_pricing(path: Path | str | None) -> dict[str, Any] | None:
    """读取定价 JSON；文件缺失/损坏/缺 models 字段返回 None。"""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict) or "models" not in data:
        return None
    return data


def pricing_entry_for(pricing: dict[str, Any] | None, profile: str | None) -> dict[str, Any] | None:
    """按 profile 取模型价格条目；profile 缺失或非 dict 时回退 default；仍缺返回 None。"""
    models = (pricing or {}).get("models") or {}
    if not isinstance(models, dict):
        return None
    entry = models.get(profile)
    if not isinstance(entry, dict):
        entry = models.get("default")
    return entry if isinstance(entry, dict) else None


def estimate_job_cost(
    *,
    input_tokens: int | float | None,
    output_tokens: int | float | None,
    pricing_entry: dict[str, Any] | None,
) -> float | None:
    """按模型价格估算单任务成本（USD）。

    - ``pricing_entry`` 应含 ``input_per_1m`` / ``output_per_1m``；
    - 输入输出 token 都缺失时返回 None（不伪造零成本）。
    """
    if not isinstance(pricing_entry, dict):
        return None
    if input_tokens is None and output_tokens is None:
        return None
    price_in = float(pricing_entry.get("input_per_1m", 0) or 0)
    price_out = float(pricing_entry.get("output_per_1m", 0) or 0)
    inp = float(input_tokens or 0)
    out = float(output_tokens or 0)
    return round((inp * price_in + out * price_out) / 1_000_000, 8)


__all__ = ["estimate_job_cost", "load_pricing", "pricing_entry_for"]
