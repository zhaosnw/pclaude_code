"""
API-native context management strategies (clear_tool_uses, thinking, compact_boundary).

Port of: src/services/compact/apiMicrocompact.ts

Provides API-level context management through the Anthropic API's
context_management field, enabling server-side token reduction without
the cost of full summarization. Integrates with the compact subsystem
for hybrid local+API context optimization.

Key capabilities:
- Clear thinking blocks after they are no longer needed
- Clear old tool use results while preserving tool use context
- Insert compact boundary markers for incremental summary
- Token-triggered auto-activation with configurable thresholds
- Runtime configuration via env vars and programmatic API
- Tool classification for fine-grained clearing strategies
- Integration with the existing compact pipeline (micro, full, reactive)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union

from hare.services.token_estimation import (
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# Threshold defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_INPUT_TOKENS = 180_000
DEFAULT_TARGET_INPUT_TOKENS = 40_000
DEFAULT_THINKING_KEEP_TURNS = 1
DEFAULT_MIN_TOKENS_TO_COMPACT = 20_000
DEFAULT_COMPACT_BOUNDARY_MAX_TOKENS = 5_000

# Optional stats logging toggle (env-driven).
LOGGING_ENABLED = not (os.environ.get("DISABLE_MICROCOMPACT_LOGGING", "") in ("1", "true", "yes", "on"))


# ---------------------------------------------------------------------------
# Tool classification — clearable vs. preserved
# ---------------------------------------------------------------------------

# Tools whose RESULTS can be cleared (the output is large and ephemeral)
TOOLS_CLEARABLE_RESULTS: list[str] = [
    "Bash",
    "BashOutput",
    "Read",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "Task",
    "BashTool",
    "LS",
    "FileRead",
]

# Tools whose USES can be cleared (the invocation itself is disposable)
TOOLS_CLEARABLE_USES: list[str] = [
    "BashOutput",
    "Read",
    "Grep",
    "Glob",
    "LS",
]

# Tools that should NEVER have their results cleared — critical for context
TOOLS_PRESERVED_RESULTS: list[str] = [
    "FileEdit",
    "FileWrite",
    "TodoWrite",
    "NotebookEdit",
    "Skill",
    "TaskCreate",
    "TaskUpdate",
    "EnterWorktree",
    "ExitWorktree",
]


# ---------------------------------------------------------------------------
# Strategy type definitions
# ---------------------------------------------------------------------------

StrategyKind = Literal[
    "clear_thinking_20251015",
    "clear_tool_uses_20250919",
    "compact_boundary",
]

TriggerKind = Literal["input_tokens", "input_tokens_pct", "message_count"]

ClearAmountKind = Literal["input_tokens", "input_tokens_pct", "message_count"]


@dataclass
class TokenTrigger:
    """Trigger condition based on token count."""

    type: TriggerKind
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenTrigger:
        return cls(type=d["type"], value=int(d["value"]))


@dataclass
class ClearAtLeast:
    """Minimum amount to clear when triggered."""

    type: ClearAmountKind
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClearAtLeast:
        return cls(type=d["type"], value=int(d["value"]))


@dataclass
class ClearThinkingEdit:
    """Clears thinking blocks from previous turns.

    Corresponds to: clear_thinking_20251015
    """

    type: Literal["clear_thinking_20251015"]
    keep: Union[Literal["all"], dict[str, Any]]
    # keep = "all" means keep everything
    # keep = {"type": "thinking_turns", "value": N} means keep last N thinking turns

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "keep": self.keep}


@dataclass
class ClearToolUsesEdit:
    """Clears tool use inputs/results from old turns.

    Corresponds to: clear_tool_uses_20250919
    """

    type: Literal["clear_tool_uses_20250919"]
    trigger: Optional[TokenTrigger] = None
    clear_at_least: Optional[ClearAtLeast] = None
    clear_tool_inputs: Optional[list[str]] = None
    clear_tool_results: Optional[list[str]] = None
    exclude_tools: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.trigger is not None:
            d["trigger"] = self.trigger.to_dict()
        if self.clear_at_least is not None:
            d["clear_at_least"] = self.clear_at_least.to_dict()
        if self.clear_tool_inputs:
            d["clear_tool_inputs"] = self.clear_tool_inputs
        if self.clear_tool_results:
            d["clear_tool_results"] = self.clear_tool_results
        if self.exclude_tools:
            d["exclude_tools"] = self.exclude_tools
        return d


@dataclass
class ExcludeToolEdit:
    """Mark tools that should be EXCLUDED from clearing.

    This is a list of tool names that the API should skip when applying
    a clear_tool_uses strategy.
    """

    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"exclude_tools": self.tools}


@dataclass
class CompactBoundaryEdit:
    """Inserts a compact boundary — the API will summarize prior messages
    and replace them with a compact boundary marker.

    Corresponds to: compact_boundary (proposed)
    """

    type: Literal["compact_boundary"]
    trigger: Optional[TokenTrigger] = None
    max_tokens: int = DEFAULT_COMPACT_BOUNDARY_MAX_TOKENS
    direction: Literal["from", "up_to"] = "from"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "max_tokens": self.max_tokens,
            "direction": self.direction,
        }
        if self.trigger is not None:
            d["trigger"] = self.trigger.to_dict()
        return d


ContextManagementEdit = Union[ClearThinkingEdit, ClearToolUsesEdit, CompactBoundaryEdit]


# ---------------------------------------------------------------------------
# Configuration objects
# ---------------------------------------------------------------------------


@dataclass
class ContextManagementConfig:
    """Configuration passed to the API as context_management.edits.

    Each edit is one strategy to apply server-side. The API processes
    them in order, applying transforms to the conversation history
    before computing the model response.
    """

    edits: list[dict[str, Any]] = field(default_factory=list)
    # Raw dict form for API serialization.

    def to_body(self) -> Optional[dict[str, Any]]:
        """Return the full context_management body, or None if empty."""
        if not self.edits:
            return None
        return {"edits": self.edits}

    @property
    def strategy_count(self) -> int:
        return len(self.edits)

    @property
    def strategy_types(self) -> list[str]:
        return [e.get("type", "unknown") for e in self.edits]


@dataclass
class APIMicroCompactOptions:
    """Runtime options controlling API-level micro-compact behavior."""

    # Token thresholds
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    target_input_tokens: int = DEFAULT_TARGET_INPUT_TOKENS
    min_tokens_to_compact: int = DEFAULT_MIN_TOKENS_TO_COMPACT

    # Thinking management
    enable_thinking_clearing: bool = True
    thinking_keep_turns: int = DEFAULT_THINKING_KEEP_TURNS
    is_redact_thinking_active: bool = False
    clear_all_thinking: bool = False

    # Tool clearing
    enable_tool_result_clearing: bool = False
    enable_tool_use_clearing: bool = False

    # Compact boundary
    enable_compact_boundary: bool = False
    compact_boundary_max_tokens: int = DEFAULT_COMPACT_BOUNDARY_MAX_TOKENS

    # General
    is_ant_user: bool = False
    enabled: bool = True
    use_api_clear_tool_results: bool = False
    use_api_clear_tool_uses: bool = False

    def validate(self) -> list[str]:
        """Validate and return a list of issues (empty = valid)."""
        issues: list[str] = []

        if self.max_input_tokens < 1:
            issues.append("max_input_tokens must be >= 1")
        if self.target_input_tokens < 1:
            issues.append("target_input_tokens must be >= 1")
        if self.target_input_tokens > self.max_input_tokens:
            issues.append("target_input_tokens must be <= max_input_tokens")
        if self.min_tokens_to_compact < 0:
            issues.append("min_tokens_to_compact must be >= 0")
        if self.thinking_keep_turns < 0:
            issues.append("thinking_keep_turns must be >= 0")
        if self.compact_boundary_max_tokens < 100:
            issues.append("compact_boundary_max_tokens must be >= 100")

        return issues


@dataclass
class MicroCompactTokenState:
    """Token tracking state for micro-compact decisions."""

    total_input_tokens: int = 0
    estimated_total_tokens: int = 0
    pre_compact_tokens: int = 0
    post_compact_tokens: int = 0
    tokens_freed_by_thinking: int = 0
    tokens_freed_by_tool_clearing: int = 0
    tokens_freed_by_boundary: int = 0
    is_above_threshold: bool = False
    percent_remaining: float = 100.0
    last_compact_turn: int = 0
    current_turn: int = 0
    consecutive_failures: int = 0

    @property
    def total_tokens_freed(self) -> int:
        return (
            self.tokens_freed_by_thinking
            + self.tokens_freed_by_tool_clearing
            + self.tokens_freed_by_boundary
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "estimated_total_tokens": self.estimated_total_tokens,
            "pre_compact_tokens": self.pre_compact_tokens,
            "post_compact_tokens": self.post_compact_tokens,
            "tokens_freed_by_thinking": self.tokens_freed_by_thinking,
            "tokens_freed_by_tool_clearing": self.tokens_freed_by_tool_clearing,
            "tokens_freed_by_boundary": self.tokens_freed_by_boundary,
            "is_above_threshold": self.is_above_threshold,
            "percent_remaining": self.percent_remaining,
            "last_compact_turn": self.last_compact_turn,
            "current_turn": self.current_turn,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MicroCompactTokenState:
        return cls(
            total_input_tokens=int(d.get("total_input_tokens", 0)),
            estimated_total_tokens=int(d.get("estimated_total_tokens", 0)),
            pre_compact_tokens=int(d.get("pre_compact_tokens", 0)),
            post_compact_tokens=int(d.get("post_compact_tokens", 0)),
            tokens_freed_by_thinking=int(d.get("tokens_freed_by_thinking", 0)),
            tokens_freed_by_tool_clearing=int(d.get("tokens_freed_by_tool_clearing", 0)),
            tokens_freed_by_boundary=int(d.get("tokens_freed_by_boundary", 0)),
            is_above_threshold=bool(d.get("is_above_threshold", False)),
            percent_remaining=float(d.get("percent_remaining", 100.0)),
            last_compact_turn=int(d.get("last_compact_turn", 0)),
            current_turn=int(d.get("current_turn", 0)),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
        )

    def reset(self) -> None:
        """Reset all fields to default values."""
        self.total_input_tokens = 0
        self.estimated_total_tokens = 0
        self.pre_compact_tokens = 0
        self.post_compact_tokens = 0
        self.tokens_freed_by_thinking = 0
        self.tokens_freed_by_tool_clearing = 0
        self.tokens_freed_by_boundary = 0
        self.is_above_threshold = False
        self.percent_remaining = 100.0
        self.last_compact_turn = 0
        self.current_turn = 0
        self.consecutive_failures = 0


@dataclass
class APIMicroCompactResult:
    """Result of applying API-level micro-compact strategies."""

    edits: list[dict[str, Any]] = field(default_factory=list)
    token_state: MicroCompactTokenState = field(default_factory=MicroCompactTokenState)
    was_applied: bool = False
    strategies_used: list[str] = field(default_factory=list)
    config: Optional[APIMicroCompactOptions] = None
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return len(self.errors) == 0

    def to_summary(self) -> dict[str, Any]:
        return {
            "was_applied": self.was_applied,
            "strategies_used": self.strategies_used,
            "tokens_freed": self.token_state.total_tokens_freed,
            "pre_compact_tokens": self.token_state.pre_compact_tokens,
            "post_compact_tokens": self.token_state.post_compact_tokens,
            "errors": self.errors,
            "strategy_count": len(self.edits),
        }


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _env_truthy(name: str) -> bool:
    """Check if an env var is truthy (1, true, yes, on)."""
    v = os.environ.get(name, "")
    return v.lower() in ("1", "true", "yes", "on")


def _parse_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Configuration factory
# ---------------------------------------------------------------------------


def get_default_api_microcompact_options() -> APIMicroCompactOptions:
    """Build default options from environment variables."""
    is_ant = os.environ.get("USER_TYPE") == "ant"

    return APIMicroCompactOptions(
        max_input_tokens=_parse_env_int(
            "API_MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS
        ),
        target_input_tokens=_parse_env_int(
            "API_TARGET_INPUT_TOKENS", DEFAULT_TARGET_INPUT_TOKENS
        ),
        min_tokens_to_compact=_parse_env_int(
            "API_MIN_TOKENS_TO_COMPACT", DEFAULT_MIN_TOKENS_TO_COMPACT
        ),
        enable_thinking_clearing=True,
        thinking_keep_turns=_parse_env_int(
            "API_THINKING_KEEP_TURNS", DEFAULT_THINKING_KEEP_TURNS
        ),
        is_redact_thinking_active=_env_truthy("REDACT_THINKING"),
        clear_all_thinking=_env_truthy("CLEAR_ALL_THINKING"),
        enable_tool_result_clearing=is_ant and _env_truthy("USE_API_CLEAR_TOOL_RESULTS"),
        enable_tool_use_clearing=is_ant and _env_truthy("USE_API_CLEAR_TOOL_USES"),
        enable_compact_boundary=is_ant and _env_truthy("USE_API_COMPACT_BOUNDARY"),
        compact_boundary_max_tokens=_parse_env_int(
            "API_COMPACT_BOUNDARY_MAX_TOKENS", DEFAULT_COMPACT_BOUNDARY_MAX_TOKENS
        ),
        is_ant_user=is_ant,
        enabled=not _env_truthy("DISABLE_API_MICROCOMPACT"),
        use_api_clear_tool_results=is_ant and _env_truthy("USE_API_CLEAR_TOOL_RESULTS"),
        use_api_clear_tool_uses=is_ant and _env_truthy("USE_API_CLEAR_TOOL_USES"),
    )


# ---------------------------------------------------------------------------
# Option validation and sanitization
# ---------------------------------------------------------------------------


def validate_microcompact_options(
    options: Optional[APIMicroCompactOptions] = None,
) -> tuple[APIMicroCompactOptions, list[str]]:
    """Validate and clamp microcompact options.

    Returns (sanitized_options, list_of_issues).
    If issues are returned, sanitized_options has been clamped to safe values.
    """
    opts = options if options is not None else get_default_api_microcompact_options()
    issues: list[str] = []

    # Clamp and validate thresholds
    if opts.max_input_tokens < 1:
        issues.append("max_input_tokens clamped to 1000 (was {})".format(opts.max_input_tokens))
        opts.max_input_tokens = 1000
    if opts.max_input_tokens > 2_000_000:
        issues.append("max_input_tokens clamped to 2,000,000 (was {})".format(opts.max_input_tokens))
        opts.max_input_tokens = 2_000_000

    if opts.target_input_tokens < 1:
        issues.append("target_input_tokens clamped to 1000 (was {})".format(opts.target_input_tokens))
        opts.target_input_tokens = 1000

    if opts.target_input_tokens > opts.max_input_tokens:
        issues.append(
            "target_input_tokens ({}) > max_input_tokens ({}); "
            "clamping target to max".format(
                opts.target_input_tokens, opts.max_input_tokens
            )
        )
        opts.target_input_tokens = opts.max_input_tokens

    if opts.min_tokens_to_compact < 0:
        issues.append(
            "min_tokens_to_compact clamped to 0 (was {})".format(opts.min_tokens_to_compact)
        )
        opts.min_tokens_to_compact = 0

    if opts.thinking_keep_turns < 0:
        issues.append(
            "thinking_keep_turns clamped to 0 (was {})".format(opts.thinking_keep_turns)
        )
        opts.thinking_keep_turns = 0
    if opts.thinking_keep_turns > 100:
        issues.append(
            "thinking_keep_turns clamped to 100 (was {})".format(opts.thinking_keep_turns)
        )
        opts.thinking_keep_turns = 100

    if opts.compact_boundary_max_tokens < 100:
        issues.append(
            "compact_boundary_max_tokens clamped to 100 (was {})".format(
                opts.compact_boundary_max_tokens
            )
        )
        opts.compact_boundary_max_tokens = 100
    if opts.compact_boundary_max_tokens > 100_000:
        issues.append(
            "compact_boundary_max_tokens clamped to 100,000 (was {})".format(
                opts.compact_boundary_max_tokens
            )
        )
        opts.compact_boundary_max_tokens = 100_000

    return opts, issues


# ---------------------------------------------------------------------------
# Internal helpers — tool name resolution
# ---------------------------------------------------------------------------


def _build_tool_use_map(
    messages: list[dict[str, Any]],
) -> dict[str, str]:
    """Build a mapping from tool_use_id to tool_name by scanning messages.

    Scans assistant messages for tool_use blocks and records the mapping
    of id -> name.  This lets downstream functions resolve a tool_result's
    tool_use_id back to the tool that produced it.

    Args:
        messages: The full conversation message list.

    Returns:
        Dict mapping each tool_use_id (str) to its tool_name (str).
    """
    mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tid = block.get("id", "")
                tname = block.get("name", "")
                if tid:
                    mapping[tid] = tname
    return mapping


def _resolve_tool_name(
    block: dict[str, Any],
    tool_use_map: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Resolve a tool_result block to its tool name.

    Precedence: explicit 'tool_name' field, then lookup via tool_use_map
    using 'tool_use_id'.

    Args:
        block: A tool_result content block.
        tool_use_map: Optional pre-built mapping from use_id to name.

    Returns:
        The tool name string, or None if unresolvable.
    """
    # Direct field (may be set by local compact passes)
    explicit = block.get("tool_name", "")
    if explicit:
        return explicit

    # Resolve via tool_use_id -> tool_use_map
    use_id = block.get("tool_use_id", "")
    if use_id and tool_use_map:
        return tool_use_map.get(use_id)

    # Fallback: try to extract from tool_use_id prefix if it follows naming convention
    if use_id:
        # Some systems use prefixes like "Bash_toolu_..." — check known tool names
        for known in TOOLS_CLEARABLE_RESULTS + TOOLS_PRESERVED_RESULTS:
            if use_id.startswith(known) or known.lower() in use_id.lower():
                return known

    return None


