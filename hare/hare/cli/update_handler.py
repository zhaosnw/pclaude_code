"""Port of: src/cli/update.ts

CLI update checker — checks for new versions.
"""

from __future__ import annotations

from typing import Any

VERSION_CHECK_URL = "https://pypi.org/pypi/hare/json"


async def check_for_updates(current_version: str = "") -> dict[str, Any]:
    """Check PyPI for newer versions.

    Returns {"update_available": True, "latest_version": "x.y.z", "current_version": "x.y.z"}.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(VERSION_CHECK_URL)
            if resp.status_code != 200:
                return {"update_available": False}
            data = resp.json()
            latest = data.get("info", {}).get("version", "")
            current = current_version or "0.0.0"
            if not latest or not current:
                return {"update_available": False}
            available = _is_newer(latest, current)
            return {
                "update_available": available,
                "latest_version": latest,
                "current_version": current,
            }
    except Exception:
        return {"update_available": False}


def _is_newer(latest: str, current: str) -> bool:
    try:
        from packaging.version import parse as parse_version

        return parse_version(latest) > parse_version(current)
    except ImportError:

        def _parts(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))

        try:
            return _parts(latest) > _parts(current)
        except Exception:
            return latest != current


async def perform_update() -> bool:
    """Attempt to update the package via pip. Returns True if update was initiated."""
    import subprocess

    try:
        result = subprocess.run(
            [subprocess.sys.executable, "-m", "pip", "install", "--upgrade", "hare"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False
