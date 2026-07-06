"""
Session lifecycle API — create, get, archive, update bridge sessions.

Port of: src/bridge/createSession.ts

HTTP clients for POST /v1/sessions, GET /v1/sessions/{id},
POST /v1/sessions/{id}/archive, PATCH /v1/sessions/{id}.
Uses org-scoped OAuth headers with anthropic-beta: ccr-byoc-2025-07-29.
"""

from __future__ import annotations

from typing import Any, Optional


async def create_bridge_session(
    environment_id: str,
    title: Optional[str] = None,
    events: Optional[list[dict[str, Any]]] = None,
    git_repo_url: Optional[str] = None,
    branch: str = "",
    signal: Any = None,
    base_url: Optional[str] = None,
    get_access_token: Any = None,
    permission_mode: Optional[str] = None,
    get_oauth_config: Any = None,
    get_organization_uuid: Any = None,
    get_oauth_headers: Any = None,
    get_main_loop_model: Any = None,
    get_default_branch: Any = None,
    parse_git_remote: Any = None,
    http_post: Any = None,
) -> Optional[str]:
    """Create a session on a bridge environment via POST /v1/sessions.

    Returns session ID on success, None on failure.
    """
    access_token = None
    if get_access_token:
        access_token = get_access_token()
    if not access_token:
        return None

    org_uuid = None
    if get_organization_uuid:
        org_uuid = await get_organization_uuid()
    if not org_uuid:
        return None

    # Build git source/outcome
    sources: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []

    if git_repo_url and parse_git_remote:
        parsed = parse_git_remote(git_repo_url)
        if parsed:
            host = parsed.get("host", "")
            owner = parsed.get("owner", "")
            name = parsed.get("name", "")
            revision = branch
            if not revision and get_default_branch:
                revision = await get_default_branch() or ""
            sources.append(
                {
                    "type": "git_repository",
                    "url": f"https://{host}/{owner}/{name}",
                    "revision": revision,
                }
            )
            outcomes.append(
                {
                    "type": "git_repository",
                    "git_info": {
                        "type": "github",
                        "repo": f"{owner}/{name}",
                        "branches": [f"claude/{branch or 'task'}"],
                    },
                }
            )

    model = ""
    if get_main_loop_model:
        model = get_main_loop_model()

    request_body: dict[str, Any] = {
        "events": events or [],
        "session_context": {
            "sources": sources,
            "outcomes": outcomes,
            "model": model,
        },
        "environment_id": environment_id,
        "source": "remote-control",
    }
    if title is not None:
        request_body["title"] = title
    if permission_mode:
        request_body["permission_mode"] = permission_mode

    headers = {}
    if get_oauth_headers:
        headers = get_oauth_headers(access_token)
    headers["anthropic-beta"] = "ccr-byoc-2025-07-29"
    headers["x-organization-uuid"] = org_uuid

    api_base = base_url
    if not api_base and get_oauth_config:
        cfg = get_oauth_config()
        api_base = cfg.get("BASE_API_URL", "")

    url = f"{api_base}/v1/sessions"

    if not http_post:
        return None

    try:
        response = await http_post(url, json=request_body, headers=headers, timeout=10)
    except Exception:
        return None

    if response.get("status") not in (200, 201):
        return None

    data = response.get("data", {})
    if isinstance(data, dict):
        sid = data.get("id")
        if isinstance(sid, str):
            return sid
    return None


async def get_bridge_session(
    session_id: str,
    base_url: Optional[str] = None,
    get_access_token: Any = None,
    get_oauth_config: Any = None,
    get_organization_uuid: Any = None,
    get_oauth_headers: Any = None,
    http_get: Any = None,
) -> Optional[dict[str, Any]]:
    """Fetch a session via GET /v1/sessions/{id}.

    Returns { environment_id?, title? } on success, None on failure.
    """
    access_token = None
    if get_access_token:
        access_token = get_access_token()
    if not access_token:
        return None

    org_uuid = None
    if get_organization_uuid:
        org_uuid = await get_organization_uuid()
    if not org_uuid:
        return None

    headers = {}
    if get_oauth_headers:
        headers = get_oauth_headers(access_token)
    headers["anthropic-beta"] = "ccr-byoc-2025-07-29"
    headers["x-organization-uuid"] = org_uuid

    api_base = base_url
    if not api_base and get_oauth_config:
        cfg = get_oauth_config()
        api_base = cfg.get("BASE_API_URL", "")

    url = f"{api_base}/v1/sessions/{session_id}"

    if not http_get:
        return None

    try:
        response = await http_get(url, headers=headers, timeout=10)
    except Exception:
        return None

    if response.get("status") != 200:
        return None

    data = response.get("data", {})
    return data if isinstance(data, dict) else None


async def archive_bridge_session(
    session_id: str,
    base_url: Optional[str] = None,
    get_access_token: Any = None,
    timeout_ms: Optional[int] = None,
    get_oauth_config: Any = None,
    get_organization_uuid: Any = None,
    get_oauth_headers: Any = None,
    http_post: Any = None,
) -> None:
    """Archive a session via POST /v1/sessions/{id}/archive.

    Idempotent — 409 (already archived) is not an error.
    Best-effort: errors are swallowed.
    """
    try:
        access_token = None
        if get_access_token:
            access_token = get_access_token()
        if not access_token:
            return

        org_uuid = None
        if get_organization_uuid:
            org_uuid = await get_organization_uuid()
        if not org_uuid:
            return

        headers = {}
        if get_oauth_headers:
            headers = get_oauth_headers(access_token)
        headers["anthropic-beta"] = "ccr-byoc-2025-07-29"
        headers["x-organization-uuid"] = org_uuid

        api_base = base_url
        if not api_base and get_oauth_config:
            cfg = get_oauth_config()
            api_base = cfg.get("BASE_API_URL", "")

        url = f"{api_base}/v1/sessions/{session_id}/archive"

        if http_post:
            await http_post(
                url,
                json={},
                headers=headers,
                timeout=(timeout_ms or 10_000) / 1000,
            )
    except Exception:
        pass


async def update_bridge_session_title(
    session_id: str,
    title: str,
    base_url: Optional[str] = None,
    get_access_token: Any = None,
    get_oauth_config: Any = None,
    get_organization_uuid: Any = None,
    get_oauth_headers: Any = None,
    to_compat_session_id: Any = None,
    http_patch: Any = None,
) -> None:
    """Update session title via PATCH /v1/sessions/{id}.

    Best-effort: errors are swallowed.
    """
    try:
        access_token = None
        if get_access_token:
            access_token = get_access_token()
        if not access_token:
            return

        org_uuid = None
        if get_organization_uuid:
            org_uuid = await get_organization_uuid()
        if not org_uuid:
            return

        headers = {}
        if get_oauth_headers:
            headers = get_oauth_headers(access_token)
        headers["anthropic-beta"] = "ccr-byoc-2025-07-29"
        headers["x-organization-uuid"] = org_uuid

        api_base = base_url
        if not api_base and get_oauth_config:
            cfg = get_oauth_config()
            api_base = cfg.get("BASE_API_URL", "")

        compat_id = (
            to_compat_session_id(session_id) if to_compat_session_id else session_id
        )
        url = f"{api_base}/v1/sessions/{compat_id}"

        if http_patch:
            await http_patch(
                url,
                json={"title": title},
                headers=headers,
                timeout=10,
            )
    except Exception:
        pass