# ---------------------------------------------------------------------------
# Token estimation for strategies
# ---------------------------------------------------------------------------


def estimate_microcompact_savings(
    messages: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
) -> MicroCompactTokenState:
    """Estimate token savings from applying API micro-compact strategies.

    Walks the message list and computes how many tokens each strategy
    would free without actually modifying the messages.

    Args:
        messages: The conversation message list.
        options: Runtime options (auto-detected from env if None).

    Returns:
        MicroCompactTokenState with detailed token accounting.
    """
    opts = options or get_default_api_microcompact_options()
    opts, _ = validate_microcompact_options(opts)

    state = MicroCompactTokenState()
    state.total_input_tokens = estimate_messages_tokens(messages)
    state.estimated_total_tokens = state.total_input_tokens
    state.pre_compact_tokens = state.total_input_tokens
    state.is_above_threshold = (
        state.total_input_tokens > opts.max_input_tokens
    )

    if state.total_input_tokens > 0:
        state.percent_remaining = max(
            0.0,
            min(
                100.0,
                100.0
                * (1.0 - state.total_input_tokens / max(opts.max_input_tokens, 1)),
            ),
        )

    if not state.is_above_threshold:
        state.post_compact_tokens = state.total_input_tokens
        return state

    # Build tool_use_map once for all helpers
    tool_use_map = _build_tool_use_map(messages)

    # 1. Estimate thinking clearing savings
    if opts.enable_thinking_clearing and not opts.is_redact_thinking_active:
        thinking_tokens = _count_thinking_tokens(messages, opts.thinking_keep_turns)
        if opts.clear_all_thinking:
            state.tokens_freed_by_thinking = thinking_tokens
        else:
            # Keep last N turns of thinking
            kept_thinking_turns = 0
            for msg in reversed(messages):
                if msg.get("type") == "assistant":
                    content = msg.get("message", {}).get("content", [])
                    if isinstance(content, list) and any(
                        b.get("type") == "thinking"
                        for b in content
                        if isinstance(b, dict)
                    ):
                        kept_thinking_turns += 1
                        if kept_thinking_turns >= opts.thinking_keep_turns:
                            break
            # Rough: all thinking minus the kept turns proportion
            kept_ratio = min(
                kept_thinking_turns / max(len(messages) or 1, 1), 1.0
            )
            state.tokens_freed_by_thinking = int(
                thinking_tokens * (1.0 - kept_ratio)
            )

    # 2. Estimate tool clearing savings
    if opts.enable_tool_result_clearing or opts.enable_tool_use_clearing:
        state.tokens_freed_by_tool_clearing = _count_clearable_tool_tokens(
            messages, opts, tool_use_map=tool_use_map
        )

    # 3. Estimate compact boundary savings — walk prefix of messages
    if opts.enable_compact_boundary and state.is_above_threshold:
        excess = state.total_input_tokens - opts.target_input_tokens
        # Identify the prefix of messages that would be covered by the boundary
        prefix_tokens = _estimate_boundary_prefix_tokens(
            messages, max_boundary=opts.compact_boundary_max_tokens
        )
        state.tokens_freed_by_boundary = min(
            excess, prefix_tokens, state.total_input_tokens // 2
        )

    state.post_compact_tokens = max(
        0,
        state.total_input_tokens - state.total_tokens_freed,
    )

    return state


