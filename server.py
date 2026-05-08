from __future__ import annotations

import ast
import os
import shutil
import time
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Filesystem Assistant", json_response=True)

PROJECT_ROOT = Path(os.getenv("MCP_ALLOWED_ROOT", Path(__file__).resolve().parent)).resolve()
DEFAULT_ENCODING = os.getenv("MCP_FILE_ENCODING", "utf-8")
MAX_READ_BYTES = int(os.getenv("MCP_MAX_READ_BYTES", "2000000"))

# ✅ Fix #7 — validate root at startup
if not PROJECT_ROOT.exists():
    raise RuntimeError(f"MCP_ALLOWED_ROOT does not exist: {PROJECT_ROOT}")
if not PROJECT_ROOT.is_dir():
    raise RuntimeError(f"MCP_ALLOWED_ROOT is not a directory: {PROJECT_ROOT}")


class ErrorInfo(TypedDict):
    code: str
    message: str


class ToolResponse(TypedDict):
    success: bool
    data: dict[str, Any] | None
    error: ErrorInfo | None


def _ok(data: dict[str, Any]) -> ToolResponse:
    return {"success": True, "data": data, "error": None}


def _err(code: str, message: str) -> ToolResponse:
    return {"success": False, "data": None, "error": {"code": code, "message": message}}


def _resolve_path(path: str) -> Path:
    raw = Path(path)
    resolved = raw.resolve() if raw.is_absolute() else (PROJECT_ROOT / raw).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise PermissionError(
            f"Path '{path}' is outside allowed root '{PROJECT_ROOT}'."
        ) from exc
    return resolved


def _handle_common_error(error: Exception) -> ToolResponse:
    if isinstance(error, PermissionError):
        return _err("permission_denied", str(error))
    if isinstance(error, FileNotFoundError):
        return _err("not_found", str(error))
    if isinstance(error, FileExistsError):
        return _err("already_exists", str(error))
    if isinstance(error, NotADirectoryError):
        return _err("not_a_directory", str(error))
    if isinstance(error, IsADirectoryError):
        return _err("is_directory", str(error))
    if isinstance(error, (UnicodeDecodeError, UnicodeEncodeError)):
        return _err("encoding_error", str(error))
    if isinstance(error, SyntaxError):
        return _err("syntax_error", f"Invalid Python: {error}")
    if isinstance(error, ValueError):
        return _err("invalid_input", str(error))
    if isinstance(error, OSError):
        return _err("os_error", str(error))
    return _err("unexpected_error", str(error))


@mcp.tool()
def read_file(path: str) -> ToolResponse:
    """Read a UTF-8 text file under the allowed project root."""
    try:
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if file_path.stat().st_size > MAX_READ_BYTES:
            raise ValueError(
                f"File exceeds MCP_MAX_READ_BYTES ({MAX_READ_BYTES} bytes): {file_path}"
            )
        content = file_path.read_text(encoding=DEFAULT_ENCODING)
        return _ok({"path": str(file_path), "encoding": DEFAULT_ENCODING, "content": content})
    except (PermissionError, FileNotFoundError, IsADirectoryError,
            UnicodeDecodeError, ValueError, OSError) as error:
        return _handle_common_error(error)


@mcp.tool()
def list_files(path: str = ".") -> ToolResponse:
    """List files and directories under the allowed project root."""
    try:
        directory = _resolve_path(path)
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        if not directory.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {directory}")

        entries: list[dict[str, Any]] = []
        for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            # ✅ Fix #1 — distinguish symlinks
            if item.is_symlink():
                kind = "symlink"
            elif item.is_dir():
                kind = "directory"
            else:
                kind = "file"
            entries.append({"name": item.name, "path": str(item), "kind": kind})
        return _ok({"path": str(directory), "entries": entries})
    except (PermissionError, FileNotFoundError, NotADirectoryError,
            ValueError, OSError) as error:
        return _handle_common_error(error)


