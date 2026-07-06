"""Side queries outside the main loop (port of sideQuery.ts).

Provides a lightweight wrapper around the Anthropic API for secondary
(non-main-loop) queries such as:

- Memory selection (fingerprint / relevance scoring)
- Model validation probes
- Agentic session search
- Beta feature detection
- Metadata extraction

The core entry point is :func:`side_query`, which accepts either a
:class:`SideQueryOptions` dataclass or keyword arguments mirroring its fields.
It returns a :class:`SideQueryResponse` with the parsed API result.

Architecture
------------
- Non-streaming only (side queries are small and latency-aware)
- Retries with exponential backoff for transient failures (429, 503, 529)
- Abort-signal integration (asyncio.Event / AbortSignal-style objects)
- Share the same auth pipeline as the main API client
  (:func:`hare.services.api.client._get_default_headers` etc.)
- Returns a dict-like response for backward compatibility with existing
  callers that use ``result.get("content")``.

Edge cases handled
------------------
- ``model=None`` → resolved to default Sonnet via
  :func:`hare.utils.model.model_full.get_default_sonnet_model`
- ``signal`` aborted mid-flight → raises :class:`SideQueryAbortedError`
- API returns 401 → raises :class:`SideQueryAuthError`
- API returns 404 → raises :class:`SideQueryNotFoundError`
- API returns 429/529 → retried up to *max_retries* times
- ``output_format`` with ``json_schema`` enables structured-output beta
- ``skip_system_prompt_prefix`` suppresses the default system-prompt prefix
  injection (used by memdir selection)
- Empty messages list → raises :class:`SideQueryValidationError`
- Unicode / emoji in messages → handled transparently
- Network timeout → raises :class:`SideQueryConnectionError`
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hare.utils.debug import log_for_debugging
from hare.utils.errors import error_message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum total retries (initial call + N retries).
_DEFAULT_MAX_RETRIES = 2

# Base delay for exponential backoff (seconds).
_BASE_RETRY_DELAY_S = 1.0

# Maximum backoff cap (seconds).
_MAX_BACKOFF_S = 15.0

# Default timeout for the entire side-query operation (seconds).
_DEFAULT_TIMEOUT_S = 30.0

# HTTP statuses treated as transient / retryable.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 503, 502, 529})

# A minimal system-prompt prefix that is prepended unless
# ``skip_system_prompt_prefix`` is ``True``.  Mirrors the TS side-query
# behaviour where most side queries get the standard prefix but memory
# selection / fingerprint queries bypass it.
_DEFAULT_SYSTEM_PROMPT_PREFIX = (
    "You are Claude, a helpful AI assistant created by Anthropic. "
    "Respond concisely and accurately."
)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class SideQueryError(Exception):
    """Base exception for all side-query failures."""


class SideQueryValidationError(SideQueryError):
    """Raised when the input parameters are invalid (e.g. empty messages)."""


class SideQueryAbortedError(SideQueryError):
    """Raised when the abort signal fires before the query completes."""


class SideQueryAuthError(SideQueryError):
    """Raised on 401 / authentication failures."""


class SideQueryNotFoundError(SideQueryError):
    """Raised on 404 / model-not-found failures."""


class SideQueryRateLimitError(SideQueryError):
    """Raised when all retries are exhausted on 429 responses."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class SideQueryConnectionError(SideQueryError):
    """Raised on network / timeout errors (including 502 / 503 / 529 exhaustion)."""


class SideQueryAPIError(SideQueryError):
    """Raised for generic API-level errors."""

    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SideQueryOptions:
    """Input options for :func:`side_query`.

    Attributes
    ----------
    model:
        Model string (e.g. ``"claude-sonnet-4-20250514"``).  ``None``
        resolves to the default Sonnet model.
    messages:
        List of message dicts in Anthropic format
        (``[{"role": "user", "content": "..."}]``).
    query_source:
        Human-readable label for telemetry / debugging (e.g.
        ``"memdir_relevance"``, ``"session_search"``).
    system:
        System prompt.  String or list of content blocks.
    tools:
        Tool schema list (Anthropic tool-use format).
    tool_choice:
        Tool choice directive.
    output_format:
        Structured-output format (e.g. ``{"type": "json_schema", "schema": ...}``).
    max_tokens:
        Maximum output tokens (default 1024 — keep side queries small).
    max_retries:
        Maximum retry count for transient failures (default 2).
    signal:
        Optional abort signal (asyncio.Event, threading.Event, or any object
        with an ``aborted`` / ``is_set`` attribute).
    skip_system_prompt_prefix:
        If ``True``, do NOT prepend the default system-prompt prefix to
        ``system``.  Used by memory-selection queries that provide their own
        complete system prompt.
    temperature:
        Sampling temperature (0.0–1.0).  ``None`` uses the model default.
    thinking:
        Thinking budget — ``int`` for token budget, ``True`` for default,
        ``False`` / ``None`` to disable.
    stop_sequences:
        Custom stop sequences.
    """

    model: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    query_source: str = ""
    system: str | list[dict[str, Any]] | None = None
    tools: list[Any] | None = None
    tool_choice: Any = None
    output_format: Any = None
    max_tokens: int = 1024
    max_retries: int = 2
    signal: Any = None
    skip_system_prompt_prefix: bool = False
    temperature: float | None = None
    thinking: int | bool | None = None
    stop_sequences: list[str] | None = None


