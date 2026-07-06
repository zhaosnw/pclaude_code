"""
Teleport API – sessions API client.

Port of: src/utils/teleport/api.ts

Full functional implementation of the Sessions API client used for remote/CCR
session management.  Covers auth, session CRUD, event submission, title updates,
retry logic for transient errors, and data-type definitions matching the
TypeScript source.

Architecture
------------
- HTTP via ``aiohttp.ClientSession`` (Pythonesque replacement for axios).
- OAuth tokens resolved through dependency-injected callbacks (matching the
  pattern already used by ``session_history.py`` and ``bridge_config.py``).
- Exponential-backoff retry (2, 4, 8, 16 s – 4 retries) on transient network
  errors (no-response or 5xx).  4xx errors are NOT retried (they are permanent
  client errors).
- All public functions that touch the network are ``async`` and can raise
  ``TeleportAuthError``, ``TeleportApiError``, or standard ``aiohttp`` errors.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid as _uuid
from dataclasses import dataclass, field, is_dataclass
from typing import Any, Callable, Literal, Optional, Union

from hare.utils.debug import log_for_debugging
from hare.utils.errors import error_message
from hare.utils.json_utils import json_stringify
from hare.utils.sleep import sleep

# ---------------------------------------------------------------------------
# Re-export the beta header key for external use (matches TS: CCR_BYOC_BETA)
# ---------------------------------------------------------------------------
CCR_BYOC_BETA = "ccr-byoc-2025-07-29"

# ---------------------------------------------------------------------------
# Retry configuration (matches TS exactly)
# ---------------------------------------------------------------------------
_TELEPORT_RETRY_DELAYS = (2_000, 4_000, 8_000, 16_000)  # ms
_MAX_TELEPORT_RETRIES = len(_TELEPORT_RETRY_DELAYS)

# Default session API URL – always overridden by get_oauth_config() in practice
_DEFAULT_BASE_API_URL = os.environ.get(
    "CLAUDE_CODE_BASE_API_URL", "https://api.claude.ai"
)
_DEFAULT_TIMEOUT_SECONDS = 15.0
_EVENT_TIMEOUT_SECONDS = 30.0  # may block until CCR worker is ready


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class TeleportAuthError(RuntimeError):
    """Authentication failed – user should re-authenticate with /login."""


class TeleportApiError(RuntimeError):
    """Non-transient API error (includes 4xx responses)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Type aliases / data classes (port of TS types)
# ---------------------------------------------------------------------------

SessionStatus = Literal["requires_action", "running", "idle", "archived"]

# Code session status as returned by code-session-flavoured endpoints
CodeSessionStatus = Literal[
    "idle", "working", "waiting", "completed", "archived", "cancelled", "rejected"
]

# Map session_status → code-session status.  Any unknown value passes through as-is.
_SESSION_STATUS_TO_CODE_STATUS: dict[str, CodeSessionStatus] = {
    "requires_action": "waiting",
    "running": "working",
    "idle": "idle",
    "archived": "archived",
}


def _map_session_status(raw: str | None) -> CodeSessionStatus:
    """Coerce a Sessions API status into a CodeSession status (default ``idle``)."""
    if not raw:
        return "idle"
    return _SESSION_STATUS_TO_CODE_STATUS.get(raw, "idle")


@dataclass
class RepoInfo:
    """Minimal repository info suitable for display in session lists.

    Mirrors the ``CodeSession['repo']`` shape from TS.
    """

    name: str
    owner: RepoOwner
    default_branch: str | None = None


@dataclass
class RepoOwner:
    login: str


@dataclass
class CodeSession:
    """A code-session record as returned by ``fetch_code_sessions``."""

    id: str
    title: str
    description: str
    status: CodeSessionStatus
    repo: RepoInfo | None
    turns: list[str]
    created_at: str
    updated_at: str


# ---- SessionResource shapes (matching TS SessionResource) ----


@dataclass
class GitSource:
    type: Literal["git_repository"]
    url: str
    revision: str | None = None
    allow_unrestricted_git_push: bool | None = None


@dataclass
class KnowledgeBaseSource:
    type: Literal["knowledge_base"]
    knowledge_base_id: str


SessionContextSource = Union[GitSource, KnowledgeBaseSource]


@dataclass
class OutcomeGitInfo:
    type: Literal["github"]
    repo: str
    branches: list[str] = field(default_factory=list)