@mcp.tool()
def analyze_python_file(path: str) -> ToolResponse:
    """Return high-level metrics for a Python file."""
    try:
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if file_path.stat().st_size > MAX_READ_BYTES:
            raise ValueError(f"File exceeds MAX_READ_BYTES: {file_path}")
        source = file_path.read_text(encoding=DEFAULT_ENCODING)
        tree = ast.parse(source)
        functions = 0
        classes = 0
        for node in ast.walk(tree):          # ✅ Fix #5 — single pass
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions += 1
            elif isinstance(node, ast.ClassDef):
                classes += 1
        return _ok({
            "path": str(file_path),
            "total_lines": len(source.splitlines()),
            "total_characters": len(source),
            "functions": functions,
            "classes": classes,
        })
    except (PermissionError, FileNotFoundError, UnicodeDecodeError,
            SyntaxError, ValueError, OSError) as error:
        return _handle_common_error(error)


@mcp.tool()
def search_word(path: str, word: str) -> ToolResponse:
    """Search for a word inside a file and return matching line numbers."""
    try:
        if not word.strip():
            raise ValueError("word cannot be empty")
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if file_path.stat().st_size > MAX_READ_BYTES:    # ✅ Fix #6
            raise ValueError(f"File exceeds MAX_READ_BYTES ({MAX_READ_BYTES}): {file_path}")
        matches = []
        with file_path.open(encoding=DEFAULT_ENCODING) as f:
            for index, line in enumerate(f, start=1):
                if word.lower() in line.lower():
                    matches.append({"line": index, "content": line.rstrip()})
        return _ok({
            "path": str(file_path),
            "word": word,
            "match_count": len(matches),
            "matches": matches,
        })
    except (PermissionError, FileNotFoundError, UnicodeDecodeError,
            ValueError, OSError) as error:
        return _handle_common_error(error)


@mcp.tool()
def create_file(path: str, overwrite: bool = False) -> ToolResponse:
    """Create a file under allowed root. By default, existing files are protected."""
    try:
        file_path = _resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            file_path.write_text("", encoding=DEFAULT_ENCODING)
        else:
            try:
                file_path.open("x").close()              # ✅ Fix #2 — atomic create
            except FileExistsError:
                raise FileExistsError(
                    f"File already exists: {file_path}. Set overwrite=true to replace."
                )
        return _ok({"path": str(file_path), "created": True})
    except (PermissionError, FileExistsError, ValueError, OSError) as error:
        return _handle_common_error(error)


@mcp.tool()
def write_file(
    path: str,
    content: str,
    mode: str = "overwrite",
    confirm_overwrite: bool = False,
    create_backup: bool = False,
) -> ToolResponse:
    """Write text to a file with overwrite protection, append mode, and optional backups."""
    try:
        file_path = _resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if mode not in {"overwrite", "append"}:
            raise ValueError("mode must be either 'overwrite' or 'append'")

        # ✅ Fix #4 — guard append too
        if file_path.exists() and not confirm_overwrite:
            raise ValueError(
                f"Refusing to {mode} existing file without confirm_overwrite=true."
            )

        backup_path = None
        if create_backup and file_path.exists():
            timestamp = int(time.time())             # ✅ Fix #3 — timestamped backup
            backup_path = file_path.with_suffix(f"{file_path.suffix}.{timestamp}.bak")
            shutil.copy2(file_path, backup_path)

        open_mode = "a" if mode == "append" else "w"
        with file_path.open(open_mode, encoding=DEFAULT_ENCODING) as handle:
            handle.write(content)

        return _ok({
            "path": str(file_path),
            "mode": mode,
            "bytes_written": len(content.encode(DEFAULT_ENCODING)),
            "backup_path": str(backup_path) if backup_path else None,
        })
    except (PermissionError, ValueError, UnicodeEncodeError, OSError) as error:
        return _handle_common_error(error)


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))