@dataclass
class SideQueryResponse:
    """Structured response from :func:`side_query`.

    Attributes
    ----------
    content:
        List of content blocks from the API response.  Each block is a dict
        with at minimum a ``"type"`` key (``"text"``, ``"tool_use"``, etc.).
    model:
        The model that produced the response.
    stop_reason:
        Why the model stopped (``"end_turn"``, ``"max_tokens"``, etc.).
    usage:
        Token usage dict with ``input_tokens`` and ``output_tokens``.
    """

    content: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for backward compat (callers use ``result.get("content")``)."""
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-like item access for backward compat."""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


# ---------------------------------------------------------------------------
# Module-level injection point (for tests / custom transports)
# ---------------------------------------------------------------------------

_SIDE_QUERY_FN: Callable[..., Any] | None = None


def set_side_query_fn(fn: Callable[..., Any]) -> None:
    """Inject a custom side-query implementation.

    When set, :func:`side_query` delegates to *fn* instead of making a real
    API call.  *fn* must accept a single :class:`SideQueryOptions` argument
    and return a :class:`SideQueryResponse` (or a dict).

    Used by the model-validation subsystem (:mod:`hare.utils.model.validate_model`)
    and test harnesses.
    """
    global _SIDE_QUERY_FN
    _SIDE_QUERY_FN = fn


def get_side_query_fn() -> Callable[..., Any] | None:
    """Return the currently-injected side-query function, or ``None``."""
    return _SIDE_QUERY_FN


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------


def _is_signal_aborted(signal: Any) -> bool:
    """Check whether *signal* has been aborted / set.

    Accepts a wide range of signal-like objects:
    - ``asyncio.Event``
    - ``threading.Event``
    - Objects with ``aborted`` attribute
    - ``None`` (never aborted)
    """
    if signal is None:
        return False
    try:
        # asyncio.Event / threading.Event
        is_set = getattr(signal, "is_set", None)
        if callable(is_set) and is_set():
            return True
        # AbortSignal-style (web API / custom)
        if getattr(signal, "aborted", False):
            return True
        # Custom signal with a boolean `set` attribute
        if getattr(signal, "set", False) is True:
            return True
    except Exception:
        pass
    return False


