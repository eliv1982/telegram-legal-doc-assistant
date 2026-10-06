"""
services.workspace: private unique directories, idempotent cleanup, and a startup purge that only ever
touches this application's own stale directories.
"""
import os
import shutil
import stat
import tempfile
import time

import pytest

from services.workspace import WORKSPACE_PREFIX, SessionWorkspace, purge_stale_workspaces


@pytest.fixture
def temp_root(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def test_workspaces_are_unique_neutrally_named_and_cleanup_is_safe(temp_root, monkeypatch):
    first, second = SessionWorkspace.create(), SessionWorkspace.create()

    assert first.path != second.path
    assert first.path.parent == temp_root and first.path.name.startswith(WORKSPACE_PREFIX)
    assert first.voice_path.name == "voice.ogg"  # nothing here identifies a Telegram user, chat or file
    assert first.document_path(".pdf").name == "document.pdf"
    if os.name == "posix":
        assert stat.S_IMODE(first.path.stat().st_mode) == 0o700

    (first.path / "nested").mkdir()
    first.voice_path.write_bytes(b"x")
    (first.path / "nested" / "y").write_bytes(b"y")
    first.cleanup()
    first.cleanup()  # idempotent
    assert not first.path.exists() and second.path.exists()

    foreign = temp_root / "not-a-workspace"
    foreign.mkdir()
    SessionWorkspace(foreign).cleanup()  # refuses anything without the prefix
    assert foreign.exists()

    def denied(path):
        raise PermissionError("in use")

    monkeypatch.setattr(shutil, "rmtree", denied)
    second.cleanup()  # a failing removal is logged, never raised


def test_purge_removes_only_stale_directories_with_the_apps_prefix(temp_root):
    old = time.time() - 3600
    stale = temp_root / f"{WORKSPACE_PREFIX}stale"
    fresh = temp_root / f"{WORKSPACE_PREFIX}fresh"
    foreign_dir = temp_root / "someone-elses-dir"
    prefixed_file = temp_root / f"{WORKSPACE_PREFIX}file"
    for directory in (stale, fresh, foreign_dir):
        directory.mkdir()
    (stale / "voice.ogg").write_bytes(b"x")
    prefixed_file.write_bytes(b"x")
    for path in (stale, foreign_dir, prefixed_file):
        os.utime(path, (old, old))

    assert purge_stale_workspaces(max_age_seconds=600) == 1
    assert sorted(p.name for p in temp_root.iterdir()) == sorted([fresh.name, foreign_dir.name, prefixed_file.name])
