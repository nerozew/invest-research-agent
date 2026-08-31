"""P05-11 fault injection: artifact file write failures.

Validate ArtifactStore atomic-write semantics under injected faults:
- temp write failure -> OSError propagates, temp cleaned, target not created;
- flush/fsync failure -> OSError propagates, temp cleaned, no half-written target;
- os.replace (rename) failure -> OSError propagates, temp cleaned, no target;
- checksum failure -> error propagates, not falsely reported succeeded;
- permission / disk-type errors -> OSError propagates, no partial artifact.

Injection targets match ArtifactStore implementation:
- write/flush -> wrap os.fdopen file object;
- fsync -> patch os.fsync;
- rename -> patch os.replace;
- checksum -> patch hashlib.sha256.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from invest_research.tools.artifact_store import ArtifactStore


class _FailingFile:
    """File-object wrapper injecting OSError on targeted method."""

    def __init__(self, f, fail_on: str, message: str, exc_type: type[OSError]) -> None:
        self._f = f
        self._fail_on = fail_on
        self._message = message
        self._exc_type = exc_type

    def write(self, data: bytes) -> int:
        if self._fail_on == "write":
            raise self._exc_type(self._message)
        return self._f.write(data)

    def flush(self) -> None:
        if self._fail_on == "flush":
            raise self._exc_type(self._message)
        return self._f.flush()

    def fileno(self) -> int:
        return self._f.fileno()

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "_FailingFile":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _patch_fdopen(
    monkeypatch, fail_on: str, message: str, exc_type: type[OSError] = OSError
) -> None:
    """Make os.fdopen return a file object that raises on write/flush."""
    orig_fdopen = os.fdopen

    def failing_fdopen(fd: int, mode: str = "r", *args: object, **kwargs: object):
        f = orig_fdopen(fd, mode, *args, **kwargs)
        if mode == "wb":
            return _FailingFile(f, fail_on, message, exc_type)  # type: ignore[return-value]
        return f

    monkeypatch.setattr(os, "fdopen", failing_fdopen)


def _assert_no_temp_leftovers(root: Path, exists_ok: tuple[str, ...] = ()) -> None:
    """Assert no .tmp-* leftover files; only allow listed target files to exist."""
    files = [p for p in root.rglob("*") if p.is_file()]
    for p in files:
        if p.name.startswith(".tmp-"):
            raise AssertionError(f"unexpected leftover temp file: {p.name}")
        if p.name not in exists_ok:
            raise AssertionError(f"unexpected file: {p.name}")


# ---- temp write failure (os.fdopen write) ----


def test_temp_write_failure_cleans_up_and_no_target(monkeypatch, tmp_path: Path) -> None:
    """f.write raises (disk full) -> OSError, temp cleaned, target not created."""
    _patch_fdopen(monkeypatch, "write", "disk full while writing temp file")
    store = ArtifactStore(tmp_path)

    with pytest.raises(OSError):
        store.write("a.json", b"data")
    _assert_no_temp_leftovers(tmp_path)


# ---- flush / fsync failure ----


def test_flush_failure_cleans_up_and_no_target(monkeypatch, tmp_path: Path) -> None:
    """f.flush raises -> OSError, temp cleaned, no target (no false success)."""
    _patch_fdopen(monkeypatch, "flush", "flush failed")
    store = ArtifactStore(tmp_path)

    with pytest.raises(OSError):
        store.write("a.json", b"data")
    _assert_no_temp_leftovers(tmp_path)


def test_fsync_failure_cleans_up_and_no_target(monkeypatch, tmp_path: Path) -> None:
    """os.fsync raises -> OSError, temp cleaned, no target."""
    import os as os_mod

    def failing_fsync(fd: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os_mod, "fsync", failing_fsync)
    store = ArtifactStore(tmp_path)

    with pytest.raises(OSError):
        store.write("a.json", b"data")
    _assert_no_temp_leftovers(tmp_path)


# ---- os.replace (rename) failure ----


def test_rename_failure_cleans_up_and_no_target(monkeypatch, tmp_path: Path) -> None:
    """os.replace raises (rename/disk error) -> OSError, temp cleaned, no target."""
    import os as os_mod

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("rename failed: cross-device or permission")

    monkeypatch.setattr(os_mod, "replace", failing_replace)
    store = ArtifactStore(tmp_path)

    with pytest.raises(OSError):
        store.write("a.json", b"data")
    _assert_no_temp_leftovers(tmp_path)


def test_rename_failure_on_overwrite_preserves_old_content(
    monkeypatch, tmp_path: Path
) -> None:
    """Overwrite path: replace failure -> old content preserved, temp cleaned."""
    store = ArtifactStore(tmp_path)
    store.write("a.json", b"v1")

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("rename failed during overwrite")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        store.write("a.json", b"v2", overwrite=True)

    # old content preserved; only target file exists (no temp leftovers)
    assert (tmp_path / "a.json").read_bytes() == b"v1"
    _assert_no_temp_leftovers(tmp_path, exists_ok=("a.json",))


# ---- checksum failure ----


def test_checksum_failure_not_falsely_succeeded(monkeypatch, tmp_path: Path) -> None:
    """checksum computation fails -> error propagates, not reported succeeded."""
    import hashlib

    class _FailingHash:
        def hexdigest(self) -> str:
            raise OSError("checksum computation failed")

    def failing_sha256(_: bytes = b"", *__: object) -> _FailingHash:
        return _FailingHash()

    monkeypatch.setattr(hashlib, "sha256", failing_sha256)
    store = ArtifactStore(tmp_path)

    with pytest.raises(OSError):
        store.write("a.json", b"data")


# ---- permission / disk-type error ----


def test_permission_error_propagates_and_no_leftovers(monkeypatch, tmp_path: Path) -> None:
    """PermissionError (subclass of OSError) -> propagates, no partial artifact."""
    _patch_fdopen(monkeypatch, "write", "permission denied writing artifact", PermissionError)
    store = ArtifactStore(tmp_path)

    with pytest.raises(PermissionError):
        store.write("a.json", b"data")
    _assert_no_temp_leftovers(tmp_path)
