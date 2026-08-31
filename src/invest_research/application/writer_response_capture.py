"""P06-11H：Writer 每轮 LLM 原始响应捕获（Job 级有界缓冲区）。

背景（根因证据链）：
- CrewAI 1.6.1 的 ``agent.last_messages`` 只保留 system/user/最后一次
  assistant（114 字符），工具循环中间轮的长正文从未进入 ``last_messages``；
- 但 ``LLMCallCompletedEvent.response`` 携带模型每轮真实输出
  （普通调用路径为 ``response_message.content``），可在调用完成边界捕获。

本模块提供：
- ``WriterResponse``：单条候选（sequence / content / char_length / sha256）；
- ``WriterResponseCapture``：应用层协议（捕获 + 读取候选）；
- ``InMemoryWriterResponseBuffer``：Job 级内存实现。

要求：
- 缓冲区实例必须属于当前 ``_RunContext``/Job，禁止模块级全局变量；
- 最多保存 16 条候选；单条内容长度上限 20000 字符；
- 按 sha256 去重；保存 sequence/char_length/sha256 元数据；
- 两个并发 Job 的 Buffer 完全隔离（各自实例）。

依赖边界：application 层只依赖标准库（hashlib/dataclasses），不导入
CrewAI、FastAPI、Redis、云厂商 SDK。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "WriterResponse",
    "WriterResponseCapture",
    "InMemoryWriterResponseBuffer",
]

# 最多保留候选条数（需求：16）。
MAX_CANDIDATES = 16
# 单条候选内容长度上限（字符，避免恶意/异常超长正文塞满内存）。
MAX_CANDIDATE_CHARS = 20_000


@dataclass(frozen=True)
class WriterResponse:
    """一条 Writer LLM 响应候选（去重后的元数据 + 纯文本正文）。"""

    sequence: int
    content: str
    char_length: int
    sha256: str

    @classmethod
    def of(cls, sequence: int, content: str) -> "WriterResponse":
        text = content.strip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(
            sequence=sequence,
            content=text,
            char_length=len(text),
            sha256=digest,
        )


class WriterResponseCapture(Protocol):
    """捕获 Writer 每轮 LLM 响应；供恢复流程读取候选。

    ``capture`` 返回稳定状态码（供 Prometheus result 白名单）：
    - ``"accepted"``：成功捕获；
    - ``"duplicate"``：内容与已捕获候选重复（sha256 相同）；
    - ``"too_long"``：超出单条长度上限；
    - ``"empty"``：非字符串/纯空白/其它无效内容（调用方先甄别）。
    """

    def capture(self, content: str) -> str:
        """捕获一条纯文本正文；返回状态码（accepted/duplicate/too_long/empty）。"""
        ...

    def candidates(self) -> list[WriterResponse]:
        """返回去重后的候选列表（按捕获顺序）。"""
        ...


class InMemoryWriterResponseBuffer:
    """Job 级有界缓冲区：去重、容量上限、单条长度上限。

    用法：每个 Job 创建一个实例（放在 ``_RunContext``/Runner 实例字段），
    由 ``LlmCallObserver`` 在 Writer LLM 调用完成边界调用 ``capture``；
    恢复流程在 final answer 无效时调用 ``candidates``。
    """

    def __init__(
        self,
        *,
        max_candidates: int = MAX_CANDIDATES,
        max_chars: int = MAX_CANDIDATE_CHARS,
    ) -> None:
        self._max_candidates = int(max_candidates)
        self._max_chars = int(max_chars)
        self._items: list[WriterResponse] = []
        self._seen: set[str] = set()
        self._sequence = 0

    def capture(self, content: str) -> str:
        """捕获一条纯文本正文并返回状态码（accepted/duplicate/too_long/empty）。"""
        if not isinstance(content, str) or not content.strip():
            return "empty"
        text = content.strip()
        if len(text) > self._max_chars:
            return "too_long"
        candidate = WriterResponse.of(self._sequence, text)
        if candidate.sha256 in self._seen:
            return "duplicate"
        self._sequence += 1
        self._seen.add(candidate.sha256)
        self._items.append(candidate)
        if len(self._items) > self._max_candidates:
            # 超容量丢弃最旧（较长 final 通常更晚出现，保留最新）。
            dropped = self._items.pop(0)
            self._seen.discard(dropped.sha256)
        return "accepted"

    def candidates(self) -> list[WriterResponse]:
        """返回去重后的候选列表（按捕获顺序；不做降序，恢复流程自行排序）。"""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)
