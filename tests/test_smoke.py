"""P00-03 最小冒烟测试：验证 invest-research 包可导入、入口可运行。"""

import contextlib
import io

from invest_research import main


def test_package_importable() -> None:
    """包必须可导入且暴露 main 入口。"""
    import invest_research

    assert invest_research is not None
    assert hasattr(invest_research, "main")


def test_main_runs_and_prints() -> None:
    """CLI 入口 main() 必须可执行并输出欢迎语。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        main()
    assert "Hello from invest-research!" in buffer.getvalue()
