"""项目统一质量门禁（P00-06）。

把 P00-04 建立的四类质量命令封装成一条命令：

    uv run invest-research-check

内部按顺序执行：
1. ruff format --check .   （格式化检查）
2. ruff check .            （Lint）
3. mypy src tests          （类型检查）
4. pytest                  （测试执行）

任一命令失败即整体失败（非零退出码），并打印失败提示。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# (步骤名, 参数列表)
_STEPS: tuple[tuple[str, list[str]], ...] = (
    ("ruff format", ["ruff", "format", "--check", "."]),
    ("ruff check", ["ruff", "check", "."]),
    ("mypy", ["mypy", "src", "tests"]),
    ("pytest", ["pytest"]),
)


def check_all() -> None:
    """顺序运行四类质量检查，任一失败则以非零码退出。"""
    # 让本脚本的所有 print 都用 UTF-8 输出，避免 Windows 默认 GBK
    # 终端遇到不可映射字符（U+FFFD）时抛 UnicodeEncodeError。
    # 注意：运行时 stdout/stderr 是 TextIOWrapper 拥有 reconfigure 方法，
    #       但 mypy 把 sys.stdout/sys.stderr 标为 TextIO，无法识别该方法。
    #       这里用 getattr 拿到 Any 类型流对象，避免类型检查报错。
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name)
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    failures: list[str] = []

    for name, args in _STEPS:
        print(f"\n===== {name} =====")
        result = subprocess.run(
            ["uv", "run", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            # 显式用 UTF-8 读取子进程输出，避免 Windows 默认 GBK
            # 解码失败抛 UnicodeDecodeError；errors="replace" 兜底乱码
            encoding="utf-8",
            errors="replace",
        )
        # 始终显示输出，方便定位问题
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        if result.returncode == 0:
            print(f"[OK] {name}")
        else:
            print(f"[FAIL] {name} (exit {result.returncode})")
            failures.append(name)

    if failures:
        print(f"\nQuality gate FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("\nAll quality gates passed.")


if __name__ == "__main__":
    check_all()
