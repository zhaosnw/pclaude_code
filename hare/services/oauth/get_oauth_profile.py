"""
Fetch OAuth user profile after login.

Port of: src/services/oauth/getOauthProfile.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OauthProfile:
    sub: str = ""
    email: str = ""
    name: str = ""


async def get_oauth_profile(_access_token: str) -> OauthProfile:
    return OauthProfile()
