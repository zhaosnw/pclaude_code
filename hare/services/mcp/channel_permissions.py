"""
Permission prompts relayed over MCP channel servers.

Port of: src/services/mcp/channelPermissions.ts

Mirrors BridgePermissionCallbacks — when CC hits a permission dialog, it ALSO
sends the prompt via active channels and races the reply against local UI,
bridge, hooks, and classifier. First resolver wins via claim().

Inbound is a structured event: the server parses the user reply and emits
notifications/claude/channel/permission with {request_id, behavior}. CC never
sees the reply as text — approval requires the server to deliberately emit
that specific event. Servers opt in by declaring
capabilities.experimental['claude/channel/permission'].

Reply format spec for channel servers:
  /^\\s*(y|yes|n|no)\\s+([a-km-z]{5})\\s*$/i
5 lowercase letters, no 'l'. Case-insensitive. No bare yes/no.

Expanded with real functional logic: permission request lifecycle, prompt
generation, reply parsing, race-to-first-resolver with timeouts, and
integration with the channel notification dispatch system.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, TypeVar

from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale
from hare.services.internal_logging import log_internal

logger = logging.getLogger(__name__)

T = TypeVar("T")  # used by filter_permission_relay_clients

PERMISSION_REPLY_RE = r"^\s*(y|yes|n|no)\s+([a-km-z]{5})\s*$"
ID_ALPHABET = "abcdefghijkmnopqrstuvwxyz"
ID_AVOID_SUBSTRINGS = (
    "fuck",
    "shit",
    "cunt",
    "cock",
    "dick",
    "twat",
    "piss",
    "crap",
    "bitch",
    "whore",
    "ass",
    "tit",
    "cum",
    "fag",
    "dyke",
    "nig",
    "kike",
    "rape",
    "nazi",
    "damn",
    "poo",
    "pee",
    "wank",
    "anus",
)

# Default timeout for channel permission prompts (30 s). Shorter than the
# bridge timeout — channels are async "also sent," not the primary path.
DEFAULT_CHANNEL_PERMISSION_TIMEOUT_S = 30.0

# Maximum pending requests before we start rejecting (bounds memory).
MAX_PENDING_PERMISSION_REQUESTS = 50

# TTL for expired pending entries (5 min); cleanup removes older entries.
PENDING_REQUEST_TTL_S = 300.0


def is_channel_permission_relay_enabled() -> bool:
    return bool(
        get_feature_value_cached_may_be_stale("tengu_harbor_permissions", False)
    )


@dataclass
class ChannelPermissionResponse:
    behavior: Literal["allow", "deny"]
    from_server: str


@dataclass
class ChannelPermissionRequest:
    """A pending permission prompt sent to channel servers for resolution.

    Key fields:
    - request_id: the full `toolu_...` tool-use ID used internally.
    - short_id: the 5-letter hash displayed to the user in the prompt.
    - tool_name: the name of the tool requesting permission.
    - input_preview: truncated JSON preview of the tool input.
    - created_at: monotonic time (time.monotonic()) when the request was created.
    - timeout_s: how long to wait for a channel reply before the request expires.
    - resolved: whether a resolver has already claimed this request.
    """

    request_id: str
    short_id: str
    tool_name: str
    tool_use_id: str
    server_name: str
    input_preview: str
    created_at: float = field(default_factory=time.monotonic)
    timeout_s: float = DEFAULT_CHANNEL_PERMISSION_TIMEOUT_S
    resolved: bool = False

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def expired(self) -> bool:
        return self.age_seconds > self.timeout_s


@dataclass
class PermissionDecision:
    """Result of a permission check, regardless of source."""

    behavior: Literal["allow", "deny"]
    from_source: str  # e.g. "channel:telegram", "user", "bridge", "hook", "classifier"
    request_id: str = ""
    reason: str = ""


# Compiled regex cache — PERMISSION_REPLY_RE is compiled once and reused.
# Case-insensitive matching per the spec: lowercase input at call site.
_permission_reply_re = re.compile(PERMISSION_REPLY_RE, re.IGNORECASE)


class ChannelPermissionCallbacks:
    """Registry for race-to-first-resolver channel permission prompts.

    Mirror of TS ChannelPermissionCallbacks type. Each pending request is
    stored by request_id (lowercased). The first resolver (Bridge, local UI,
    hooks, classifier, or channel) claims the request via resolve(); subsequent
    resolve() calls for the same request return False.
    """

    def __init__(self) -> None:
        self._pending: dict[str, Callable[[ChannelPermissionResponse], None]] = {}

    def on_response(
        self, request_id: str, handler: Callable[[ChannelPermissionResponse], None]
    ) -> Callable[[], None]:
        key = request_id.lower()
        self._pending[key] = handler

        def unsub() -> None:
            self._pending.pop(key, None)

        return unsub

    def resolve(
        self, request_id: str, behavior: Literal["allow", "deny"], from_server: str
    ) -> bool:
        key = request_id.lower()
        resolver = self._pending.pop(key, None)
        if not resolver:
            return False
        resolver(ChannelPermissionResponse(behavior=behavior, from_server=from_server))
        return True


def _fnv1a32(data: str) -> int:
    h = 0x811C9DC5
    for ch in data:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _hash_to_id(input_str: str) -> str:
    h = _fnv1a32(input_str)
    s = ""
    for _ in range(5):
        s += ID_ALPHABET[h % 25]
        h //= 25
    return s


def short_request_id(tool_use_id: str) -> str:
    candidate = _hash_to_id(tool_use_id)
    for salt in range(10):
        if not any(bad in candidate for bad in ID_AVOID_SUBSTRINGS):
            return candidate
        candidate = _hash_to_id(f"{tool_use_id}:{salt}")
    return candidate


def truncate_for_preview(input_obj: Any) -> str:
    try:
        s = json.dumps(input_obj, default=str)
    except Exception:
        return "(unserializable)"
    return s if len(s) <= 200 else s[:200] + "…"


def filter_permission_relay_clients(
    clients: list[T],
    is_in_allowlist: Callable[[str], bool],
) -> list[T]:
    out: list[T] = []
    for c in clients:
        if getattr(c, "type", None) != "connected":
            continue
        name = getattr(c, "name", "")
        if not is_in_allowlist(str(name)):
            continue
        cap = getattr(c, "capabilities", None)
        exp = getattr(cap, "experimental", None) if cap else None
        if isinstance(exp, dict):
            if (
                exp.get("hare/channel") is not None
                and exp.get("hare/channel/permission") is not None
            ):
                out.append(c)
    return out


# ---------------------------------------------------------------------------
# Reply parsing — used by channel notification dispatch to parse incoming
# user messages against the structured reply format.
# ---------------------------------------------------------------------------


def parse_permission_reply(text: str) -> tuple[str, str] | None:
    """Parse a user message against PERMISSION_REPLY_RE.

    Returns (behavior, short_id) on match, or None. Behavior is lowercased
    to "allow" (y/yes) or "deny" (n/no). short_id is the 5-letter hash.

    This is exported for channel servers that want to use the same regex;
    CC should NOT regex-match text itself — servers emit structured events.
    But it is available for reference and for use by notification handlers
    that receive raw text from channels.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    match = _permission_reply_re.match(text.strip())
    if not match:
        return None
    raw_behavior = match.group(1).lower()
    short_id = match.group(2).lower()
    behavior = "allow" if raw_behavior.startswith("y") else "deny"
    return behavior, short_id


