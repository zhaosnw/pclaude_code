"""Create a session on a direct-connect server (port of src/server/createDirectConnectSession.ts)."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from hare.server.direct_connect_manager import DirectConnectConfig


class DirectConnectError(Exception):
    pass


async def create_direct_connect_session(
    *,
    server_url: str,
    auth_token: str | None,
    cwd: str,
    dangerously_skip_permissions: bool | None = None,
) -> tuple[dict[str, Any], str | None]:
    headers = {"content-type": "application/json"}
    if auth_token:
        headers["authorization"] = f"Bearer {auth_token}"
    body: dict[str, Any] = {"cwd": cwd}
    if dangerously_skip_permissions:
        body["dangerously_skip_permissions"] = True
    req = Request(
        f"{server_url.rstrip('/')}/sessions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError) as e:
        raise DirectConnectError(
            f"Failed to connect to server at {server_url}: {e}"
        ) from e
    session_id = data.get("session_id")
    ws_url = data.get("ws_url")
    if not isinstance(session_id, str) or not isinstance(ws_url, str):
        raise DirectConnectError("Invalid session response")
    cfg: DirectConnectConfig = {
        "server_url": server_url,
        "session_id": session_id,
        "ws_url": ws_url,
        "auth_token": auth_token,
    }
    work_dir = data.get("work_dir")
    return cfg, work_dir if isinstance(work_dir, str) else None
