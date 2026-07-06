"""
Local HTTP server for OAuth authorization code callback.

Port of: src/services/oauth/auth-code-listener.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuthCodeResult:
    code: Optional[str] = None
    error: Optional[str] = None


async def listen_for_authorization_code(
    port: int,
    path: str = "/callback",
    timeout_s: float = 300.0,
) -> AuthCodeResult:
    """Stub: real implementation binds asyncio Server or aiohttp."""
    del port, path, timeout_s
    return AuthCodeResult()