def _estimate_boundary_prefix_tokens(
    messages: list[dict[str, Any]],
    *,
    max_boundary: int,
) -> int:
    """Estimate how many tokens would be freed by a compact boundary
    that summarizes the oldest messages.

    Walks messages from the start to find the prefix whose cumulative
    token count is close to max_boundary (the API will summarize up to
    that point).  The savings is the token count of those prefix messages
    minus a small overhead for the boundary marker itself.

    Args:
        messages: Full message list.
        max_boundary: Max tokens the boundary marker will consume.

    Returns:
        Estimated tokens freed.
    """
    if not messages:
        return 0

    cumulative = 0
    idx = 0
    boundary_overhead = 200  # approximate tokens for the boundary marker

    for i, msg in enumerate(messages):
        msg_tokens = estimate_message_tokens(msg) + 4  # +4 overhead
        if cumulative + msg_tokens > max_boundary:
            idx = i
            break
        cumulative += msg_tokens
        idx = i + 1
    else:
        idx = len(messages)

    return max(0, cumulative - boundary_overhead)


# ---------------------------------------------------------------------------
# Strategy builders
# ---------------------------------------------------------------------------


def build_thinking_clear_edit(
    *,
    keep_turns: int = DEFAULT_THINKING_KEEP_TURNS,
    clear_all: bool = False,
) -> ClearThinkingEdit:
    """Build a clear_thinking edit for the API.

    Args:
        keep_turns: Number of most recent thinking turns to preserve.
        clear_all: If True, clear all thinking content.
    """
    keep: Union[Literal["all"], dict[str, Any]]
    if clear_all:
        keep = {
            "type": "thinking_turns",
            "value": 1,
        }
    else:
        keep = {
            "type": "thinking_turns",
            "value": max(keep_turns, 0),
        }
    return ClearThinkingEdit(type="clear_thinking_20251015", keep=keep)


