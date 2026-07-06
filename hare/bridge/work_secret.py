"""
Work secret — decode base64url-encoded JSON secret from bridge poll response.

Port of: src/bridge/workSecret.ts
"""

from __future__ import annotations

import base64
import json
from typing import Any

from hare.bridge.types import WorkSecret, WorkSecretAuth, WorkSecretSource, GitInfo


def decode_work_secret(encoded: str) -> WorkSecret:
    """Decode a base64url-encoded work secret and validate its fields.

    Raises ValueError if version is not 1 or required fields are missing.
    """
    # Add padding, decode base64url
    padded = encoded + "=" * (-len(encoded) % 4)
    raw_bytes = base64.urlsafe_b64decode(padded)
    data = json.loads(raw_bytes)

    if not isinstance(data, dict):
        raise ValueError("Invalid work secret: expected JSON object")

    version = data.get("version")
    if version != 1:
        raise ValueError(f"Unsupported work secret version: {version}")

    session_ingress_token = data.get("session_ingress_token", "")
    if not session_ingress_token or not isinstance(session_ingress_token, str):
        raise ValueError("Invalid work secret: missing or empty session_ingress_token")

    api_base_url = data.get("api_base_url", "")
    if not api_base_url or not isinstance(api_base_url, str):
        raise ValueError("Invalid work secret: missing api_base_url")

    # Parse sources
    sources: list[WorkSecretSource] = []
    for s in data.get("sources", []) or []:
        if isinstance(s, dict):
            src = WorkSecretSource(type=s.get("type", ""))
            gi = s.get("git_info")
            if isinstance(gi, dict):
                src.git_info = GitInfo(
                    type=gi.get("type", ""),
                    repo=gi.get("repo", ""),
                    ref=gi.get("ref", ""),
                    token=gi.get("token", ""),
                )
            sources.append(src)

    # Parse auth
    auth: list[WorkSecretAuth] = []
    for a in data.get("auth", []) or []:
        if isinstance(a, dict):
            auth.append(
                WorkSecretAuth(
                    type=a.get("type", ""),
                    token=a.get("token", ""),
                )
            )

    return WorkSecret(
        version=1,
        session_ingress_token=session_ingress_token,
        api_base_url=api_base_url,
        sources=sources,
        auth=auth,
        claude_code_args=data.get("claude_code_args"),
        mcp_config=data.get("mcp_config"),
        environment_variables=data.get("environment_variables"),
        use_code_sessions=data.get("use_code_sessions"),
    )


def build_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a WebSocket SDK URL from the API base URL and session ID.

    Uses /v2/ for localhost (direct to session-ingress), /v1/ for production.
    """
    is_localhost = "localhost" in api_base_url or "127.0.0.1" in api_base_url
    protocol = "ws" if is_localhost else "wss"
    version = "v2" if is_localhost else "v1"
    host = api_base_url.replace("https://", "").replace("http://", "").rstrip("/")
    return f"{protocol}://{host}/{version}/session_ingress/ws/{session_id}"


def build_ccr_v2_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a CCR v2 session URL (HTTP, not ws://).

    Points at /v1/code/sessions/{id} — child CC derives SSE stream path from this.
    """
    base = api_base_url.rstrip("/")
    return f"{base}/v1/code/sessions/{session_id}"


def same_session_id(a: str, b: str) -> bool:
    """Compare two session IDs regardless of tagged-ID prefix.

    Both cse_* and session_* have the same underlying UUID body.
    """
    if a == b:
        return True
    a_body = a[a.rfind("_") + 1 :]
    b_body = b[b.rfind("_") + 1 :]
    return len(a_body) >= 4 and a_body == b_body


async def register_worker(
    session_url: str,
    access_token: str,
    http_post: Any = None,
) -> int:
    """Register this bridge as the worker for a CCR v2 session.

    Returns the worker_epoch (int64).
    """
    if not http_post:
        raise RuntimeError("HTTP client required for register_worker")

    response = await http_post(
        f"{session_url}/worker/register",
        json={},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        timeout=10,
    )

    raw = response.get("data", {}).get("worker_epoch")
    if isinstance(raw, str):
        epoch = int(raw)
    elif isinstance(raw, (int, float)):
        epoch = int(raw)
    else:
        raise ValueError("registerWorker: invalid worker_epoch in response")

    return epoch