def _make_permission_prompt_text(
    request: ChannelPermissionRequest,
    *,
    server_display_name: str | None = None,
) -> str:
    """Build the human-facing text sent via a channel for a permission prompt.

    Format example:
        [Claude Code]
        Allow "Write" on myserver?
        Input: {"file_path": "/tmp/d..."}
        Reply: yes tbxkq  or  no tbxkq
    """
    label = server_display_name or request.server_name
    lines = [
        f"[Claude Code on {label}]",
        f'Allow "{request.tool_name}"?',
    ]
    if request.input_preview:
        lines.append(f"Input: {request.input_preview}")
    lines.append(f"Reply: yes {request.short_id}  or  no {request.short_id}")
    return "\n".join(lines)


def generate_permission_prompt(
    request: ChannelPermissionRequest,
    *,
    server_display_name: str | None = None,
) -> dict[str, Any]:
    """Generate a channel permission prompt payload ready for dispatch.

    Returns a dict suitable for sending via channel notification:
        {
            "type": "permission_prompt",
            "request_id": <tool_use_id>,
            "short_id": <5-char hash>,
            "tool_name": <name>,
            "input_preview": <truncated JSON>,
            "text": <human-readable prompt>
        }
    """
    text = _make_permission_prompt_text(request, server_display_name=server_display_name)
    return {
        "type": "permission_prompt",
        "request_id": request.request_id,
        "tool_use_id": request.tool_use_id,
        "short_id": request.short_id,
        "tool_name": request.tool_name,
        "input_preview": request.input_preview,
        "text": text,
    }


# ---------------------------------------------------------------------------
# ChannelPermissionManager — orchestrates the lifecycle of channel-based
# permission prompts, including dispatch, timeout, and cleanup.
# ---------------------------------------------------------------------------


