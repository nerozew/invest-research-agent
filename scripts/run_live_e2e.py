"""P05-13 真实 E2E smoke 运行入口（opt-in，需要真实 API Key）。

用法（在具备真实 LLM_API_KEY/SERPER_API_KEY 的环境中）:

    # 1. 准备 .env（复制 .env.example 并填写真实值）
    cp .env.example .env
    # 编辑 .env：
    #   FLOW_MODE=live
    #   LLM_API_KEY=sk-...   （你的阿里云百炼/OpenAI-compatible key）
    #   SERPER_API_KEY=...    （你的 Serper key）

    # 2. 运行真实 E2E
    export RUN_LIVE_E2E=1
    export FLOW_MODE=live
    uv run pytest tests/test_live_e2e.py -v

    # 3. 运行成功后查看工件
    #    artifacts/AAPL_2025-10-31/07_manifest.json

验证目标（对齐 P05-13 验收）：
- 真实 SEC 数据（data.sec.gov）；
- 真实搜索服务（Serper）；
- 真实千问调用（LLM_API_KEY）；
- 报告非空；引用可追溯到官方 URL 和 locator；
- 质量门禁通过；中间工件及 RunManifest 完整；
- 无未来数据；无残留 pending/running 状态；
- 记录耗时、Token、重试和外部调用次数；
- 日志和工件无密钥（本入口只输出 Status，不打印 key）。

注意：
- 本脚本本身不调用真实模型；真实调用由 test_live_e2e.py 的
  test_live_e2e_aapl_report（RUN_LIVE_E2E=1 + FLOW_MODE=live 时）触发。
- 缺真实 .env 时运行 pytest 会得到 3 passed + 1 skipped，绝不联网。
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    # 只做"运行前自检"，不调用任何真实服务
    missing: list[str] = []
    for key in ("LLM_API_KEY", "SERPER_API_KEY"):
        if not os.environ.get(key):
            missing.append(key)

    if missing:
        print("P05-13 live E2E 需要以下环境变量（来自真实 .env，非占位符）:")
        for k in missing:
            print(f"  - {k}")
        print("请先在项目根目录创建 .env 并填写真实值，然后:")
        print("  export RUN_LIVE_E2E=1 FLOW_MODE=live")
        print("  uv run pytest tests/test_live_e2e.py -v")
        sys.exit(1)

    print("环境自检通过。执行真实 E2E（可能产生真实模型费用，最多 2 次付费尝试）:")
    print("  export RUN_LIVE_E2E=1 FLOW_MODE=live")
    print("  uv run pytest tests/test_live_e2e.py -v")
    sys.exit(0)
