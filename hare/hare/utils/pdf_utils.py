"""
PDF page-range parsing and capability checks. Port of src/utils/pdfUtils.ts.
"""

from __future__ import annotations

import math

DOCUMENT_EXTENSIONS = frozenset({"pdf"})


def parse_pdf_page_range(pages: str) -> dict[str, float | int] | None:
    trimmed = pages.strip()
    if not trimmed:
        return None
    try:
        if trimmed.endswith("-"):
            first = int(trimmed[:-1])
            if first < 1:
                return None
            return {"firstPage": first, "lastPage": math.inf}
        dash = trimmed.find("-")
        if dash == -1:
            page = int(trimmed)
            if page < 1:
                return None
            return {"firstPage": page, "lastPage": page}
        first = int(trimmed[:dash])
        last = int(trimmed[dash + 1 :])
    except ValueError:
        return None
    if first < 1 or last < 1 or last < first:
        return None
    return {"firstPage": first, "lastPage": last}


def is_pdf_supported() -> bool:
    from hare.utils.model import get_main_loop_model

    m = str(get_main_loop_model())
    return "hare-3-haiku" not in m.lower()


def is_pdf_extension(ext: str) -> bool:
    normalized = ext[1:] if ext.startswith(".") else ext
    return normalized.lower() in DOCUMENT_EXTENSIONS
