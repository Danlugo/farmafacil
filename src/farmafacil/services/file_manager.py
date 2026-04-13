"""File management service — per-user and project-level file operations.

Provides CRUD for files within safe boundaries:
- Per-user folders: ``data/users/{phone}/`` — uploads, documents, results
- Project docs: ``docs/`` — read/write for documentation

All paths are validated to prevent directory traversal attacks.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Base directories — in Docker this is /app/data, locally ./data
_APP_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # project root
DATA_DIR = Path(os.getenv("DATA_DIR", str(_APP_ROOT / "data")))
USERS_DIR = DATA_DIR / "users"

# Project directories the admin can read/write
PROJECT_ROOT = _APP_ROOT
WRITABLE_PROJECT_DIRS = {"docs"}
READABLE_PROJECT_FILES = {"CLAUDE.md", "IMPROVEMENT-PLAN.md", "README.md", "pyproject.toml"}

# Limits
MAX_FILE_SIZE = 1024 * 1024  # 1 MB
MAX_FILES_PER_USER = 100
MAX_LIST_ENTRIES = 50


def _user_dir(phone: str) -> Path:
    """Get or create the user's file directory."""
    safe_phone = "".join(c for c in phone if c.isalnum())
    user_dir = USERS_DIR / safe_phone
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _is_safe_path(base: Path, target: Path) -> bool:
    """Check that target is inside base (no traversal)."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _resolve_path(path_str: str, phone: str | None = None) -> tuple[Path, str] | None:
    """Resolve a path string to an absolute path and scope label.

    Supports two scopes:
    - ``user:{filename}`` or just ``{filename}`` — user's folder
    - ``docs/{filename}`` — project docs directory
    - ``project:{filename}`` — readable project root files

    Returns:
        Tuple of (resolved_path, scope_label) or None if invalid.
    """
    path_str = path_str.strip()

    # Reject obvious traversal
    if ".." in path_str:
        return None

    # Scope: project docs
    if path_str.startswith("docs/"):
        target = PROJECT_ROOT / path_str
        if not _is_safe_path(PROJECT_ROOT / "docs", target):
            return None
        return target, "docs"

    # Scope: project root files
    if path_str.startswith("project:"):
        filename = path_str[len("project:"):]
        if filename not in READABLE_PROJECT_FILES:
            return None
        return PROJECT_ROOT / filename, "project"

    # Scope: user folder
    if phone:
        # Strip optional "user:" prefix
        if path_str.startswith("user:"):
            path_str = path_str[len("user:"):]

        user_dir = _user_dir(phone)
        target = user_dir / path_str
        if not _is_safe_path(user_dir, target):
            return None
        return target, "user"

    return None


def list_files(phone: str | None = None, scope: str = "user") -> str:
    """List files in a user folder or project docs.

    Args:
        phone: User phone for user-scope listing.
        scope: 'user' or 'docs'.

    Returns:
        Formatted file listing.
    """
    if scope == "docs":
        docs_dir = PROJECT_ROOT / "docs"
        if not docs_dir.exists():
            return "No docs/ directory found."
        files = sorted(docs_dir.iterdir())
        if not files:
            return "docs/ is empty."
        lines = ["**docs/**"]
        for f in files[:MAX_LIST_ENTRIES]:
            size = f.stat().st_size if f.is_file() else 0
            label = f"  {f.name} ({size:,} bytes)" if f.is_file() else f"  {f.name}/"
            lines.append(label)
        return "\n".join(lines)

    if not phone:
        return "Error: phone is required for user scope."

    user_dir = _user_dir(phone)
    files = sorted(user_dir.rglob("*"))
    files = [f for f in files if f.is_file()]
    if not files:
        return f"No files for user {phone}."

    lines = [f"**Files for {phone}:**"]
    for f in files[:MAX_LIST_ENTRIES]:
        rel = f.relative_to(user_dir)
        size = f.stat().st_size
        lines.append(f"  {rel} ({size:,} bytes)")
    if len(files) > MAX_LIST_ENTRIES:
        lines.append(f"  ... and {len(files) - MAX_LIST_ENTRIES} more")
    return "\n".join(lines)


def read_file(path_str: str, phone: str | None = None) -> str:
    """Read a file's content.

    Args:
        path_str: File path (supports user:, docs/, project: scopes).
        phone: User phone for user-scope paths.

    Returns:
        File content or error message.
    """
    resolved = _resolve_path(path_str, phone)
    if resolved is None:
        return f"Error: path not allowed: {path_str}"

    target, scope = resolved
    if not target.exists():
        return f"File not found: {path_str}"
    if not target.is_file():
        return f"Not a file: {path_str}"
    if target.stat().st_size > MAX_FILE_SIZE:
        return f"File too large (max {MAX_FILE_SIZE // 1024}KB): {path_str}"

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Cannot read binary file as text: {path_str}"
    except OSError as exc:
        return f"Error reading {path_str}: {exc}"


def write_file(path_str: str, content: str, phone: str | None = None) -> str:
    """Create or overwrite a file.

    Args:
        path_str: File path (user: or docs/ scope).
        content: Text content to write.
        phone: User phone for user-scope paths.

    Returns:
        Success or error message.
    """
    resolved = _resolve_path(path_str, phone)
    if resolved is None:
        return f"Error: path not allowed: {path_str}"

    target, scope = resolved

    # Project root files are read-only
    if scope == "project":
        return f"Error: {path_str} is read-only."

    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        return f"Error: content too large (max {MAX_FILE_SIZE // 1024}KB)."

    # Check file count limit for user scope
    if scope == "user" and phone:
        user_dir = _user_dir(phone)
        existing = list(user_dir.rglob("*"))
        file_count = sum(1 for f in existing if f.is_file())
        if file_count >= MAX_FILES_PER_USER and not target.exists():
            return f"Error: user has {file_count} files (max {MAX_FILES_PER_USER})."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        size = target.stat().st_size
        logger.info("Wrote file %s (%d bytes, scope=%s)", target, size, scope)
        return f"File written: {path_str} ({size:,} bytes)"
    except OSError as exc:
        return f"Error writing {path_str}: {exc}"


def delete_file(path_str: str, phone: str | None = None) -> str:
    """Delete a file.

    Args:
        path_str: File path (user: scope only — cannot delete project files).
        phone: User phone.

    Returns:
        Success or error message.
    """
    resolved = _resolve_path(path_str, phone)
    if resolved is None:
        return f"Error: path not allowed: {path_str}"

    target, scope = resolved

    # Only user files can be deleted, not project files
    if scope != "user":
        return f"Error: cannot delete {scope} files — only user files can be deleted."

    if not target.exists():
        return f"File not found: {path_str}"

    try:
        target.unlink()
        logger.info("Deleted file %s", target)
        return f"File deleted: {path_str}"
    except OSError as exc:
        return f"Error deleting {path_str}: {exc}"
