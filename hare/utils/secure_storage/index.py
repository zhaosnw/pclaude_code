"""Barrel matching secureStorage/index.ts — delegates to storage.py."""

from __future__ import annotations

from hare.utils.secure_storage.storage import (
    SecureStorage,
    delete_item,
    get_item,
    get_secure_storage,
    set_item,
)

__all__ = [
    "SecureStorage",
    "delete_item",
    "get_item",
    "get_secure_storage",
    "set_item",
]
