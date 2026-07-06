"""Random UUID indirection (mirrors recovered `crypto.ts` export of `randomUUID`)."""

from __future__ import annotations

from hare.utils.crypto import random_uuid

__all__ = ["random_uuid"]
