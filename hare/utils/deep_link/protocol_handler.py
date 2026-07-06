"""OS deep-link protocol handler registration.

Port of: src/utils/deepLink/protocolHandler.ts
"""

from __future__ import annotations

from typing import Callable


def register_protocol_handler(
    scheme: str,
    on_url: Callable[[str], None],
) -> None:
    """Register *scheme* with the OS (stub)."""
    del scheme, on_url