@dataclass
class GitRepositoryOutcome:
    type: Literal["git_repository"]
    git_info: OutcomeGitInfo


Outcome = GitRepositoryOutcome  # currently the only outcome type


@dataclass
class SessionContext:
    sources: list[SessionContextSource] = field(default_factory=list)
    cwd: str = ""
    outcomes: list[Outcome] | None = None
    custom_system_prompt: str | None = None
    append_system_prompt: str | None = None
    model: str | None = None
    seed_bundle_file_id: str | None = None
    github_pr: dict[str, Any] | None = None
    reuse_outcome_branches: bool | None = None


@dataclass
class SessionResource:
    type: Literal["session"] = "session"
    id: str = ""
    title: str | None = None
    session_status: str = "idle"
    environment_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    session_context: SessionContext = field(default_factory=SessionContext)


@dataclass
class ListSessionsResponse:
    data: list[SessionResource] = field(default_factory=list)
    has_more: bool = False
    first_id: str | None = None
    last_id: str | None = None


# Content for a remote session message – a plain string or content blocks.
RemoteMessageContent = Union[
    str, list[dict[str, Any]]
]


# ---------------------------------------------------------------------------
# Helpers – transient-error detection (port of isTransientNetworkError)
# ---------------------------------------------------------------------------


def is_transient_network_error(error: Any) -> bool:
    """Return ``True`` if *error* is a retryable transport-level failure.

    Retryable cases:
    * No HTTP response at all (DNS / connection refused / timeout).
    * Server errors (5xx).

    Non-retryable:
    * Client errors (4xx) – these are permanent and would fail again.
    * Non-HTTP errors (e.g. ``TypeError``).
    """
    # ConnectionError / TimeoutError / OSError → always transient transport failures
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True

    # aiohttp raises ClientResponseError for non-2xx; it has .status
    status: int | None = getattr(error, "status", None)
    if status is not None:
        return 500 <= status < 600  # 5xx only

    # No HTTP-semantics attribute → not transient
    return False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _http_request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_data: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = _MAX_TELEPORT_RETRIES,
) -> tuple[int, dict[str, Any] | None, str]:
    """Make an HTTP request with automatic retry for transient errors.

    Returns ``(status_code, parsed_json_or_None, raw_text)``.

    Parameters
    ----------
    method: HTTP method (GET, POST, PATCH, …).
    url: Fully qualified URL.
    headers: Request headers.
    json_data: JSON body (serialized automatically).
    timeout: Request timeout in seconds.
    max_retries: Maximum number of retries (default = 4).
    """
    import aiohttp

    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):  # 1 initial + N retries
        try:
            client_timeout = aiohttp.ClientTimeout(
                total=timeout, connect=10.0, sock_read=timeout
            )
            async with aiohttp.ClientSession(
                timeout=client_timeout,
                headers=headers or {},
            ) as session:
                async with session.request(
                    method, url, json=json_data
                ) as response:
                    text = await response.text()
                    data: dict[str, Any] | None = None
                    try:
                        data = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        pass

                    if response.status < 500:
                        # Success or client error – do NOT retry
                        return response.status, data, text

                    # 5xx – will retry below
                    log_for_debugging(
                        f"Teleport {method} {url} → {response.status} "
                        f"(attempt {attempt + 1}/{max_retries + 1}): {text[:200]}"
                    )
                    raise aiohttp.ClientResponseError(
                        request_info=getattr(response, "request_info", None),
                        history=getattr(response, "history", ()),
                        status=response.status,
                        message=text[:200],
                        headers=response.headers,
                    )
        except aiohttp.ClientResponseError as e:
            if not (500 <= e.status < 600):
                # Non-5xx aiohttp error – raise immediately
                raise
            last_error = e
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError, OSError) as e:
            # Transient transport errors
            last_error = e
        except Exception as e:
            # Unexpected – do not retry
            raise

        if attempt >= max_retries:
            log_for_debugging(
                f"Teleport request failed after {attempt + 1} attempts: "
                f"{error_message(last_error)}"
            )
            break

        delay = _TELEPORT_RETRY_DELAYS[attempt] if attempt < len(_TELEPORT_RETRY_DELAYS) else 2000
        log_for_debugging(
            f"Teleport request failed (attempt {attempt + 1}/{max_retries + 1}), "
            f"retrying in {delay}ms: {error_message(last_error)}"
        )
        await sleep(delay)

    # Exhausted retries
    if isinstance(last_error, aiohttp.ClientResponseError):
        raise last_error
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Teleport request failed after {max_retries} retries")


