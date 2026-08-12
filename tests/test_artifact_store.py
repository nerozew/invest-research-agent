"""P02-12 ArtifactStoreTool 契约测试（原子本地存储）。

验证目标（docs/05 P02-12 验收）：
- 临时写：写入不留中间文件，成功后无残留临时文件；
- checksum：写入返回 sha256，与内容一致；读回可校验；
- 读取：按 artifact_key 读回原始字节；
- 列出：返回已登记的工件清单（key/checksum/byte_size）；
- 重复写：默认拒绝（INPUT_INVALID，不可重试），原文件不变；
- 覆盖开启：原子替换为新内容（旧内容不再存在）；
- 非法 key（路径穿越/危险字符/绝对路径）：拒绝，且不产生任何文件；
- 满足 P02-01 Tool 契约（name + execute → ToolResult，请求为 BaseModel）；
- 不修改数据库、不联网、不依赖外部服务。

不写真实磁盘以外的路径；每个测试用 pytest tmp_path 隔离。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from invest_research.domain.errors import ErrorCode
from invest_research.tools.artifact_store import (
    ArtifactOperationResponse,
    ArtifactRef,
    ArtifactStore,
    ArtifactStoreRequest,
    ArtifactStoreTool,
)
from invest_research.tools.base import Tool, ToolFailure, ToolSuccess

# ---------------------------------------------------------------------------
# ArtifactStore 单元测试（纯文件系统逻辑）
# ---------------------------------------------------------------------------


def test_write_creates_file_with_checksum(tmp_path: Path) -> None:
    """临时写：文件落盘，返回内容、大小与 sha256 checksum。"""
    store = ArtifactStore(tmp_path)
    content = b"<h1>Report</h1>"

    ref = store.write("00_request.json", content)

    assert isinstance(ref, ArtifactRef)
    assert ref.artifact_key == "00_request.json"
    assert (tmp_path / "00_request.json").read_bytes() == content
    assert ref.byte_size == len(content)
    assert ref.content_checksum == hashlib.sha256(content).hexdigest()


def test_write_leaves_no_temp_files(tmp_path: Path) -> None:
    """原子写：成功后目录中不存在临时文件（无中间态残留）。"""
    store = ArtifactStore(tmp_path)
    store.write("a.json", b"data")

    leftovers = [p for p in tmp_path.rglob("*") if p.is_file() and p.name != "a.json"]
    assert leftovers == []


def test_write_supports_nested_key(tmp_path: Path) -> None:
    """key 支持 / 分隔的子目录（对齐 artifacts/{job_id}/... 结构）。"""
    store = ArtifactStore(tmp_path)
    ref = store.write("documents/msft.html", b"<p>x</p>")

    assert (tmp_path / "documents" / "msft.html").read_bytes() == b"<p>x</p>"
    assert ref.artifact_key == "documents/msft.html"


def test_read_returns_original_content(tmp_path: Path) -> None:
    """读取：按 key 返回原始字节。"""
    store = ArtifactStore(tmp_path)
    store.write("fact.json", b'{"v": 1}')

    assert store.read("fact.json") == b'{"v": 1}'


def test_read_missing_key_raises(tmp_path: Path) -> None:
    """读取不存在的 key → 明确异常。"""
    store = ArtifactStore(tmp_path)
    with pytest.raises(KeyError):
        store.read("missing.json")


def test_duplicate_write_rejected_without_overwrite(tmp_path: Path) -> None:
    """重复写：默认拒绝覆盖，原内容保持不变。"""
    store = ArtifactStore(tmp_path)
    store.write("a.json", b"v1")

    with pytest.raises(FileExistsError):
        store.write("a.json", b"v2")

    assert (tmp_path / "a.json").read_bytes() == b"v1"


def test_overwrite_atomically_replaces_content(tmp_path: Path) -> None:
    """覆盖开启：原子替换，旧内容不再存在，新 checksum 生效。"""
    store = ArtifactStore(tmp_path)
    store.write("a.json", b"v1")

    ref = store.write("a.json", b"v2-very-long-content", overwrite=True)

    assert (tmp_path / "a.json").read_bytes() == b"v2-very-long-content"
    assert ref.content_checksum == hashlib.sha256(b"v2-very-long-content").hexdigest()


def test_list_returns_registered_artifacts(tmp_path: Path) -> None:
    """列出：返回全部工件（key/checksum/size）。"""
    store = ArtifactStore(tmp_path)
    store.write("a.json", b"aaa")
    store.write("b.json", b"bb")

    refs = store.list()
    keys = {r.artifact_key for r in refs}
    assert keys == {"a.json", "b.json"}
    by_key = {r.artifact_key: r for r in refs}
    assert by_key["a.json"].content_checksum == hashlib.sha256(b"aaa").hexdigest()
    assert by_key["b.json"].byte_size == 2


@pytest.mark.parametrize(
    "bad_key",
    ["../escape", "a/../../b", "/abs/path", "..", "a/b/../c", "with space", "semi;colon", "a\\b"],
)
def test_unsafe_key_rejected(tmp_path: Path, bad_key: str) -> None:
    """非法 key（路径穿越/绝对路径/危险字符）→ 拒绝且不产生文件。"""
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        store.write(bad_key, b"x")
    assert list(tmp_path.rglob("*")) == []


# ---------------------------------------------------------------------------
# 请求模型校验（operation 判别字段约束）
# ---------------------------------------------------------------------------


def test_write_request_requires_key_and_content() -> None:
    """write 请求必须提供 artifact_key 与 content。"""
    with pytest.raises(ValidationError):
        ArtifactStoreRequest(operation="write", artifact_key="a.json", content=None)
    with pytest.raises(ValidationError):
        ArtifactStoreRequest(operation="write", artifact_key=None, content=b"x")


def test_read_request_requires_key() -> None:
    """read 请求必须提供 artifact_key。"""
    with pytest.raises(ValidationError):
        ArtifactStoreRequest(operation="read", artifact_key=None)


def test_list_request_forbids_extra_fields() -> None:
    """list 请求不允许传 artifact_key/content。"""
    with pytest.raises(ValidationError):
        ArtifactStoreRequest(operation="list", artifact_key="x")


def test_invalid_operation_rejected() -> None:
    """未知 operation 被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        ArtifactStoreRequest(operation="delete")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ArtifactStoreTool 契约测试（P02-01 Tool Protocol）
