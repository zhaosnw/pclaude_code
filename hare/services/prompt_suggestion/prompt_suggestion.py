"""
Prompt suggestion: generates follow-up suggestions after turns.

Port of: src/services/PromptSuggestion/promptSuggestion.ts (524 lines)
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable, Optional

from hare.services.analytics import log_event
from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale
from hare.utils.env_utils import is_env_defined_falsy, is_env_truthy
from hare.utils.errors import error_message as _to_error_message

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PromptVariant = str  # 'user_intent' | 'stated_intent'

# ---------------------------------------------------------------------------
# Abort controller (module-level for fire-and-forget lifecycle)
# ---------------------------------------------------------------------------

_current_abort_controller: Optional[asyncio.Event] = None


# ---------------------------------------------------------------------------
# Prompt variant
# ---------------------------------------------------------------------------


def get_prompt_variant() -> PromptVariant:
    """Return the current prompt variant in use."""
    return "user_intent"


# ---------------------------------------------------------------------------
# Enabling / suppressing
# ---------------------------------------------------------------------------


def should_enable_prompt_suggestion() -> bool:
    """Check whether prompt suggestion should be enabled.

    Checks order: env override → growthbook flag → non-interactive session →
    swarm teammate → user setting.
    """
    # Env var overrides everything (for testing)
    env_override = os.environ.get("CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION")
    if is_env_defined_falsy(env_override):
        log_event("tengu_prompt_suggestion_init", {"enabled": False, "source": "env"})
        return False
    if is_env_truthy(env_override):
        log_event("tengu_prompt_suggestion_init", {"enabled": True, "source": "env"})
        return True

    # Keep default in sync with Config.tsx (settings toggle visibility)
    if not get_feature_value_cached_may_be_stale("tengu_chomp_inflection", False):
        log_event(
            "tengu_prompt_suggestion_init", {"enabled": False, "source": "growthbook"}
        )
        return False

    # Disable in non-interactive mode (print mode, piped input, SDK)
    if _is_non_interactive_session():
        log_event(
            "tengu_prompt_suggestion_init",
            {"enabled": False, "source": "non_interactive"},
        )
        return False

    # Disable for swarm teammates (only leader should show suggestions)
    if _is_swarm_teammate():
        log_event(
            "tengu_prompt_suggestion_init",
            {"enabled": False, "source": "swarm_teammate"},
        )
        return False

    enabled = _get_settings_prompt_suggestion_enabled()
    log_event(
        "tengu_prompt_suggestion_init", {"enabled": enabled, "source": "setting"}
    )
    return enabled


def abort_prompt_suggestion() -> None:
    """Abort the current in-flight prompt suggestion generation."""
    global _current_abort_controller
    if _current_abort_controller is not None:
        _current_abort_controller.set()
        _current_abort_controller = None


def get_suggestion_suppress_reason(app_state: dict[str, Any]) -> Optional[str]:
    """Return a suppression reason if suggestions should not be generated,
    or None if generation is allowed. Shared by main and pipelined paths.
    """
    if not app_state.get("promptSuggestionEnabled"):
        return "disabled"
    if app_state.get("pendingWorkerRequest") or app_state.get("pendingSandboxRequest"):
        return "pending_permission"
    if len(app_state.get("elicitation", {}).get("queue", [])) > 0:
        return "elicitation_active"
    tpc = app_state.get("toolPermissionContext", {})
    if tpc.get("mode") == "plan":
        return "plan_mode"
    if (
        os.environ.get("USER_TYPE") == "external"
        and _get_current_limits_status() != "allowed"
    ):
        return "rate_limit"
    return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_PARENT_UNCACHED_TOKENS = 10_000

_SUGGESTION_PROMPT = """[SUGGESTION MODE: Suggest what the user might naturally type next into Claude Code.]

FIRST: Look at the user's recent messages and original request.

Your job is to predict what THEY would type - not what you think they should do.

THE TEST: Would they think "I was just about to type that"?

