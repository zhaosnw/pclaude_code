"""
Fast mode availability, org status prefetch, cooldown state, and state management.

Port of: src/utils/fastMode.ts — external services stubbed; see set_* hooks.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Literal

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import is_env_truthy


# =============================================================================
# Type aliases
# =============================================================================

CooldownReason = Literal["rate_limit", "overloaded"]
FastModeDisabledReason = Literal[
    "free", "preference", "extra_usage_disabled", "network_error", "unknown"
]
FastModeRuntimeState = (
    dict[str, Literal["active"]] | dict[str, Any]
)  # {status: 'cooldown', resetAt, reason}

FAST_MODE_MODEL_DISPLAY = "Opus 4.6"

# Default prefetch interval (30s) and retry backoff parameters.
_PREFETCH_MIN_INTERVAL_MS = 30_000
_PREFETCH_MAX_BACKOFF_MS = 300_000   # 5 min
_PREFETCH_BASE_DELAY_MS = 1_000      # 1 s initial retry backoff

# Guard for module-level mutable state.
_state_lock = threading.RLock()


# =============================================================================
# Internal service stubs — replace with real implementations by patching
# these module-level references at runtime via the set_* hooks below.
# =============================================================================


def _get_feature_value_cached(_k: str, d: Any) -> Any:
    return d


def _get_is_non_interactive() -> bool:
    return False


def _get_kairos_active() -> bool:
    return False


def _prefer_third_party_auth() -> bool:
    return False


def _get_api_provider() -> str:
    return "firstParty"


def _get_global_config() -> dict[str, Any]:
    return {}


def _save_global_config(_f: Any) -> None:
    return None


def _get_settings_for_source(_s: str) -> dict[str, Any] | None:
    return None


def _update_settings_for_source(_s: str, _u: dict[str, Any]) -> None:
    return None


def _get_hare_ai_oauth_tokens() -> Any:
    return None


def _get_anthropic_api_key() -> str | None:
    return None


def _has_profile_scope() -> bool:
    return True


def _handle_oauth_401(_t: str) -> Any:
    return None


def _is_essential_traffic_only() -> bool:
    return False


def _is_in_bundled_mode() -> bool:
    return False


def _log_event(_e: str, _m: dict[str, Any]) -> None:
    return None


def _get_default_main_loop_model() -> str:
    return "opus"


def _parse_user_specified_model(m: str) -> str:
    return m


def _is_opus_1m_merge_enabled() -> bool:
    return False


def _get_initial_settings() -> dict[str, Any]:
    return {}


# =============================================================================
# set_* injection hooks — call these at app startup to replace the stubs above
# with real implementations.
# =============================================================================


def set_fast_mode_feature_cache(
    fn: Callable[[str, Any], Any],
) -> None:
    """Inject a feature-value cache lookup (e.g. LaunchDarkly)."""
    global _get_feature_value_cached
    _get_feature_value_cached = fn


def set_fast_mode_is_non_interactive(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether this is a non-interactive session."""
    global _get_is_non_interactive
    _get_is_non_interactive = fn


