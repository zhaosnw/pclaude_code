"""
PKCE and OAuth crypto helpers.

Port of: src/services/oauth/crypto.ts
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def random_url_safe_string(num_bytes: int = 32) -> str:
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(num_bytes))
        .rstrip(b"=")
        .decode("ascii")
    )


def sha256_base64url(data: str) -> str:
    digest = hashlib.sha256(data.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
