"""
FileReadTool — read files from the filesystem.

Port of: src/tools/FileReadTool/FileReadTool.ts
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

from hare.tools_impl.FileReadTool.prompt import MAX_LINES_TO_READ, FILE_UNCHANGED_STUB

TOOL_NAME = "Read"
FILE_READ_TOOL_NAME = TOOL_NAME
ALIASES: list[str] = []
SEARCH_HINT = "read file contents"

_read_file_state: dict[str, dict[str, Any]] = {}

# Device / special files that should not be read
_BLOCKED_PREFIXES = ("/dev/",)


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read (must be absolute, not relative)",
            },
            "offset": {
                "type": "number",
                "description": "The line number to start reading from. Only provide if the file is too large to read at once",
                "minimum": 0,
            },
            "limit": {
                "type": "number",
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
                "exclusiveMinimum": 0,
            },
            "pages": {
                "type": "string",
                "description": 'Page range for PDF files (e.g., "1-5", "3", "10-20"). Only applicable to PDF files. Maximum 20 pages per request.',
            },
        },
        "required": ["file_path"],
    }


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["text", "image", "notebook"],
                "description": "The type of content read",
            },
            "content": {
                "type": "string",
                "description": "The file content (text, base64 image data, or notebook JSON)",
            },
            "lineCount": {
                "type": "number",
                "description": "Number of lines in the output",
            },
            "totalLines": {
                "type": "number",
                "description": "Total lines in the file (when truncated)",
            },
            "truncated": {
                "type": "boolean",
                "description": "Whether the output was truncated",
            },
        },
        "required": ["type", "content"],
    }


def is_read_only(input: dict[str, Any]) -> bool:
    return True


def is_destructive(input: dict[str, Any]) -> bool:
    return False


def validate_input(input: dict[str, Any]) -> dict[str, Any]:
    """Validate read input before execution."""
    file_path = input.get("file_path", "")

    if not file_path:
        return {
            "result": False,
            "message": "file_path is required.",
            "errorCode": 1,
        }

    # Expand and make absolute
    full_path = os.path.expanduser(file_path)
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(full_path)

    # Block device files
    for prefix in _BLOCKED_PREFIXES:
        if full_path.startswith(prefix):
            return {
                "result": False,
                "message": f"Cannot read device file: {full_path}",
                "errorCode": 2,
            }

    return {"result": True}


def _format_lines(lines: list[str], start: int = 1) -> str:
    result: list[str] = []
    for i, line in enumerate(lines, start=start):
        result.append(f"{i:6d}|{line}")
    return "\n".join(result)


async def call(
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
    pages: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Read a file, returning its contents with line numbers.

    Supports text files, images (base64), Jupyter notebooks (.ipynb), and PDFs.
    """
    validation = validate_input({"file_path": file_path})
    if not validation.get("result"):
        return {"error": validation.get("message", "Validation failed")}

    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}
    if os.path.isdir(file_path):
        return {"error": f"Path is a directory: {file_path}. Use ls via Bash."}

    # Check if file unchanged
    try:
        mtime = os.path.getmtime(file_path)
        cached = _read_file_state.get(file_path)
        if cached and cached.get("timestamp") == mtime and not offset and not limit:
            return {"data": FILE_UNCHANGED_STUB, "type": "text"}
        _read_file_state[file_path] = {"timestamp": mtime}
    except OSError:
        pass

    ext = os.path.splitext(file_path)[1].lower()

    # Image files: read as base64
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
    if ext in image_exts:
        return await _read_image(file_path)

    # PDF files
    if ext == ".pdf":
        return await _read_pdf(file_path, offset, limit, pages)

    # Jupyter notebooks
    if ext == ".ipynb":
        return await _read_notebook(file_path)

    # Default: text file
    return await _read_text(file_path, offset or 1, limit or MAX_LINES_TO_READ)


async def _read_text(file_path: str, start: int, max_lines: int) -> dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        start = max(1, start)
        limit = max_lines
        selected = all_lines[start - 1 : start - 1 + limit]

        formatted = _format_lines(selected, start)
        result: dict[str, Any] = {
            "data": formatted,
            "type": "text",
            "lineCount": len(selected),
        }

        if len(all_lines) > start - 1 + limit:
            result["truncated"] = True
            result["totalLines"] = len(all_lines)

        total_size = sum(len(line) for line in all_lines)
        if total_size > 10 * 1024 * 1024:  # >10MB text file
            result["fileTooLarge"] = True

        # Mark as read for FileEditTool validation
        try:
            from hare.tools_impl.FileEditTool.file_edit_tool import mark_file_read

            content = "".join(all_lines).replace("\r\n", "\n")
            mark_file_read(file_path, offset=start, limit=limit, content=content)
        except ImportError:
            pass

        return result
    except Exception as e:
        return {"error": str(e)}


