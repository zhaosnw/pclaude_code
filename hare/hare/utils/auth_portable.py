"""Portable auth helpers (`authPortable.ts`)."""

from __future__ import annotations

import platform
import subprocess


async def maybe_remove_api_key_from_macos_keychain_throws() -> None:
    if platform.system() != "Darwin":
        return
    try:
        from hare.utils.secure_storage.mac_os_keychain_helpers import (  # type: ignore[import-not-found]
            get_macos_keychain_storage_service_name,
        )

        name = get_macos_keychain_storage_service_name()
    except Exception:
        name = "hare-code"
    proc = subprocess.run(
        f'security delete-generic-password -a "$USER" -s "{name}"',
        shell=True,  # nosec B602
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("Failed to delete keychain entry")


def normalize_api_key_for_config(api_key: str) -> str:
    return api_key[-20:]
