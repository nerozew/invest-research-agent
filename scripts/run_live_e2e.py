"""P05-13 真实 E2E smoke 运行入口（opt-in，需要真实 API Key）。

用法（在具备真实 LLM_API_KEY/SERPER_API_KEY 的环境中）:

    # 1. 准备 .env（复制 .env.example 并填写真实值）；本脚本通过项目
    #    Settings 读取根目录 .env（与应用运行时完全一致），不会打印 key。
    cp .env.example .env
    # 编辑 .env：
    #   FLOW_MODE=live
    #   LLM_API_KEY=sk-...   （你的阿里云百炼/OpenAI-compatible key）
    #   SERPER_API_KEY=...    （你的 Serper key）

    # 2. 运行自检（确认 Settings 能读到真实 .env 的 key；不打印值）
    uv run python scripts/run_live_e2e.py

    # 3. 运行真实 E2E
    export RUN_LIVE_E2E=1
    export FLOW_MODE=live
    uv run pytest tests/test_live_e2e.py -v

    # 4. 运行成功后查看工件
    #    artifacts/AAPL_2025-10-31/07_manifest.json

验证目标（对齐 P05-13 验收）：
- 真实 SEC 数据（data.sec.gov）；
- 真实搜索服务（Serper）；
- 真实千问调用（LLM_API_KEY）；
- 报告非空；引用可追溯到官方 URL 和 locator；
- 质量门禁通过；中间工件及 RunManifest 完整；
- 无未来数据；无残留 pending/running 状态；
- 记录耗时、Token、重试和外部调用次数；
- 日志和工件无密钥（本入口只输出是否配置，不打印 key）。

注意：
- 本脚本本身不调用真实模型；真实调用由 test_live_e2e.py 的
  test_live_e2e_aapl_report（RUN_LIVE_E2E=1 + FLOW_MODE=live 时）触发。
- 通过 ``invest_research.settings.Settings`` 读取根目录 ``.env``，
  与 Worker/API 运行时的配置来源一致（缺真实 .env 时无法启动 live）。
- 缺真实 .env 时运行 pytest 会得到离线契约 passed + live skip，绝不联网。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _readable_keys() -> tuple[str, bool, bool]:
    """读取根目录 .env 的 LLM/Serper key 可用性（不打印值）。

    返回 (llm_key, has_llm, has_serper)：has_* 表示对应 key 非空。
    """
    from invest_research.settings import Settings

    settings = Settings(_env_file=PROJECT_ROOT / ".env")
    llm = settings.llm_api_key.get_secret_value()
    serper = settings.serper_api_key.get_secret_value() if settings.serper_api_key else ""
    return llm, bool(llm and llm.strip()), bool(serper and serper.strip())


if __name__ == "__main__":
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print("项目根目录缺少 .env（请先 cp .env.example .env 并填写真实值）。")
        print("按应用运行时同一来源读取：Settings(env_file='.env')。")
        print("不要在任何聊天/日志/issue 中粘贴或回显 API Key。")
        sys.exit(1)

    try:
        _llm_value, has_llm, has_serper = _readable_keys()
    except Exception as exc:  # noqa: BLE001 - 运行前自检：把缺失字段转为可读提示
        print(f".env 读取失败：{type(exc).__name__}（请检查 .env 中字段名）")
        sys.exit(1)

    missing: list[str] = []
    if not has_llm:
        missing.append("LLM_API_KEY")
    if not has_serper:
        missing.append("SERPER_API_KEY")

    # 只输出"是否已配置"，绝不打印 key 值
    print(f"LLM_API_KEY: {'已配置' if has_llm else '未配置'}")
    print(f"SERPER_API_KEY: {'已配置' if has_serper else '未配置'}")
    if not has_serper:
        print("注意：live E2E 需要 SERPER_API_KEY（Research 工具链含真实搜索）。")

    if missing:
        print("P05-13 live E2E 需要以下字段（来自真实 .env，非占位符）:")
        for k in missing:
            print(f"  - {k}")
        print("请先在项目根目录 .env 填写真实值，然后:")
        print("  export RUN_LIVE_E2E=1 FLOW_MODE=live")
        print("  uv run pytest tests/test_live_e2e.py -v")
        sys.exit(1)

    print("环境自检通过（Settings 已从根目录 .env 读到 LLM_API_KEY 与 SERPER_API_KEY）。")
    print("执行真实 E2E（可能产生真实模型费用，最多 2 次付费尝试）:")
    print("  export RUN_LIVE_E2E=1 FLOW_MODE=live")
    print("  uv run pytest tests/test_live_e2e.py -v")
    sys.exit(0)
