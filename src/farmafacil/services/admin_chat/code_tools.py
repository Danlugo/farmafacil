"""Admin chat tools: code introspection (read_code, list_code)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Project root + allowed paths for code introspection ────────────────

# /src/farmafacil/services/admin_chat/code_tools.py -> project root is parents[4]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Directory prefixes relative to PROJECT_ROOT where read/list are allowed.
_ALLOWED_DIR_PREFIXES: tuple[str, ...] = (
    "src/farmafacil",
    "tests",
    "docs",
)

# Individual files at the project root that can always be read.
_ALLOWED_ROOT_FILES: frozenset[str] = frozenset({
    "CLAUDE.md",
    "IMPROVEMENT-PLAN.md",
    "README.md",
    "pyproject.toml",
    "MEMORY.md",
})

# Files we never read even if inside an allowed dir.
_FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".so",
)
_FORBIDDEN_NAMES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.prod", ".env.dev", "credentials.json",
    "farmafacil.db",
})

MAX_READ_BYTES = 64 * 1024  # 64 KiB hard cap on file reads


def _is_allowed_path(rel: str) -> tuple[bool, str]:
    """Validate a path relative to PROJECT_ROOT against the allowlist.

    Returns (allowed, reason_if_not).
    """
    if not rel:
        return (False, "ruta vacía")
    # Reject absolute / home-expanded paths outright — even if they would
    # resolve inside PROJECT_ROOT, they're never a legitimate admin request.
    if rel.startswith(("/", "~")):
        return (False, "ruta fuera del proyecto")
    # Resolve the path and rely SOLELY on the post-resolution
    # ``relative_to(PROJECT_ROOT)`` guard to catch ``..`` escapes. Do NOT
    # pre-check for ``..`` segments — a naive split-based check gives false
    # confidence and can mask future bypasses. ``resolve()`` + ``relative_to``
    # is the correct and sufficient sandbox boundary.
    try:
        resolved = (PROJECT_ROOT / rel).resolve()
    except (OSError, ValueError):
        return (False, "ruta inválida")
    try:
        resolved_rel = resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return (False, "ruta fuera del proyecto")
    rel_str = str(resolved_rel).replace("\\", "/")
    name = resolved.name
    # Reject hidden files and forbidden names/suffixes
    if name in _FORBIDDEN_NAMES or name.startswith("."):
        return (False, "archivo bloqueado")
    if name.endswith(_FORBIDDEN_SUFFIXES):
        return (False, "tipo de archivo bloqueado")
    # Must be either an allowed root file or inside an allowed dir prefix
    if rel_str in _ALLOWED_ROOT_FILES:
        return (True, "")
    for prefix in _ALLOWED_DIR_PREFIXES:
        if rel_str == prefix or rel_str.startswith(prefix + "/"):
            return (True, "")
    return (False, f"fuera del allowlist ({rel_str})")


async def _tool_read_code(args: dict[str, Any]) -> str:
    path = str(args.get("path", "")).strip()
    ok, reason = _is_allowed_path(path)
    if not ok:
        return f"Lectura denegada: {reason}"
    full = (PROJECT_ROOT / path).resolve()
    if not full.is_file():
        return f"{path} no existe."
    try:
        with open(full, "rb") as f:
            raw = f.read(MAX_READ_BYTES + 1)
    except OSError as exc:
        return f"Error leyendo {path}: {exc}"
    truncated = len(raw) > MAX_READ_BYTES
    text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    suffix = "\n...[truncado]" if truncated else ""
    return f"=== {path} ===\n{text}{suffix}"


async def _tool_list_code(args: dict[str, Any]) -> str:
    path = str(args.get("path", "src/farmafacil")).strip()
    ok, reason = _is_allowed_path(path)
    if not ok:
        return f"Listado denegado: {reason}"
    full = (PROJECT_ROOT / path).resolve()
    if not full.exists():
        return f"{path} no existe."
    if full.is_file():
        return f"{path} es un archivo (usa read_code)."
    entries = []
    try:
        for child in sorted(full.iterdir()):
            if child.name.startswith("."):
                continue
            rel = str(child.relative_to(PROJECT_ROOT)).replace("\\", "/")
            if child.is_dir():
                entries.append(f"  [dir] {rel}/")
            else:
                entries.append(f"  {rel}")
    except OSError as exc:
        return f"Error listando {path}: {exc}"
    if not entries:
        return f"{path}: vacío."
    total = len(entries)
    shown = entries[:100]
    suffix = (
        f"\n...[truncado a 100 de {total} entradas]" if total > 100 else ""
    )
    return f"{path}:\n" + "\n".join(shown) + suffix