EXAMPLES:
User asked "fix the bug and run tests", bug is fixed → "run the tests"
After code written → "try it out"
Claude offers options → suggest the one the user would likely pick, based on conversation
Claude asks to continue → "yes" or "go ahead"
Task complete, obvious follow-up → "commit this" or "push it"
After error or misunderstanding → silence (let them assess/correct)

Be specific: "run the tests" beats "continue".

NEVER SUGGEST:
- Evaluative ("looks good", "thanks")
- Questions ("what about...?")
- Claude-voice ("Let me...", "I'll...", "Here's...")
- New ideas they didn't ask about
- Multiple sentences

Stay silent if the next step isn't obvious from what the user said.

Format: 2-12 words, match the user's style. Or nothing.

Reply with ONLY the suggestion, no quotes or explanation."""

_SUGGESTION_PROMPTS: dict[PromptVariant, str] = {
    "user_intent": _SUGGESTION_PROMPT,
    "stated_intent": _SUGGESTION_PROMPT,
}


# ---------------------------------------------------------------------------
# Filtering constants
# ---------------------------------------------------------------------------

_ALLOWED_SINGLE_WORDS: set[str] = {
    # Affirmatives
    "yes", "yeah", "yep", "yea", "yup", "sure", "ok", "okay",
    # Actions
    "push", "commit", "deploy", "stop", "continue", "check", "exit", "quit",
    # Negation
    "no",
}

_EVALUATIVE_PATTERN = re.compile(
    r"thanks|thank you|looks good|sounds good|that works|that worked|"
    r"that's all|nice|great|perfect|makes sense|awesome|excellent"
)

_CLAUDE_VOICE_PATTERN = re.compile(
    r"^(let me|i'll|i've|i'm|i can|i would|i think|i notice|"
    r"here's|here is|here are|that's|this is|this will|"
    r"you can|you should|you could|sure,|of course|certainly)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_non_interactive_session() -> bool:
    """Check if running in non-interactive mode."""
    try:
        from hare.bootstrap.state import get_is_non_interactive_session
        return get_is_non_interactive_session()
    except ImportError:
        return False


def _is_swarm_teammate() -> bool:
    """Check if running as a swarm teammate (not leader)."""
    try:
        from hare.bootstrap.state import (
            is_agent_swarms_enabled,
            is_teammate,
        )
        return is_agent_swarms_enabled() and is_teammate()
    except ImportError:
        return False


def _get_settings_prompt_suggestion_enabled() -> bool:
    """Read the user's promptSuggestionEnabled setting."""
    try:
        from hare.utils.settings.settings import get_initial_settings
        settings = get_initial_settings()
        return settings.get("promptSuggestionEnabled", True) is not False
    except (ImportError, AttributeError):
        return True


def _get_current_limits_status() -> str:
    """Get current usage limits status."""
    try:
        from hare.services.claude_ai_limits import current_limits
        return getattr(current_limits, "status", "allowed")
    except ImportError:
        return "allowed"


def _count_messages_by_type(messages: list[dict[str, Any]], msg_type: str) -> int:
    """Count messages of a given type in a message list."""
    return sum(1 for m in messages if m.get("type") == msg_type)


