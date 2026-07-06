"""
Bridge API client — HTTP communication with CCR backend.

Port of: src/bridge/bridgeApi.ts

Full REST client for the environments API with:
  - OAuth token auth with 401 retry
  - Safe ID validation
  - Error classification by status code
  - All 9 API methods
  - Injected HTTP backend (async functions returning {status, data} dicts)
  - Debug logging with request/response body redaction
  - Timeout and signal propagation
  - 409 idempotent handling for archive_session
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from hare.bridge.debug_utils import debug_body, extract_error_detail
from hare.bridge.types import (
    BRIDGE_LOGIN_INSTRUCTION,
    BridgeConfig,
    PermissionResponseEvent,
    WorkResponse,
)

BETA_HEADER = "environments-2025-11-01"
SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
EMPTY_POLL_LOG_INTERVAL = 100
DEFAULT_TIMEOUT_S = 10.0

# ---------------------------------------------------------------------------
# HTTP function type aliases
# ---------------------------------------------------------------------------
# Each injected HTTP function is async, accepts keyword arguments, and
# returns a dict: {"status": int, "data": Any}.
# Callers that don't provide an HTTP backend get NotImplementedError at
# call time (graceful degradation rather than silent no-op).
HttpFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class BridgeFatalError(Exception):
    """Fatal bridge errors that should not be retried (e.g. auth failures)."""

    def __init__(
        self,
        message: str,
        status: int,
        error_type: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


# ---------------------------------------------------------------------------
# ID validation
# ---------------------------------------------------------------------------


def validate_bridge_id(id_val: str, label: str) -> str:
    """Validate that a server-provided ID is safe to interpolate into a URL path.

    Prevents path traversal (e.g. ``../../admin``) and injection via IDs that
    contain slashes, dots, or other special characters.
    """
    if not id_val or not SAFE_ID_PATTERN.match(id_val):
        raise ValueError(f"Invalid {label}: contains unsafe characters")
    return id_val


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@dataclass
class BridgeApiDeps:
    """Dependencies injected into BridgeApiClient.

    All fields are optional; missing fields degrade gracefully (the client
    raises NotImplementedError for HTTP calls without a backend).
    """

    base_url: str = ""
    get_access_token: Any = None
    runner_version: str = "0.0.0"
    on_debug: Any = None
    on_auth_401: Any = None
    get_trusted_device_token: Any = None

    # Injected HTTP backends — async functions with signature:
    #   http_get(url, *, headers, timeout, params?) -> {"status": int, "data": Any}
    #   http_post(url, *, json, headers, timeout)    -> {"status": int, "data": Any}
    #   http_delete(url, *, headers, timeout)         -> {"status": int, "data": Any}
    http_get: Optional[HttpFn] = None
    http_post: Optional[HttpFn] = None
    http_delete: Optional[HttpFn] = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BridgeApiClient:
    """Full implementation of the BridgeApiClient interface.

    Makes real HTTP calls through injected async backends. Every method
    validates IDs, applies auth appropriately (OAuth for lifecycle methods,
    session/secret tokens for per-session methods), handles 401 retry for
    OAuth-authenticated methods, and classifies errors via status code.
    """

    def __init__(self, deps: BridgeApiDeps) -> None:
        self._deps = deps
        self._consecutive_empty_polls = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _debug(self, msg: str) -> None:
        if self._deps.on_debug:
            self._deps.on_debug(msg)

    def _get_headers(self, access_token: str) -> dict[str, str]:
        """Build request headers for OAuth or session-token auth.

        Always includes the ``anthropic-beta`` header so the server
        gates on the environments API beta.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": BETA_HEADER,
            "x-environment-runner-version": self._deps.runner_version,
        }
        if self._deps.get_trusted_device_token:
            token = self._deps.get_trusted_device_token()
            if token:
                headers["X-Trusted-Device-Token"] = token
        return headers

    def _resolve_auth(self) -> str:
        """Resolve the OAuth access token, raising on missing credentials."""
        access_token = (
            self._deps.get_access_token() if self._deps.get_access_token else ""
        )
        if not access_token:
            raise BridgeFatalError(BRIDGE_LOGIN_INSTRUCTION, 401)
        return access_token

    async def _http_get(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float = DEFAULT_TIMEOUT_S,
        params: Optional[dict[str, Any]] = None,
        signal: Any = None,
    ) -> tuple[int, Any]:
        """Execute an HTTP GET through the injected backend."""
        if not self._deps.http_get:
            raise NotImplementedError(
                "BridgeApiClient requires an http_get backend (inject via BridgeApiDeps)"
            )
        kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
        if params is not None:
            kwargs["params"] = params
        if signal is not None:
            kwargs["signal"] = signal
        resp = await self._deps.http_get(url, **kwargs)
        status = resp.get("status", 0) if isinstance(resp, dict) else 0
        data = resp.get("data") if isinstance(resp, dict) else None
        return status, data

    async def _http_post(
        self,
        url: str,
        json_data: Any,
        headers: dict[str, str],
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> tuple[int, Any]:
        """Execute an HTTP POST through the injected backend."""
        if not self._deps.http_post:
            raise NotImplementedError(
                "BridgeApiClient requires an http_post backend (inject via BridgeApiDeps)"
            )
        resp = await self._deps.http_post(
            url,
            json=json_data,
            headers=headers,
            timeout=timeout,
        )
        status = resp.get("status", 0) if isinstance(resp, dict) else 0
        data = resp.get("data") if isinstance(resp, dict) else None
        return status, data

    async def _http_delete(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> tuple[int, Any]:
        """Execute an HTTP DELETE through the injected backend."""
        if not self._deps.http_delete:
            raise NotImplementedError(
                "BridgeApiClient requires an http_delete backend (inject via BridgeApiDeps)"
            )
        resp = await self._deps.http_delete(
            url,
            headers=headers,
            timeout=timeout,
        )
        status = resp.get("status", 0) if isinstance(resp, dict) else 0
        data = resp.get("data") if isinstance(resp, dict) else None
        return status, data

    async def _with_oauth_retry(
        self,
        fn: Any,
        context: str,
    ) -> tuple[int, Any]:
        """Execute an OAuth-authenticated request with a single 401 retry.

        On 401, attempts token refresh via ``on_auth_401`` (same pattern as
        ``withRetry.ts`` for v1/messages). If refresh succeeds, retries once
        with the new token. If refresh fails or the retry also returns 401,
        the 401 response is returned for ``_handle_error_status`` to throw
        ``BridgeFatalError``.
        """
        access_token = self._resolve_auth()
        status, data = await fn(access_token)

        if status != 401:
            return status, data

        if not self._deps.on_auth_401:
            self._debug(f"[bridge:api] {context}: 401 received, no refresh handler")
            return status, data

        self._debug(f"[bridge:api] {context}: 401 received, attempting token refresh")
        refreshed = await self._deps.on_auth_401(access_token)
        if refreshed:
            self._debug(f"[bridge:api] {context}: Token refreshed, retrying")
            new_token = self._resolve_auth()
            retry_status, retry_data = await fn(new_token)
            if retry_status != 401:
                return retry_status, retry_data
            self._debug(f"[bridge:api] {context}: Retry after refresh also got 401")
        else:
            self._debug(f"[bridge:api] {context}: Token refresh failed")

        # Return the original 401 so handleErrorStatus throws BridgeFatalError
        return status, data

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def register_bridge_environment(
        self, config: BridgeConfig
    ) -> dict[str, str]:
        """Register or re-register a bridge environment.

        POST /v1/environments/bridge

        Returns ``{"environment_id": str, "environment_secret": str}``.
        """
        self._debug(
            f"[bridge:api] POST /v1/environments/bridge bridgeId={config.bridge_id}"
        )

        async def _do(token: str) -> tuple[int, Any]:
            body: dict[str, Any] = {
                "machine_name": config.machine_name,
                "directory": config.dir,
                "branch": config.branch,
                "git_repo_url": config.git_repo_url,
                "max_sessions": config.max_sessions,
                "metadata": {"worker_type": config.worker_type},
            }
            # Idempotent re-registration: if we have a backend-issued
            # environment_id from a prior session, send it back so the
            # backend reattaches instead of creating a new env.
            if config.reuse_environment_id:
                body["environment_id"] = config.reuse_environment_id

            url = f"{self._deps.base_url}/v1/environments/bridge"
            return await self._http_post(
                url,
                json_data=body,
                headers=self._get_headers(token),
                timeout=15.0,
            )

        status, data = await self._with_oauth_retry(_do, "Registration")
        _handle_error_status(status, data, "Registration")
        self._debug(
            f"[bridge:api] POST /v1/environments/bridge -> {status} "
            f"environment_id={data.get('environment_id') if isinstance(data, dict) else '?'}"
        )
        self._debug(
            f"[bridge:api] >>> {debug_body({'machine_name': config.machine_name, 'directory': config.dir, 'branch': config.branch, 'git_repo_url': config.git_repo_url, 'max_sessions': config.max_sessions, 'metadata': {'worker_type': config.worker_type}})}"
        )
        self._debug(f"[bridge:api] <<< {debug_body(data)}")
        return data if isinstance(data, dict) else {}

    async def poll_for_work(
        self,
        environment_id: str,
        environment_secret: str,
        signal: Any = None,
        reclaim_older_than_ms: Optional[int] = None,
    ) -> Optional[WorkResponse]:
        """Long-poll for available work on a bridge environment.

        GET /v1/environments/{environment_id}/work/poll

        Uses the *environment secret* (not OAuth) for auth.
        Returns ``None`` when no work is available.
        """
        validate_bridge_id(environment_id, "environmentId")

        # Save and reset so errors break the "consecutive empty" streak.
        # Restored below when the response is truly empty.
        prev_empty = self._consecutive_empty_polls
        self._consecutive_empty_polls = 0

        url = f"{self._deps.base_url}/v1/environments/{environment_id}/work/poll"
        params: Optional[dict[str, Any]] = None
        if reclaim_older_than_ms is not None:
            params = {"reclaim_older_than_ms": reclaim_older_than_ms}

        status, data = await self._http_get(
            url,
            headers=self._get_headers(environment_secret),
            timeout=10.0,
            params=params,
            signal=signal,
        )

        _handle_error_status(status, data, "Poll")

        # Empty body or null = no work available
        if not data:
            self._consecutive_empty_polls = prev_empty + 1
            if (
                self._consecutive_empty_polls == 1
                or self._consecutive_empty_polls % EMPTY_POLL_LOG_INTERVAL == 0
            ):
                self._debug(
                    f"[bridge:api] GET .../work/poll -> {status} (no work, "
                    f"{self._consecutive_empty_polls} consecutive empty polls)"
                )
            return None

        try:
            wr = WorkResponse(**data) if isinstance(data, dict) else None
        except (TypeError, ValueError):
            self._debug(
                f"[bridge:api] GET .../work/poll -> {status} "
                f"(unexpected response shape)"
            )
            return None

        self._debug(
            f"[bridge:api] GET .../work/poll -> {status} "
            f"workId={data.get('id') if isinstance(data, dict) else '?'}"
            f"{' type=' + data.get('data', {}).get('type', '') if isinstance(data, dict) and isinstance(data.get('data'), dict) else ''}"
        )
        self._debug(f"[bridge:api] <<< {debug_body(data)}")
        return wr

    async def acknowledge_work(
        self,
        environment_id: str,
        work_id: str,
        session_token: str,
    ) -> None:
        """Acknowledge receipt of a work item.

        POST /v1/environments/{environment_id}/work/{work_id}/ack

        Uses the *session token* (not OAuth) for auth.
        """
        validate_bridge_id(environment_id, "environmentId")
        validate_bridge_id(work_id, "workId")

        self._debug(f"[bridge:api] POST .../work/{work_id}/ack")

        url = (
            f"{self._deps.base_url}/v1/environments/{environment_id}"
            f"/work/{work_id}/ack"
        )
        status, data = await self._http_post(
            url,
            json_data={},
            headers=self._get_headers(session_token),
            timeout=10.0,
        )

        _handle_error_status(status, data, "Acknowledge")
        self._debug(f"[bridge:api] POST .../work/{work_id}/ack -> {status}")

    async def stop_work(
        self,
        environment_id: str,
        work_id: str,
        force: bool,
    ) -> None:
        """Request that a work item be stopped.

        POST /v1/environments/{environment_id}/work/{work_id}/stop

        Uses OAuth auth with 401 retry. The ``force`` flag controls whether
        the server should force-stop or gracefully request stop.
        """
        validate_bridge_id(environment_id, "environmentId")
        validate_bridge_id(work_id, "workId")

        self._debug(f"[bridge:api] POST .../work/{work_id}/stop force={force}")

        async def _do(token: str) -> tuple[int, Any]:
            url = (
                f"{self._deps.base_url}/v1/environments/{environment_id}"
                f"/work/{work_id}/stop"
            )
            return await self._http_post(
                url,
                json_data={"force": force},
                headers=self._get_headers(token),
                timeout=10.0,
            )

        status, data = await self._with_oauth_retry(_do, "StopWork")
        _handle_error_status(status, data, "StopWork")
        self._debug(f"[bridge:api] POST .../work/{work_id}/stop -> {status}")

    async def deregister_environment(self, environment_id: str) -> None:
        """Deregister (delete) a bridge environment.

        DELETE /v1/environments/bridge/{environment_id}

        Uses OAuth auth with 401 retry.
        """
        validate_bridge_id(environment_id, "environmentId")

        self._debug(
            f"[bridge:api] DELETE /v1/environments/bridge/{environment_id}"
        )

        async def _do(token: str) -> tuple[int, Any]:
            url = f"{self._deps.base_url}/v1/environments/bridge/{environment_id}"
            return await self._http_delete(
                url,
                headers=self._get_headers(token),
                timeout=10.0,
            )

        status, data = await self._with_oauth_retry(_do, "Deregister")
        _handle_error_status(status, data, "Deregister")
        self._debug(
            f"[bridge:api] DELETE /v1/environments/bridge/{environment_id} -> {status}"
        )

    async def archive_session(self, session_id: str) -> None:
        """Archive a session.

        POST /v1/sessions/{session_id}/archive

        Uses OAuth auth with 401 retry.
        Idempotent: 409 (already archived) is silently accepted.
        """
        validate_bridge_id(session_id, "sessionId")

        self._debug(f"[bridge:api] POST /v1/sessions/{session_id}/archive")

        async def _do(token: str) -> tuple[int, Any]:
            url = f"{self._deps.base_url}/v1/sessions/{session_id}/archive"
            return await self._http_post(
                url,
                json_data={},
                headers=self._get_headers(token),
                timeout=10.0,
            )

        status, data = await self._with_oauth_retry(_do, "ArchiveSession")

        # 409 = already archived (idempotent, not an error)
        if status == 409:
            self._debug(
                f"[bridge:api] POST /v1/sessions/{session_id}/archive "
                f"-> 409 (already archived)"
            )
            return

        _handle_error_status(status, data, "ArchiveSession")
        self._debug(
            f"[bridge:api] POST /v1/sessions/{session_id}/archive -> {status}"
        )

    async def reconnect_session(
        self, environment_id: str, session_id: str
    ) -> None:
        """Reconnect a session to a bridge environment.

        POST /v1/environments/{environment_id}/bridge/reconnect

        Sends the session_id in the JSON body so the server can look up
        the session and reattach it. Uses OAuth auth with 401 retry.
        """
        validate_bridge_id(environment_id, "environmentId")
        validate_bridge_id(session_id, "sessionId")

        self._debug(
            f"[bridge:api] POST /v1/environments/{environment_id}"
            f"/bridge/reconnect session_id={session_id}"
        )

        async def _do(token: str) -> tuple[int, Any]:
            url = (
                f"{self._deps.base_url}/v1/environments/{environment_id}"
                f"/bridge/reconnect"
            )
            return await self._http_post(
                url,
                json_data={"session_id": session_id},
                headers=self._get_headers(token),
                timeout=10.0,
            )

        status, data = await self._with_oauth_retry(_do, "ReconnectSession")
        _handle_error_status(status, data, "ReconnectSession")
        self._debug(
            f"[bridge:api] POST .../bridge/reconnect -> {status}"
        )

    async def heartbeat_work(
        self,
        environment_id: str,
        work_id: str,
        session_token: str,
    ) -> dict[str, Any]:
        """Send a heartbeat for an active work item to extend its lease.

        POST /v1/environments/{environment_id}/work/{work_id}/heartbeat

        Uses the *session token* (not OAuth) for auth.
        Returns ``{"lease_extended": bool, "state": str, ...}``.
        """
        validate_bridge_id(environment_id, "environmentId")
        validate_bridge_id(work_id, "workId")

        self._debug(f"[bridge:api] POST .../work/{work_id}/heartbeat")

        url = (
            f"{self._deps.base_url}/v1/environments/{environment_id}"
            f"/work/{work_id}/heartbeat"
        )
        status, data = await self._http_post(
            url,
            json_data={},
            headers=self._get_headers(session_token),
            timeout=10.0,
        )

        _handle_error_status(status, data, "Heartbeat")
        self._debug(
            f"[bridge:api] POST .../work/{work_id}/heartbeat -> {status} "
            f"lease_extended={data.get('lease_extended') if isinstance(data, dict) else '?'} "
            f"state={data.get('state') if isinstance(data, dict) else '?'}"
        )
        return data if isinstance(data, dict) else {}

    async def send_permission_response_event(
        self,
        session_id: str,
        event: PermissionResponseEvent,
        session_token: str,
    ) -> None:
        """Send a permission response event to a session.

        POST /v1/sessions/{session_id}/events

        Wraps the event in ``{"events": [event]}`` per the API contract.
        Uses the *session token* (not OAuth) for auth.
        """
        validate_bridge_id(session_id, "sessionId")

        self._debug(
            f"[bridge:api] POST /v1/sessions/{session_id}/events "
            f"type={event.type}"
        )

        url = f"{self._deps.base_url}/v1/sessions/{session_id}/events"
        status, data = await self._http_post(
            url,
            json_data={"events": [event]},
            headers=self._get_headers(session_token),
            timeout=10.0,
        )

        _handle_error_status(status, data, "SendPermissionResponseEvent")
        self._debug(
            f"[bridge:api] POST /v1/sessions/{session_id}/events -> {status}"
        )
        self._debug(f"[bridge:api] >>> {debug_body({'events': [event]})}")
        self._debug(f"[bridge:api] <<< {debug_body(data)}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_bridge_api_client(deps: BridgeApiDeps) -> BridgeApiClient:
    """Create a BridgeApiClient with the given dependencies."""
    return BridgeApiClient(deps)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def _handle_error_status(status: int, data: Any, context: str) -> None:
    """Classify HTTP status codes and raise appropriate exceptions.

    Non-2xx statuses throw either ``BridgeFatalError`` (for auth/permission
    failures that should not be retried) or ``RuntimeError`` (for transient
    issues like rate limiting or unexpected server errors).
    """
    if status in (200, 204):
        return
    detail = extract_error_detail(data)
    error_type = _extract_error_type_from_data(data)

    if status == 401:
        raise BridgeFatalError(
            f"{context}: Authentication failed (401)"
            f"{': ' + detail if detail else ''}. {BRIDGE_LOGIN_INSTRUCTION}",
            401,
            error_type,
        )
    if status == 403:
        msg = (
            "Remote Control session has expired."
            if _is_expired_error_type(error_type)
            else (
                f"{context}: Access denied (403)"
                f"{': ' + detail if detail else ''}. "
                "Check your organization permissions."
            )
        )
        raise BridgeFatalError(msg, 403, error_type)
    if status == 404:
        raise BridgeFatalError(
            detail
            or f"{context}: Not found (404). Remote Control may not be available.",
            404,
            error_type,
        )
    if status == 410:
        raise BridgeFatalError(
            detail or "Remote Control session has expired.",
            410,
            error_type or "environment_expired",
        )
    if status == 429:
        raise RuntimeError(
            f"{context}: Rate limited (429). Polling too frequently."
        )
    raise RuntimeError(
        f"{context}: Failed with status {status}"
        f"{': ' + detail if detail else ''}"
    )


def _extract_error_detail(data: Any) -> Optional[str]:
    """Pull a human-readable message from an API error response body."""
    if not isinstance(data, dict):
        return None
    msg = data.get("message")
    if isinstance(msg, str) and msg:
        return str(msg)
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg:
            return str(msg)
    return None


def _extract_error_type_from_data(data: Any) -> Optional[str]:
    """Extract the server-provided error type string, e.g. "environment_expired"."""
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        t = err.get("type")
        if isinstance(t, str):
            return t
    return None


def _is_expired_error_type(error_type: Optional[str]) -> bool:
    """Return True if the error type indicates a session/environment expiry."""
    if not error_type:
        return False
    return "expired" in error_type or "lifetime" in error_type


# ---------------------------------------------------------------------------
# Public helpers re-exported for callers
# ---------------------------------------------------------------------------


def is_expired_error_type(error_type: Optional[str]) -> bool:
    """Public helper: check whether an error type string indicates expiry."""
    return _is_expired_error_type(error_type)


def is_suppressible_403(err: BridgeFatalError) -> bool:
    """Check whether a BridgeFatalError is a suppressible 403 permission error.

    These are 403 errors for scopes like 'external_poll_sessions' or
    operations like StopWork that fail because the user's role lacks
    'environments:manage'. They don't affect core functionality and
    shouldn't be shown to users.
    """
    if err.status != 403:
        return False
    message = str(err)
    return "external_poll_sessions" in message or "environments:manage" in message
