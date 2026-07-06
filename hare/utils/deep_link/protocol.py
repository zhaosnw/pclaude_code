"""
Protocol handler registration.

Port of: src/utils/deepLink/registerProtocol.ts + protocolHandler.ts
"""

from __future__ import annotations
import sys


def register_protocol_handler() -> bool:
    """Register hare-code:// protocol handler. Platform-specific stub."""
    return False


def get_terminal_launcher() -> str:
    if sys.platform == "darwin":
        return "open"
    elif sys.platform == "win32":
        return "start"
    return "xdg-open"