def _get_last_assistant_message(
    messages: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Get the last assistant message from a conversation."""
    for m in reversed(messages):
        if m.get("type") == "assistant":
            return m
    return None


# ---------------------------------------------------------------------------
# Cache suppression
# ---------------------------------------------------------------------------


def get_parent_cache_suppress_reason(
    last_assistant_message: Optional[dict[str, Any]],
) -> Optional[str]:
    """Check if the parent conversation is too large for efficient cache reuse.

    The fork re-processes the parent's output (never cached) plus its own prompt.
    If the total tokens exceed the threshold, return 'cache_cold' to suppress.
    """
    if last_assistant_message is None:
        return None

    usage = last_assistant_message.get("message", {}).get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    if (input_tokens + cache_write_tokens + output_tokens) > _MAX_PARENT_UNCACHED_TOKENS:
        return "cache_cold"
    return None


# ---------------------------------------------------------------------------
# Suggestion generation
# ---------------------------------------------------------------------------


async def generate_suggestion(
    abort_event: asyncio.Event,
    prompt_id: PromptVariant,
    cache_safe_params: dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Generate a suggestion by forking a sub-agent with the suggestion prompt.

    Returns (suggestion_text, generation_request_id) — both may be None.
    """
    prompt = _SUGGESTION_PROMPTS.get(prompt_id, _SUGGESTION_PROMPT)

    # Deny tools via callback, NOT by passing tools:[] — that busts cache (0% hit)
    async def _can_use_tool(_tool: Any, _input: Any) -> dict[str, Any]:
        return {
            "behavior": "deny",
            "message": "No tools needed for suggestion",
            "decisionReason": {"type": "other", "reason": "suggestion only"},
        }

    try:
        from hare.utils.forked_agent import ForkedAgentParams, run_forked_agent
        from hare.utils.messages import create_user_message

        result = await run_forked_agent(
            ForkedAgentParams(
                prompt_messages=[create_user_message(content=prompt)],
                cache_safe_params=cache_safe_params,
                can_use_tool=_can_use_tool,
                query_source="prompt_suggestion",
                fork_label="prompt_suggestion",
                overrides={"abort_event": abort_event},
                skip_transcript=True,
                skip_cache_write=True,
            )
        )

        # Extract the request_id from the first assistant message for RL dataset joins
        first_assistant = next(
            (m for m in result.messages if m.get("type") == "assistant"), None
        )
        generation_request_id: Optional[str] = None
        if first_assistant is not None:
            generation_request_id = first_assistant.get("requestId")

        # Check ALL messages — model may loop (try tool → denied → text in next message)
        for msg in result.messages:
            if msg.get("type") != "assistant":
                continue
            content = msg.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        return text, generation_request_id

        return None, generation_request_id

    except ImportError:
        # Fallback when forked_agent module is not yet wired
        return None, None


# ---------------------------------------------------------------------------
# Suggestion filtering
# ---------------------------------------------------------------------------


def should_filter_suggestion(
    suggestion: Optional[str],
    prompt_id: PromptVariant,
    source: Optional[str] = None,
) -> bool:
    """Apply post-generation filters to suppress low-quality suggestions.

    Returns True if the suggestion should be filtered out (suppressed).
    """
    if not suggestion:
        log_suggestion_suppressed("empty", suggestion, prompt_id, source)
        return True

    lower = suggestion.lower()
    word_count = len(suggestion.strip().split())

    filters: list[tuple[str, Callable[[], bool]]] = [
        ("done", lambda: lower == "done"),
        (
            "meta_text",
            lambda: (
                lower == "nothing found"
                or lower == "nothing found."
                or lower.startswith("nothing to suggest")
                or lower.startswith("no suggestion")
                or bool(re.search(r"\bsilence is\b|\bstay(s|ing)? silent\b", lower))
                or bool(re.match(r"^\W*silence\W*$", lower))
            ),
        ),
        (
            "meta_wrapped",
            lambda: bool(
                re.match(r"^\(.*\)$", suggestion)
                or re.match(r"^\[.*\]$", suggestion)
            ),
        ),
        (
            "error_message",
            lambda: (
                lower.startswith("api error:")
                or lower.startswith("prompt is too long")
                or lower.startswith("request timed out")
                or lower.startswith("invalid api key")
                or lower.startswith("image was too large")
            ),
        ),
        ("prefixed_label", lambda: bool(re.match(r"^\w+:\s", suggestion))),
        (
            "too_few_words",
            lambda: _check_too_few_words(suggestion, lower, word_count),
        ),
        ("too_many_words", lambda: word_count > 12),
        ("too_long", lambda: len(suggestion) >= 100),
        (
            "multiple_sentences",
            lambda: bool(re.search(r"[.!?]\s+[A-Z]", suggestion)),
        ),
        ("has_formatting", lambda: bool(re.search(r"[\n*]|\*\*", suggestion))),
        (
            "evaluative",
            lambda: bool(_EVALUATIVE_PATTERN.search(lower)),
        ),
        (
            "claude_voice",
            lambda: bool(_CLAUDE_VOICE_PATTERN.search(suggestion)),
        ),
    ]

    for reason, check in filters:
        if check():
            log_suggestion_suppressed(reason, suggestion, prompt_id, source)
            return True

    return False


def _check_too_few_words(suggestion: str, lower: str, word_count: int) -> bool:
    """Check if suggestion has too few words, allowing slash commands
    and common single-word inputs.
    """
    if word_count >= 2:
        return False
    # Allow slash commands — these are valid user commands
    if suggestion.startswith("/"):
        return False
    # Allow common single-word inputs that are valid user commands
    return lower not in _ALLOWED_SINGLE_WORDS


# ---------------------------------------------------------------------------
# Main generation guard + pipeline
# ---------------------------------------------------------------------------


async def try_generate_suggestion(
    abort_event: asyncio.Event,
    messages: list[dict[str, Any]],
    get_app_state: Callable[[], dict[str, Any]],
    cache_safe_params: dict[str, Any],
    source: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Shared guard + generation logic used by both CLI TUI and SDK push paths.

    Returns the suggestion with metadata, or None if suppressed/filtered.
    """
    if abort_event.is_set():
        log_suggestion_suppressed("aborted", source=source)
        return None

    assistant_turn_count = _count_messages_by_type(messages, "assistant")
    if assistant_turn_count < 2:
        log_suggestion_suppressed("early_conversation", source=source)
        return None

    last_assistant = _get_last_assistant_message(messages)
    if last_assistant and last_assistant.get("isApiErrorMessage"):
        log_suggestion_suppressed("last_response_error", source=source)
        return None

    cache_reason = get_parent_cache_suppress_reason(last_assistant)
    if cache_reason:
        log_suggestion_suppressed(cache_reason, source=source)
        return None

    app_state = get_app_state()
    suppress_reason = get_suggestion_suppress_reason(app_state)
    if suppress_reason:
        log_suggestion_suppressed(suppress_reason, source=source)
        return None

    prompt_id = get_prompt_variant()
    suggestion, generation_request_id = await generate_suggestion(
        abort_event, prompt_id, cache_safe_params
    )

    if abort_event.is_set():
        log_suggestion_suppressed("aborted", source=source)
        return None

    if not suggestion:
        log_suggestion_suppressed("empty", prompt_id=prompt_id, source=source)
        return None

    if should_filter_suggestion(suggestion, prompt_id, source):
        return None

    return {
        "suggestion": suggestion,
        "promptId": prompt_id,
        "generationRequestId": generation_request_id,
    }


# ---------------------------------------------------------------------------
# Main entry point (post-sampling hook)
# ---------------------------------------------------------------------------


async def execute_prompt_suggestion(hook_context: dict[str, Any]) -> None:
    """Fire-and-forget prompt suggestion after each turn.

    Called as a post-sampling hook. Generates a follow-up suggestion
    and optionally starts speculation (when enabled).

    Args:
        hook_context: The REPLHookContext from the post-sampling hook system.
    """
    if hook_context.get("querySource") != "repl_main_thread":
        return

    global _current_abort_controller
    _current_abort_controller = asyncio.Event()
    abort_event = _current_abort_controller

    cache_safe_params = _create_cache_safe_params(hook_context)

    try:
        tool_use_context = hook_context.get("toolUseContext", {})
        result = await try_generate_suggestion(
            abort_event,
            hook_context.get("messages", []),
            tool_use_context.get("getAppState", lambda: {}),
            cache_safe_params,
            source="cli",
        )
        if not result:
            return

        # Set the suggestion in app state
        set_app_state = tool_use_context.get("setAppState")
        if callable(set_app_state):

            def _updater(prev: dict[str, Any]) -> dict[str, Any]:
                return {
                    **prev,
                    "promptSuggestion": {
                        "text": result["suggestion"],
                        "promptId": result["promptId"],
                        "shownAt": 0,
                        "acceptedAt": 0,
                        "generationRequestId": result["generationRequestId"],
                    },
                }

            set_app_state(_updater)

        # Optionally start speculation if enabled
        if _is_speculation_enabled() and result["suggestion"]:
            await _start_speculation(
                result["suggestion"],
                hook_context,
                set_app_state,
                is_pipelined=False,
                cache_safe_params=cache_safe_params,
            )

    except asyncio.CancelledError:
        log_suggestion_suppressed("aborted", source="cli")
    except Exception as exc:
        log_event("tengu_prompt_suggestion_error", {
            "error": _to_error_message(exc),
            "error_type": type(exc).__name__,
        })
    finally:
        if _current_abort_controller is abort_event:
            _current_abort_controller = None


# ---------------------------------------------------------------------------
# Cache-safe params helper
# ---------------------------------------------------------------------------


def _create_cache_safe_params(context: dict[str, Any]) -> dict[str, Any]:
    """Build cache-safe params from hook context."""
    try:
        from hare.utils.forked_agent import create_cache_safe_params as _make_csp
        return _make_csp(context)
    except ImportError:
        return {
            "system_prompt": context.get("systemPrompt", context.get("system_prompt")),
            "user_context": context.get("userContext", context.get("user_context")),
            "system_context": context.get("systemContext", context.get("system_context")),
            "tool_use_context": context.get("toolUseContext", context.get("tool_use_context")),
            "fork_context_messages": context.get("messages"),
        }


def _is_speculation_enabled() -> bool:
    """Check whether speculation is enabled."""
    try:
        from hare.services.prompt_suggestion.speculation import is_speculation_enabled
        return is_speculation_enabled()
    except ImportError:
        return False


async def _start_speculation(
    suggestion_text: str,
    context: dict[str, Any],
    set_app_state: Any,
    is_pipelined: bool = False,
    cache_safe_params: Optional[dict[str, Any]] = None,
) -> None:
    """Fire-and-forget speculation on the generated suggestion."""
    try:
        from hare.services.prompt_suggestion.speculation import start_speculation

        await start_speculation(
            suggestion_text=suggestion_text,
            context=context,
            set_app_state=set_app_state,
            is_pipelined=is_pipelined,
            cache_safe_params=cache_safe_params,
        )
    except ImportError:
        pass
    except Exception:
        # Speculation is best-effort; failures are non-fatal
        pass


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def log_suggestion_suppressed(
    reason: str,
    suggestion: Optional[str] = None,
    prompt_id: Optional[PromptVariant] = None,
    source: Optional[str] = None,
) -> None:
    """Log when a suggestion is suppressed, filtered, or otherwise not shown."""
    resolved_prompt_id = prompt_id or get_prompt_variant()
    metadata: dict[str, Any] = {
        "outcome": "suppressed",
        "reason": reason,
        "prompt_id": resolved_prompt_id,
    }
    if source:
        metadata["source"] = source
    if os.environ.get("USER_TYPE") == "ant" and suggestion:
        metadata["suggestion"] = suggestion

    log_event("tengu_prompt_suggestion", metadata)


def log_suggestion_outcome(
    suggestion: str,
    user_input: str,
    emitted_at: float,
    prompt_id: PromptVariant,
    generation_request_id: Optional[str] = None,
) -> None:
    """Log acceptance/ignoring of a prompt suggestion.

    Used by the SDK push path to track outcomes when the next user message arrives.
    """
    similarity = round(user_input and len(user_input) / (len(suggestion) or 1), 2)
    was_accepted = user_input == suggestion
    time_ms = max(0, int((asyncio.get_event_loop().time() * 1000) - emitted_at))

    metadata: dict[str, Any] = {
        "source": "sdk",
        "outcome": "accepted" if was_accepted else "ignored",
        "prompt_id": prompt_id,
    }
    if generation_request_id:
        metadata["generationRequestId"] = generation_request_id
    if was_accepted:
        metadata["timeToAcceptMs"] = time_ms
    else:
        metadata["timeToIgnoreMs"] = time_ms
    metadata["similarity"] = similarity
    if os.environ.get("USER_TYPE") == "ant":
        metadata["suggestion"] = suggestion
        metadata["userInput"] = user_input

    log_event("tengu_prompt_suggestion", metadata)
