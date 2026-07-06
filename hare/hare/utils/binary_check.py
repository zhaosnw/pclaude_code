"""Check if external binaries exist (`binaryCheck.ts`)."""

from __future__ import annotations

from hare.utils.debug import log_for_debugging
from hare.utils.which import which

_binary_cache: dict[str, bool] = {}


async def is_binary_installed(command: str) -> bool:
    if not command or not command.strip():
        log_for_debugging("[binaryCheck] Empty command provided, returning false")
        return False
    trimmed = command.strip()
    cached = _binary_cache.get(trimmed)
    if cached is not None:
        log_for_debugging(f"[binaryCheck] Cache hit for '{trimmed}': {cached}")
        return cached
    exists = False
    try:
        if await which(trimmed):
            exists = True
    except Exception:
        exists = False
    _binary_cache[trimmed] = exists
    log_for_debugging(
        f"[binaryCheck] Binary '{trimmed}' {'found' if exists else 'not found'}"
    )
    return exists


def clear_binary_cache() -> None:
    _binary_cache.clear()