# ---------------------------------------------------------------------------


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_tool_satisfies_tool_contract(tmp_path: Path) -> None:
    """ArtifactStoreTool 满足 P02-01 Tool 契约。"""
    tool = ArtifactStoreTool(ArtifactStore(tmp_path))
    assert tool.name == "artifact_store"
    assert isinstance(tool, Tool)


def test_tool_write_success(tmp_path: Path) -> None:
    """工具层写：ToolSuccess 携带 ArtifactRef。"""
    tool = ArtifactStoreTool(ArtifactStore(tmp_path))
    result = tool.execute(
        ArtifactStoreRequest(operation="write", artifact_key="doc.json", content=b"data")
    )

    assert isinstance(result, ToolSuccess)
    value = result.value
    assert isinstance(value, ArtifactOperationResponse)
    assert value.operation == "write"
    assert value.artifact is not None
    assert value.artifact.artifact_key == "doc.json"
    assert value.artifact.content_checksum == _checksum(b"data")


def test_tool_duplicate_write_returns_failure(tmp_path: Path) -> None:
    """工具层重复写：ToolFailure(INPUT_INVALID)，不可重试。"""
    store = ArtifactStore(tmp_path)
    tool = ArtifactStoreTool(store)
    tool.execute(ArtifactStoreRequest(operation="write", artifact_key="doc.json", content=b"v1"))

    result = tool.execute(
        ArtifactStoreRequest(operation="write", artifact_key="doc.json", content=b"v2")
    )

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INPUT_INVALID
    assert result.error.is_retryable is False
    # 原内容未被破坏
    assert store.read("doc.json") == b"v1"


def test_tool_read_success(tmp_path: Path) -> None:
    """工具层读：返回内容与 checksum。"""
    store = ArtifactStore(tmp_path)
    store.write("doc.json", b"abc")
    tool = ArtifactStoreTool(store)

    result = tool.execute(ArtifactStoreRequest(operation="read", artifact_key="doc.json"))

    assert isinstance(result, ToolSuccess)
    value = result.value
    assert value.operation == "read"
    assert value.content == b"abc"
    assert value.content_checksum == _checksum(b"abc")


def test_tool_read_missing_returns_failure(tmp_path: Path) -> None:
    """工具层读不存在 key → ToolFailure（INPUT_INVALID）。"""
    tool = ArtifactStoreTool(ArtifactStore(tmp_path))
    result = tool.execute(ArtifactStoreRequest(operation="read", artifact_key="nope.json"))

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INPUT_INVALID


def test_tool_list_success(tmp_path: Path) -> None:
    """工具层列出：返回全部工件。"""
    store = ArtifactStore(tmp_path)
    store.write("a.json", b"x")
    store.write("b.json", b"yy")
    tool = ArtifactStoreTool(store)

    result = tool.execute(ArtifactStoreRequest(operation="list"))

    assert isinstance(result, ToolSuccess)
    value = result.value
    assert value.operation == "list"
    assert sorted(r.artifact_key for r in value.artifacts) == ["a.json", "b.json"]


def test_tool_rejects_unsafe_key_without_writing(tmp_path: Path) -> None:
    """工具层非法 key → ToolFailure，且目录无残留。"""
    tool = ArtifactStoreTool(ArtifactStore(tmp_path))
    result = tool.execute(
        ArtifactStoreRequest(operation="write", artifact_key="../escape", content=b"x")
    )

    assert isinstance(result, ToolFailure)
    assert result.error.error_code == ErrorCode.INPUT_INVALID
    assert list(tmp_path.rglob("*")) == []