def build_tool_uses_clear_edit(
    *,
    trigger_value: int,
    keep_target: int,
    clear_inputs: bool = True,
    clear_results: bool = True,
    tool_allowlist: Optional[list[str]] = None,
    tool_excludelist: Optional[list[str]] = None,
) -> ClearToolUsesEdit:
    """Build a clear_tool_uses edit for the API.

    Args:
        trigger_value: Input token count that triggers clearing.
        keep_target: Token count to keep after clearing.
        clear_inputs: If True, clear tool inputs.
        clear_results: If True, clear tool results.
        tool_allowlist: Specific tools to clear. If None, uses
            TOOLS_CLEARABLE_RESULTS for results, TOOLS_CLEARABLE_USES
            for inputs.
        tool_excludelist: Tools to exclude from clearing (applied on top
            of the allowlist).  Mutually exclusive with tool_allowlist
            for the same category — allowlist wins.

    Returns:
        A populated ClearToolUsesEdit ready for API serialization.
    """
    # Compute the effective clear-at-least value (must be positive)
    clear_amount = max(trigger_value - keep_target, 1)

    edit = ClearToolUsesEdit(
        type="clear_tool_uses_20250919",
        trigger=TokenTrigger(type="input_tokens", value=trigger_value),
        clear_at_least=ClearAtLeast(
            type="input_tokens",
            value=clear_amount,
        ),
    )

    if clear_results:
        if tool_allowlist is not None:
            # User-provided allowlist takes priority
            edit.clear_tool_results = list(tool_allowlist)
        else:
            results = list(TOOLS_CLEARABLE_RESULTS)
            # Apply excludelist as a filter on the default results set
            if tool_excludelist:
                exclude_set = set(tool_excludelist)
                results = [t for t in results if t not in exclude_set]
            edit.clear_tool_results = results

    if clear_inputs:
        # Tool inputs use TOOLS_CLEARABLE_USES by default (narrower set)
        inputs = list(TOOLS_CLEARABLE_USES)
        if tool_excludelist:
            exclude_set = set(tool_excludelist)
            inputs = [t for t in inputs if t not in exclude_set]
        edit.clear_tool_inputs = inputs

    # If excludelist provided and no allowlist, also set exclude_tools
    # to handle tools not in the base clearable lists
    if tool_excludelist and tool_allowlist is None:
        # Only exclude tools that are NOT already in the inputs/results lists
        edit.exclude_tools = list(tool_excludelist)

    return edit


def build_compact_boundary_edit(
    *,
    trigger_value: int,
    max_tokens: int = DEFAULT_COMPACT_BOUNDARY_MAX_TOKENS,
    direction: Literal["from", "up_to"] = "from",
) -> CompactBoundaryEdit:
    """Build a compact_boundary edit for the API.

    When triggered, the API inserts a compact boundary that summarizes
    prior messages and replaces them with a concise marker.

    Args:
        trigger_value: Token count at which to trigger the boundary.
        max_tokens: Maximum tokens for the boundary marker.
        direction: 'from' = summarize from start, 'up_to' = summarize up to trigger.
    """
    return CompactBoundaryEdit(
        type="compact_boundary",
        trigger=TokenTrigger(type="input_tokens", value=trigger_value),
        max_tokens=max(max_tokens, 100),
        direction=direction,
    )


# ---------------------------------------------------------------------------
# Primary context management entry point
# ---------------------------------------------------------------------------


def get_api_context_management(
    *,
    has_thinking: bool = False,
    is_redact_thinking_active: bool = False,
    clear_all_thinking: bool = False,
    options: Optional[APIMicroCompactOptions] = None,
    messages_token_count: int = 0,
) -> Optional[ContextManagementConfig]:
    """Build the context_management config for an API call.

    This is the primary entry point — called before each model turn
    to determine which server-side context management strategies
    should be active.

    Args:
        has_thinking: Whether the current model supports thinking.
        is_redact_thinking_active: Whether thinking redaction is on.
        clear_all_thinking: Force-clear all thinking blocks.
        options: Runtime options (auto-detected from env if None).
        messages_token_count: Estimated token count for messages.
            Used to decide whether tool-clearing should trigger.

    Returns:
        ContextManagementConfig with edits list, or None if no strategies apply.
    """
    opts = options or get_default_api_microcompact_options()
    opts, _ = validate_microcompact_options(opts)

    if not opts.enabled:
        return None

    strategies: list[dict[str, Any]] = []

    # --- Thinking clearing ---
    if has_thinking and not is_redact_thinking_active and opts.enable_thinking_clearing:
        keep: Union[Literal["all"], dict[str, Any]]
        if clear_all_thinking or opts.clear_all_thinking:
            keep = {"type": "thinking_turns", "value": 1}
        elif opts.thinking_keep_turns > 0:
            keep = {"type": "thinking_turns", "value": opts.thinking_keep_turns}
        else:
            keep = "all"

        strategies.append(
            {
                "type": "clear_thinking_20251015",
                "keep": keep,
            }
        )

    # --- Tool clearing (ant users only) ---
    if not opts.is_ant_user:
        return ContextManagementConfig(edits=strategies) if strategies else None

    use_clear_tool_results = opts.use_api_clear_tool_results
    use_clear_tool_uses = opts.use_api_clear_tool_uses

    if not use_clear_tool_results and not use_clear_tool_uses:
        return ContextManagementConfig(edits=strategies) if strategies else None

    trigger = opts.max_input_tokens
    keep_target = opts.target_input_tokens

    if use_clear_tool_results:
        strategies.append(
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": trigger},
                "clear_at_least": {
                    "type": "input_tokens",
                    "value": max(trigger - keep_target, 1),
                },
                "clear_tool_results": TOOLS_CLEARABLE_RESULTS,
            }
        )

    if use_clear_tool_uses:
        strategies.append(
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": trigger},
                "clear_at_least": {
                    "type": "input_tokens",
                    "value": max(trigger - keep_target, 1),
                },
                "clear_tool_inputs": TOOLS_CLEARABLE_USES,
            }
        )

    # --- Compact boundary (ant users with flag) ---
    if opts.enable_compact_boundary:
        strategies.append(
            {
                "type": "compact_boundary",
                "trigger": {"type": "input_tokens", "value": trigger},
                "max_tokens": opts.compact_boundary_max_tokens,
                "direction": "from",
            }
        )

    return ContextManagementConfig(edits=strategies) if strategies else None


# ---------------------------------------------------------------------------
# Full request-body builder
# ---------------------------------------------------------------------------


def build_context_management_body(
    *,
    has_thinking: bool = False,
    is_redact_thinking_active: bool = False,
    options: Optional[APIMicroCompactOptions] = None,
    messages_token_count: int = 0,
) -> Optional[dict[str, Any]]:
    """Build the full context_management field for an API request.

    Returns a dict suitable for serialization in the API request body:
        {"edits": [...]}

    Returns None if no context management strategies apply.
    """
    config = get_api_context_management(
        has_thinking=has_thinking,
        is_redact_thinking_active=is_redact_thinking_active,
        options=options,
        messages_token_count=messages_token_count,
    )

    if config is None:
        return None

    return config.to_body()


# ---------------------------------------------------------------------------
# Trigger decision logic
# ---------------------------------------------------------------------------


def should_apply_api_microcompact(
    messages: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
    token_state: Optional[MicroCompactTokenState] = None,
) -> bool:
    """Determine whether API-level micro-compact should be applied.

    Checks token counts against configured thresholds. Returns True
    if the estimated input tokens exceed the trigger threshold.

    Args:
        messages: Current message list.
        options: Runtime options.
        token_state: Pre-computed token state (optional — computed if None).
    """
    opts = options or get_default_api_microcompact_options()

    if not opts.enabled:
        return False

    if token_state is None:
        token_state = estimate_microcompact_savings(messages, options=opts)

    if token_state.total_input_tokens < opts.min_tokens_to_compact:
        return False

    return token_state.is_above_threshold


# ---------------------------------------------------------------------------
# Internal DRY helper — rebuild a message with modified content
# ---------------------------------------------------------------------------


