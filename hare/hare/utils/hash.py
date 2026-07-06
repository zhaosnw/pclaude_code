"""djb2 and content hashing — port of `src/utils/hash.ts`."""

from __future__ import annotations

import hashlib


def djb2_hash(s: str) -> int:
    """Fast non-cryptographic hash as a signed 32-bit int (djb2)."""
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    if h & 0x80000000:
        h = h - 0x100000000
    return h


def hash_content(content: str) -> str:
    """Stable SHA-256 hex digest for change detection (not crypto-hardened usage)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_pair(a: str, b: str) -> str:
    """Hash two strings with a NUL separator to avoid ambiguity."""
    h = hashlib.sha256()
    h.update(a.encode("utf-8"))
    h.update(b"\0")
    h.update(b.encode("utf-8"))
    return h.hexdigest()
