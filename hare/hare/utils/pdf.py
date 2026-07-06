"""
PDF loading and page extraction via poppler utils. Port of src/utils/pdf.ts.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal, TypedDict

from hare.utils.errors import error_message
from hare.utils.exec_file_no_throw import exec_file_no_throw
from hare.utils.format import format_bytes
from hare.utils.fs_operations import get_fs_implementation

# apiLimits.ts
PDF_TARGET_RAW_SIZE = 20 * 1024 * 1024
PDF_MAX_EXTRACT_SIZE = 100 * 1024 * 1024


PDFErrorReason = Literal[
    "empty",
    "too_large",
    "password_protected",
    "corrupted",
    "unknown",
    "unavailable",
]


class PDFError(TypedDict):
    reason: PDFErrorReason
    message: str


class PDFReadSuccess(TypedDict):
    type: Literal["pdf"]
    file: dict[str, Any]


class PDFExtractSuccess(TypedDict):
    type: Literal["parts"]
    file: dict[str, Any]


async def read_pdf(file_path: str) -> dict[str, Any]:
    try:
        fs = get_fs_implementation()
        st = fs.stat_sync(file_path)
        size = st.st_size
        if size == 0:
            return {
                "success": False,
                "error": {
                    "reason": "empty",
                    "message": f"PDF file is empty: {file_path}",
                },
            }
        if size > PDF_TARGET_RAW_SIZE:
            return {
                "success": False,
                "error": {
                    "reason": "too_large",
                    "message": (
                        f"PDF file exceeds maximum allowed size of {format_bytes(PDF_TARGET_RAW_SIZE)}."
                    ),
                },
            }
        with open(file_path, "rb") as f:
            buf = f.read()
        header = buf[:5].decode("ascii", errors="replace")
        if not header.startswith("%PDF-"):
            return {
                "success": False,
                "error": {
                    "reason": "corrupted",
                    "message": f"File is not a valid PDF (missing %PDF- header): {file_path}",
                },
            }
        import base64

        b64 = base64.standard_b64encode(buf).decode("ascii")
        return {
            "success": True,
            "data": {
                "type": "pdf",
                "file": {"filePath": file_path, "base64": b64, "originalSize": size},
            },
        }
    except Exception as e:
        return {
            "success": False,
            "error": {"reason": "unknown", "message": error_message(e)},
        }


async def get_pdf_page_count(file_path: str) -> int | None:
    r = await exec_file_no_throw(
        "pdfinfo",
        [file_path],
        {"timeout": 10_000, "use_cwd": False},
    )
    if r.get("code") != 0:
        return None
    import re

    m = re.search(r"^Pages:\s+(\d+)", r.get("stdout") or "", re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


_pdftoppm_available: bool | None = None


def reset_pdftoppm_cache() -> None:
    global _pdftoppm_available
    _pdftoppm_available = None


async def is_pdftoppm_available() -> bool:
    global _pdftoppm_available
    if _pdftoppm_available is not None:
        return _pdftoppm_available
    r = await exec_file_no_throw(
        "pdftoppm", ["-v"], {"timeout": 5000, "use_cwd": False}
    )
    code = r.get("code", 1)
    stderr = r.get("stderr") or ""
    _pdftoppm_available = code == 0 or len(stderr) > 0
    return _pdftoppm_available


def _tool_results_dir() -> str:
    try:
        from hare.utils.tool_result_storage import get_tool_results_dir

        return get_tool_results_dir()
    except ImportError:
        return os.path.join(os.getcwd(), ".hare", "tool-results")


async def extract_pdf_pages(
    file_path: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = options or {}
    first_page = opts.get("firstPage")
    last_page = opts.get("lastPage")
    try:
        fs = get_fs_implementation()
        size = fs.stat_sync(file_path).st_size
        if size == 0:
            return {
                "success": False,
                "error": {
                    "reason": "empty",
                    "message": f"PDF file is empty: {file_path}",
                },
            }
        if size > PDF_MAX_EXTRACT_SIZE:
            return {
                "success": False,
                "error": {
                    "reason": "too_large",
                    "message": (
                        "PDF file exceeds maximum allowed size for text extraction "
                        f"({format_bytes(PDF_MAX_EXTRACT_SIZE)})."
                    ),
                },
            }
        if not await is_pdftoppm_available():
            return {
                "success": False,
                "error": {
                    "reason": "unavailable",
                    "message": (
                        "pdftoppm is not installed. Install poppler-utils "
                        "(e.g. `brew install poppler` or `apt-get install poppler-utils`) "
                        "to enable PDF page rendering."
                    ),
                },
            }
        uid = str(uuid.uuid4())
        output_dir = os.path.join(_tool_results_dir(), f"pdf-{uid}")
        os.makedirs(output_dir, exist_ok=True)
        prefix = os.path.join(output_dir, "page")
        args = ["-jpeg", "-r", "100"]
        if first_page:
            args.extend(["-f", str(first_page)])
        if last_page is not None and last_page != float("inf"):
            args.extend(["-l", str(last_page)])
        args.extend([file_path, prefix])
        r = await exec_file_no_throw(
            "pdftoppm", args, {"timeout": 120_000, "use_cwd": False}
        )
        stderr = r.get("stderr") or ""
        if r.get("code") != 0:
            low = stderr.lower()
            if "password" in low:
                return {
                    "success": False,
                    "error": {
                        "reason": "password_protected",
                        "message": "PDF is password-protected. Please provide an unprotected version.",
                    },
                }
            if any(x in low for x in ("damaged", "corrupt", "invalid")):
                return {
                    "success": False,
                    "error": {
                        "reason": "corrupted",
                        "message": "PDF file is corrupted or invalid.",
                    },
                }
            return {
                "success": False,
                "error": {"reason": "unknown", "message": f"pdftoppm failed: {stderr}"},
            }
        names = sorted(f for f in os.listdir(output_dir) if f.endswith(".jpg"))
        count = len(names)
        if count == 0:
            return {
                "success": False,
                "error": {
                    "reason": "corrupted",
                    "message": "pdftoppm produced no output pages. The PDF may be invalid.",
                },
            }
        return {
            "success": True,
            "data": {
                "type": "parts",
                "file": {
                    "filePath": file_path,
                    "originalSize": size,
                    "outputDir": output_dir,
                    "count": count,
                },
            },
        }
    except Exception as e:
        return {
            "success": False,
            "error": {"reason": "unknown", "message": error_message(e)},
        }
