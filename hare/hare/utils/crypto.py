"""Crypto indirection — re-export UUID for callers that avoid direct imports."""

from __future__ import annotations

from uuid import uuid4


def random_uuid() -> str:
    return str(uuid4())
