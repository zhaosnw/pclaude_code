"""Port of: src/services/oauth/"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class OAuthToken:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    token_type: str = "bearer"


async def start_oauth_flow(
    client_id: str = "", redirect_port: int = 19485
) -> Optional[OAuthToken]:
    """Start OAuth login flow (stub - requires browser interaction)."""
    return None


async def refresh_token(token: OAuthToken) -> Optional[OAuthToken]:
    return None


def is_token_expired(token: OAuthToken) -> bool:
    import time

    return token.expires_at > 0 and time.time() > token.expires_at