class ChannelPermissionManager:
    """Orchestrates channel permission prompt lifecycle.

    Manages a registry of pending ChannelPermissionRequest objects. When a
    permission prompt is needed, request_permission() fires prompts to all
    eligible channel servers and races their responses against the local UI /
    bridge / hooks / classifier (handled externally via
    ChannelPermissionCallbacks).

    The manager enforces:
    - A cap on total pending requests (MAX_PENDING_PERMISSION_REQUESTS).
    - Per-request timeouts (DEFAULT_CHANNEL_PERMISSION_TIMEOUT_S).
    - Periodic cleanup of expired entries (cleanup_expired).

    Integration point: callbacks from channel_notification.py invoke
    resolve() on the ChannelPermissionCallbacks instance, which triggers
    the resolver and marks the request as resolved.
    """

    def __init__(self, callbacks: ChannelPermissionCallbacks | None = None) -> None:
        self._pending: dict[str, ChannelPermissionRequest] = {}
        self._callbacks = callbacks or create_channel_permission_callbacks()

    @property
    def callbacks(self) -> ChannelPermissionCallbacks:
        return self._callbacks

    # ---- request lifecycle ------------------------------------------------

    async def request_permission(
        self,
        *,
        tool_use_id: str,
        tool_name: str,
        tool_input: Any,
        server_name: str,
        timeout_s: float = DEFAULT_CHANNEL_PERMISSION_TIMEOUT_S,
    ) -> PermissionDecision:
        """Create a permission request and race channel servers for a reply.

        Returns a PermissionDecision. If no channel server responds before
        the timeout, returns a default "allow" decision (channels are
        advisory, not the primary gate — the local UI / bridge / hooks are).
        """
        # Enforce pending cap — if too many, return a safe default.
        if len(self._pending) >= MAX_PENDING_PERMISSION_REQUESTS:
            logger.warning(
                "ChannelPermissionManager: pending limit reached (%d), "
                "rejecting new request for tool=%s",
                MAX_PENDING_PERMISSION_REQUESTS,
                tool_name,
            )
            log_internal("channel_permission_cap_reached", {
                "tool_name": tool_name,
                "server_name": server_name,
                "pending_count": len(self._pending),
            })
            return PermissionDecision(
                behavior="allow",
                from_source="channel:capacity_exceeded",
                reason="Too many pending permission requests",
            )

        short_id = short_request_id(tool_use_id)
        input_preview = truncate_for_preview(tool_input)

        req = ChannelPermissionRequest(
            request_id=tool_use_id,
            short_id=short_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            server_name=server_name,
            input_preview=input_preview,
            created_at=time.monotonic(),
            timeout_s=timeout_s,
        )
        self._pending[tool_use_id] = req

        # Set up the resolver callback for this request. When any channel
        # server emits a structured permission reply, channel_notification.py
        # calls self._callbacks.resolve(), which triggers this future.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ChannelPermissionResponse] = loop.create_future()

        def _on_response(response: ChannelPermissionResponse) -> None:
            if not future.done():
                future.set_result(response)

        unsub = self._callbacks.on_response(tool_use_id, _on_response)

        try:
            # Race: wait for a channel reply, but only up to the timeout.
            # If no channel replies in time, we return a neutral "allow"
            # because channels are advisory-only — the real gate is the
            # bridge / local UI / hooks / classifier.
            response = await asyncio.wait_for(future, timeout=timeout_s)

            req.resolved = True
            log_internal("channel_permission_resolved", {
                "request_id": tool_use_id,
                "short_id": short_id,
                "behavior": response.behavior,
                "from_server": response.from_server,
                "tool_name": tool_name,
            })
            return PermissionDecision(
                behavior=response.behavior,
                from_source=f"channel:{response.from_server}",
                request_id=tool_use_id,
            )
        except asyncio.TimeoutError:
            logger.debug(
                "Channel permission request %s timed out after %.1fs",
                short_id,
                timeout_s,
            )
            log_internal("channel_permission_timeout", {
                "request_id": tool_use_id,
                "short_id": short_id,
                "tool_name": tool_name,
                "timeout_s": timeout_s,
            })
            return PermissionDecision(
                behavior="allow",
                from_source="channel:timeout",
                request_id=tool_use_id,
                reason=f"No channel reply within {timeout_s:.0f}s",
            )
        except Exception as exc:
            logger.error(
                "Channel permission request %s failed: %s",
                short_id,
                exc,
            )
            log_internal("channel_permission_error", {
                "request_id": tool_use_id,
                "short_id": short_id,
                "error": str(exc),
            })
            return PermissionDecision(
                behavior="allow",
                from_source="channel:error",
                request_id=tool_use_id,
                reason=str(exc),
            )
        finally:
            unsub()
            # Remove resolved or errored request from pending map.
            self._pending.pop(tool_use_id, None)

    # ---- inspection -------------------------------------------------------

    def has_pending(self, request_id: str) -> bool:
        """Check whether a request_id is currently pending."""
        return request_id in self._pending

    def get_pending(self, request_id: str) -> ChannelPermissionRequest | None:
        """Return a pending request by request_id, or None."""
        return self._pending.get(request_id)

    def list_pending(self) -> list[ChannelPermissionRequest]:
        """Return all currently pending permission requests."""
        return list(self._pending.values())

    def pending_count(self) -> int:
        """Return the number of pending permission requests."""
        return len(self._pending)

    # ---- cancellation and cleanup -----------------------------------------

    def cancel_pending(self, request_id: str) -> bool:
        """Cancel a pending permission request without resolving it.

        Returns True if a request was removed, False if it was not found.
        """
        if request_id in self._pending:
            del self._pending[request_id]
            return True
        return False

    def cleanup_expired(self, *, max_age_s: float | None = None) -> int:
        """Remove expired pending requests. Returns count of removed entries.

        Args:
            max_age_s: max age in seconds before removal (default PENDING_REQUEST_TTL_S).
        """
        ttl = max_age_s if max_age_s is not None else PENDING_REQUEST_TTL_S
        now = time.monotonic()
        expired = [
            rid
            for rid, req in self._pending.items()
            if (now - req.created_at) > ttl
        ]
        for rid in expired:
            del self._pending[rid]
        if expired:
            logger.debug(
                "ChannelPermissionManager: cleaned up %d expired requests",
                len(expired),
            )
        return len(expired)


