"""Tests for services/file_manager.py — file CRUD with path safety.

Covers: path resolution, traversal prevention, list/read/write/delete,
user scope, docs scope, project scope, file limits.
"""

import os

import pytest

from farmafacil.services.file_manager import (
    MAX_FILE_SIZE,
    MAX_FILES_PER_USER,
    MAX_LIST_ENTRIES,
    READABLE_PROJECT_FILES,
    WRITABLE_PROJECT_DIRS,
    _is_safe_path,
    _resolve_path,
    _user_dir,
    delete_file,
    list_files,
    read_file,
    write_file,
)


# ── Path safety ────────────────────────────────────────────────────────


class TestIsSafePath:
    """Verify _is_safe_path catches traversal attempts."""

    def test_child_is_safe(self, tmp_path):
        child = tmp_path / "sub" / "file.txt"
        assert _is_safe_path(tmp_path, child) is True

    def test_traversal_is_blocked(self, tmp_path):
        outside = tmp_path / ".." / "etc" / "passwd"
        assert _is_safe_path(tmp_path, outside) is False

    def test_same_path_is_safe(self, tmp_path):
        assert _is_safe_path(tmp_path, tmp_path) is True


# ── User directory ─────────────────────────────────────────────────────


class TestUserDir:
    """Verify _user_dir creates sanitized directories."""

    def test_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = _user_dir("5551234567")
        assert user_dir.exists()
        assert user_dir.is_dir()

    def test_strips_non_alphanumeric(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = _user_dir("+58-414-123-4567")
        assert user_dir.name == "584141234567"

    def test_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        d1 = _user_dir("5551234567")
        d2 = _user_dir("5551234567")
        assert d1 == d2


# ── Path resolution ───────────────────────────────────────────────────


class TestResolvePath:
    """Verify _resolve_path handles scopes and rejects traversal."""

    def test_traversal_rejected(self):
        assert _resolve_path("../../etc/passwd", phone="555") is None

    def test_user_scope_without_phone_returns_none(self):
        assert _resolve_path("notes.txt") is None

    def test_user_scope_with_phone(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        result = _resolve_path("notes.txt", phone="555")
        assert result is not None
        _, scope = result
        assert scope == "user"

    def test_user_prefix_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        result = _resolve_path("user:notes.txt", phone="555")
        assert result is not None
        path, scope = result
        assert scope == "user"
        assert path.name == "notes.txt"

    def test_docs_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        (tmp_path / "docs").mkdir()
        result = _resolve_path("docs/test.md")
        assert result is not None
        _, scope = result
        assert scope == "docs"

    def test_project_scope_allowed_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        result = _resolve_path("project:CLAUDE.md")
        assert result is not None
        _, scope = result
        assert scope == "project"

    def test_project_scope_disallowed_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        result = _resolve_path("project:secrets.env")
        assert result is None


# ── List files ─────────────────────────────────────────────────────────


class TestListFiles:
    """Verify list_files returns formatted output."""

    def test_list_user_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        (user_dir / "report.txt").write_text("hello")
        result = list_files(phone="555", scope="user")
        assert "report.txt" in result
        assert "555" in result

    def test_list_empty_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        result = list_files(phone="999", scope="user")
        assert "No files" in result

    def test_list_no_phone_returns_error(self):
        result = list_files(phone=None, scope="user")
        assert "Error" in result

    def test_list_docs_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "README.md").write_text("# Docs")
        result = list_files(scope="docs")
        assert "README.md" in result


# ── Read file ──────────────────────────────────────────────────────────


class TestReadFile:
    """Verify read_file handles scopes, errors, and size limits."""

    def test_read_user_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        (user_dir / "data.txt").write_text("contenido")
        result = read_file("data.txt", phone="555")
        assert result == "contenido"

    def test_read_nonexistent_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        result = read_file("ghost.txt", phone="555")
        assert "not found" in result.lower()

    def test_read_traversal_blocked(self):
        result = read_file("../../etc/passwd", phone="555")
        assert "not allowed" in result.lower()

    def test_read_oversized_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        big = user_dir / "big.bin"
        big.write_bytes(b"\x00" * (MAX_FILE_SIZE + 1))
        result = read_file("big.bin", phone="555")
        assert "too large" in result.lower()

    def test_read_binary_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        (user_dir / "img.bin").write_bytes(bytes(range(256)))
        result = read_file("img.bin", phone="555")
        assert "binary" in result.lower()

    def test_read_project_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Project")
        result = read_file("project:CLAUDE.md")
        assert "# Project" in result


# ── Write file ─────────────────────────────────────────────────────────


class TestWriteFile:
    """Verify write_file creates/overwrites files safely."""

    def test_write_user_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        result = write_file("notes.txt", "hello world", phone="555")
        assert "written" in result.lower()
        content = (tmp_path / "555" / "notes.txt").read_text()
        assert content == "hello world"

    def test_write_project_root_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        result = write_file("project:CLAUDE.md", "hacked", phone=None)
        assert "read-only" in result.lower()

    def test_write_docs_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        (tmp_path / "docs").mkdir()
        result = write_file("docs/test.md", "# Test", phone=None)
        assert "written" in result.lower()
        assert (tmp_path / "docs" / "test.md").read_text() == "# Test"

    def test_write_oversized_content(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        big = "x" * (MAX_FILE_SIZE + 1)
        result = write_file("big.txt", big, phone="555")
        assert "too large" in result.lower()

    def test_write_enforces_file_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        for i in range(MAX_FILES_PER_USER):
            (user_dir / f"file_{i}.txt").write_text("x")
        result = write_file("one_more.txt", "y", phone="555")
        assert "max" in result.lower() or str(MAX_FILES_PER_USER) in result

    def test_overwrite_existing_does_not_count_as_new(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        for i in range(MAX_FILES_PER_USER):
            (user_dir / f"file_{i}.txt").write_text("x")
        # Overwriting an existing file should succeed
        result = write_file("file_0.txt", "updated", phone="555")
        assert "written" in result.lower()

    def test_traversal_blocked(self):
        result = write_file("../../etc/hacked", "bad", phone="555")
        assert "not allowed" in result.lower()


# ── Delete file ────────────────────────────────────────────────────────


class TestDeleteFile:
    """Verify delete_file removes user files only."""

    def test_delete_user_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        user_dir = tmp_path / "555"
        user_dir.mkdir()
        (user_dir / "old.txt").write_text("bye")
        result = delete_file("old.txt", phone="555")
        assert "deleted" in result.lower()
        assert not (user_dir / "old.txt").exists()

    def test_delete_nonexistent_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.USERS_DIR", tmp_path)
        result = delete_file("ghost.txt", phone="555")
        assert "not found" in result.lower()

    def test_delete_docs_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "important.md").write_text("keep")
        result = delete_file("docs/important.md")
        assert "cannot delete" in result.lower()
        assert (docs / "important.md").exists()

    def test_delete_project_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("farmafacil.services.file_manager.PROJECT_ROOT", tmp_path)
        result = delete_file("project:CLAUDE.md")
        assert "cannot delete" in result.lower()

    def test_traversal_blocked(self):
        result = delete_file("../../etc/passwd", phone="555")
        assert "not allowed" in result.lower()


# ── Constants and invariants ───────────────────────────────────────────


class TestConstants:
    """Verify module constants are sensible."""

    def test_max_file_size_is_1mb(self):
        assert MAX_FILE_SIZE == 1024 * 1024

    def test_readable_project_files_includes_claude_md(self):
        assert "CLAUDE.md" in READABLE_PROJECT_FILES

    def test_writable_project_dirs_includes_docs(self):
        assert "docs" in WRITABLE_PROJECT_DIRS

    def test_max_files_per_user_is_positive(self):
        assert MAX_FILES_PER_USER > 0

    def test_max_list_entries_is_positive(self):
        assert MAX_LIST_ENTRIES > 0
