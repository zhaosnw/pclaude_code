"""Prefetch macOS keychain entries for faster reads.

Port of: src/utils/secureStorage/keychainPrefetch.ts
"""

from __future__ import annotations

from typing import Sequence


async def prefetch_keychain_keys(_keys: Sequence[str]) -> None:
    """Warm the keychain cache (no-op off macOS)."""
    return None