# ---------------------------------------------------------------------------
# Auth / config resolution
# ---------------------------------------------------------------------------

# Callable signatures for dependency injection (callers provide these at
# import time or pass them explicitly).  This avoids a hard dependency on
# the OAuth service modules, which may not be importable in all contexts.
PrepareApiRequestFn = Callable[[], Any]
"""``() -> {accessToken: str, orgUUID: str}`` or raises."""

GetOAuthConfigFn = Callable[[], dict[str, str]]
"""``() -> {BASE_API_URL: str, ...}``."""

GetClaudeAIOAuthTokensFn = Callable[[], dict[str, str] | None]
"""``() -> {accessToken: str, ...} | None``."""

GetOrganizationUUIDFn = Callable[[], Any]
"""``() -> str | None`` (may be coroutine)."""

# Module-level injectable references (file-private).
_prepare_api_request_fn: PrepareApiRequestFn | None = None
_get_oauth_config_fn: GetOAuthConfigFn | None = None
_get_claude_ai_oauth_tokens_fn: GetClaudeAIOAuthTokensFn | None = None
_get_organization_uuid_fn: GetOrganizationUUIDFn | None = None


def _set_injectables(
    *,
    prepare_api_request: PrepareApiRequestFn | None = None,
    get_oauth_config: GetOAuthConfigFn | None = None,
    get_claude_ai_oauth_tokens: GetClaudeAIOAuthTokensFn | None = None,
    get_organization_uuid: GetOrganizationUUIDFn | None = None,
) -> None:
    """Wire up the injectable callbacks (called once at boot)."""
    global _prepare_api_request_fn, _get_oauth_config_fn
    global _get_claude_ai_oauth_tokens_fn, _get_organization_uuid_fn
    if prepare_api_request:
        _prepare_api_request_fn = prepare_api_request
    if get_oauth_config:
        _get_oauth_config_fn = get_oauth_config
    if get_claude_ai_oauth_tokens:
        _get_claude_ai_oauth_tokens_fn = get_claude_ai_oauth_tokens
    if get_organization_uuid:
        _get_organization_uuid_fn = get_organization_uuid


def _resolve_oauth_config() -> dict[str, str]:
    """Return the OAuth config dict with at least ``BASE_API_URL``."""
    if _get_oauth_config_fn:
        return _get_oauth_config_fn()
    return {"BASE_API_URL": _DEFAULT_BASE_API_URL}


def _resolve_base_api_url() -> str:
    return _resolve_oauth_config().get("BASE_API_URL", _DEFAULT_BASE_API_URL)


# ---------------------------------------------------------------------------
# OAuth headers (public)
# ---------------------------------------------------------------------------


def get_oauth_headers(access_token: str) -> dict[str, str]:
    """Build standard OAuth headers for Sessions API calls.

    Includes: Authorization (Bearer), Content-Type, anthropic-version.
    """
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }


# ---------------------------------------------------------------------------
# prepare_api_request – validate and obtain auth context
# ---------------------------------------------------------------------------


async def prepare_api_request() -> dict[str, str]:
    """Validate credentials and return ``{accessToken, orgUUID}``.

    Raises ``TeleportAuthError`` when authentication is missing or incomplete.
    """

    # 1. Retrieve access token (try injected function → env → fallback)
    access_token: str | None = None

    if _get_claude_ai_oauth_tokens_fn:
        tokens = _get_claude_ai_oauth_tokens_fn()
        if tokens and isinstance(tokens, dict):
            access_token = tokens.get("accessToken")

    if not access_token:
        access_token = os.environ.get("CLAUDE_CODE_OAUTH_ACCESS_TOKEN")

    if not access_token:
        raise TeleportAuthError(
            "Claude Code web sessions require authentication with a Claude.ai "
            "account. API key authentication is not sufficient. Please run "
            "/login to authenticate, or check your authentication status with "
            "/status."
        )

    # 2. Retrieve organization UUID
    org_uuid: str | None = None

    if _get_organization_uuid_fn:
        try:
            result = _get_organization_uuid_fn()
            if asyncio.iscoroutine(result):
                result = await result
            if result:
                org_uuid = str(result)
        except Exception:
            org_uuid = None

    if not org_uuid:
        org_uuid = os.environ.get("CLAUDE_CODE_ORG_UUID", "")

    if not org_uuid:
        raise TeleportAuthError("Unable to get organization UUID")

    return {"accessToken": access_token, "orgUUID": org_uuid}


