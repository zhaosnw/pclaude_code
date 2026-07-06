"""
HTTP wrappers for the CCR v2 code-session API.

Port of: src/bridge/codeSessionApi.ts

Thin HTTP client for createCodeSession + fetchRemoteCredentials.
No implicit auth or config reads — callers supply explicit params.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class RemoteCredentials:
    worker_jwt: str = ""
    api_base_url: str = ""
    expires_in: int = 0
    worker_epoch: int = 0  # int64, bumped server-side on each /bridge call


def _oauth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }


async def create_code_session(
    base_url: str,
    access_token: str,
    title: str,
    timeout_ms: int,
    tags: Optional[list[str]] = None,
    http_post: Any = None,
) -> Optional[str]:
    """Create a code session via POST /v1/code/sessions.

    Returns the session ID (cse_* format) on success, or None on failure.
    """
    url = f"{base_url}/v1/code/sessions"
    body: dict[str, Any] = {"title": title, "bridge": {}}
    if tags:
        body["tags"] = tags

    if not http_post:
        return None  # Requires HTTP client injection

    try:
        response = await http_post(
            url,
            json=body,
            headers=_oauth_headers(access_token),
            timeout=timeout_ms / 1000,
        )
    except Exception:
        return None

    if response.get("status") not in (200, 201):
        return None

    data = response.get("data", {})
    session = data.get("session") if isinstance(data, dict) else None
    session_id = session.get("id") if isinstance(session, dict) else None

    if (
        not session_id
        or not isinstance(session_id, str)
        or not session_id.startswith("cse_")
    ):
        return None

    return session_id


async def fetch_remote_credentials(
    session_id: str,
    base_url: str,
    access_token: str,
    timeout_ms: int,
    trusted_device_token: Optional[str] = None,
    http_post: Any = None,
) -> Optional[RemoteCredentials]:
    """Fetch bridge credentials via POST /v1/code/sessions/{id}/bridge.

    Returns RemoteCredentials (worker_jwt, api_base_url, expires_in, worker_epoch)
    or None on failure.
    """
    url = f"{base_url}/v1/code/sessions/{session_id}/bridge"
    headers = _oauth_headers(access_token)
    if trusted_device_token:
        headers["X-Trusted-Device-Token"] = trusted_device_token

    if not http_post:
        return None

    try:
        response = await http_post(
            url,
            json={},
            headers=headers,
            timeout=timeout_ms / 1000,
        )
    except Exception:
        return None

    if response.get("status") != 200:
        return None

    data = response.get("data", {})
    if not isinstance(data, dict):
        return None

    worker_jwt = data.get("worker_jwt")
    api_base = data.get("api_base_url")
    expires_in = data.get("expires_in")
    raw_epoch = data.get("worker_epoch")

    if (
        not isinstance(worker_jwt, str)
        or not isinstance(api_base, str)
        or not isinstance(expires_in, (int, float))
    ):
        return None

    # protojson serializes int64 as string
    if isinstance(raw_epoch, str):
        try:
            epoch = int(raw_epoch)
        except (ValueError, TypeError):
            return None
    elif isinstance(raw_epoch, (int, float)):
        epoch = int(raw_epoch)
    else:
        return None

    return RemoteCredentials(
        worker_jwt=worker_jwt,
        api_base_url=api_base,
        expires_in=int(expires_in),
        worker_epoch=epoch,
    )
