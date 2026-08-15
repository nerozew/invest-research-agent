"""P05-13 .env 加载诊断（安全：只报键名/长度/占位符/覆盖项，不打印密钥值）。

用法：uv run python scripts/diagnose_env.py

输出项（绝不输出 LLM_API_KEY / SERPER_API_KEY 的具体值）：
- .env 中已定义的键名列表；
- 首行是否有 BOM（Windows UTF-8 with BOM 会导致首键名匹配失败）；
- Settings 实际读到的 llm 配置是否可用（只报长度/前缀/占位符命中）；
- 系统环境变量是否有与 .env 冲突的覆盖项（如 LLM_BASE_URL/FLOW_MODE）；
- 测试开关 RUN_LIVE_E2E / FLOW_MODE 的当前值。
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

# 允许安全展示的诊断信息（不含任何密钥值）
_PLACEHOLDER_MARKERS = ("your-llm-api-key-placeholder", "your-serper-api-key-placeholder", "sk-")


def _show_secure_label(key: str, value: str) -> str:
    """对密钥类值只输出长度与占位符命中；对非密钥输出简短形态。"""
    if key in ("LLM_API_KEY", "SERPER_API_KEY", "llm_api_key", "serper_api_key"):
        if not value:
            return "空"
        hits = [m for m in _PLACEHOLDER_MARKERS if m in value]
        if hits:
            return f"疑似占位符/测试值（命中: {hits[0]}）"
        return f"已配置（长度 {len(value)}）"
    return value  # 非密钥字段（URL/模型名/开关）可安全展示


def main() -> int:
    issues: list[str] = []

    if not ENV_FILE.exists():
        print(f"[缺失] 根目录无 .env：{ENV_FILE}")
        print("请 cp .env.example .env 后填写真实值。")
        return 1

    raw = ENV_FILE.read_bytes()
    # BOM 检测
    if raw.startswith(b"\xef\xbb\xbf"):
        issues.append("文件含 UTF-8 BOM（首行键名将带不可见 \\ufeff，导致 Settings 不匹配）")

    # 解析键名（不显示值）
    text = raw.decode("utf-8-sig", errors="replace")
    keys: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            keys.append(key)

    print(f"[信息] .env 存在，包含 {len(keys)} 个键：{', '.join(keys) if keys else '（无）'}")

    # 通过 Settings 实际加载检验（只报可用性）
    from invest_research.settings import Settings

    try:
        settings = Settings(_env_file=ENV_FILE)

        llm_key_ok = bool((settings.llm_api_key.get_secret_value() or "").strip())
        serper = settings.serper_api_key
        serper_key_ok = bool(serper and serper.get_secret_value().strip())

        print(f"[Settings] llm_api_key: {'可用' if llm_key_ok else '空/缺失'}")
        print(f"[Settings] serper_api_key: {'可用' if serper_key_ok else '空/缺失'}")
        print(f"[Settings] llm_base_url: {settings.llm_base_url}")
        print(f"[Settings] llm_model_research: {settings.llm_model_research}")
        print(f"[Settings] flow_mode: {settings.flow_mode}")

        if not llm_key_ok:
            issues.append("Settings 读不到非空 LLM_API_KEY")
        if not serper_key_ok:
            issues.append("Settings 读不到非空 SERPER_API_KEY")
        if "placeholder" in settings.llm_base_url:
            issues.append("LLM_BASE_URL 仍是占位符（your-... 或默认值）")
    except Exception as exc:  # noqa: BLE001 - 诊断脚本：把加载异常转为可读信息
        print(f"[错误] Settings 加载失败：{type(exc).__name__}")
        issues.append(f"Settings 加载异常: {type(exc).__name__}")

    # 环境变量冲突检测（pydantic 优先级：环境变量 > .env）
    env_conflicts = ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_RESEARCH", "LLM_MODEL_ANALYSIS",
                     "LLM_MODEL_WRITER", "SERPER_API_KEY", "SERPER_ENDPOINT", "FLOW_MODE"]
    active_env = [k for k in env_conflicts if os.environ.get(k)]
    if active_env:
        issues.append(f"系统环境变量覆盖 .env（将优先被使用）：{', '.join(active_env)}")
    else:
        print("[信息] 无系统环境变量覆盖 .env 的 LLM/Serper/FLOW_MODE 配置")

    print(f"[测试开关] RUN_LIVE_E2E={os.environ.get('RUN_LIVE_E2E', '(未设置)')} "
          f"FLOW_MODE(env)={os.environ.get('FLOW_MODE', '(未设置)')} "
          f"FLOW_MODE(.env)={settings.flow_mode if 'settings' in dir() else '(未加载)'}")
    if os.environ.get("RUN_LIVE_E2E") != "1":
        issues.append(
            "RUN_LIVE_E2E 未设为 1：真实 E2E 恒 skip。"
            "PowerShell: $env:RUN_LIVE_E2E=\"1\";  cmd: set RUN_LIVE_E2E=1"
        )
    flow_mode = os.environ.get("FLOW_MODE") or getattr(settings, "flow_mode", "fake")
    if flow_mode != "live":
        issues.append(
            f"FLOW_MODE 当前是 {flow_mode!r}（需要 'live' 才走真实模型/SEC/Serper）。"
            "PowerShell: $env:FLOW_MODE=\"live\";  cmd: set FLOW_MODE=live；"
            "或把根目录 .env 的 FLOW_MODE 改为 live"
        )

    if issues:
        print("\n===== 检测到的问题 =====")
        for i, msg in enumerate(issues, 1):
            print(f"{i}. {msg}")
        return 1
    print("\n===== 全部正常：Settings 可从 .env 加载 live 所需配置 =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