# ---------------------------------------------------------------------------
# fetch_session – single session lookup
# ---------------------------------------------------------------------------


async def fetch_session(session_id: str) -> SessionResource | None:
    """Fetch a single session by ID from ``GET /v1/sessions/{sessionId}``.

    Returns ``None`` when the session is not found (404) instead of raising,
    matching the caller-friendly convention of the stub.

    Raises ``TeleportAuthError`` on 401, ``TeleportApiError`` on other 4xx,
    and propagates transport errors.
    """
    try:
        auth = await prepare_api_request()
    except TeleportAuthError:
        raise
    except Exception as exc:
        raise TeleportApiError(f"Failed to prepare auth: {exc}") from exc

    base_url = _resolve_base_api_url()
    url = f"{base_url}/v1/sessions/{session_id}"

    headers = {
        **get_oauth_headers(auth["accessToken"]),
        "anthropic-beta": CCR_BYOC_BETA,
        "x-organization-uuid": auth["orgUUID"],
    }

    status, data, text = await _http_request_with_retry(
        "GET", url, headers=headers, timeout=_DEFAULT_TIMEOUT_SECONDS
    )

    if status == 200 and data:
        return _parse_session_resource(data)

    if status == 404:
        log_for_debugging(f"Session not found: {session_id}")
        return None

    if status == 401:
        raise TeleportAuthError(
            "Session expired. Please run /login to sign in again."
        )

    api_message = _extract_api_error_message(data)
    raise TeleportApiError(
        api_message or f"Failed to fetch session: {status}",
        status=status,
    )


# ---------------------------------------------------------------------------
# fetch_code_sessions – list all sessions
# ---------------------------------------------------------------------------


async def fetch_code_sessions() -> list[CodeSession]:
    """Fetch code sessions from ``GET /v1/sessions``.

    Transforms ``SessionResource`` items returned by the API into the
    ``CodeSession`` shape used by the rest of the application.

    Raises ``TeleportAuthError`` or ``TeleportApiError`` on failure.
    """
    try:
        auth = await prepare_api_request()
    except TeleportAuthError:
        raise
    except Exception as exc:
        raise TeleportApiError(f"Failed to prepare auth: {exc}") from exc

    base_url = _resolve_base_api_url()
    url = f"{base_url}/v1/sessions"

    headers = {
        **get_oauth_headers(auth["accessToken"]),
        "anthropic-beta": CCR_BYOC_BETA,
        "x-organization-uuid": auth["orgUUID"],
    }

    status, data, _text = await _http_request_with_retry(
        "GET", url, headers=headers, timeout=_DEFAULT_TIMEOUT_SECONDS
    )

    if status != 200:
        raise TeleportApiError(
            f"Failed to fetch code sessions: HTTP {status}", status=status
        )
    if not data:
        return []

    # The API returns ListSessionsResponse; extract the `data` array.
    raw_list = data.get("data") if isinstance(data, dict) else data
    if not isinstance(raw_list, list):
        return []

    result: list[CodeSession] = []
    for item in raw_list:
        if isinstance(item, dict):
            cs = _session_resource_to_code_session(item)
            if cs:
                result.append(cs)
    return result


# ---------------------------------------------------------------------------
# send_event_to_remote_session – submit a user message
# ---------------------------------------------------------------------------