def _rebuild_message_with_modified_content(
    msg: dict[str, Any],
    new_content: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a copy of *msg* with its content replaced by *new_content*.

    Keeps all other message fields intact.  Used by local-compact
    transforms to avoid repetitive dict-merging code.
    """
    return {
        **msg,
        "message": {**msg.get("message", {}), "content": new_content},
    }


# ---------------------------------------------------------------------------
# Apply to messages (local simulation of API behavior)
# ---------------------------------------------------------------------------


def apply_api_microcompact_to_messages(
    messages: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
    thinking_keep_turns: int = DEFAULT_THINKING_KEEP_TURNS,
    clear_all_thinking: bool = False,
    is_redact_thinking_active: bool = False,
) -> APIMicroCompactResult:
    """Apply API-microcompact transformations locally to messages.

    Simulates what the API would do server-side. Useful for:
    - Estimating token savings before making an API call
    - Dry-run previews of compaction effects
    - Testing and debugging context management strategies

    Returns an APIMicroCompactResult with the modified messages and
    token accounting.
    """
    opts = options or get_default_api_microcompact_options()
    errors: list[str] = []

    try:
        opts, issues = validate_microcompact_options(opts)
        if issues:
            errors.extend(issues)
    except Exception as exc:
        errors.append("Option validation failed: {}".format(exc))
        return APIMicroCompactResult(
            edits=[],
            token_state=MicroCompactTokenState(),
            was_applied=False,
            strategies_used=[],
            config=opts,
            errors=errors,
        )

    token_state = estimate_microcompact_savings(messages, options=opts)
    token_state.current_turn = token_state.last_compact_turn + 1

    if not token_state.is_above_threshold:
        return APIMicroCompactResult(
            edits=[],
            token_state=token_state,
            was_applied=False,
            strategies_used=[],
            config=opts,
        )

    result = list(messages)
    edits: list[dict[str, Any]] = []
    strategies_used: list[str] = []

    # 1. Apply thinking clearing
    if opts.enable_thinking_clearing and not is_redact_thinking_active:
        keep_value = (
            1 if (clear_all_thinking or opts.clear_all_thinking)
            else thinking_keep_turns
        )
        result, thinking_freed = _clear_thinking_from_messages(
            result, keep_turns=keep_value
        )
        token_state.tokens_freed_by_thinking = thinking_freed
        if thinking_freed > 0:
            strategies_used.append("clear_thinking")
            edits.append(
                {
                    "type": "clear_thinking_20251015",
                    "keep": {"type": "thinking_turns", "value": keep_value},
                }
            )

    # 2. Apply tool result clearing
    tool_use_map = _build_tool_use_map(result)
    if opts.enable_tool_result_clearing:
        result, tool_freed = _clear_tool_results_from_messages(
            result,
            clear_results=TOOLS_CLEARABLE_RESULTS,
            clear_inputs=TOOLS_CLEARABLE_USES,
            preserved=TOOLS_PRESERVED_RESULTS,
            keep_recent=4,
            tool_use_map=tool_use_map,
        )
        token_state.tokens_freed_by_tool_clearing += tool_freed
        if tool_freed > 0:
            strategies_used.append("clear_tool_results")

    # 3. Apply tool use (input) clearing
    if opts.enable_tool_use_clearing:
        result, use_freed = _clear_tool_uses_from_messages(
            result, clearable=TOOLS_CLEARABLE_USES, keep_recent=4
        )
        token_state.tokens_freed_by_tool_clearing += use_freed
        if use_freed > 0:
            strategies_used.append("clear_tool_uses")

    token_state.post_compact_tokens = max(
        0, token_state.total_input_tokens - token_state.total_tokens_freed
    )
    token_state.last_compact_turn = token_state.current_turn

    return APIMicroCompactResult(
        edits=edits,
        token_state=token_state,
        was_applied=len(strategies_used) > 0,
        strategies_used=strategies_used,
        config=opts,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Merge API strategies with local microcompact
# ---------------------------------------------------------------------------


def merge_api_and_local_microcompact(
    messages: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
    local_microcompact_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Merge API-level and local microcompact strategies.

    Called after local microcompact to determine if API-level strategies
    should also be applied. Returns a dict with:
        - messages: The (potentially further compacted) message list
        - api_context_management: The context_management body for the API
        - token_state: Token tracking
        - strategies: List of strategies applied
    """
    opts = options or get_default_api_microcompact_options()
    opts, _ = validate_microcompact_options(opts)

    working_messages = (
        local_microcompact_result.get("messages", messages)
        if local_microcompact_result
        else messages
    )

    local_tokens_freed = (
        local_microcompact_result.get("tokens_saved", 0)
        if local_microcompact_result
        else 0
    )

    api_result = apply_api_microcompact_to_messages(
        working_messages,
        options=opts,
    )

    api_result.token_state.pre_compact_tokens = max(
        0, api_result.token_state.pre_compact_tokens - local_tokens_freed
    )

    context_mgmt = build_context_management_body(
        has_thinking=True,
        options=opts,
        messages_token_count=api_result.token_state.total_input_tokens,
    )

    return {
        "messages": working_messages,
        "api_context_management": context_mgmt,
        "token_state": api_result.token_state,
        "strategies": api_result.strategies_used,
        "was_api_applied": api_result.was_applied,
        "local_tokens_freed": local_tokens_freed,
        "api_tokens_freed": api_result.token_state.total_tokens_freed,
        "errors": api_result.errors,
    }


# ---------------------------------------------------------------------------
# Compact boundary — summary formatting and validation
# ---------------------------------------------------------------------------


def format_compact_boundary_summary(
    summary: str,
    *,
    include_tokens_saved: bool = True,
    tokens_saved: int = 0,
) -> str:
    """Format a compact boundary summary message.

    Wraps the summary in <conversation_history_summary> tags as expected
    by the model, with optional token savings metadata.
    """
    lines = ["<conversation_history_summary>", summary]
    if include_tokens_saved and tokens_saved > 0:
        lines.append("[Approximately {} tokens freed]".format(tokens_saved))
    lines.append("</conversation_history_summary>")
    return "\n".join(lines)


def validate_compact_boundary_summary(summary: str) -> bool:
    """Validate that a compact boundary summary is well-formed.

    Checks:
    - Non-empty summary text
    - Presence of balanced <conversation_history_summary> tags
    - Summary does not exceed unreasonable length (> 100k chars)

    Returns True if the summary passes all validation checks.
    """
    if not summary or not summary.strip():
        return False
    if len(summary) > 100_000:
        return False
    # Check balanced tags
    open_count = summary.count("<conversation_history_summary>")
    close_count = summary.count("</conversation_history_summary>")
    if open_count != close_count or open_count == 0:
        return False
    return True


def compact_boundary_to_message(
    summary: str,
    *,
    tokens_saved: int = 0,
    include_tags: bool = True,
) -> dict[str, Any]:
    """Convert a compact boundary summary into an assistant message.

    This message can be prepended to a conversation to simulate the
    effect of a compact boundary on the client side.

    Args:
        summary: The summary text.
        tokens_saved: Optional token savings for metadata.
        include_tags: If True, wrap in <conversation_history_summary> tags.

    Returns:
        A dict suitable for inclusion in the messages list as an
        assistant-role message with the summary as text content.
    """
    text = format_compact_boundary_summary(
        summary,
        include_tokens_saved=tokens_saved > 0,
        tokens_saved=tokens_saved,
    ) if include_tags else summary

    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": text,
                    "_compact_boundary": True,
                }
            ],
        },
    }


