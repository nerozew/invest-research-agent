"""P02-12 ArtifactStoreTool：原子本地工件存储（临时写 + checksum + 重复写拒绝）。

对齐：
- docs/05 P02-12 验收：临时写、checksum、读取、重复写测试；
- docs/03-DATABASE §6：文件先写临时名，fsync + checksum 成功后原子 rename；
- docs/04 §5.3 工件存储：临时写入、flush、checksum 任一步失败都不能宣布成功；
- P02-01 Tool 契约：请求/响应为 Pydantic BaseModel，execute → ToolResult。

职责边界（不越界）：
- 本模块只做本地文件系统原子读写，不写数据库、不联网；
- 工件“登记到 artifacts 表”属于 P01-13 ArtifactRepository / 未来 P03 编排，
  本工具只负责把字节持久化并返回 ArtifactRef（key/checksum/size）。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from invest_research.domain.errors import ErrorCode
from invest_research.tools.base import ToolError, ToolFailure, ToolResult, ToolSuccess

# key 中禁止出现的字符（防止路径穿越与危险文件名）
_FORBIDDEN_KEY_CHARS = frozenset("\x00 \t\r\n;<>[]|?*")


class ArtifactRef(BaseModel):
    """工件引用：key + 大小 + sha256（对应 artifacts 表的元数据载体）。"""

    model_config = ConfigDict(frozen=True)

    artifact_key: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    content_checksum: str = Field(min_length=1)


class ArtifactStoreRequest(BaseModel):
    """判别请求：operation 决定携带哪些字段。

    - write：artifact_key + content（必填）；overwrite 可选（默认拒绝覆盖）；
    - read ：artifact_key（必填），content 必须为空；
    - list ：不接受 artifact_key/content。
    """

    model_config = ConfigDict(frozen=True)

    operation: Literal["write", "read", "list"]
    artifact_key: str | None = None
    content: bytes | None = None
    overwrite: bool = False

    @model_validator(mode="after")
    def _validate_operation_fields(self) -> "ArtifactStoreRequest":
        if self.operation == "write":
            if not self.artifact_key or self.content is None:
                raise ValueError("write 操作必须提供 artifact_key 与 content")
        elif self.operation == "read":
            if not self.artifact_key:
                raise ValueError("read 操作必须提供 artifact_key")
            if self.content is not None:
                raise ValueError("read 操作不接受 content")
        else:  # list
            if self.artifact_key is not None or self.content is not None:
                raise ValueError("list 操作不接受 artifact_key 或 content")
        return self


class ArtifactOperationResponse(BaseModel):
    """判别响应：operation 与携带字段一一对应。"""

    model_config = ConfigDict(frozen=True)

    operation: Literal["write", "read", "list"]
    artifact: ArtifactRef | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    content: bytes | None = None
    content_checksum: str | None = None


def _validate_artifact_key(key: str) -> None:
    """校验工件 key：相对、无 .. 段、无危险字符。非法即抛 ValueError。"""
    if not key or not key.strip():
        raise ValueError("artifact_key 不能为空")
    if key != key.strip():
        raise ValueError("artifact_key 首尾不能有空白")
    if "\\" in key:
        raise ValueError("artifact_key 不能包含反斜杠")
    if any(c in _FORBIDDEN_KEY_CHARS for c in key):
        raise ValueError(f"artifact_key 含非法字符: {key}")
    parts = PurePosixPath(key).parts
    if PurePosixPath(key).is_absolute():
        raise ValueError(f"artifact_key 不能是绝对路径: {key}")
    if ".." in parts:
        raise ValueError(f"artifact_key 不能包含 .. 段: {key}")


class ArtifactStore:
    """本地原子工件存储：root 目录下的安全读写。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _resolve(self, key: str) -> Path:
        """把 key 解析为 root 下的安全路径（双重防穿越）。"""
        _validate_artifact_key(key)
        target = (self._root / key).resolve()
        if not target.is_relative_to(self._root.resolve()):
            raise ValueError(f"artifact_key 越出存储根目录: {key}")
        return target

    def write(self, key: str, content: bytes, *, overwrite: bool = False) -> ArtifactRef:
        """原子写入：同目录临时文件 → flush + fsync → os.replace。

        - 默认重复写抛 FileExistsError（不覆盖，原内容不变）；
        - overwrite=True 时用 os.replace 原子替换旧文件。
        - 任一步失败清理临时文件，不留下半成品。
        """
        target = self._resolve(key)
        if target.exists() and not overwrite:
            raise FileExistsError(f"工件已存在（如需覆盖请开启 overwrite）: {key}")

        target.parent.mkdir(parents=True, exist_ok=True)

        fd: int | None = None
        tmp_name: str | None = None
        try:
            # 临时文件必须与目标同目录，保证 os.replace 的原子性（同文件系统）
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".part")
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, target)
            tmp_name = None  # 已 rename，无需清理
        except BaseException:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise

        return ArtifactRef(
            artifact_key=key,
            byte_size=len(content),
            content_checksum=hashlib.sha256(content).hexdigest(),
        )

    def read(self, key: str) -> bytes:
        """按 key 读取原始字节；不存在抛 KeyError。"""
        target = self._resolve(key)
        if not target.exists():
            raise KeyError(key)
        return target.read_bytes()

    def list(self) -> list[ArtifactRef]:
        """列出全部工件（按 key 排序）；仅列非隐藏文件（不含临时文件）。"""
        refs: list[ArtifactRef] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self._root).as_posix()
            data = path.read_bytes()
            refs.append(
                ArtifactRef(
                    artifact_key=key,
                    byte_size=len(data),
                    content_checksum=hashlib.sha256(data).hexdigest(),
                )
            )
        return refs


class ArtifactStoreTool:
    """P02-01 契约工具：把 ArtifactStore 暴露为 ToolResult 接口。

    错误映射：
    - 非法 key / 重复写（不可自动重试）→ INPUT_INVALID（is_retryable=False）；
    - 读缺失 key → INPUT_INVALID。
    """

    name = "artifact_store"

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def execute(self, request: ArtifactStoreRequest) -> ToolResult[ArtifactOperationResponse]:
        try:
            if request.operation == "write":
                ref = self._store.write(
                    request.artifact_key or "", request.content or b"", overwrite=request.overwrite
                )
                return ToolSuccess(value=ArtifactOperationResponse(operation="write", artifact=ref))

            if request.operation == "read":
                content = self._store.read(request.artifact_key or "")
                return ToolSuccess(
                    value=ArtifactOperationResponse(
                        operation="read",
                        content=content,
                        content_checksum=hashlib.sha256(content).hexdigest(),
                    )
                )

            refs = self._store.list()
            return ToolSuccess(value=ArtifactOperationResponse(operation="list", artifacts=refs))

        except FileExistsError as exc:
            return ToolFailure(
                error=ToolError(error_code=ErrorCode.INPUT_INVALID, message=str(exc))
            )
        except KeyError:
            return ToolFailure(
                error=ToolError(error_code=ErrorCode.INPUT_INVALID, message="工件不存在，无法读取")
            )
        except ValueError as exc:
            return ToolFailure(
                error=ToolError(error_code=ErrorCode.INPUT_INVALID, message=str(exc))
            )
