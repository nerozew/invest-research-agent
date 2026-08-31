"""版本化运行时 Agent 提示词（P03-02 起）。

对齐 docs/02-ARCHITECTURE.md §8「提示词以文件形式版本化」：
- 长提示词放版本化文本文件（research_prompt_v1.md 等），不硬编码在业务类中；
- ``loader.load_prompt`` 统一加载，按 ``PromptName`` 常量取用；
- manifest 记录 prompt hash 与版本（P03-14 / P05-04 可据此判断重算）。
"""

from invest_research.prompts.loader import (
    PROMPT_VERSION,
    PromptName,
    load_prompt,
    prompt_sha256,
)

__all__ = ["PROMPT_VERSION", "PromptName", "load_prompt", "prompt_sha256"]
