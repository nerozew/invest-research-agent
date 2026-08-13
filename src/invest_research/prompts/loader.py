"""版本化提示词加载器（P03-02）。

职责：
- 通过 ``PromptName`` 枚举引用提示词（避免裸字符串拼错）；
- ``load_prompt`` 从 ``prompts/`` 目录加载对应版本文件；
- 提供版本常量，供 manifest / 输入 hash（P03-14 / P05-04）使用。

依赖边界：只依赖标准库，不导入 CrewAI/外部 SDK。
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


class PromptName(StrEnum):
    """运行时 Agent 提示词文件名（不带 .md 后缀）。"""

    RESEARCH = "research_prompt_v1"
    ANALYSIS = "analysis_prompt_v1"  # P03-03 创建
    WRITER = "writer_prompt_v1"  # P03-04 创建


# 与提示词文件内 `> 版本：` 声明保持一致的版本标识（manifest 记录用）。
PROMPT_VERSION: dict[PromptName, str] = {
    PromptName.RESEARCH: "research_prompt_v1",
    PromptName.ANALYSIS: "analysis_prompt_v1",
    PromptName.WRITER: "writer_prompt_v1",
}


def load_prompt(name: PromptName) -> str:
    """返回指定提示词文件的完整文本（含头部版本元信息）。

    文件不存在时抛出可读 FileNotFoundError（fail-fast，别到运行期才发现缺提示词）。
    """
    path = _PROMPTS_DIR / f"{name.value}.md"
    if not path.is_file():
        raise FileNotFoundError(f"缺少提示词文件: {path}")
    return path.read_text(encoding="utf-8")


def prompt_sha256(name: PromptName) -> str:
    """提示词内容的 sha256（供 run manifest 记录，P03-14）。"""
    return hashlib.sha256(load_prompt(name).encode("utf-8")).hexdigest()
