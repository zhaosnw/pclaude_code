"""Open paths and http(s) URLs with the OS default handler. Port of: src/utils/browser.ts"""

from __future__ import annotations

import os
import platform
import shutil
from urllib.parse import urlparse

from hare.utils.exec_file_no_throw import exec_file_no_throw


def _validate_url(url: str) -> None:
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"Invalid URL format: {url}") from e
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL protocol: must use http:// or https://, got {parsed.scheme!r}"
        )


async def open_path(path: str) -> bool:
    """Open a file or folder with the system default application."""
    try:
        system = platform.system().lower()
        if system == "windows":
            r = await exec_file_no_throw("explorer", [path])
            return r.get("code") == 0
        cmd = "open" if system == "darwin" else shutil.which("xdg-open") or "xdg-open"
        r = await exec_file_no_throw(cmd, [path])
        return r.get("code") == 0
    except Exception:
        return False


async def open_browser(url: str) -> bool:
    """Open a validated http(s) URL in the default browser."""
    try:
        _validate_url(url)
    except ValueError:
        return False
    try:
        system = platform.system().lower()
        if system == "windows":
            browser = os.environ.get("BROWSER")
            if browser:
                r = await exec_file_no_throw(browser, [url])
                return r.get("code") == 0
            r = await exec_file_no_throw("rundll32", ["url,OpenURL", url])
            return r.get("code") == 0
        browser = os.environ.get("BROWSER")
        cmd = browser or (
            "open" if system == "darwin" else shutil.which("xdg-open") or "xdg-open"
        )
        r = await exec_file_no_throw(cmd, [url])
        return r.get("code") == 0
    except Exception:
        return False