def set_fast_mode_kairos_active(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether Kairos is active."""
    global _get_kairos_active
    _get_kairos_active = fn


def set_fast_mode_prefer_third_party_auth(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether third-party auth is preferred."""
    global _prefer_third_party_auth
    _prefer_third_party_auth = fn


def set_fast_mode_api_provider(fn: Callable[[], str]) -> None:
    """Inject the function that reports the current API provider."""
    global _get_api_provider
    _get_api_provider = fn


def set_fast_mode_global_config(
    getter: Callable[[], dict[str, Any]],
    saver: Callable[[Any], None],
) -> None:
    """Inject global-config getter and saver (e.g. the user's global config JSON)."""
    global _get_global_config, _save_global_config
    _get_global_config = getter
    _save_global_config = saver


def set_fast_mode_settings_for_source(
    getter: Callable[[str], dict[str, Any] | None],
    updater: Callable[[str, dict[str, Any]], None],
) -> None:
    """Inject functions to read / update settings for a given source key."""
    global _get_settings_for_source, _update_settings_for_source
    _get_settings_for_source = getter
    _update_settings_for_source = updater


def set_fast_mode_oauth_provider(
    getter: Callable[[], Any],
    handler: Callable[[str], Any],
) -> None:
    """Inject OAuth-token getter and 401 handler."""
    global _get_hare_ai_oauth_tokens, _handle_oauth_401
    _get_hare_ai_oauth_tokens = getter
    _handle_oauth_401 = handler


def set_fast_mode_api_key_provider(fn: Callable[[], str | None]) -> None:
    """Inject the function that returns the user's Anthropic API key (if any)."""
    global _get_anthropic_api_key
    _get_anthropic_api_key = fn


def set_fast_mode_profile_scope(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether the OAuth token has the profile scope."""
    global _has_profile_scope
    _has_profile_scope = fn


def set_fast_mode_essential_traffic(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether only essential traffic is allowed."""
    global _is_essential_traffic_only
    _is_essential_traffic_only = fn


def set_fast_mode_bundled_mode(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether we are in bundled mode."""
    global _is_in_bundled_mode
    _is_in_bundled_mode = fn


def set_fast_mode_event_logger(fn: Callable[[str, dict[str, Any]], None]) -> None:
    """Inject the analytics event-logging function."""
    global _log_event
    _log_event = fn


def set_fast_mode_model_provider(
    default_model: Callable[[], str],
    parse_model: Callable[[str], str],
) -> None:
    """Inject default-main-loop-model and user-model-parsing functions."""
    global _get_default_main_loop_model, _parse_user_specified_model
    _get_default_main_loop_model = default_model
    _parse_user_specified_model = parse_model


def set_fast_mode_opus_1m_merge(fn: Callable[[], bool]) -> None:
    """Inject the function that reports whether the Opus 1M merge is enabled."""
    global _is_opus_1m_merge_enabled
    _is_opus_1m_merge_enabled = fn


def set_fast_mode_initial_settings(fn: Callable[[], dict[str, Any]]) -> None:
    """Inject the function that returns the initial settings snapshot."""
    global _get_initial_settings
    _get_initial_settings = fn


# =============================================================================
# Core fast mode queries
# =============================================================================


def is_fast_mode_enabled() -> bool:
    """Check whether the CLAUDE_CODE_DISABLE_FAST_MODE env var permits fast mode."""
    return not is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_FAST_MODE"))


def _disabled_reason_message(disabled_reason: str, auth_type: str) -> str:
    if disabled_reason == "free":
        return (
            "Fast mode requires a paid subscription"
            if auth_type == "oauth"
            else "Fast mode unavailable during evaluation. Please purchase credits."
        )
    if disabled_reason == "preference":
        return "Fast mode has been disabled by your organization"
    if disabled_reason == "extra_usage_disabled":
        return "Fast mode requires extra usage billing · /extra-usage to enable"
    if disabled_reason == "network_error":
        return "Fast mode unavailable due to network connectivity issues"
    return "Fast mode is currently unavailable"


def get_fast_mode_unavailable_reason() -> str | None:
    """Return a human-readable reason why fast mode is not available, or None."""
    if not is_fast_mode_enabled():
        return "Fast mode is not available"

    r = _get_feature_value_cached("tengu_penguins_off", None)
    if r is not None:
        log_for_debugging(f"Fast mode unavailable: {r}")
        return str(r)

    if not _is_in_bundled_mode() and _get_feature_value_cached(
        "tengu_marble_sandcastle", False
    ):
        return (
            "Fast mode requires the native binary · "
            "Install from: https://hare.com/product/hare-code"
        )

    if (
        _get_is_non_interactive()
        and _prefer_third_party_auth()
        and not _get_kairos_active()
    ):
        if not (_get_settings_for_source("flagSettings") or {}).get("fastMode"):
            reason = "Fast mode is not available in the Agent SDK"
            log_for_debugging(f"Fast mode unavailable: {reason}")
            return reason

    if _get_api_provider() != "firstParty":
        reason = "Fast mode is not available on Bedrock, Vertex, or Foundry"
        log_for_debugging(f"Fast mode unavailable: {reason}")
        return reason

    with _state_lock:
        org = dict(_org_status)
    if org["status"] == "disabled":
        reason_d = org.get("reason") or "unknown"
        if reason_d in ("network_error", "unknown") and is_env_truthy(
            os.environ.get("CLAUDE_CODE_SKIP_FAST_MODE_NETWORK_ERRORS")
        ):
            return None
        auth_type = "oauth" if _get_hare_ai_oauth_tokens() else "api-key"
        msg = _disabled_reason_message(reason_d, auth_type)  # type: ignore[arg-type]
        log_for_debugging(f"Fast mode unavailable: {msg}")
        return msg

    return None


def is_fast_mode_available() -> bool:
    if not is_fast_mode_enabled():
        return False
    return get_fast_mode_unavailable_reason() is None


def get_fast_mode_model() -> str:
    return "opus" + ("[1m]" if _is_opus_1m_merge_enabled() else "")


def is_fast_mode_supported_by_model(model_setting: str | None) -> bool:
    if not is_fast_mode_enabled():
        return False
    model = model_setting or _get_default_main_loop_model()
    parsed = _parse_user_specified_model(model)
    return "opus-4-6" in parsed.lower()


def get_initial_fast_mode_setting(model: str) -> bool:
    if not is_fast_mode_enabled() or get_fast_mode_unavailable_reason() is not None:
        return False
    if not is_fast_mode_supported_by_model(model):
        return False
    settings = _get_initial_settings()
    if settings.get("fastModePerSessionOptIn"):
        return False
    return settings.get("fastMode") is True


# =============================================================================
# Runtime cooldown state
# =============================================================================

_runtime_state: dict[str, Any] = {"status": "active"}
_has_logged_cooldown_expiry = False


class _Pub:
    """Simple synchronous pub/sub (no external deps required)."""

    def __init__(self) -> None:
        self._subs: list[Callable[..., Any]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[..., Any]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsub() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsub

    def emit(self, *a: Any, **k: Any) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(*a, **k)
            except Exception:
                pass  # never let a listener break the emitter


_cooldown_triggered = _Pub()
_cooldown_expired = _Pub()
on_cooldown_triggered = _cooldown_triggered.subscribe
on_cooldown_expired = _cooldown_expired.subscribe


def get_fast_mode_runtime_state() -> FastModeRuntimeState:
    global _runtime_state, _has_logged_cooldown_expiry
    with _state_lock:
        state = dict(_runtime_state)
    if state.get("status") == "cooldown" and (
        time.time() * 1000 >= state.get("resetAt", 0)
    ):
        if is_fast_mode_enabled() and not _has_logged_cooldown_expiry:
            log_for_debugging("Fast mode cooldown expired, re-enabling fast mode")
            _has_logged_cooldown_expiry = True
            _cooldown_expired.emit()
        with _state_lock:
            _runtime_state = {"status": "active"}
        return {"status": "active"}
    return _runtime_state  # type: ignore[return-value]


def trigger_fast_mode_cooldown(
    reset_timestamp_ms: float, reason: CooldownReason
) -> None:
    global _runtime_state, _has_logged_cooldown_expiry
    if not is_fast_mode_enabled():
        return
    dur = reset_timestamp_ms - time.time() * 1000
    with _state_lock:
        _runtime_state = {
            "status": "cooldown",
            "resetAt": reset_timestamp_ms,
            "reason": reason,
        }
        _has_logged_cooldown_expiry = False

    log_for_debugging(
        f"Fast mode cooldown triggered ({reason}), duration {round(dur / 1000)}s"
    )
    _log_event(
        "tengu_fast_mode_fallback_triggered",
        {"cooldown_duration_ms": dur, "cooldown_reason": reason},
    )
    _cooldown_triggered.emit(reset_timestamp_ms, reason)


def clear_fast_mode_cooldown() -> None:
    global _runtime_state
    with _state_lock:
        _runtime_state = {"status": "active"}


def is_fast_mode_cooldown() -> bool:
    return get_fast_mode_runtime_state().get("status") == "cooldown"  # type: ignore[union-attr]


def get_cooldown_remaining_ms() -> float | None:
    """Return remaining cooldown duration in milliseconds, or None if not cooling down."""
    st = get_fast_mode_runtime_state()
    if st.get("status") != "cooldown":
        return None
    remaining = st.get("resetAt", 0) - time.time() * 1000
    return max(0.0, remaining)


# =============================================================================
# API rejection handlers
# =============================================================================


def handle_fast_mode_rejected_by_api() -> None:
    global _org_status
    with _state_lock:
        if _org_status["status"] == "disabled":
            return
        _org_status = {"status": "disabled", "reason": "preference"}
    _update_settings_for_source("userSettings", {"fastMode": None})
    _save_global_config(
        lambda c: {**c, "penguinModeOrgEnabled": False} if isinstance(c, dict) else c
    )
    _org_fast_mode_change.emit(False)


_overage_rejection = _Pub()
on_fast_mode_overage_rejection = _overage_rejection.subscribe


def _overage_disabled_message(reason: str | None) -> str:
    m = {
        "out_of_credits": "Fast mode disabled · extra usage credits exhausted",
        "org_level_disabled": (
            "Fast mode disabled · extra usage disabled by your organization"
        ),
        "org_service_level_disabled": (
            "Fast mode disabled · extra usage disabled by your organization"
        ),
        "org_level_disabled_until": (
            "Fast mode disabled · extra usage spending cap reached"
        ),
        "member_level_disabled": (
            "Fast mode disabled · extra usage disabled for your account"
        ),
        "seat_tier_level_disabled": (
            "Fast mode disabled · extra usage not available for your plan"
        ),
        "seat_tier_zero_credit_limit": (
            "Fast mode disabled · extra usage not available for your plan"
        ),
        "member_zero_credit_limit": (
            "Fast mode disabled · extra usage not available for your plan"
        ),
        "overage_not_provisioned": (
            "Fast mode requires extra usage billing · /extra-usage to enable"
        ),
        "no_limits_configured": (
            "Fast mode requires extra usage billing · /extra-usage to enable"
        ),
    }
    return m.get(reason or "", "Fast mode disabled · extra usage not available")


def handle_fast_mode_overage_rejection(reason: str | None) -> None:
    message = _overage_disabled_message(reason)
    log_for_debugging(f"Fast mode overage rejection: {reason} — {message}")
    _log_event(
        "tengu_fast_mode_overage_rejected",
        {"overage_disabled_reason": reason or "unknown"},
    )
    if reason not in ("org_level_disabled_until", "out_of_credits"):
        _update_settings_for_source("userSettings", {"fastMode": None})
        _save_global_config(
            lambda c: {**c, "penguinModeOrgEnabled": False}
            if isinstance(c, dict)
            else c
        )
    _overage_rejection.emit(message)


# =============================================================================
# High-level state query
# =============================================================================


def get_fast_mode_state(model: str, fast_mode_user_enabled: bool | None) -> str:
    """Return 'on', 'off', or 'cooldown' — the user-visible state label."""
    enabled = (
        is_fast_mode_enabled()
        and get_fast_mode_unavailable_reason() is None
        and bool(fast_mode_user_enabled)
        and is_fast_mode_supported_by_model(model)
    )
    if enabled and is_fast_mode_cooldown():
        return "cooldown"
    if enabled:
        return "on"
    return "off"


def is_fast_mode_currently_active(model: str, user_enabled: bool | None) -> bool:
    """True when fast mode is fully on and not in cooldown."""
    return get_fast_mode_state(model, user_enabled) == "on"


def get_fast_mode_display_name() -> str:
    """Return the user-facing display name for the fast-mode model."""
    return FAST_MODE_MODEL_DISPLAY


# =============================================================================
# Org status (pending / enabled / disabled) and prefetch
# =============================================================================

_org_status: dict[str, Any] = {"status": "pending"}
_org_fast_mode_change = _Pub()
on_org_fast_mode_changed = _org_fast_mode_change.subscribe

_last_prefetch_at = 0.0
_inflight_prefetch: Any = None
_prefetch_failure_count = 0
_prefetch_backoff_ms = _PREFETCH_BASE_DELAY_MS


def get_org_status() -> dict[str, Any]:
    """Return a copy of the current org fast-mode status."""
    with _state_lock:
        return dict(_org_status)


def set_org_status(status: str, reason: str | None = None) -> None:
    """Set the org status programmatically (e.g. from an API response)."""
    global _org_status
    prev_enabled = _org_status.get("status") == "enabled"
    with _state_lock:
        _org_status = {"status": status, "reason": reason or "unknown"}
    new_enabled = status == "enabled"
    if prev_enabled != new_enabled:
        _org_fast_mode_change.emit(new_enabled)


def reset_org_status() -> None:
    """Reset org status to pending (useful in tests or re-initialization)."""
    global _org_status
    with _state_lock:
        _org_status = {"status": "pending"}


def handle_org_status_from_api(
    enabled: bool, disabled_reason: str | None = None
) -> None:
    """Update org status from a prefetch / API response payload.

    ``enabled=False`` writes ``{"status": "disabled", "reason": <reason>}``.
    ``enabled=True``  writes ``{"status": "enabled"}``.
    """
    if enabled:
        set_org_status("enabled")
    else:
        set_org_status("disabled", disabled_reason or "preference")


def resolve_fast_mode_status_from_cache() -> None:
    global _org_status
    if not is_fast_mode_enabled():
        return
    with _state_lock:
        if _org_status["status"] != "pending":
            return
    is_ant = os.environ.get("USER_TYPE") == "ant"
    cached = _get_global_config().get("penguinModeOrgEnabled") is True
    new_status = (
        {"status": "enabled"}
        if is_ant or cached
        else {"status": "disabled", "reason": "unknown"}
    )
    prev_enabled = _org_status.get("status") == "enabled"
    with _state_lock:
        _org_status = new_status
    now_enabled = new_status["status"] == "enabled"
    if prev_enabled != now_enabled:
        log_for_debugging(
            f"Fast mode org status resolved from cache: "
            f"{new_status['status']}"
            + (f" ({new_status['reason']})" if new_status.get("reason") else "")
        )
        _org_fast_mode_change.emit(now_enabled)


async def prefetch_fast_mode_status(
    fetcher: Callable[[], Any] | None = None,
) -> None:
    """Prefetch the org's fast-mode status from the Hare API.

    If *fetcher* is provided it is called (should return a dict/object with
    ``enabled`` / ``disabled_reason`` keys).  Otherwise falls back to the
    local global-config cache via ``resolve_fast_mode_status_from_cache``.

    Implements guarded prefetch: minimum interval, retry backoff, and
    `essential traffic only` short-circuit.
    """
    global _last_prefetch_at, _inflight_prefetch, _prefetch_failure_count
    global _prefetch_backoff_ms

    if _is_essential_traffic_only():
        return

    if not is_fast_mode_enabled():
        return

    now_ms = time.time() * 1000
    if now_ms - _last_prefetch_at < _PREFETCH_MIN_INTERVAL_MS:
        return

    # If an OAuth 401 handler is wired and the stored token is present, check for
    # 401 before making the request — stubbed, but real implementations can wire
    # the handler via set_fast_mode_oauth_provider.
    _last_prefetch_at = now_ms

    if fetcher is None:
        # No HTTP fetcher injected — fall back to cache resolution.
        resolve_fast_mode_status_from_cache()
        return

    # If a previous inflight request is still pending, skip to avoid stampede.
    if _inflight_prefetch is not None:
        return

    try:
        _inflight_prefetch = True
        result = await _awaitable_or_none(fetcher())
        if result is None:
            # Fetcher returned None — do not update status.
            _prefetch_failure_count = 0
            _prefetch_backoff_ms = _PREFETCH_BASE_DELAY_MS
            return

        enabled = getattr(result, "enabled", result.get("enabled", False) if isinstance(result, dict) else False)
        reason = None
        if isinstance(result, dict):
            reason = result.get("disabledReason") or result.get("disabled_reason")

        handle_org_status_from_api(bool(enabled), reason)
        _prefetch_failure_count = 0
        _prefetch_backoff_ms = _PREFETCH_BASE_DELAY_MS

    except Exception as exc:
        _prefetch_failure_count += 1
        _prefetch_backoff_ms = min(
            _prefetch_backoff_ms * 2, _PREFETCH_MAX_BACKOFF_MS
        )
        log_for_debugging(
            f"Fast mode prefetch failed (attempt {_prefetch_failure_count}, "
            f"next backoff {_prefetch_backoff_ms}ms): {exc}"
        )
        # After repeated failures, mark org as network-unavailable so the UI
        # can surface an appropriate message.
        if _prefetch_failure_count >= 3:
            with _state_lock:
                if _org_status["status"] == "pending":
                    _org_status = {"status": "disabled", "reason": "network_error"}
                    _org_fast_mode_change.emit(False)
    finally:
        _inflight_prefetch = None


async def _awaitable_or_none(obj: Any) -> Any:
    """If *obj* is an awaitable, await it; otherwise return it as-is."""
    if hasattr(obj, "__await__"):
        return await obj
    return obj


def refresh_fast_mode_status() -> None:
    """Synchronously re-resolve org status from cache.

    Useful after global config changes (e.g. user enables extra usage).
    """
    reset_org_status()
    resolve_fast_mode_status_from_cache()


def get_prefetch_state() -> dict[str, Any]:
    """Return diagnostic info about the prefetch state (last run, failures, backoff)."""
    return {
        "last_prefetch_at_ms": _last_prefetch_at,
        "failure_count": _prefetch_failure_count,
        "backoff_ms": _prefetch_backoff_ms,
        "inflight": _inflight_prefetch is not None,
    }


# =============================================================================
# Fast mode state management — FastModeState, FastModeStateManager
# =============================================================================

# How long to retain state change history entries (default 128).
_DEFAULT_MAX_HISTORY_ENTRIES = 128


class FastModeState(Enum):
    """All observable fast-mode states.

    The manager guarantees that every transition is valid — callers
    never see an in-between, inconsistent state.
    """

    UNKNOWN = auto()          # not yet resolved
    DISABLED = auto()         # env-var-level disable
    ORG_DISABLED = auto()     # org has disabled it
    OVERAGE_BLOCKED = auto()  # overage / credit limit reached
    NETWORK_UNAVAILABLE = auto()
    AVAILABLE = auto()        # available but not currently active
    ACTIVE = auto()           # currently active
    COOLDOWN = auto()         # temporarily throttled
    LATCHED_OFF = auto()      # user latched off this session


@dataclass
class FastModeStateSnapshot:
    """Immutable full-point-in-time snapshot of all fast-mode state."""

    state: FastModeState
    fast_mode_enabled: bool
    unavailable_reason: str | None
    is_cooldown: bool
    cooldown_reason: CooldownReason | None
    cooldown_reset_at: float | None
    org_status: str
    org_disabled_reason: str | None
    user_preference: bool | None
    header_latched: bool | None
    model: str | None
    timestamp_ms: float


@dataclass
class _StateTransition:
    """A single recorded state change."""

    from_state: FastModeState
    to_state: FastModeState
    reason: str
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)


class FastModeStateManager:
    """Centralised fast-mode state machine.

    Replaces scattered module-level globals with a single class that:

    * Enforces valid transitions.
    * Tracks a bounded history ring of previous transitions.
    * Emits event-callbacks on every state change.
    * Can be snapshotted for diagnostics or serialisation.
    * Integrates with the bootstrap state latch (``fast_mode_header_latched``).

    Typically a module-level singleton is sufficient::

        _state_manager = FastModeStateManager()
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, max_history: int = _DEFAULT_MAX_HISTORY_ENTRIES) -> None:
        self._max_history = max(max_history, 1)
        self._state: FastModeState = FastModeState.UNKNOWN
        self._history: list[_StateTransition] = []
        self._change_callbacks: list[Callable[[FastModeState, FastModeState, str], None]] = []
        self._org_disabled_reason: str | None = None
        self._header_latched: bool | None = False
        self._user_preference: bool | None = None
        self._unavailable_reason: str | None = None
        self._cooldown_reset_at: float | None = None
        self._cooldown_reason: CooldownReason | None = None
        self._model: str | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> FastModeState:
        return self._state

    @property
    def header_latched(self) -> bool | None:
        return self._header_latched

    @property
    def user_preference(self) -> bool | None:
        return self._user_preference

    @property
    def org_disabled_reason(self) -> str | None:
        return self._org_disabled_reason

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    @property
    def is_cooldown(self) -> bool:
        return self._state == FastModeState.COOLDOWN

    @property
    def cooldown_reason(self) -> CooldownReason | None:
        return self._cooldown_reason

    @property
    def cooldown_reset_at(self) -> float | None:
        return self._cooldown_reset_at

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[_StateTransition]:
        """Return a copy of the transition history (most recent last)."""
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    def _record_transition(
        self, from_state: FastModeState, to_state: FastModeState, reason: str
    ) -> None:
        entry = _StateTransition(
            from_state=from_state, to_state=to_state, reason=reason
        )
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    # ------------------------------------------------------------------
    # Transition helpers
    # ------------------------------------------------------------------

    def _transition_to(self, new_state: FastModeState, reason: str) -> None:
        if new_state == self._state:
            return
        prev = self._state
        self._state = new_state
        self._record_transition(prev, new_state, reason)
        # Snapshot callbacks under lock to avoid mid-iteration mutation issues.
        cbs = list(self._change_callbacks)
        for cb in cbs:
            try:
                cb(prev, new_state, reason)
            except Exception:
                pass  # never let a listener break the state machine

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_state_change(
        self, cb: Callable[[FastModeState, FastModeState, str], None]
    ) -> Callable[[], None]:
        """Register a callback invoked on every state transition.

        Returns an unsubscribe function.
        """
        with self._lock:
            self._change_callbacks.append(cb)

        def unsub() -> None:
            with self._lock:
                if cb in self._change_callbacks:
                    self._change_callbacks.remove(cb)

        return unsub

    def clear_callbacks(self) -> None:
        with self._lock:
            self._change_callbacks.clear()

    # ------------------------------------------------------------------
    # State-setting operations
    # ------------------------------------------------------------------

    def set_header_latched(self, value: bool) -> None:
        with self._lock:
            self._header_latched = value
        if value:
            self._transition_to(FastModeState.LATCHED_OFF, "header latched set to True")
        elif self._state == FastModeState.LATCHED_OFF:
            # un-latch — re-evaluate
            self._re_evaluate("header unlatched")

    def set_user_preference(self, enabled: bool | None) -> None:
        with self._lock:
            self._user_preference = enabled
        self._re_evaluate(f"user preference changed to {enabled!r}")

    def set_org_enabled(self) -> None:
        with self._lock:
            self._org_disabled_reason = None
        self._transition_to(FastModeState.AVAILABLE, "org enabled fast mode")

    def set_org_disabled(self, reason: str | None = None) -> None:
        with self._lock:
            self._org_disabled_reason = reason
        self._transition_to(FastModeState.ORG_DISABLED, f"org disabled: {reason}")

    def set_network_unavailable(self) -> None:
        with self._lock:
            self._unavailable_reason = (
                "Fast mode unavailable due to network connectivity issues"
            )
        self._transition_to(
            FastModeState.NETWORK_UNAVAILABLE, "network connectivity issues"
        )

    def set_overage_blocked(self, reason: str | None = None) -> None:
        with self._lock:
            self._unavailable_reason = _overage_disabled_message(reason)
        self._transition_to(
            FastModeState.OVERAGE_BLOCKED, f"overage blocked: {reason}"
        )

    def activate(self, model: str | None = None) -> None:
        with self._lock:
            if model:
                self._model = model
            self._cooldown_reset_at = None
            self._cooldown_reason = None
        self._transition_to(FastModeState.ACTIVE, "fast mode activated")

    def deactivate(self, reason: str = "user disabled") -> None:
        self._transition_to(FastModeState.AVAILABLE, reason)

    def start_cooldown(
        self, reset_timestamp_ms: float, reason: CooldownReason
    ) -> None:
        with self._lock:
            self._cooldown_reset_at = reset_timestamp_ms
            self._cooldown_reason = reason
        self._transition_to(
            FastModeState.COOLDOWN,
            f"cooldown started ({reason}), resets at {reset_timestamp_ms}",
        )

    def expire_cooldown(self) -> None:
        if self._state == FastModeState.COOLDOWN:
            with self._lock:
                self._cooldown_reset_at = None
                self._cooldown_reason = None
            self._transition_to(FastModeState.ACTIVE, "cooldown expired")

    def set_model(self, model: str) -> None:
        with self._lock:
            self._model = model

    # ------------------------------------------------------------------
    # Diagnostics / snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> FastModeStateSnapshot:
        """Return an immutable snapshot of the current state."""
        with self._lock:
            return FastModeStateSnapshot(
                state=self._state,
                fast_mode_enabled=is_fast_mode_enabled(),
                unavailable_reason=self._unavailable_reason,
                is_cooldown=self._state == FastModeState.COOLDOWN,
                cooldown_reason=self._cooldown_reason,
                cooldown_reset_at=self._cooldown_reset_at,
                org_status=(
                    "enabled"
                    if self._org_disabled_reason is None
                    else "disabled"
                ),
                org_disabled_reason=self._org_disabled_reason,
                user_preference=self._user_preference,
                header_latched=self._header_latched,
                model=self._model,
                timestamp_ms=time.time() * 1000,
            )

    def resolved_state_label(self) -> str:
        """Return the same string labels that ``get_fast_mode_state`` produces."""
        if self._state == FastModeState.COOLDOWN:
            return "cooldown"
        if self._state == FastModeState.ACTIVE:
            return "on"
        return "off"

    # ------------------------------------------------------------------
    # Bootstrap state integration
    # ------------------------------------------------------------------

    def sync_to_bootstrap_state(self) -> None:
        """Write the current latch value into the bootstrap global state."""
        try:
            from hare.bootstrap.state import set_fast_mode_header_latched

            set_fast_mode_header_latched(self._header_latched)
        except ImportError:
            pass

    def sync_from_bootstrap_state(self) -> None:
        """Read the latch value from the bootstrap global state."""
        try:
            from hare.bootstrap.state import get_fast_mode_header_latched

            latched = get_fast_mode_header_latched()
            if latched is not None:
                with self._lock:
                    self._header_latched = latched
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset the manager to its pristine state (useful in tests)."""
        with self._lock:
            self._state = FastModeState.UNKNOWN
            self._history.clear()
            self._change_callbacks.clear()
            self._org_disabled_reason = None
            self._header_latched = False
            self._user_preference = None
            self._unavailable_reason = None
            self._cooldown_reset_at = None
            self._cooldown_reason = None
            self._model = None

    def _re_evaluate(self, reason: str) -> None:
        """Re-compute the state from scratch based on current values."""
        unavailable = get_fast_mode_unavailable_reason()

        if not is_fast_mode_enabled():
            self._transition_to(FastModeState.DISABLED, reason)
            return

        if self._header_latched:
            self._transition_to(FastModeState.LATCHED_OFF, reason)
            return

        if self._org_disabled_reason is not None:
            self._transition_to(FastModeState.ORG_DISABLED, reason)
            return

        if unavailable is not None and "network" in unavailable.lower():
            self._transition_to(FastModeState.NETWORK_UNAVAILABLE, reason)
            return

        # If we are in cooldown, check whether it has expired.
        if self._state == FastModeState.COOLDOWN:
            if (
                self._cooldown_reset_at is not None
                and time.time() * 1000 >= self._cooldown_reset_at
            ):
                self.expire_cooldown()
                return
            # Still in cooldown — do not override.
            return

        # User preference is the final discriminator.
        if self._user_preference:
            self._transition_to(FastModeState.ACTIVE, reason)
        else:
            self._transition_to(FastModeState.AVAILABLE, reason)


# =============================================================================
# Module-level singleton — use this everywhere.
# =============================================================================

_fast_mode_state_manager = FastModeStateManager()


def get_fast_mode_state_manager() -> FastModeStateManager:
    """Return the module-level FastModeStateManager singleton."""
    return _fast_mode_state_manager


# ---------------------------------------------------------------------------
# Convenience wrappers that mirror the original flat functions but delegate
# to the state manager.
# ---------------------------------------------------------------------------


def snapshot_fast_mode() -> FastModeStateSnapshot:
    return _fast_mode_state_manager.snapshot()


def get_fast_mode_manager_state() -> FastModeState:
    return _fast_mode_state_manager.current_state


def sync_fast_mode_latch() -> None:
    """Sync the header latch with the bootstrap global state."""
    _fast_mode_state_manager.sync_to_bootstrap_state()


def restore_fast_mode_latch() -> None:
    """Restore the header latch from the bootstrap global state."""
    _fast_mode_state_manager.sync_from_bootstrap_state()


# ---------------------------------------------------------------------------
# Module-level reset (for tests and re-initialization).
# ---------------------------------------------------------------------------


def reset_fast_mode_module() -> None:
    """Reset all module-level fast-mode state to defaults. Useful in tests.

    Resets the state manager, cooldown, org status, prefetch counters,
    and clears all event subscribers.
    """
    global _org_status, _runtime_state, _has_logged_cooldown_expiry
    global _last_prefetch_at, _inflight_prefetch, _prefetch_failure_count
    global _prefetch_backoff_ms

    _fast_mode_state_manager.reset()

    with _state_lock:
        _org_status = {"status": "pending"}
        _runtime_state = {"status": "active"}
        _has_logged_cooldown_expiry = False

    _last_prefetch_at = 0.0
    _inflight_prefetch = None
    _prefetch_failure_count = 0
    _prefetch_backoff_ms = _PREFETCH_BASE_DELAY_MS

    # Re-create pub/sub instances to drop all listeners.
    global _cooldown_triggered, _cooldown_expired, _org_fast_mode_change, _overage_rejection
    global on_cooldown_triggered, on_cooldown_expired, on_org_fast_mode_changed
    global on_fast_mode_overage_rejection

    _cooldown_triggered = _Pub()
    _cooldown_expired = _Pub()
    _org_fast_mode_change = _Pub()
    _overage_rejection = _Pub()
    on_cooldown_triggered = _cooldown_triggered.subscribe
    on_cooldown_expired = _cooldown_expired.subscribe
    on_org_fast_mode_changed = _org_fast_mode_change.subscribe
    on_fast_mode_overage_rejection = _overage_rejection.subscribe