def extract_compact_boundaries_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract compact boundary markers from a message list.

    Scans for messages/blocks marked with _compact_boundary = True
    and returns them as a list.

    Args:
        messages: Message list to scan.

    Returns:
        List of compact boundary marker messages, in order.
    """
    boundaries: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("_compact_boundary"):
                boundaries.append(msg)
                break
    return boundaries


# ---------------------------------------------------------------------------
# Tool registration (runtime modification of clearable lists)
# ---------------------------------------------------------------------------


def register_clearable_tool_result(tool_name: str) -> None:
    """Register a tool whose results can be API-cleared at runtime.

    Idempotent — duplicates are not added.
    """
    if tool_name and tool_name not in TOOLS_CLEARABLE_RESULTS:
        TOOLS_CLEARABLE_RESULTS.append(tool_name)


def register_clearable_tool_use(tool_name: str) -> None:
    """Register a tool whose uses can be API-cleared at runtime.

    Idempotent — duplicates are not added.
    """
    if tool_name and tool_name not in TOOLS_CLEARABLE_USES:
        TOOLS_CLEARABLE_USES.append(tool_name)


def register_preserved_tool(tool_name: str) -> None:
    """Register a tool whose results should NEVER be cleared.

    Idempotent — duplicates are not added.
    """
    if tool_name and tool_name not in TOOLS_PRESERVED_RESULTS:
        TOOLS_PRESERVED_RESULTS.append(tool_name)


def unregister_clearable_tool_result(tool_name: str) -> bool:
    """Remove a tool from the clearable results list.

    Returns True if the tool was removed, False if it was not present.
    """
    if tool_name in TOOLS_CLEARABLE_RESULTS:
        TOOLS_CLEARABLE_RESULTS.remove(tool_name)
        return True
    return False


def unregister_clearable_tool_use(tool_name: str) -> bool:
    """Remove a tool from the clearable uses list.

    Returns True if the tool was removed, False if it was not present.
    """
    if tool_name in TOOLS_CLEARABLE_USES:
        TOOLS_CLEARABLE_USES.remove(tool_name)
        return True
    return False


def unregister_preserved_tool(tool_name: str) -> bool:
    """Remove a tool from the preserved results list.

    Returns True if the tool was removed, False if it was not present.
    """
    if tool_name in TOOLS_PRESERVED_RESULTS:
        TOOLS_PRESERVED_RESULTS.remove(tool_name)
        return True
    return False


def reset_tool_classifications() -> None:
    """Reset all tool lists to their original default values."""
    TOOLS_CLEARABLE_RESULTS.clear()
    TOOLS_CLEARABLE_RESULTS.extend([
        "Bash", "BashOutput", "Read", "Grep", "Glob",
        "WebSearch", "WebFetch", "Task", "BashTool", "LS", "FileRead",
    ])

    TOOLS_CLEARABLE_USES.clear()
    TOOLS_CLEARABLE_USES.extend([
        "BashOutput", "Read", "Grep", "Glob", "LS",
    ])

    TOOLS_PRESERVED_RESULTS.clear()
    TOOLS_PRESERVED_RESULTS.extend([
        "FileEdit", "FileWrite", "TodoWrite", "NotebookEdit",
        "Skill", "TaskCreate", "TaskUpdate", "EnterWorktree", "ExitWorktree",
    ])


# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------


def classify_tools_for_clearing(
    tools: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
) -> dict[str, list[str]]:
    """Classify a list of tool definitions into clearable/preserved groups.

    Returns a dict with keys:
        - 'clearable_results': tools whose results can be cleared
        - 'clearable_uses': tools whose uses (inputs) can be cleared
        - 'preserved': tools whose results MUST be preserved
        - 'unclassified': tools not in any known category
        - 'all_tools': all tool names

    Usage example:
        >>> classify_tools_for_clearing([{"name": "Bash"}, {"name": "FileEdit"}])
        {
            "clearable_results": ["Bash"],
            "clearable_uses": [],
            "preserved": ["FileEdit"],
            "unclassified": [],
            "all_tools": ["Bash", "FileEdit"],
        }
    """
    _ = options  # reserved for future per-option classification
    tool_names = [
        t.get("name", "")
        for t in tools
        if isinstance(t, dict) and t.get("name")
    ]

    clearable_results_set = set(TOOLS_CLEARABLE_RESULTS)
    clearable_uses_set = set(TOOLS_CLEARABLE_USES)
    preserved_set = set(TOOLS_PRESERVED_RESULTS)

    clearable_results = [n for n in tool_names if n in clearable_results_set]
    clearable_uses = [n for n in tool_names if n in clearable_uses_set]
    preserved = [n for n in tool_names if n in preserved_set]

    # Tools not classified into any of the above buckets
    classified = clearable_results_set | clearable_uses_set | preserved_set
    unclassified = [n for n in tool_names if n not in classified]

    return {
        "clearable_results": clearable_results,
        "clearable_uses": clearable_uses,
        "preserved": preserved,
        "unclassified": unclassified,
        "all_tools": tool_names,
    }


# ---------------------------------------------------------------------------
# Integration helpers — called from compact pipeline
# ---------------------------------------------------------------------------


def should_apply_local_thinking_clear(
    messages: list[dict[str, Any]],
    *,
    is_redact_thinking_active: bool = False,
    clear_all_thinking: bool = False,
    options: Optional[APIMicroCompactOptions] = None,
) -> bool:
    """Check if local thinking-clearing should run.

    Only applies when the API won't handle it server-side (redact active
    or feature not enabled).
    """
    if is_redact_thinking_active:
        return True  # API can't clear what's already redacted
    if clear_all_thinking:
        return True
    opts = options or get_default_api_microcompact_options()
    return not opts.enable_thinking_clearing


def compute_token_diff(
    messages_before: list[dict[str, Any]],
    messages_after: list[dict[str, Any]],
) -> dict[str, int]:
    """Compute token difference between two message lists.

    Returns dict with:
        - tokens_before: Token count of original messages
        - tokens_after: Token count of modified messages
        - tokens_freed: Difference (before - after)
        - pct_reduction: Percentage reduction
    """
    before = estimate_messages_tokens(messages_before)
    after = estimate_messages_tokens(messages_after)
    delta = max(0, before - after)
    pct = (delta / max(before, 1)) * 100.0

    return {
        "tokens_before": before,
        "tokens_after": after,
        "tokens_freed": delta,
        "pct_reduction": round(pct, 1),
    }


# ---------------------------------------------------------------------------
# Internal helpers — thinking clearing
# ---------------------------------------------------------------------------


def _count_thinking_tokens(
    messages: list[dict[str, Any]],
    keep_turns: int = DEFAULT_THINKING_KEEP_TURNS,
) -> int:
    """Count tokens in thinking blocks across all messages.

    Args:
        messages: Full message list.
        keep_turns: (unused currently; reserved for per-message granularity).

    Returns:
        Total estimated token count of all thinking blocks.
    """
    total = 0
    for msg in messages:
        if msg.get("type") == "assistant":
            content = msg.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "thinking":
                        total += estimate_tokens(
                            block.get("thinking", "")
                        )
    return total


def _clear_thinking_from_messages(
    messages: list[dict[str, Any]],
    *,
    keep_turns: int = 1,
) -> tuple[list[dict[str, Any]], int]:
    """Remove thinking blocks from older messages, keeping last N turns.

    Returns (modified_messages, tokens_freed).

    Args:
        messages: The message list to transform.
        keep_turns: Number of most recent thinking turns to preserve.
            A value of 0 clears ALL thinking blocks.

    Returns:
        Tuple of (modified_messages, tokens_freed).
    """
    result: list[dict[str, Any]] = []
    tokens_freed = 0
    thinking_turns_seen = 0

    # Count thinking turns from the end to identify which indices to keep
    thinking_turn_indices: set[int] = set()
    if keep_turns > 0:
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("type") == "assistant":
                content = msg.get("message", {}).get("content", [])
                if isinstance(content, list) and any(
                    b.get("type") == "thinking"
                    for b in content
                    if isinstance(b, dict)
                ):
                    if thinking_turns_seen < keep_turns:
                        thinking_turn_indices.add(i)
                        thinking_turns_seen += 1

    for i, msg in enumerate(messages):
        if i in thinking_turn_indices:
            result.append(msg)
            continue

        if msg.get("type") != "assistant":
            result.append(msg)
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content: list[dict[str, Any]] = []
        modified = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thinking_text = block.get("thinking", "")
                if isinstance(thinking_text, str):
                    tokens_freed += estimate_tokens(thinking_text)
                modified = True
                # Replace with a placeholder
                new_content.append(
                    {
                        "type": "thinking",
                        "thinking": "[Thinking removed by micro-compact]",
                    }
                )
            else:
                new_content.append(block)

        if modified:
            new_msg = _rebuild_message_with_modified_content(msg, new_content)
            result.append(new_msg)
        else:
            result.append(msg)

    return result, tokens_freed


# ---------------------------------------------------------------------------
# Internal helpers — tool clearing
# ---------------------------------------------------------------------------


def _count_clearable_tool_tokens(
    messages: list[dict[str, Any]],
    options: APIMicroCompactOptions,
    *,
    tool_use_map: Optional[dict[str, str]] = None,
) -> int:
    """Count tokens in clearable tool result blocks.

    Uses the tool_use_map to resolve tool_use_id to tool names,
    so we can accurately determine which results are clearable.

    Args:
        messages: The full message list.
        options: Runtime options controlling which categories to count.
        tool_use_map: Optional precomputed id->name mapping. If not
            provided it will be built from the messages.

    Returns:
        Total estimated token count of clearable tool results.
    """
    if tool_use_map is None:
        tool_use_map = _build_tool_use_map(messages)

    total = 0
    # Only count older messages (exclude the last 4)
    limit = max(0, len(messages) - 4)

    for i in range(limit):
        msg = messages[i]
        if msg.get("type") != "user":
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue

            tool_name = _resolve_tool_name(block, tool_use_map)

            # Determine if this tool's results should be counted
            if options.enable_tool_result_clearing and (
                tool_name in TOOLS_CLEARABLE_RESULTS
            ):
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    total += estimate_tokens(result_content)
                elif isinstance(result_content, list):
                    total += estimate_tokens(str(result_content))

            # Also count tool_use input clearing if enabled
            if options.enable_tool_use_clearing and (
                tool_name in TOOLS_CLEARABLE_USES
            ):
                total += 20  # overhead for the tool use input

    return total


def _clear_tool_results_from_messages(
    messages: list[dict[str, Any]],
    *,
    clear_results: list[str],
    clear_inputs: list[str],
    preserved: list[str],
    keep_recent: int = 4,
    tool_use_map: Optional[dict[str, str]] = None,
) -> tuple[list[dict[str, Any]], int]:
    """Clear tool results from older messages.

    Replaces tool_result blocks whose tool is in *clear_results* (and
    NOT in *preserved*) with a placeholder.  The most recent *keep_recent*
    messages are always left untouched.

    Args:
        messages: Message list to transform.
        clear_results: List of tool names whose results may be cleared.
        clear_inputs: (unused; reserved for future input-clearing within results).
        preserved: List of tool names whose results must be kept.
        keep_recent: Number of most-recent messages to skip entirely.
        tool_use_map: Optional precomputed id->name mapping.

    Returns:
        Tuple of (modified_messages, tokens_freed).
    """
    if tool_use_map is None:
        tool_use_map = _build_tool_use_map(messages)

    clear_set = set(clear_results)
    preserve_set = set(preserved)
    result: list[dict[str, Any]] = []
    tokens_freed = 0

    for i, msg in enumerate(messages):
        # Keep most recent messages intact
        if i >= len(messages) - keep_recent:
            result.append(msg)
            continue

        if msg.get("type") != "user":
            result.append(msg)
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content: list[dict[str, Any]] = []
        modified = False
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                new_content.append(block)
                continue

            tool_name = _resolve_tool_name(block, tool_use_map)

            # Never clear tool results that are in the preserved set
            if tool_name and tool_name in preserve_set:
                new_content.append(block)
                continue

            # Only clear if tool is in the clear_results set
            if not tool_name or tool_name not in clear_set:
                new_content.append(block)
                continue

            result_content = block.get("content", "")
            if isinstance(result_content, str):
                tokens_freed += estimate_tokens(result_content)
            elif isinstance(result_content, list):
                tokens_freed += estimate_tokens(str(result_content))

            # Replace with placeholder
            placeholder = "[Tool result content cleared by API micro-compact]"
            new_content.append(
                {
                    **block,
                    "content": placeholder,
                    "is_compacted": True,
                }
            )
            modified = True

        if modified:
            new_msg = _rebuild_message_with_modified_content(msg, new_content)
            result.append(new_msg)
        else:
            result.append(msg)

    return result, tokens_freed


def _clear_tool_uses_from_messages(
    messages: list[dict[str, Any]],
    *,
    clearable: list[str],
    keep_recent: int = 4,
) -> tuple[list[dict[str, Any]], int]:
    """Clear tool use inputs from older messages.

    Replaces tool_use blocks whose tool is in *clearable* with
    stripped placeholder inputs. The most recent *keep_recent*
    messages are always left untouched.

    Args:
        messages: Message list to transform.
        clearable: Tool names whose inputs can be cleared.
        keep_recent: Number of most-recent messages to skip entirely.

    Returns:
        Tuple of (modified_messages, tokens_freed).
    """
    clearable_set = set(clearable)
    result: list[dict[str, Any]] = []
    tokens_freed = 0

    for i, msg in enumerate(messages):
        if i >= len(messages) - keep_recent:
            result.append(msg)
            continue

        if msg.get("type") != "assistant":
            result.append(msg)
            continue

        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content: list[dict[str, Any]] = []
        modified = False
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                new_content.append(block)
                continue

            tool_name = block.get("name", "")
            if not tool_name or tool_name not in clearable_set:
                new_content.append(block)
                continue

            input_val = block.get("input", {})
            input_str = str(input_val)
            tokens_freed += estimate_tokens(input_str)

            # Replace with stripped version
            new_content.append(
                {
                    **block,
                    "input": {
                        "_compacted": True,
                        "_tool": tool_name,
                        "_original_size": len(input_str),
                    },
                    "is_compacted": True,
                }
            )
            modified = True

        if modified:
            new_msg = _rebuild_message_with_modified_content(msg, new_content)
            result.append(new_msg)
        else:
            result.append(msg)

    return result, tokens_freed


# ---------------------------------------------------------------------------
# Serialization helpers — token state
# ---------------------------------------------------------------------------


def serialize_token_state(state: Optional[MicroCompactTokenState]) -> Optional[str]:
    """Serialize a MicroCompactTokenState to a JSON string.

    Returns None if state is None.
    """
    if state is None:
        return None
    return json.dumps(state.to_dict(), sort_keys=True)


def deserialize_token_state(raw: Optional[str]) -> Optional[MicroCompactTokenState]:
    """Deserialize a JSON string back to a MicroCompactTokenState.

    Returns None if raw is None or empty, or on parse failure.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return MicroCompactTokenState.from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None