async def _wait_with_signal(
    coro: Any,
    signal: Any,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Any:
    """Await *coro* but cancel if *signal* fires or *timeout_s* elapses.

    Returns the coroutine result on success.
    Raises :class:`SideQueryAbortedError` on signal.
    Raises :class:`SideQueryConnectionError` on timeout.
    """
    if signal is None:
        try:
            return await asyncio.wait_for(coro, timeout=timeout_s)
        except asyncio.TimeoutError:
            raise SideQueryConnectionError(
                f"Side query timed out after {timeout_s:.0f}s"
            ) from None

    # Build an event-watcher that fires when the signal is set
    abort_event = asyncio.Event()

    async def _poll_signal() -> None:
        while not _is_signal_aborted(signal):
            await asyncio.sleep(0.05)
        abort_event.set()

    poll_task = asyncio.ensure_future(_poll_signal())
    main_task = asyncio.ensure_future(coro)

    try:
        done, pending = await asyncio.wait(
            [main_task, poll_task, abort_event.wait()],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=timeout_s,
        )

        if not done:
            # Timeout
            for t in [main_task, poll_task]:
                t.cancel()
            raise SideQueryConnectionError(
                f"Side query timed out after {timeout_s:.0f}s"
            )

        if abort_event in done or poll_task in done or abort_event.is_set():
            # Signal fired
            main_task.cancel()
            raise SideQueryAbortedError("Side query aborted by signal")

        if main_task in done:
            return main_task.result()

        # Should not reach here, but safety net
        raise SideQueryError("Side query failed: unexpected state")

    finally:
        for t in [main_task, poll_task]:
            if not t.done():
                t.cancel()


# ---------------------------------------------------------------------------
# Client building (shares auth pipeline with main API)
# ---------------------------------------------------------------------------


def _get_anthropic_client() -> Any:
    """Build an ``anthropic.AsyncAnthropic`` client using the standard auth pipeline.

    Mirrors the credential resolution in :mod:`hare.services.api.client`:
    1. ``ANTHROPIC_API_KEY`` env var
    2. ``ANTHROPIC_AUTH_TOKEN`` env var
    3. API key helper program
    4. Custom ``Authorization`` header from ``ANTHROPIC_CUSTOM_HEADERS``

    Returns the client instance.

    Raises:
        SideQueryAuthError: When no credentials are available.
        ImportError: When the ``anthropic`` package is not installed.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "The 'anthropic' package is required for side queries. "
            "Install it with: pip install anthropic"
        ) from exc

    base_url = _normalize_anthropic_base_url(os.environ.get("ANTHROPIC_BASE_URL"))
    default_headers = _get_default_headers()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or None

    if not auth_token:
        auth_token = _get_api_key_from_api_key_helper()
    if not auth_token:
        auth_token = _auth_token_from_authorization_header(
            default_headers.get("Authorization")
        )
    if not api_key:
        api_key = _header_value(default_headers, "x-api-key")

    if not api_key and not auth_token and "Authorization" not in default_headers:
        raise SideQueryAuthError(
            "No API credentials available. Set ANTHROPIC_API_KEY, "
            "ANTHROPIC_AUTH_TOKEN, or configure an apiKeyHelper."
        )

    client_kwargs: dict[str, Any] = {
        **({"base_url": base_url} if base_url else {}),
        **({"default_headers": default_headers} if default_headers else {}),
    }
    if api_key:
        client_kwargs["api_key"] = api_key
    if auth_token:
        client_kwargs["auth_token"] = auth_token

    # Respect user's custom timeout for side queries
    max_retries_env = os.environ.get("ANTHROPIC_MAX_RETRIES")
    if max_retries_env is not None:
        try:
            client_kwargs["max_retries"] = int(max_retries_env)
        except ValueError:
            pass

    return anthropic.AsyncAnthropic(**client_kwargs)


def _get_default_headers() -> dict[str, str]:
    """Build default HTTP headers matching the main API client."""
    try:
        from hare.bootstrap.state import get_session_id

        session_id = get_session_id()
    except Exception:
        session_id = "unknown"

    headers: dict[str, str] = {
        "x-app": "cli",
        "User-Agent": "hare/side-query",
        "X-Claude-Code-Session-Id": session_id,
    }

    container_id = os.environ.get("CLAUDE_CODE_CONTAINER_ID")
    remote_session_id = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    client_app = os.environ.get("CLAUDE_AGENT_SDK_CLIENT_APP")
    if container_id:
        headers["x-claude-remote-container-id"] = container_id
    if remote_session_id:
        headers["x-claude-remote-session-id"] = remote_session_id
    if client_app:
        headers["x-client-app"] = client_app

    # Additional protection flag
    from hare.utils.env_utils import is_env_truthy

    if is_env_truthy(os.environ.get("CLAUDE_CODE_ADDITIONAL_PROTECTION")):
        headers["x-anthropic-additional-protection"] = "true"

    # Custom headers from env
    raw = os.environ.get("ANTHROPIC_CUSTOM_HEADERS", "")
    if raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            name = name.strip()
            value = value.strip()
            if name:
                headers[name] = value

    return headers


def _normalize_anthropic_base_url(base_url: str | None) -> str | None:
    """Accept common provider base URLs while using Anthropic SDK paths."""
    if not base_url:
        return None
    from urllib.parse import urlparse

    trimmed = base_url.rstrip("/")
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return trimmed
    if parsed.hostname == "api.deepseek.com" and parsed.path in ("", "/"):
        return f"{trimmed}/anthropic"
    return trimmed


def _get_api_key_from_api_key_helper() -> str | None:
    """Run the configured apiKeyHelper program to obtain a token."""
    import subprocess

    try:
        from hare.utils.cwd import get_cwd
        from hare.utils.settings.settings import get_initial_settings

        helper = (get_initial_settings(project_dir=get_cwd()) or {}).get("apiKeyHelper")
    except Exception:
        helper = None
    if not isinstance(helper, str) or not helper.strip():
        return None
    try:
        result = subprocess.run(
            helper,
            shell=True,  # nosec B602
            capture_output=True,
            text=True,
            timeout=10 * 60,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _auth_token_from_authorization_header(value: str | None) -> str | None:
    """Extract an auth token from an ``Authorization: Bearer <token>`` header."""
    if not value:
        return None
    trimmed = value.strip()
    lower = trimmed.lower()
    if lower.startswith("bearer "):
        token = trimmed[7:].strip()
        return token or None
    return trimmed or None


def _header_value(headers: dict[str, str], wanted: str) -> str:
    """Case-insensitive lookup in a headers dict."""
    for name, value in headers.items():
        if name.lower() == wanted.lower():
            return value
    return ""


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def _resolve_model(model: str | None) -> str:
    """Resolve a model string for the API call.

    - ``None`` → default Sonnet
    - Otherwise, normalize (strip ``[1m]`` suffix, whitespace)
    """
    if model is None or not model.strip():
        from hare.utils.model.model_full import get_default_sonnet_model

        return get_default_sonnet_model()

    from hare.utils.model import normalize_model_string_for_api

    return normalize_model_string_for_api(model)


# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------


def _assemble_system_prompt(
    system: str | list[dict[str, Any]] | None,
    skip_prefix: bool,
) -> str | list[dict[str, Any]] | None:
    """Assemble the final system prompt.

    When *skip_prefix* is ``False`` (the default), prepend the standard
    system-prompt prefix to string-typed system prompts.  List-typed and
    ``None`` system prompts are passed through unchanged.
    """
    if system is None:
        return None
    if isinstance(system, list):
        return system
    if skip_prefix or not _DEFAULT_SYSTEM_PROMPT_PREFIX:
        return system
    # Prepend the default prefix
    return _DEFAULT_SYSTEM_PROMPT_PREFIX + "\n\n" + system


# ---------------------------------------------------------------------------
# API call internals
# ---------------------------------------------------------------------------


def _classify_http_error(error: Exception) -> SideQueryError:
    """Map a raw exception from the Anthropic SDK to a typed SideQueryError.

    Inspects HTTP status codes and error body to produce a meaningful
    classification.
    """
    status = getattr(error, "status_code", None) or getattr(error, "status", None)

    # Try to extract response body for richer error messages
    response_body: dict[str, Any] | None = None
    for attr in ("response_body", "body", "response"):
        raw = getattr(error, attr, None)
        if raw is None:
            continue
        if isinstance(raw, dict):
            response_body = raw
            break
        if hasattr(raw, "json"):
            try:
                response_body = raw.json()
                break
            except Exception:
                pass

    # Inspect structured error body if available
    if response_body and isinstance(response_body, dict):
        err_type = str(response_body.get("type", ""))
        err_msg = str(
            response_body.get("error", {}).get("message", "")
            if isinstance(response_body.get("error"), dict)
            else response_body.get("message", "")
        )
        inner_status = response_body.get("status")

        if inner_status == 401 or err_type == "authentication_error":
            return SideQueryAuthError(
                err_msg or "Authentication failed. Check your API credentials."
            )
        if inner_status == 404 or err_type == "not_found_error":
            return SideQueryNotFoundError(err_msg or "Model not found")
        if inner_status == 429 or err_type == "rate_limit_error":
            retry_after = _extract_retry_after(error)
            return SideQueryRateLimitError(
                err_msg or "Rate limited. Wait and retry.",
                retry_after=retry_after,
            )

    # Fallback: classify by HTTP status
    if isinstance(status, int):
        if status == 401:
            return SideQueryAuthError("Authentication failed.")
        if status == 404:
            return SideQueryNotFoundError("Model not found.")
        if status == 429:
            return SideQueryRateLimitError(
                "Rate limited.", retry_after=_extract_retry_after(error)
            )
        if status in (502, 503):
            return SideQueryConnectionError(
                f"Service temporarily unavailable (HTTP {status})."
            )
        if status == 529:
            return SideQueryConnectionError(
                "Service is overloaded. Try again later."
            )

    # Name-based heuristic
    cls_name = type(error).__name__.lower()
    msg_lower = str(error).lower()

    if any(kw in cls_name for kw in ("auth", "unauthorized", "forbidden")):
        return SideQueryAuthError(str(error))
    if any(kw in cls_name for kw in ("notfound", "not_found")):
        return SideQueryNotFoundError(str(error))
    if any(kw in cls_name for kw in ("connection", "connect", "network", "timeout")):
        return SideQueryConnectionError(str(error))
    if "rate" in cls_name and "limit" in cls_name:
        return SideQueryRateLimitError(str(error))

    if "unauthorized" in msg_lower or "forbidden" in msg_lower:
        return SideQueryAuthError(str(error))
    if "not found" in msg_lower:
        return SideQueryNotFoundError(str(error))
    if "connection" in msg_lower or "network" in msg_lower or "timeout" in msg_lower:
        return SideQueryConnectionError(str(error))
    if "rate limit" in msg_lower or "too many requests" in msg_lower:
        return SideQueryRateLimitError(str(error))

    return SideQueryAPIError(status=status, message=str(error))


def _extract_status_code(error: Exception) -> int:
    """Extract HTTP status code from an exception."""
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if isinstance(status, int):
        return status
    # Heuristic from error message
    msg = str(error).lower()
    for code in (529, 503, 502, 429, 404, 401):
        if str(code) in msg:
            return code
    return 0


def _extract_retry_after(error: Exception) -> float | None:
    """Extract a ``retry-after`` hint (seconds) from an error."""
    retry_after = getattr(error, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    headers = getattr(error, "headers", None)
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter("retry-after")
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
    return None


def _is_retryable_status(status: int) -> bool:
    """Return ``True`` for transient / retryable HTTP statuses."""
    return status in _RETRYABLE_STATUSES


def _compute_backoff(attempt: int, retry_after: float | None = None) -> float:
    """Compute backoff duration for retry attempt *attempt* (0-indexed).

    - If *retry_after* is provided, use it directly.
    - Otherwise, exponential backoff with jitter: ``min(2^attempt, cap) * jitter``.
    """
    if retry_after is not None and retry_after > 0:
        return retry_after
    base = min(_BASE_RETRY_DELAY_S * (2 ** attempt), _MAX_BACKOFF_S)
    # Add 0-25% jitter to avoid thundering-herd
    jitter = 1.0 + (hash(str(time.monotonic())) % 250) / 1000.0
    return base * jitter


async def _execute_api_call(
    client: Any, kwargs: dict[str, Any]
) -> SideQueryResponse:
    """Make a single non-streaming API call and parse the result."""
    response = await client.messages.create(**kwargs)

    content_blocks: list[dict[str, Any]] = []
    for block in response.content:
        block_type = getattr(block, "type", "text")
        if block_type == "text":
            content_blocks.append({"type": "text", "text": block.text})
        elif block_type == "tool_use":
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        elif block_type == "thinking":
            thinking_block: dict[str, Any] = {
                "type": "thinking",
                "thinking": getattr(block, "thinking", ""),
                "signature": getattr(block, "signature", ""),
            }
            citations = getattr(block, "citations", None)
            if citations is not None:
                thinking_block["citations"] = citations
            content_blocks.append(thinking_block)
        elif block_type == "redacted_thinking":
            content_blocks.append(
                {
                    "type": "redacted_thinking",
                    "data": getattr(block, "data", ""),
                }
            )
        else:
            # Unknown block type — preserve as-is
            try:
                if hasattr(block, "model_dump"):
                    content_blocks.append(block.model_dump())
                elif hasattr(block, "__dict__"):
                    content_blocks.append(
                        {k: v for k, v in vars(block).items() if not k.startswith("_")}
                    )
                else:
                    content_blocks.append({"type": str(block_type), "text": str(block)})
            except Exception:
                content_blocks.append({"type": str(block_type)})

    usage_dict: dict[str, int] = {}
    if hasattr(response, "usage") and response.usage is not None:
        usage_dict["input_tokens"] = getattr(response.usage, "input_tokens", 0)
        usage_dict["output_tokens"] = getattr(response.usage, "output_tokens", 0)
        # Cache-related tokens (may not be present on all responses)
        for attr in (
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            val = getattr(response.usage, attr, None)
            if isinstance(val, int):
                usage_dict[attr] = val

    return SideQueryResponse(
        content=content_blocks,
        model=getattr(response, "model", ""),
        stop_reason=getattr(response, "stop_reason", "end_turn") or "end_turn",
        usage=usage_dict,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def side_query(
    opts: SideQueryOptions | None = None,
    **kwargs: Any,
) -> SideQueryResponse:
    """Execute a lightweight side query to the Anthropic API.

    This is the primary entry point.  Accepts either a :class:`SideQueryOptions`
    instance or keyword arguments matching its fields.

    Parameters
    ----------
    opts:
        A fully-populated :class:`SideQueryOptions` instance.  If provided,
        *kwargs* are merged on top (kwargs win).  If ``None``, *kwargs*
        alone are used.
    **kwargs:
        Individual options matching :class:`SideQueryOptions` fields.

    Returns
    -------
    SideQueryResponse:
        The parsed API response with ``content``, ``model``, ``stop_reason``,
        and ``usage`` fields.  Supports both attribute and dict-like access
        for backward compatibility.

    Raises
    ------
    SideQueryValidationError:
        If messages are empty or required fields are missing.
    SideQueryAbortedError:
        If the abort signal fires before completion.
    SideQueryAuthError:
        If API credentials are missing or invalid (HTTP 401).
    SideQueryNotFoundError:
        If the model is not found (HTTP 404).
    SideQueryRateLimitError:
        If rate-limited after exhausting all retries (HTTP 429).
    SideQueryConnectionError:
        On network errors, timeouts, or service-unavailable responses
        (HTTP 502 / 503 / 529) after exhausting retries.
    SideQueryAPIError:
        For other API-level errors.

    Examples
    --------
    Using a dataclass (caller: agentic_session_search):

    >>> response = await side_query(SideQueryOptions(
    ...     model="claude-sonnet-4-20250514",
    ...     system="You are a helpful assistant.",
    ...     messages=[{"role": "user", "content": "Hello"}],
    ...     query_source="session_search",
    ... ))
    >>> response["content"]
    [{'type': 'text', 'text': 'Hello! How can I help?'}]

    Using keyword arguments (caller: find_relevant_memories):

    >>> response = await side_query(
    ...     model=None,   # uses default Sonnet
    ...     system="Select memories...",
    ...     skip_system_prompt_prefix=True,
    ...     messages=[{"role": "user", "content": "..."}],
    ...     max_tokens=256,
    ...     output_format={"type": "json_schema", "schema": {...}},
    ...     signal=abort_signal,
    ...     query_source="memdir_relevance",
    ... )
    >>> response.get("content")
    [{'type': 'text', 'text': '{"selected_memories": [...]}'}]
    """
    # ------------------------------------------------------------------
    # 1. Merge opts + kwargs into a unified options dict
    # ------------------------------------------------------------------
    if opts is not None and isinstance(opts, SideQueryOptions):
        merged: dict[str, Any] = {
            "model": opts.model,
            "messages": opts.messages,
            "query_source": opts.query_source,
            "system": opts.system,
            "tools": opts.tools,
            "tool_choice": opts.tool_choice,
            "output_format": opts.output_format,
            "max_tokens": opts.max_tokens,
            "max_retries": opts.max_retries,
            "signal": opts.signal,
            "skip_system_prompt_prefix": opts.skip_system_prompt_prefix,
            "temperature": opts.temperature,
            "thinking": opts.thinking,
            "stop_sequences": opts.stop_sequences,
        }
        merged.update(kwargs)
    else:
        merged = dict(kwargs)

    # ------------------------------------------------------------------
    # 2. Check for injected implementation
    # ------------------------------------------------------------------
    if _SIDE_QUERY_FN is not None:
        try:
            result = _SIDE_QUERY_FN(merged)
            if isinstance(result, SideQueryResponse):
                return result
            if isinstance(result, dict):
                return SideQueryResponse(
                    content=result.get("content", []),
                    model=result.get("model", ""),
                    stop_reason=result.get("stop_reason", "end_turn"),
                    usage=result.get("usage", {}),
                )
            return SideQueryResponse()
        except SideQueryError:
            raise
        except Exception as exc:
            log_for_debugging(
                f"[side_query] Injected fn failed: {error_message(exc)}",
                level="error",
            )
            raise SideQueryAPIError(status=None, message=str(exc)) from exc

    # ------------------------------------------------------------------
    # 3. Validate required fields
    # ------------------------------------------------------------------
    messages: list[dict[str, Any]] = merged.get("messages", [])
    if not messages:
        raise SideQueryValidationError(
            "side_query requires at least one message in 'messages'."
        )

    # ------------------------------------------------------------------
    # 4. Resolve options with defaults
    # ------------------------------------------------------------------
    model = _resolve_model(merged.get("model"))
    system_raw: str | list[dict[str, Any]] | None = merged.get("system")
    skip_prefix: bool = bool(merged.get("skip_system_prompt_prefix", False))
    system = _assemble_system_prompt(system_raw, skip_prefix)
    max_tokens: int = merged.get("max_tokens", 1024)
    max_retries: int = merged.get("max_retries", _DEFAULT_MAX_RETRIES)
    signal = merged.get("signal")
    query_source: str = merged.get("query_source", "side_query")
    tools: list[Any] | None = merged.get("tools")
    tool_choice: Any = merged.get("tool_choice")
    output_format: Any = merged.get("output_format")
    temperature: float | None = merged.get("temperature")
    thinking: int | bool | None = merged.get("thinking")
    stop_sequences: list[str] | None = merged.get("stop_sequences")

    # ------------------------------------------------------------------
    # 5. Check abort signal before doing any work
    # ------------------------------------------------------------------
    if _is_signal_aborted(signal):
        raise SideQueryAbortedError("Side query aborted before execution.")

    # ------------------------------------------------------------------
    # 6. Build the API request parameters
    # ------------------------------------------------------------------
    request_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }

    if system is not None:
        request_kwargs["system"] = system

    if tools:
        # Accept dict schemas directly or objects with input_schema()
        tool_schemas: list[dict[str, Any]] = []
        for t in tools:
            if isinstance(t, dict):
                tool_schemas.append(t)
            elif callable(getattr(t, "input_schema", None)):
                tool_schemas.append(
                    {
                        "name": getattr(t, "name", "tool"),
                        "description": getattr(t, "description", ""),
                        "input_schema": t.input_schema(),
                    }
                )
            else:
                # Best-effort: try to serialize
                try:
                    tool_schemas.append(
                        {"name": getattr(t, "name", "tool"), "input_schema": {}}
                    )
                except Exception:
                    pass
        if tool_schemas:
            request_kwargs["tools"] = tool_schemas

    if tool_choice is not None:
        request_kwargs["tool_choice"] = tool_choice

    if output_format is not None:
        # Structured-output: set the beta header
        betas: list[str] = request_kwargs.get("betas", [])
        if "output-128k-2025-02-19" not in betas:
            betas.append("output-128k-2025-02-19")
        request_kwargs["betas"] = betas

        # Anthropic SDK expects 'response_format' not 'output_format'
        if (
            isinstance(output_format, dict)
            and output_format.get("type") == "json_schema"
        ):
            # Convert to SDK-compatible response_format
            schema_obj = output_format.get("schema")
            name = "structured_output"
            if isinstance(schema_obj, dict):
                name = schema_obj.get("name", "structured_output")
            request_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "schema": schema_obj.get("schema", schema_obj)
                    if isinstance(schema_obj, dict)
                    else schema_obj or {},
                    "strict": True,
                },
            }
        else:
            request_kwargs["output_format"] = output_format

    if temperature is not None:
        request_kwargs["temperature"] = temperature

    if thinking is not None:
        if isinstance(thinking, bool):
            if thinking:
                request_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}
            else:
                request_kwargs["thinking"] = {"type": "disabled"}
        elif isinstance(thinking, int) and thinking > 0:
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking,
            }

    if stop_sequences:
        request_kwargs["stop_sequences"] = stop_sequences

    # Don't stream — side queries are small and non-streaming is simpler
    request_kwargs["stream"] = False

    # ------------------------------------------------------------------
    # 7. Build the client
    # ------------------------------------------------------------------
    try:
        client = _get_anthropic_client()
    except SideQueryError:
        raise
    except ImportError as exc:
        raise SideQueryAPIError(
            status=None,
            message=str(exc),
        ) from exc
    except Exception as exc:
        raise SideQueryAPIError(
            status=None,
            message=f"Failed to initialize API client: {error_message(exc)}",
        ) from exc

    # ------------------------------------------------------------------
    # 8. Execute with retry logic
    # ------------------------------------------------------------------
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        # Check abort signal before each attempt
        if _is_signal_aborted(signal):
            raise SideQueryAbortedError("Side query aborted before retry.")

        try:
            result = await _wait_with_signal(
                _execute_api_call(client, request_kwargs),
                signal=signal,
                timeout_s=_DEFAULT_TIMEOUT_S,
            )

            # Log successful query for debugging
            log_for_debugging(
                f"[side_query] {query_source}: success "
                f"(model={model}, tokens={result.usage.get('input_tokens', 0)}/"
                f"{result.usage.get('output_tokens', 0)}, "
                f"attempt={attempt + 1}/{max_retries + 1})"
            )
            return result

        except SideQueryAbortedError:
            raise
        except SideQueryError as exc:
            # Typed errors — only retry on rate-limit / connection errors
            if isinstance(exc, SideQueryRateLimitError) and attempt < max_retries:
                delay = _compute_backoff(attempt, exc.retry_after)
                log_for_debugging(
                    f"[side_query] {query_source}: rate-limited, "
                    f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})",
                    level="warn",
                )
                await asyncio.sleep(delay)
                last_error = exc
                continue
            if isinstance(exc, SideQueryConnectionError) and attempt < max_retries:
                delay = _compute_backoff(attempt)
                log_for_debugging(
                    f"[side_query] {query_source}: connection error, "
                    f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})",
                    level="warn",
                )
                await asyncio.sleep(delay)
                last_error = exc
                continue
            raise

        except Exception as exc:
            # Raw exception — classify and decide whether to retry
            classified = _classify_http_error(exc)
            status = _extract_status_code(exc)

            if isinstance(classified, (SideQueryRateLimitError, SideQueryConnectionError)):
                if attempt < max_retries:
                    retry_after = (
                        classified.retry_after
                        if isinstance(classified, SideQueryRateLimitError)
                        else None
                    )
                    delay = _compute_backoff(attempt, retry_after)
                    log_for_debugging(
                        f"[side_query] {query_source}: {type(classified).__name__} "
                        f"(HTTP {status}), retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{max_retries + 1})",
                        level="warn",
                    )
                    await asyncio.sleep(delay)
                    last_error = classified
                    continue

            # Non-retryable or retries exhausted
            log_for_debugging(
                f"[side_query] {query_source}: {type(classified).__name__}: "
                f"{error_message(exc)}",
                level="error",
            )
            raise classified from exc

    # All retries exhausted
    if last_error is not None:
        raise last_error
    raise SideQueryAPIError(
        status=None, message="Side query failed after exhausting all retries."
    )


# ---------------------------------------------------------------------------
# Synchronous convenience wrapper
# ---------------------------------------------------------------------------


def side_query_sync(
    opts: SideQueryOptions | None = None,
    **kwargs: Any,
) -> SideQueryResponse:
    """Synchronous wrapper around :func:`side_query`.

    Runs the async function in a new event loop.  Useful for REPL, tests,
    and synchronous tool callbacks.

    Parameters
    ----------
    opts, **kwargs:
        Same as :func:`side_query`.

    Returns
    -------
    SideQueryResponse

    Raises
    ------
    Same as :func:`side_query`, plus ``RuntimeError`` if called from within
    a running event loop without ``nest_asyncio``.
    """
    coro = side_query(opts, **kwargs)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — use asyncio.run
        return asyncio.run(coro)

    # Loop is already running — try nest_asyncio, or create a new loop
    # in a thread (less safe but functional).
    try:
        import nest_asyncio  # type: ignore[import-untyped]

        nest_asyncio.apply(loop)
        return loop.run_until_complete(coro)
    except ImportError:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=_DEFAULT_TIMEOUT_S + 10)


# ---------------------------------------------------------------------------
# Utility: check if side_query is properly wired
# ---------------------------------------------------------------------------


def is_side_query_available() -> bool:
    """Return ``True`` if side queries can be executed.

    Checks for:
    - Injected implementation via :func:`set_side_query_fn`
    - API credentials in the environment
    - ``anthropic`` package availability
    """
    if _SIDE_QUERY_FN is not None:
        return True

    # Check for credentials
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or None
    if not auth_token:
        try:
            auth_token = _get_api_key_from_api_key_helper()
        except Exception:
            pass
    if api_key or auth_token:
        try:
            import anthropic  # noqa: F401

            return True
        except ImportError:
            return False

    return False


# ---------------------------------------------------------------------------
# Convenience: quick single-message query
# ---------------------------------------------------------------------------


async def quick_side_query(
    prompt: str,
    *,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int = 512,
    query_source: str = "quick_side_query",
    signal: Any = None,
    temperature: float | None = None,
) -> str:
    """Execute a side query with a single user message and return the text response.

    Convenience wrapper for the common case: send one prompt, get back
    the first text block's content as a string.

    Parameters
    ----------
    prompt:
        The user message text.
    model:
        Model string (default: Sonnet).
    system:
        Optional system prompt.
    max_tokens:
        Maximum output tokens.
    query_source:
        Label for debugging / telemetry.
    signal:
        Optional abort signal.
    temperature:
        Sampling temperature.

    Returns
    -------
    str:
        The text content of the first response block.  Empty string if the
        response has no text blocks.

    Raises
    ------
    Same as :func:`side_query`.
    """
    response = await side_query(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        system=system,
        max_tokens=max_tokens,
        query_source=query_source,
        signal=signal,
        temperature=temperature,
    )

    for block in response.content:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""