async def send_event_to_remote_session(
    session_id: str,
    message_content: RemoteMessageContent,
    uuid: str | None = None,
) -> bool:
    """Send a user message event to ``POST /v1/sessions/{session_id}/events``.

    Parameters
    ----------
    session_id: Target session.
    message_content: A plain string or a list of content blocks.
    uuid: Optional event UUID.  Callers that have already created a local
        ``UserMessage`` should pass its UUID so that echo filtering can dedup.

    Returns
    -------
    ``True`` on success (200 or 201), ``False`` otherwise.
    Errors are logged and swallowed – the caller should treat this as a
    best-effort fire-and-forget.
    """
    try:
        auth = await prepare_api_request()
    except Exception as exc:
        log_for_debugging(
            f"[sendEventToRemoteSession] Auth failed: {error_message(exc)}"
        )
        return False

    base_url = _resolve_base_api_url()
    url = f"{base_url}/v1/sessions/{session_id}/events"

    headers = {
        **get_oauth_headers(auth["accessToken"]),
        "anthropic-beta": CCR_BYOC_BETA,
        "x-organization-uuid": auth["orgUUID"],
    }

    event_uuid = uuid or str(_uuid.uuid4())

    user_event: dict[str, Any] = {
        "uuid": event_uuid,
        "session_id": session_id,
        "type": "user",
        "parent_tool_use_id": None,
        "message": {
            "role": "user",
            "content": message_content,
        },
    }

    request_body = {"events": [user_event]}

    log_for_debugging(
        f"[sendEventToRemoteSession] Sending event to session {session_id}"
    )

    try:
        status, data, _text = await _http_request_with_retry(
            "POST",
            url,
            headers=headers,
            json_data=request_body,
            timeout=_EVENT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log_for_debugging(
            f"[sendEventToRemoteSession] Error: {error_message(exc)}"
        )
        return False

    if status in (200, 201):
        log_for_debugging(
            f"[sendEventToRemoteSession] Successfully sent event to "
            f"session {session_id}"
        )
        return True

    log_for_debugging(
        f"[sendEventToRemoteSession] Failed with status {status}: "
        f"{json_stringify(data)}"
    )
    return False


# ---------------------------------------------------------------------------
# update_session_title – PATCH the session title
# ---------------------------------------------------------------------------


async def update_session_title(session_id: str, title: str) -> bool:
    """Update the title of a remote session via ``PATCH /v1/sessions/{sessionId}``.

    Returns ``True`` on success, ``False`` on any failure.
    """
    try:
        auth = await prepare_api_request()
    except Exception as exc:
        log_for_debugging(
            f"[updateSessionTitle] Auth failed: {error_message(exc)}"
        )
        return False

    base_url = _resolve_base_api_url()
    url = f"{base_url}/v1/sessions/{session_id}"

    headers = {
        **get_oauth_headers(auth["accessToken"]),
        "anthropic-beta": CCR_BYOC_BETA,
        "x-organization-uuid": auth["orgUUID"],
    }

    log_for_debugging(
        f'[updateSessionTitle] Updating title for session {session_id}: "{title}"'
    )

    try:
        status, data, _text = await _http_request_with_retry(
            "PATCH",
            url,
            headers=headers,
            json_data={"title": title},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        log_for_debugging(
            f"[updateSessionTitle] Error: {error_message(exc)}"
        )
        return False

    if status == 200:
        log_for_debugging(
            f"[updateSessionTitle] Successfully updated title for "
            f"session {session_id}"
        )
        return True

    log_for_debugging(
        f"[updateSessionTitle] Failed with status {status}: "
        f"{json_stringify(data)}"
    )
    return False


# ---------------------------------------------------------------------------
# get_branch_from_session – extract first branch from git outcomes
# ---------------------------------------------------------------------------


def get_branch_from_session(session: SessionResource) -> str | None:
    """Extract the first branch name from a session's git repository outcomes.

    Returns ``None`` if there are no git-repo outcomes.
    """
    outcomes = session.session_context.outcomes
    if not outcomes:
        return None
    for outcome in outcomes:
        if isinstance(outcome, GitRepositoryOutcome):
            return outcome.git_info.branches[0] if outcome.git_info.branches else None
        # Handle dict form for unparsed / raw data
        if isinstance(outcome, dict):
            if outcome.get("type") == "git_repository":
                git_info = outcome.get("git_info") or {}
                branches = git_info.get("branches") or []
                return branches[0] if branches else None
    return None


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------


def _parse_session_resource(data: dict[str, Any]) -> SessionResource:
    """Parse a raw API dict into a ``SessionResource`` dataclass."""
    ctx_raw = data.get("session_context") or {}
    outcomes_raw = ctx_raw.get("outcomes")

    sources: list[SessionContextSource] = []
    for s in ctx_raw.get("sources") or []:
        if isinstance(s, dict):
            kind = s.get("type")
            if kind == "git_repository":
                sources.append(
                    GitSource(
                        type="git_repository",
                        url=s.get("url", ""),
                        revision=s.get("revision"),
                        allow_unrestricted_git_push=s.get(
                            "allow_unrestricted_git_push"
                        ),
                    )
                )
            elif kind == "knowledge_base":
                sources.append(
                    KnowledgeBaseSource(
                        type="knowledge_base",
                        knowledge_base_id=s.get("knowledge_base_id", ""),
                    )
                )

    outcomes: list[Outcome] | None = None
    if outcomes_raw and isinstance(outcomes_raw, list):
        parsed: list[Outcome] = []
        for o in outcomes_raw:
            if isinstance(o, dict) and o.get("type") == "git_repository":
                gi_raw = o.get("git_info") or {}
                parsed.append(
                    GitRepositoryOutcome(
                        type="git_repository",
                        git_info=OutcomeGitInfo(
                            type=gi_raw.get("type", "github"),
                            repo=gi_raw.get("repo", ""),
                            branches=list(gi_raw.get("branches") or []),
                        ),
                    )
                )
        outcomes = parsed if parsed else None

    return SessionResource(
        id=data.get("id", ""),
        title=data.get("title"),
        session_status=data.get("session_status", "idle"),
        environment_id=data.get("environment_id", ""),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        session_context=SessionContext(
            sources=sources,
            cwd=ctx_raw.get("cwd", ""),
            outcomes=outcomes,
            custom_system_prompt=ctx_raw.get("custom_system_prompt"),
            append_system_prompt=ctx_raw.get("append_system_prompt"),
            model=ctx_raw.get("model"),
            seed_bundle_file_id=ctx_raw.get("seed_bundle_file_id"),
            github_pr=ctx_raw.get("github_pr"),
            reuse_outcome_branches=ctx_raw.get("reuse_outcome_branches"),
        ),
    )


def _session_resource_to_code_session(
    data: dict[str, Any],
) -> CodeSession | None:
    """Convert a raw session dict into a ``CodeSession``.

    Returns ``None`` when the input is malformed beyond recovery.
    """
    if not isinstance(data.get("id"), str):
        return None

    session_id = str(data["id"])
    title = str(data.get("title") or "Untitled")
    raw_status = data.get("session_status")
    status = _map_session_status(raw_status if isinstance(raw_status, str) else None)

    # Extract repository info from the first git source
    ctx = data.get("session_context") or {}
    sources = ctx.get("sources") or []
    repo: RepoInfo | None = None

    for source in sources or []:
        if not isinstance(source, dict):
            continue
        if source.get("type") != "git_repository":
            continue
        git_url = source.get("url", "")
        if not git_url:
            continue
        try:
            owner_name = _parse_github_repo_path(git_url)
            if owner_name:
                owner, name = owner_name
                if owner and name:
                    repo = RepoInfo(
                        name=name,
                        owner=RepoOwner(login=owner),
                        default_branch=source.get("revision") or None,
                    )
                    break
        except Exception:
            pass

    return CodeSession(
        id=session_id,
        title=title,
        description="",  # SessionResource has no dedicated description field
        status=status,
        repo=repo,
        turns=[],  # SessionResource has no turns field
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def _parse_github_repo_path(url: str) -> tuple[str, str] | None:
    """Try to extract (owner, name) from a GitHub URL or ``owner/name`` string.

    Delegates to ``detectRepository.parse_github_repository`` when possible;
    falls back to local heuristics.
    """
    try:
        from hare.utils.detect_repository import parse_github_repository

        result = parse_github_repository(url)
        if result and "/" in result:
            parts = result.split("/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]
    except Exception:
        pass

    # Fallback: simple owner/repo extraction for https://github.com/... URLs
    import re

    gh_pattern = re.compile(
        r"(?:https?://|git@)github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$",
        re.IGNORECASE,
    )
    m = gh_pattern.search(url.strip())
    if m:
        name = m.group(2).removesuffix("/")
        return m.group(1), name

    # Bare owner/name
    if "://" not in url and "@" not in url and "/" in url:
        parts = url.strip().split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            name = parts[1].removesuffix(".git")
            return parts[0], name

    return None


def _extract_api_error_message(data: dict[str, Any] | None) -> str | None:
    """Extract a user-facing error message from API response JSON."""
    if not isinstance(data, dict):
        return None
    error_block = data.get("error")
    if isinstance(error_block, dict):
        return error_block.get("message")
    if isinstance(error_block, str):
        return error_block
    return data.get("message")