def serialize_options(opts: Optional[APIMicroCompactOptions]) -> Optional[str]:
    """Serialize APIMicroCompactOptions to a JSON string.

    Only serializes fields that differ from defaults for compactness.
    """
    if opts is None:
        return None
    data: dict[str, Any] = {
        "enabled": opts.enabled,
        "max_input_tokens": opts.max_input_tokens,
        "target_input_tokens": opts.target_input_tokens,
        "enable_thinking_clearing": opts.enable_thinking_clearing,
        "enable_tool_result_clearing": opts.enable_tool_result_clearing,
        "enable_tool_use_clearing": opts.enable_tool_use_clearing,
        "enable_compact_boundary": opts.enable_compact_boundary,
        "thinking_keep_turns": opts.thinking_keep_turns,
        "clear_all_thinking": opts.clear_all_thinking,
        "is_ant_user": opts.is_ant_user,
    }
    return json.dumps(data, sort_keys=True)


def deserialize_options(raw: Optional[str]) -> Optional[APIMicroCompactOptions]:
    """Deserialize a JSON string back to APIMicroCompactOptions."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        opts = get_default_api_microcompact_options()
        for key in (
            "enabled", "max_input_tokens", "target_input_tokens",
            "enable_thinking_clearing", "enable_tool_result_clearing",
            "enable_tool_use_clearing", "enable_compact_boundary",
            "thinking_keep_turns", "clear_all_thinking", "is_ant_user",
        ):
            if key in data:
                setattr(opts, key, data[key])
        return opts
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def reset_microcompact_state(state: Optional[MicroCompactTokenState]) -> MicroCompactTokenState:
    """Reset a MicroCompactTokenState, or create a fresh one if None.

    Convenience helper for start-of-session or config-change resets.
    """
    if state is None:
        return MicroCompactTokenState()
    state.reset()
    return state


def increment_turn_state(state: MicroCompactTokenState) -> None:
    """Advance the turn counter and carry forward last-compact tracking.

    Call this at the start of each model turn to keep the state aligned.
    """
    state.last_compact_turn = state.current_turn
    state.current_turn += 1
    state.percent_remaining = max(
        0.0,
        100.0 - (state.total_input_tokens / max(state.total_input_tokens or 1, 1)) * 100.0,
    )


# ---------------------------------------------------------------------------
# Result logging
# ---------------------------------------------------------------------------


def log_microcompact_result(
    result: APIMicroCompactResult,
    *,
    prefix: str = "[api_microcompact]",
) -> None:
    """Log a structured summary of a microcompact result.

    Emits to stderr so it does not interfere with stdout-based protocols.
    Logging is gated by LOGGING_ENABLED (env-driven).
    """
    if not LOGGING_ENABLED:
        return

    summary = result.to_summary()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    import sys

    print(
        "{} {} applied={} strategies={} freed={} pre={} post={} errors={}".format(
            ts,
            prefix,
            summary["was_applied"],
            ",".join(summary["strategies_used"]) or "none",
            summary["tokens_freed"],
            summary["pre_compact_tokens"],
            summary["post_compact_tokens"],
            len(summary["errors"]),
        ),
        file=sys.stderr,
        flush=True,
    )

    if result.errors:
        for err in result.errors:
            print("{} {} ERROR: {}".format(ts, prefix, err), file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Aggregated status summary
# ---------------------------------------------------------------------------


def get_microcompact_summary(
    messages: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
) -> dict[str, Any]:
    """Produce an aggregated status summary for monitoring/dashboard use.

    Combines estimation, configuration, and classification into one
    dict suitable for logging, telemetry, or debugging.

    Args:
        messages: Current message list.
        options: Runtime options.

    Returns:
        Dict with keys:
            - estimated_tokens: Total estimated input tokens
            - percent_used: Percentage of limit consumed
            - is_critical: True if above threshold
            - strategies_enabled: Which strategies are active
            - configured_thresholds: Current threshold values
            - tool_classifications: Breakdown of tools in the message list
            - validation_issues: Any config validation problems
    """
    opts, issues = validate_microcompact_options(
        options or get_default_api_microcompact_options()
    )
    state = estimate_microcompact_savings(messages, options=opts)

    # Classify tools found in messages
    all_tool_names: set[str] = set()
    for msg in messages:
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name:
                        all_tool_names.add(name)

    tool_defs = [{"name": n} for n in all_tool_names]
    classification = classify_tools_for_clearing(tool_defs, options=opts)

    return {
        "estimated_tokens": state.total_input_tokens,
        "percent_used": round(100.0 - state.percent_remaining, 1),
        "is_critical": state.is_above_threshold,
        "pre_compact_tokens": state.pre_compact_tokens,
        "estimated_post_compact_tokens": state.post_compact_tokens,
        "estimated_tokens_freed": state.total_tokens_freed,
        "strategies_enabled": {
            "thinking_clearing": opts.enable_thinking_clearing,
            "tool_result_clearing": opts.enable_tool_result_clearing,
            "tool_use_clearing": opts.enable_tool_use_clearing,
            "compact_boundary": opts.enable_compact_boundary,
        },
        "configured_thresholds": {
            "max_input_tokens": opts.max_input_tokens,
            "target_input_tokens": opts.target_input_tokens,
            "min_tokens_to_compact": opts.min_tokens_to_compact,
        },
        "tool_classifications": classification,
        "validation_issues": issues,
    }


# ---------------------------------------------------------------------------
# Public API — simplified entry points for external consumers
# ---------------------------------------------------------------------------


def get_context_management_for_turn(
    *,
    messages: list[dict[str, Any]],
    model_supports_thinking: bool = False,
    is_redact_active: bool = False,
    options: Optional[APIMicroCompactOptions] = None,
) -> Optional[dict[str, Any]]:
    """All-in-one entry point for a model turn.

    Combines: token estimation, trigger decision, strategy selection,
    and context_management body construction.

    Returns the API request body's context_management dict, or None
    if nothing should be applied.
    """
    opts = options or get_default_api_microcompact_options()

    if not opts.enabled:
        return None

    if not should_apply_api_microcompact(
        messages, options=opts
    ):
        return None

    return build_context_management_body(
        has_thinking=model_supports_thinking,
        is_redact_thinking_active=is_redact_active,
        options=opts,
        messages_token_count=estimate_messages_tokens(messages),
    )


def preview_api_microcompact(
    messages: list[dict[str, Any]],
    *,
    options: Optional[APIMicroCompactOptions] = None,
) -> dict[str, Any]:
    """Preview what API micro-compact would do to messages.

    Returns a summary dict with token estimates, strategies, and
    whether compaction would trigger. Does NOT modify messages.
    """
    opts = options or get_default_api_microcompact_options()
    opts, issues = validate_microcompact_options(opts)

    token_state = estimate_microcompact_savings(messages, options=opts)
    body = build_context_management_body(
        has_thinking=True,
        options=opts,
        messages_token_count=token_state.total_input_tokens,
    )

    return {
        "would_trigger": token_state.is_above_threshold,
        "pre_compact_tokens": token_state.pre_compact_tokens,
        "estimated_post_compact_tokens": token_state.post_compact_tokens,
        "estimated_tokens_freed": token_state.total_tokens_freed,
        "breakdown": {
            "thinking": token_state.tokens_freed_by_thinking,
            "tool_clearing": token_state.tokens_freed_by_tool_clearing,
            "boundary": token_state.tokens_freed_by_boundary,
        },
        "percent_remaining": token_state.percent_remaining,
        "context_management_body": body,
        "threshold": {
            "max_input_tokens": opts.max_input_tokens,
            "target_input_tokens": opts.target_input_tokens,
            "min_tokens_to_compact": opts.min_tokens_to_compact,
        },
        "strategies_available": {
            "thinking_clearing": opts.enable_thinking_clearing,
            "tool_result_clearing": opts.enable_tool_result_clearing,
            "tool_use_clearing": opts.enable_tool_use_clearing,
            "compact_boundary": opts.enable_compact_boundary,
        },
        "validation_issues": issues,
    }


def configure_api_microcompact(
    *,
    enabled: Optional[bool] = None,
    max_input_tokens: Optional[int] = None,
    target_input_tokens: Optional[int] = None,
    enable_thinking_clearing: Optional[bool] = None,
    enable_tool_result_clearing: Optional[bool] = None,
    enable_tool_use_clearing: Optional[bool] = None,
    enable_compact_boundary: Optional[bool] = None,
    thinking_keep_turns: Optional[int] = None,
    clear_all_thinking: Optional[bool] = None,
) -> APIMicroCompactOptions:
    """Programmatic configuration of API micro-compact options.

    Values provided override env defaults. Unspecified values keep
    their default or env-derived values.

    Returns a validated and clamped APIMicroCompactOptions.
    """
    opts = get_default_api_microcompact_options()

    if enabled is not None:
        opts.enabled = enabled
    if max_input_tokens is not None:
        opts.max_input_tokens = max_input_tokens
    if target_input_tokens is not None:
        opts.target_input_tokens = target_input_tokens
    if enable_thinking_clearing is not None:
        opts.enable_thinking_clearing = enable_thinking_clearing
    if enable_tool_result_clearing is not None:
        opts.enable_tool_result_clearing = enable_tool_result_clearing
    if enable_tool_use_clearing is not None:
        opts.enable_tool_use_clearing = enable_tool_use_clearing
    if enable_compact_boundary is not None:
        opts.enable_compact_boundary = enable_compact_boundary
    if thinking_keep_turns is not None:
        opts.thinking_keep_turns = thinking_keep_turns
    if clear_all_thinking is not None:
        opts.clear_all_thinking = clear_all_thinking

    # Validate and return clamped options
    validated, _ = validate_microcompact_options(opts)
    return validated
