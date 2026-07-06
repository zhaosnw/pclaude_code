"""Console file upload API. Port of: src/services/api/filesApi.ts"""

from __future__ import annotations

from typing import Any


async def upload_file_bytes(_data: bytes, _filename: str) -> dict[str, Any]:
    return {"id": "", "url": ""}
