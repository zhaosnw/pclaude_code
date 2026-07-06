"""
Parse CLI marketplace input into a MarketplaceSource dict.

Port of: src/utils/plugins/parseMarketplaceInput.ts
"""

from __future__ import annotations

import asyncio
import os
import re
import stat as stat_mod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hare.utils.errors import get_errno_code


async def parse_marketplace_input(
    input_str: str,
) -> dict[str, Any] | dict[str, str] | None:
    trimmed = input_str.strip()

    ssh_match = re.match(
        r"^([a-zA-Z0-9._-]+@[^:]+:.+?(?:\.git)?)(#(.+))?$",
        trimmed,
    )
    if ssh_match and ssh_match.group(1):
        url = ssh_match.group(1)
        ref = ssh_match.group(3)
        out: dict[str, Any] = {"source": "git", "url": url}
        if ref:
            out["ref"] = ref
        return out

    if trimmed.startswith("http://") or trimmed.startswith("https://"):
        fragment_match = re.match(r"^([^#]+)(#(.+))?$", trimmed)
        url_without_fragment = fragment_match.group(1) if fragment_match else trimmed
        ref = fragment_match.group(3) if fragment_match else None

        if url_without_fragment.endswith(".git") or "/_git/" in url_without_fragment:
            out = {"source": "git", "url": url_without_fragment}
            if ref:
                out["ref"] = ref
            return out

        try:
            url = urlparse(url_without_fragment)
        except Exception:
            return {"source": "url", "url": url_without_fragment}

        if url.hostname in ("github.com", "www.github.com"):
            match = re.match(r"^/([^/]+/[^/]+?)(/|\.git|$)", url.path or "")
            if match:
                git_url = (
                    url_without_fragment
                    if url_without_fragment.endswith(".git")
                    else f"{url_without_fragment}.git"
                )
                out = {"source": "git", "url": git_url}
                if ref:
                    out["ref"] = ref
                return out
        return {"source": "url", "url": url_without_fragment}

    is_windows = os.name == "nt"
    is_windows_path = is_windows and (
        trimmed.startswith(".\\")
        or trimmed.startswith("..\\")
        or bool(re.match(r"^[a-zA-Z]:[/\\]", trimmed))
    )
    if (
        trimmed.startswith("./")
        or trimmed.startswith("../")
        or trimmed.startswith("/")
        or trimmed.startswith("~")
        or is_windows_path
    ):
        raw = str(Path.home()) + trimmed[1:] if trimmed.startswith("~") else trimmed
        resolved_path = str(Path(raw).resolve())

        try:
            st = await asyncio.to_thread(os.stat, resolved_path)
        except OSError as e:
            code = get_errno_code(e)
            return {
                "error": (
                    f"Path does not exist: {resolved_path}"
                    if code == "ENOENT"
                    else f"Cannot access path: {resolved_path} ({code or e})"
                )
            }

        if stat_mod.S_ISREG(st.st_mode):
            if resolved_path.endswith(".json"):
                return {"source": "file", "path": resolved_path}
            return {
                "error": (
                    f"File path must point to a .json file (marketplace.json), "
                    f"but got: {resolved_path}"
                )
            }
        if stat_mod.S_ISDIR(st.st_mode):
            return {"source": "directory", "path": resolved_path}
        return {"error": f"Path is neither a file nor a directory: {resolved_path}"}

    if "/" in trimmed and not trimmed.startswith("@"):
        if ":" in trimmed:
            return None
        fragment_match = re.match(r"^([^#@]+)(?:[#@](.+))?$", trimmed)
        repo = fragment_match.group(1) if fragment_match else trimmed
        ref = fragment_match.group(2) if fragment_match else None
        out = {"source": "github", "repo": repo}
        if ref:
            out["ref"] = ref
        return out

    return None
