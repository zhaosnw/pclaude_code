"""
Inbound attachments — download files from API and prepend @-refs to content.

Port of: src/bridge/inboundAttachments.ts

Downloads files from GET /api/oauth/files/{uuid}/content with OAuth headers,
writes to ~/.claude/uploads/{sessionId}/, generates @path refs, and prepends
to content blocks.
"""

from __future__ import annotations

import os
from typing import Any


async def resolve_inbound_attachments(
    content: str | list[dict[str, Any]],
    session_id: str,
    get_access_token: Any = None,
    base_url: str = "",
    http_get: Any = None,
) -> str | list[dict[str, Any]]:
    """Download and resolve inbound attachments, prepending @path refs to content.

    Handles both string content and ContentBlockParam[].
    """
    if not http_get or not get_access_token:
        return content

    access_token = get_access_token()
    if not access_token:
        return content

    # Scan for attachment references in text content
    if isinstance(content, str):
        refs = _extract_file_refs(content)
    else:
        refs = _extract_file_refs_from_blocks(content)

    if not refs:
        return content

    uploads_dir = os.path.join(
        os.path.expanduser("~"), ".claude", "uploads", session_id
    )
    os.makedirs(uploads_dir, exist_ok=True)

    downloaded: list[str] = []
    for file_uuid in refs:
        try:
            url = f"{base_url}/api/oauth/files/{file_uuid}/content"
            response = await http_get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            if response.get("status") == 200:
                data = response.get("data")
                body = (
                    data
                    if isinstance(data, str)
                    else data.get("body", "")
                    if isinstance(data, dict)
                    else ""
                )
                file_path = os.path.join(uploads_dir, file_uuid)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(body)
                downloaded.append(file_path)
        except Exception:
            continue

    if not downloaded:
        return content

    # Prepend @path refs to content
    ref_lines = "\n".join(f"@{p}" for p in downloaded) + "\n\n"
    if isinstance(content, str):
        return ref_lines + content
    else:
        return [{"type": "text", "text": ref_lines}] + content


def _extract_file_refs(text: str) -> list[str]:
    """Extract UUID file references from text content."""
    import re

    pattern = re.compile(r"@file:([a-f0-9-]{36})", re.IGNORECASE)
    return pattern.findall(text)


def _extract_file_refs_from_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    """Extract UUID file references from ContentBlockParam[]."""
    refs: list[str] = []
    for block in blocks:
        if block.get("type") == "text":
            refs.extend(_extract_file_refs(block.get("text", "")))
    return refs