async def _read_image(file_path: str) -> dict[str, Any]:
    """Read an image file as base64-encoded data."""
    try:
        file_size = os.path.getsize(file_path)
        # Resize large images by reading as thumbnail
        if file_size > 5 * 1024 * 1024:  # >5MB
            try:
                from PIL import Image

                img = Image.open(file_path)
                img.thumbnail((2000, 2000))
                buf = io.BytesIO()
                img.save(buf, format=img.format or "PNG")
                data = base64.b64encode(buf.getvalue()).decode("ascii")
            except ImportError:
                with open(file_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
        else:
            with open(file_path, "rb") as f:
                raw = f.read()
            data = base64.b64encode(raw).decode("ascii")

        return {
            "data": f"[Image: {file_path}]\nbase64:{data[:100]}...",
            "type": "image",
        }
    except Exception as e:
        return {"error": f"Cannot read image: {e}"}


_PDF_MAX_PAGES_PER_READ = 20


def _parse_pages(pages: str, num_pages: int) -> tuple[int, int]:
    """Parse a 1-based page range like "3", "1-5", "10-20" into 0-based
    [start, end) page indices, clamped to the doc and the per-read cap."""
    spec = pages.strip()
    if "-" in spec:
        a, _, b = spec.partition("-")
        start = int(a) if a.strip() else 1
        end = int(b) if b.strip() else num_pages
    else:
        start = end = int(spec)
    start0 = max(0, start - 1)
    end0 = min(num_pages, end)
    end0 = min(end0, start0 + _PDF_MAX_PAGES_PER_READ)
    return start0, max(start0, end0)


async def _read_pdf(
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
    pages: str | None = None,
) -> dict[str, Any]:
    """Read a PDF file, extracting text content."""
    try:
        try:
            import PyPDF2

            reader = PyPDF2.PdfReader(file_path)
            num_pages = len(reader.pages)
        except ImportError:
            return {
                "data": f"[PDF file: {file_path}. Install PyPDF2 to read PDFs.]",
                "type": "text",
            }

        # `pages` (e.g. "1-5") takes precedence over offset/limit for PDFs.
        if pages and pages.strip():
            try:
                start_page, end_page = _parse_pages(pages, num_pages)
            except ValueError:
                return {"error": f"Invalid pages range: {pages!r}"}
        else:
            start_page = max(0, (offset or 1) - 1)
            end_page = min(num_pages, start_page + (limit or _PDF_MAX_PAGES_PER_READ))

        parts: list[str] = []
        for i in range(start_page, end_page):
            page = reader.pages[i]
            text = page.extract_text()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")

        if not parts:
            parts = ["[No extractable text in this PDF]"]

        result: dict[str, Any] = {
            "data": "\n\n".join(parts),
            "type": "text",
            "lineCount": sum(p.count("\n") + 1 for p in parts),
        }
        if end_page < num_pages:
            result["truncated"] = True
            result["totalPages"] = num_pages
        return result
    except Exception as e:
        return {"error": f"Cannot read PDF: {e}"}


async def _read_notebook(path: str) -> dict[str, Any]:
    """Read a Jupyter notebook, extracting cells with outputs."""
    import json

    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)
    cells = nb.get("cells", [])
    parts: list[str] = []
    for i, cell in enumerate(cells):
        ctype = cell.get("cell_type", "code")
        source = "".join(cell.get("source", []))
        out_text = ""
        # Include cell outputs for better context
        if ctype == "code" and cell.get("outputs"):
            for out in cell["outputs"]:
                if out.get("output_type") == "stream":
                    out_text += "".join(out.get("text", []))
                elif out.get("output_type") == "execute_result":
                    out_text += "".join(out.get("data", {}).get("text/plain", []))
                elif out.get("output_type") == "error":
                    out_text += (
                        f"Error: {out.get('ename', '')} - {out.get('evalue', '')}"
                    )
                out_text += "\n"
        if out_text.strip():
            parts.append(f"--- Cell {i} ({ctype}) ---\n{source}\n\nOutput:\n{out_text}")
        else:
            parts.append(f"--- Cell {i} ({ctype}) ---\n{source}")
    return {
        "data": "\n\n".join(parts),
        "type": "notebook",
        "lineCount": sum(p.count("\n") + 1 for p in parts),
    }