# ---------------------------------------------------------------------------
# Top-level convenience: check whether channel permission relay is possible
# for a set of channel clients, and broadcast a prompt to all eligible ones.
# ---------------------------------------------------------------------------


async def check_channel_operation_permission(
    *,
    tool_use_id: str,
    tool_name: str,
    tool_input: Any,
    server_name: str,
    channel_clients: list[Any],
    is_in_allowlist: Callable[[str], bool],
    manager: ChannelPermissionManager | None = None,
    timeout_s: float = DEFAULT_CHANNEL_PERMISSION_TIMEOUT_S,
) -> PermissionDecision:
    """Check if a channel operation is permitted by racing all eligible channels.

    Filters channel_clients for permission-relay-capable servers, creates a
    permission request, and races the first channel reply. If no eligible
    channel servers exist, returns a neutral "allow" immediately.

    This is the main entry point for permission checks integrated with the
    channel relay subsystem. Callers should already have verified that
    is_channel_permission_relay_enabled() is True.

    Args:
        tool_use_id: the `toolu_...` ID for dedup.
        tool_name: e.g. "Write", "Bash".
        tool_input: the tool arguments dict.
        server_name: identifier for the MCP server that requested the tool.
        channel_clients: list of connected MCP channel client objects.
        is_in_allowlist: callable that returns True for allowlisted channel names.
        manager: optional pre-existing ChannelPermissionManager; created if None.
        timeout_s: timeout for waiting on a channel reply.

    Returns:
        PermissionDecision with the first-arriving channel resolution, or
        a neutral "allow" if no channels are eligible / no reply arrives.
    """
    eligible = filter_permission_relay_clients(channel_clients, is_in_allowlist)
    if not eligible:
        logger.debug(
            "check_channel_operation_permission: no eligible channel clients "
            "for tool=%s server=%s",
            tool_name,
            server_name,
        )
        return PermissionDecision(
            behavior="allow",
            from_source="channel:none_eligible",
            request_id=tool_use_id,
            reason="No eligible channel permission relay clients",
        )

    mgr = manager or ChannelPermissionManager()

    # Broadcast the prompt payload to each eligible channel server.
    # Each server receives the structured prompt and is expected to
    # relay it to the user; when the user replies, the server emits
    # notifications/claude/channel/permission.
    _req = ChannelPermissionRequest(
        request_id=tool_use_id,
        short_id=short_request_id(tool_use_id),
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        server_name=server_name,
        input_preview=truncate_for_preview(tool_input),
        timeout_s=timeout_s,
    )
    prompt_payload = generate_permission_prompt(_req)

    for client in eligible:
        client_name = getattr(client, "name", "unknown")
        try:
            # Fire-and-forget — each channel server gets the prompt.
            # The structured reply will come back via the notification
            # path and be routed to mgr._callbacks.resolve().
            if hasattr(client, "send_notification"):
                await client.send_notification(
                    "notifications/claude/channel/permission",
                    prompt_payload,
                )
        except Exception as exc:
            logger.warning(
                "Failed to send permission prompt to channel %s: %s",
                client_name,
                exc,
            )

    # Race channel replies.
    return await mgr.request_permission(
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        tool_input=tool_input,
        server_name=server_name,
        timeout_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# Factory — TS parity
# ---------------------------------------------------------------------------


def create_channel_permission_callbacks() -> ChannelPermissionCallbacks:
    """Create a ChannelPermissionCallbacks instance (TS parity factory)."""
    return ChannelPermissionCallbacks()
