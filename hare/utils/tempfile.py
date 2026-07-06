"""Temporary file path generation (port of tempfile.ts)."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path


def generate_temp_file_path(
    prefix: str = "hare-prompt",
    extension: str = ".md",
    *,
    content_hash: str | None = None,
) -> str:
    if content_hash is not None:
        digest = hashlib.sha256(content_hash.encode("utf-8")).hexdigest()[:16]
        ident = digest
    else:
        ident = str(uuid.uuid4())
    return str(Path(tempfile.gettempdir()) / f"{prefix}-{ident}{extension}")
