"""Compatibility alias for `hash.py` (port of `hash.ts`)."""

from __future__ import annotations

from hare.utils.hash import djb2_hash, hash_content, hash_pair

__all__ = ["djb2_hash", "hash_content", "hash_pair"]
