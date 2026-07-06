"""
Plan mode v2 — agent counts, feature gates, tool allowlists, plan lifecycle,
pewter ledger cost tracking, and multi-agent plan orchestration.

Port of: src/utils/planModeV2.ts (expanded with full plan lifecycle logic).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

from hare.utils.auth import get_subscription_type
from hare.utils.env_utils import is_env_defined_falsy, is_env_truthy


# ============================================================================
# Type aliases
# ============================================================================

PewterLedgerVariant = Optional[Literal["trim", "cut", "cap"]]

PlanModePhase = Literal[
    "idle", "entering", "exploring", "writing_plan",
    "awaiting_approval", "approved", "rejected", "exiting"
]

PlanModeAgentType = Literal["explore", "code_review", "architect", "implementer"]

PlanApprovalStatus = Literal["pending", "approved", "rejected", "modified"]


# ============================================================================
# Constants
# ============================================================================

# Default plan file path (relative to project root)
DEFAULT_PLAN_FILE = ".claude/plans/plan.md"

# Maximum plan file size in bytes (5 MB)
MAX_PLAN_FILE_SIZE = 5 * 1024 * 1024

# Minimum plan content length to be considered valid (50 chars)
MIN_PLAN_CONTENT_LENGTH = 50

# Maximum plan mode duration in milliseconds (default: 30 minutes)
DEFAULT_PLAN_MODE_TIMEOUT_MS = 30 * 60 * 1000

# Maximum number of plan rejections before forcing exit
MAX_PLAN_REJECTIONS = 3

# Required sections in a plan (checked during validation)
REQUIRED_PLAN_SECTIONS = [
    "summary",
    "approach",
    "implementation",
]

# Optional but recommended plan sections
OPTIONAL_PLAN_SECTIONS = [
    "testing",
    "risks",
    "alternatives",
    "dependencies",
    "rollback",
]

# Plan mode v2 feature flag names (GrowthBook keys)
PLAN_MODE_V2_FEATURE_FLAG = "tengu_plan_mode_v2"
PLAN_MODE_INTERVIEW_PHASE_FLAG = "tengu_plan_mode_interview_phase"
PEWTER_LEDGER_FLAG = "tengu_pewter_ledger"
PLAN_MODE_MULTI_AGENT_FLAG = "tengu_plan_mode_multi_agent_v2"

# Tools allowed during plan mode (read-only by default)
PLAN_MODE_ALLOWED_TOOLS: list[str] = [
    "FileRead",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Task",
    "TaskCreate",
    "TaskList",
    "TaskGet",
    "TaskUpdate",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "ListMcpResources",
    "ReadMcpResource",
    "Skill",
    "ToolSearch",
    "LSP",
]

# Tools explicitly blocked in plan mode
PLAN_MODE_BLOCKED_TOOLS: list[str] = [
    "FileWrite",
    "FileEdit",
    "NotebookEdit",
    "SendMessage",
    "Config",
    "EnterWorktree",
    "ExitWorktree",
]

# Tools allowed when plan_mode_v2_interview_phase is active
PLAN_MODE_INTERVIEW_ALLOWED_EXTRA: list[str] = [
    "AskUserQuestion",
]

# Multi-agent plan mode roles and corresponding agent types
PLAN_MODE_AGENT_ROLES: dict[str, dict[str, Any]] = {
    "explore": {
        "type": "subagent",
        "agent_type": "general-purpose",
        "description": "Codebase explorer — searches, reads, and maps relevant code",
        "max_turns": 15,
    },
    "code_review": {
        "type": "subagent",
        "agent_type": "general-purpose",
        "description": "Reviews proposed plan for correctness and completeness",
        "max_turns": 10,
    },
    "architect": {
        "type": "subagent",
        "agent_type": "general-purpose",
        "description": "Designs high-level architecture and trade-off analysis",
        "max_turns": 12,
    },
}

# Rate limit tiers and their corresponding agent counts
TIER_AGENT_COUNTS: dict[str, int] = {
    "default_hare_max_20x": 3,
    "default_hare_pro_10x": 2,
    "default_hare_pro_5x": 1,
    "default": 1,
}

# Persistence path for plan mode state (relative to config dir)
PLAN_STATE_PERSIST_FILE = ".claude/plans/.plan_state.json"


# ============================================================================
# Plan state tracking
# ============================================================================


@dataclass
class PlanIteration:
    """A single iteration (revision) of the plan."""

    content: str
    approval_status: PlanApprovalStatus = "pending"
    timestamp: float = 0.0
    feedback: str = ""


@dataclass
class PlanState:
    """Current plan mode session state."""

    is_active: bool = False
    phase: PlanModePhase = "idle"
    plan_topic: str = ""
    plan_file_path: str = ""
    plan_content: str = ""
    approval_status: PlanApprovalStatus = "pending"
    entered_at: float = 0.0
    approved_at: float = 0.0
    rejected_at: float = 0.0
    explored_files: list[str] = field(default_factory=list)
    agent_ids: list[str] = field(default_factory=list)
    allowed_prompts: list[dict[str, str]] = field(default_factory=list)
    interview_answers: dict[str, str] = field(default_factory=dict)
    pewter_variant: PewterLedgerVariant = None
    # Iteration tracking
    revision_count: int = 0
    rejection_count: int = 0
    iterations: list[PlanIteration] = field(default_factory=list)
    # Plan metadata
    session_id: str = ""
    model: str = ""
    budget_limit_usd: float = 0.0
    # Interview
    interview_complete: bool = False

    def reset(self) -> None:
        self.is_active = False
        self.phase = "idle"
        self.plan_topic = ""
        self.plan_file_path = ""
        self.plan_content = ""
        self.approval_status = "pending"
        self.entered_at = 0.0
        self.approved_at = 0.0
        self.rejected_at = 0.0
        self.explored_files.clear()
        self.agent_ids.clear()
        self.allowed_prompts.clear()
        self.interview_answers.clear()
        self.pewter_variant = None
        self.revision_count = 0
        self.rejection_count = 0
        self.iterations.clear()
        self.session_id = ""
        self.model = ""
        self.budget_limit_usd = 0.0
        self.interview_complete = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize plan state to a dict for persistence."""
        return {
            "is_active": self.is_active,
            "phase": self.phase,
            "plan_topic": self.plan_topic,
            "plan_file_path": self.plan_file_path,
            "plan_content": self.plan_content[:10000],  # Truncate for persistence
            "approval_status": self.approval_status,
            "entered_at": self.entered_at,
            "approved_at": self.approved_at,
            "rejected_at": self.rejected_at,
            "explored_files": self.explored_files[:200],  # Limit stored entries
            "agent_ids": self.agent_ids,
            "allowed_prompts": self.allowed_prompts,
            "interview_answers": self.interview_answers,
            "pewter_variant": self.pewter_variant,
            "revision_count": self.revision_count,
            "rejection_count": self.rejection_count,
            "session_id": self.session_id,
            "model": self.model,
            "budget_limit_usd": self.budget_limit_usd,
            "interview_complete": self.interview_complete,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanState:
        """Restore plan state from a serialized dict."""
        state = cls()
        state.is_active = data.get("is_active", False)
        state.phase = data.get("phase", "idle")
        state.plan_topic = data.get("plan_topic", "")
        state.plan_file_path = data.get("plan_file_path", "")
        state.plan_content = data.get("plan_content", "")
        state.approval_status = data.get("approval_status", "pending")
        state.entered_at = data.get("entered_at", 0.0)
        state.approved_at = data.get("approved_at", 0.0)
        state.rejected_at = data.get("rejected_at", 0.0)
        state.explored_files = data.get("explored_files", [])
        state.agent_ids = data.get("agent_ids", [])
        state.allowed_prompts = data.get("allowed_prompts", [])
        state.interview_answers = data.get("interview_answers", {})
        state.pewter_variant = data.get("pewter_variant")
        state.revision_count = data.get("revision_count", 0)
        state.rejection_count = data.get("rejection_count", 0)
        state.session_id = data.get("session_id", "")
        state.model = data.get("model", "")
        state.budget_limit_usd = data.get("budget_limit_usd", 0.0)
        state.interview_complete = data.get("interview_complete", False)
        return state


# Module-level plan state singleton — protected by _plan_lock
_plan_state = PlanState()
_plan_lock = threading.RLock()


def _with_plan_lock(fn):
    """Decorator to protect plan state access with the module lock."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        with _plan_lock:
            return fn(*args, **kwargs)

    return wrapper


# ============================================================================
# Helper — rate limit tier detection
# ============================================================================


def _get_rate_limit_tier() -> str | None:
    """Resolve the current user's rate limit tier.

    Queries auth state, environment, and subscription metadata to determine
    the active rate limit tier name (e.g. "default_hare_max_20x").

    Priority:
    1. CLAUDE_CODE_RATE_LIMIT_TIER env var (explicit override)
    2. Auth state cached rate limit tier
    3. Subscription-type derived tier
    """
    env = os.environ.get("CLAUDE_CODE_RATE_LIMIT_TIER")
    if env:
        return env.strip() or None

    # Attempt to read from auth state's cached rate limit info
    try:
        from hare.utils.auth_file_descriptor import (
            get_auth_file,
            read_auth_file,
        )

        auth_file = get_auth_file()
        if auth_file:
            data = read_auth_file(auth_file)
            if data:
                tier = data.get("rateLimitTier") or data.get("rate_limit_tier")
                if tier:
                    return str(tier)
    except Exception:
        pass

    # Fall back to subscription-derived tier
    sub = get_subscription_type() or ""
    sub_lower = sub.lower()
    if sub_lower in ("max", "enterprise"):
        return "default_hare_max_20x"
    if sub_lower == "pro":
        return "default_hare_pro_10x"
    if sub_lower == "team":
        return "default_hare_pro_5x"

    return None


# ============================================================================
# Feature gate detection
# ============================================================================


def is_plan_mode_v2_enabled() -> bool:
    """Check whether plan mode v2 (multi-agent + interview + pewter) is enabled.

    Resolution order:
    1. Env var CLAUDE_CODE_PLAN_MODE_V2 — explicit override
    2. USER_TYPE == 'ant' — internal users always get v2
    3. GrowthBook feature flag tengu_plan_mode_v2
    4. Subscription-based eligibility
    """
    env = os.environ.get("CLAUDE_CODE_PLAN_MODE_V2")
    if is_env_truthy(env):
        return True
    if is_env_defined_falsy(env):
        return False

    if os.environ.get("USER_TYPE") == "ant":
        return True

    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        return bool(
            get_feature_value_cached_may_be_stale(PLAN_MODE_V2_FEATURE_FLAG, False)
        )
    except ImportError:
        pass

    # Fallback: subscription-based eligibility
    sub = get_subscription_type() or ""
    tier = _get_rate_limit_tier() or ""
    if sub in ("max", "enterprise") and tier == "default_hare_max_20x":
        return True
    return False


def is_plan_mode_interview_phase_enabled() -> bool:
    """Check if the plan mode interview phase (initial Q&A before exploration) is enabled.

    Resolution order:
    1. Env var CLAUDE_CODE_PLAN_MODE_INTERVIEW_PHASE
    2. USER_TYPE == 'ant'
    3. GrowthBook feature flag tengu_plan_mode_interview_phase
    """
    if os.environ.get("USER_TYPE") == "ant":
        return True
    env = os.environ.get("CLAUDE_CODE_PLAN_MODE_INTERVIEW_PHASE")
    if is_env_truthy(env):
        return True
    if is_env_defined_falsy(env):
        return False
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        return bool(
            get_feature_value_cached_may_be_stale(
                PLAN_MODE_INTERVIEW_PHASE_FLAG, False
            )
        )
    except ImportError:
        return False


def is_plan_mode_multi_agent_enabled() -> bool:
    """Check if multi-agent plan execution (explore + review agents) is enabled.

    Requires plan mode v2 to be enabled as a prerequisite.
    """
    if not is_plan_mode_v2_enabled():
        return False

    env = os.environ.get("CLAUDE_CODE_PLAN_MODE_MULTI_AGENT")
    if is_env_truthy(env):
        return True
    if is_env_defined_falsy(env):
        return False

    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        return bool(
            get_feature_value_cached_may_be_stale(PLAN_MODE_MULTI_AGENT_FLAG, False)
        )
    except ImportError:
        pass

    # Default: enabled for max/enterprise tier users
    sub = get_subscription_type() or ""
    return sub in ("max", "enterprise")


def get_pewter_ledger_variant() -> PewterLedgerVariant:
    """Get the pewter ledger cost-tracking variant.

    Pewter ledger controls how costs and token usage are tracked during
    plan mode sessions.

    Variants:
    - "trim": Remove cost data from plan mode turns (privacy-focused)
    - "cut":  Completely zero out cost display in plan mode
    - "cap":  Cap cost display at a fixed threshold
    - None:   Normal cost tracking
    """
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        raw = get_feature_value_cached_may_be_stale(PEWTER_LEDGER_FLAG, None)
    except ImportError:
        raw = None
    if raw in ("trim", "cut", "cap"):
        return raw
    return None


# ============================================================================
# Agent count resolution
# ============================================================================


def get_plan_mode_v2_agent_count() -> int:
    """Resolve the number of agents to spawn for plan mode v2.

    Priority:
    1. CLAUDE_CODE_PLAN_V2_AGENT_COUNT env var (1-10)
    2. Rate limit tier mapping
    3. Subscription-based default (max/enterprise/team: 3, others: 1)
    """
    raw = os.environ.get("CLAUDE_CODE_PLAN_V2_AGENT_COUNT")
    if raw:
        try:
            count = int(raw, 10)
            if 0 < count <= 10:
                return count
        except ValueError:
            pass
    sub = get_subscription_type() or ""
    tier = _get_rate_limit_tier() or ""

    if tier in TIER_AGENT_COUNTS:
        return TIER_AGENT_COUNTS[tier]

    if sub == "max" and tier == "default_hare_max_20x":
        return 3
    if sub in ("enterprise", "team"):
        return 3
    return 1


def get_plan_mode_v2_explore_agent_count() -> int:
    """Resolve the number of explore agents for plan mode v2.

    Explore agents are the subagents spawned to search, read, and map
    the codebase during the exploration phase.

    Priority:
    1. CLAUDE_CODE_PLAN_V2_EXPLORE_AGENT_COUNT env var (1-10)
    2. Default: 3
    """
    raw = os.environ.get("CLAUDE_CODE_PLAN_V2_EXPLORE_AGENT_COUNT")
    if raw:
        try:
            count = int(raw, 10)
            if 0 < count <= 10:
                return count
        except ValueError:
            pass
    return 3


def get_plan_mode_review_agent_count() -> int:
    """How many review agents run in parallel during plan review phase."""
    raw = os.environ.get("CLAUDE_CODE_PLAN_V2_REVIEW_AGENT_COUNT")
    if raw:
        try:
            count = int(raw, 10)
            if 0 < count <= 5:
                return count
        except ValueError:
            pass
    # Default: if multi-agent is enabled, use 1 review agent
    return 1 if is_plan_mode_multi_agent_enabled() else 0


def get_max_plan_exploration_turns() -> int:
    """Maximum number of turns each explore agent can take."""
    raw = os.environ.get("CLAUDE_CODE_PLAN_V2_MAX_EXPLORE_TURNS")
    if raw:
        try:
            turns = int(raw, 10)
            if 0 < turns <= 50:
                return turns
        except ValueError:
            pass
    return PLAN_MODE_AGENT_ROLES["explore"]["max_turns"]


# ============================================================================
# Tool allowlist management
# ============================================================================


def get_plan_mode_allowed_tools() -> list[str]:
    """Get the list of tools allowed during plan mode.

    Extends the base list when interview phase is active.
    """
    tools = list(PLAN_MODE_ALLOWED_TOOLS)
    if is_plan_mode_interview_phase_enabled():
        for t in PLAN_MODE_INTERVIEW_ALLOWED_EXTRA:
            if t not in tools:
                tools.append(t)
    return tools


def get_plan_mode_blocked_tools() -> list[str]:
    """Get the list of tools explicitly blocked during plan mode."""
    return list(PLAN_MODE_BLOCKED_TOOLS)


def is_tool_allowed_in_plan_mode(tool_name: str) -> bool:
    """Check if a tool is allowed during plan mode (read-only filtering)."""
    if not tool_name:
        return False
    # Case-insensitive match against allowed list
    name_lower = tool_name.lower()
    for allowed in PLAN_MODE_ALLOWED_TOOLS:
        if allowed.lower() == name_lower:
            return True
    # Also allow via interview phase extensions
    if is_plan_mode_interview_phase_enabled():
        for extra in PLAN_MODE_INTERVIEW_ALLOWED_EXTRA:
            if extra.lower() == name_lower:
                return True
    return False


def is_file_write_allowed_in_plan_mode() -> bool:
    """During plan mode v2, file writes are generally blocked.

    Returns True only if the session is in 'approved' phase with explicit
    user-granted write permission for the plan file.
    """
    with _plan_lock:
        if _plan_state.phase == "approved" and is_plan_approved():
            # Check if plan_file write is explicitly allowed
            return bool(
                os.environ.get("CLAUDE_CODE_PLAN_MODE_ALLOW_WRITE")
            )
        return False


# ============================================================================
# Plan lifecycle management
# ============================================================================


def get_plan_state() -> PlanState:
    """Get the current plan mode session state (copy for external reads)."""
    with _plan_lock:
        # Return a shallow copy to prevent accidental external mutation
        return _plan_state


def enter_plan_mode(topic: str = "", plan_file_path: str = "",
                    session_id: str = "", model: str = "",
                    budget_limit_usd: float = 0.0) -> PlanState:
    """Mark plan mode as active and initialize plan state.

    Called when the user or system enters plan mode.
    Validates inputs and returns a snapshot of the initialized state.
    """
    with _plan_lock:
        _plan_state.reset()
        _plan_state.is_active = True
        _plan_state.phase = "entering"
        _plan_state.plan_topic = topic.strip()[:500] if topic else ""
        _plan_state.plan_file_path = plan_file_path or _default_plan_file_path()
        _plan_state.entered_at = time.time() * 1000
        _plan_state.pewter_variant = get_pewter_ledger_variant()
        _plan_state.session_id = session_id
        _plan_state.model = model
        _plan_state.budget_limit_usd = max(0.0, budget_limit_usd)
        return _plan_state


def begin_exploration() -> PlanState:
    """Transition from entering to exploring phase.

    If interview phase is enabled and not complete, stays in 'entering' phase
    and returns with a marker.
    """
    with _plan_lock:
        if is_plan_mode_interview_phase_enabled() and not _plan_state.interview_complete:
            # Remain in entering until interview is done
            return _plan_state
        _plan_state.phase = "exploring"
        return _plan_state


def track_explored_file(file_path: str) -> None:
    """Record a file that was explored during the exploration phase.

    Deduplicates entries and sanitizes the path.
    """
    if not file_path:
        return
    normalized = os.path.normpath(file_path.strip())
    if not normalized:
        return
    with _plan_lock:
        if normalized not in _plan_state.explored_files:
            _plan_state.explored_files.append(normalized)


def begin_writing_plan() -> PlanState:
    """Transition from exploring to writing the plan.

    If the interview phase is enabled but not yet complete, triggers
    interview first.
    """
    with _plan_lock:
        if (
            is_plan_mode_interview_phase_enabled()
            and not _plan_state.interview_complete
        ):
            # Cannot write plan without interview completion
            return _plan_state
        _plan_state.phase = "writing_plan"
        return _plan_state


def submit_plan_for_approval(plan_content: str = "") -> PlanState:
    """Submit the plan for user approval.

    The plan content is stored in memory; the tool layer writes it to
    the plan file on disk separately.

    Increments revision counter and records this iteration in history.
    """
    with _plan_lock:
        if plan_content:
            _plan_state.plan_content = plan_content
        _plan_state.revision_count += 1
        _plan_state.phase = "awaiting_approval"
        _plan_state.approval_status = "pending"

        # Record iteration history
        iteration = PlanIteration(
            content=_plan_state.plan_content[:10000],
            approval_status="pending",
            timestamp=time.time() * 1000,
        )
        _plan_state.iterations.append(iteration)
        return _plan_state


def approve_plan() -> PlanState:
    """Mark the plan as approved by the user.

    Records approval timestamp and captures permission grants.
    """
    with _plan_lock:
        _plan_state.approval_status = "approved"
        _plan_state.phase = "approved"
        _plan_state.approved_at = time.time() * 1000

        # Update latest iteration
        if _plan_state.iterations:
            _plan_state.iterations[-1].approval_status = "approved"
            _plan_state.iterations[-1].timestamp = _plan_state.approved_at

        # Auto-persist on approval
        _persist_plan_state()
        return _plan_state


def reject_plan(reason: str = "") -> PlanState:
    """Mark the plan as rejected by the user.

    Returns to exploring phase so the model can revise.
    Rejection limit enforcement: if MAX_PLAN_REJECTIONS is reached,
    transitions to 'exiting' instead.
    """
    with _plan_lock:
        _plan_state.approval_status = "rejected"
        _plan_state.phase = "rejected"
        _plan_state.rejected_at = time.time() * 1000
        _plan_state.rejection_count += 1

        # Update latest iteration
        if _plan_state.iterations:
            _plan_state.iterations[-1].approval_status = "rejected"
            _plan_state.iterations[-1].feedback = reason[:1000]

        # Check rejection limit
        if _plan_state.rejection_count >= MAX_PLAN_REJECTIONS:
            _plan_state.phase = "exiting"
        return _plan_state


def mark_plan_modified(
    allowed_prompts: list[dict[str, str]] | None = None,
) -> PlanState:
    """Mark the plan as modified (approved with user modifications).

    The user has approved the plan but made changes to it, or
    approved with specific prompt restrictions.
    """
    with _plan_lock:
        _plan_state.approval_status = "modified"
        _plan_state.phase = "approved"
        _plan_state.approved_at = time.time() * 1000
        if allowed_prompts is not None:
            _plan_state.allowed_prompts = list(allowed_prompts)

        # Update latest iteration
        if _plan_state.iterations:
            _plan_state.iterations[-1].approval_status = "modified"

        _persist_plan_state()
        return _plan_state


def exit_plan_mode() -> PlanState:
    """Exit plan mode and return to normal execution.

    The phase is set to 'exiting' and is_active is cleared.
    Callers should also invoke on_session_exit_plan_mode()
    to sync bootstrap state.
    """
    with _plan_lock:
        _plan_state.phase = "exiting"
        _plan_state.is_active = False

        # Clean up persisted state
        _delete_persisted_plan_state()
        return _plan_state


def is_plan_approved() -> bool:
    """Check if the current plan has been approved."""
    with _plan_lock:
        return _plan_state.approval_status in ("approved", "modified")


def is_plan_rejected() -> bool:
    """Check if the current plan has been rejected."""
    with _plan_lock:
        return _plan_state.approval_status == "rejected"


def get_rejection_count() -> int:
    """Get how many times the plan has been rejected."""
    with _plan_lock:
        return _plan_state.rejection_count


def is_rejection_limit_reached() -> bool:
    """Check if the maximum number of plan rejections has been reached."""
    with _plan_lock:
        return _plan_state.rejection_count >= MAX_PLAN_REJECTIONS


def get_plan_elapsed_ms() -> float:
    """Get elapsed time in plan mode in milliseconds."""
    with _plan_lock:
        if _plan_state.entered_at == 0.0:
            return 0.0
        return time.time() * 1000 - _plan_state.entered_at


def get_explored_file_paths() -> list[str]:
    """Get all file paths explored during plan mode."""
    with _plan_lock:
        return list(_plan_state.explored_files)


def get_plan_content() -> str:
    """Get the current plan content."""
    with _plan_lock:
        return _plan_state.plan_content


def set_plan_content(content: str) -> None:
    """Set/update the plan content."""
    with _plan_lock:
        _plan_state.plan_content = content


def get_plan_file_path() -> str:
    """Get the path to the plan file on disk."""
    with _plan_lock:
        return _plan_state.plan_file_path or _default_plan_file_path()


def get_allowed_prompts() -> list[dict[str, str]]:
    """Get pre-approved prompts from the plan approval."""
    with _plan_lock:
        return list(_plan_state.allowed_prompts)


def set_allowed_prompts(prompts: list[dict[str, str]]) -> None:
    """Set pre-approved prompts for the plan execution phase."""
    with _plan_lock:
        _plan_state.allowed_prompts = list(prompts)


def record_interview_answer(question: str, answer: str) -> None:
    """Record an answer from the plan mode interview phase."""
    if not question:
        return
    with _plan_lock:
        _plan_state.interview_answers[question] = answer


def get_interview_answers() -> dict[str, str]:
    """Get all answers collected during the interview phase."""
    with _plan_lock:
        return dict(_plan_state.interview_answers)


def register_agent_id(agent_id: str) -> None:
    """Register a sub-agent ID with the current plan session."""
    if not agent_id:
        return
    with _plan_lock:
        if agent_id not in _plan_state.agent_ids:
            _plan_state.agent_ids.append(agent_id)


def get_registered_agent_ids() -> list[str]:
    """Get all agent IDs registered with the current plan session."""
    with _plan_lock:
        return list(_plan_state.agent_ids)


# ============================================================================
# Iteration history and revision tracking
# ============================================================================


def get_iteration_history() -> list[dict[str, Any]]:
    """Get the full iteration history for this plan session."""
    with _plan_lock:
        return [
            {
                "content_preview": it.content[:200] if it.content else "",
                "approval_status": it.approval_status,
                "timestamp": it.timestamp,
                "feedback": it.feedback,
            }
            for it in _plan_state.iterations
        ]


def get_revision_count() -> int:
    """Get the total number of plan revisions submitted."""
    with _plan_lock:
        return _plan_state.revision_count


def get_latest_iteration_feedback() -> str:
    """Get the feedback from the most recent rejection, if any."""
    with _plan_lock:
        if _plan_state.iterations:
            last = _plan_state.iterations[-1]
            if last.approval_status == "rejected":
                return last.feedback
        return ""


def clear_iteration_history() -> None:
    """Clear iteration history (for test isolation)."""
    with _plan_lock:
        _plan_state.iterations.clear()
        _plan_state.revision_count = 0
        _plan_state.rejection_count = 0


# ============================================================================
# Plan state persistence
# ============================================================================


def _persist_plan_state_file_path() -> str:
    """Get the file path for persisting plan state."""
    from hare.utils.env_utils import get_hare_config_home_dir
    return os.path.join(get_hare_config_home_dir(), PLAN_STATE_PERSIST_FILE)


def _persist_plan_state() -> None:
    """Save the current plan state to disk for recovery."""
    try:
        persist_path = _persist_plan_state_file_path()
        os.makedirs(os.path.dirname(persist_path), exist_ok=True)
        data = _plan_state.to_dict()
        with open(persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        # Persistence is best-effort; never crash on it
        pass


def _delete_persisted_plan_state() -> None:
    """Remove the persisted plan state file."""
    try:
        persist_path = _persist_plan_state_file_path()
        if os.path.isfile(persist_path):
            os.remove(persist_path)
    except Exception:
        pass


def restore_plan_state_from_disk() -> PlanState | None:
    """Restore plan state from disk (for session resumption).

    Returns the restored PlanState if a persisted state exists and is
    valid, otherwise returns None.
    """
    try:
        persist_path = _persist_plan_state_file_path()
        if not os.path.isfile(persist_path):
            return None

        with open(persist_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return None

        with _plan_lock:
            restored = PlanState.from_dict(data)

            # Validate restored state
            if not restored.is_active:
                return None

            # Apply to global state
            _plan_state.is_active = restored.is_active
            _plan_state.phase = restored.phase
            _plan_state.plan_topic = restored.plan_topic
            _plan_state.plan_file_path = restored.plan_file_path
            _plan_state.plan_content = restored.plan_content
            _plan_state.approval_status = restored.approval_status
            _plan_state.entered_at = restored.entered_at
            _plan_state.approved_at = restored.approved_at
            _plan_state.rejected_at = restored.rejected_at
            _plan_state.explored_files = restored.explored_files
            _plan_state.agent_ids = restored.agent_ids
            _plan_state.allowed_prompts = restored.allowed_prompts
            _plan_state.interview_answers = restored.interview_answers
            _plan_state.pewter_variant = restored.pewter_variant
            _plan_state.revision_count = restored.revision_count
            _plan_state.rejection_count = restored.rejection_count
            _plan_state.session_id = restored.session_id
            _plan_state.model = restored.model
            _plan_state.budget_limit_usd = restored.budget_limit_usd
            _plan_state.interview_complete = restored.interview_complete

            return _plan_state

    except Exception:
        return None


def has_persisted_plan_state() -> bool:
    """Check if a persisted plan state exists on disk."""
    persist_path = _persist_plan_state_file_path()
    return os.path.isfile(persist_path)


# ============================================================================
# Plan validation
# ============================================================================


@dataclass
class PlanValidationResult:
    """Result of plan content validation."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    estimated_file_count: int = 0
    content_length: int = 0


def validate_plan_content(plan_content: str = "") -> PlanValidationResult:
    """Validate plan content before submission.

    Checks:
    - Non-empty content (min length)
    - Required sections present
    - Size limits
    - Markdown structure hints
    """
    content = plan_content or get_plan_content()
    errors: list[str] = []
    warnings: list[str] = []
    missing_sections: list[str] = []

    content_len = len(content)

    # 1. Minimum length check
    if content_len < MIN_PLAN_CONTENT_LENGTH:
        errors.append(
            f"Plan content too short ({content_len} chars). "
            f"Minimum {MIN_PLAN_CONTENT_LENGTH} required."
        )

    # 2. Maximum size check
    if content_len > MAX_PLAN_FILE_SIZE:
        errors.append(
            f"Plan content exceeds maximum size of {MAX_PLAN_FILE_SIZE} bytes "
            f"(current: {content_len})."
        )

    # 3. Required sections check
    content_lower = content.lower()
    for section in REQUIRED_PLAN_SECTIONS:
        pattern = re.compile(
            rf"^#{1,3}\s+.*{re.escape(section)}.*$",
            re.MULTILINE | re.IGNORECASE,
        )
        if not pattern.search(content):
            missing_sections.append(section)
            warnings.append(
                f"Missing recommended section: {section}"
            )

    # 4. Optional sections hint
    for section in OPTIONAL_PLAN_SECTIONS:
        pattern = re.compile(
            rf"^#{1,3}\s+.*{re.escape(section)}.*$",
            re.MULTILINE | re.IGNORECASE,
        )
        if not pattern.search(content):
            warnings.append(
                f"Consider adding section: {section}"
            )

    # 5. Estimate affected file count from content
    file_pattern = re.findall(r"`([^`]+\.(?:py|ts|js|rs|go|java|rb))`", content)
    estimated_file_count = len(set(file_pattern))

    # 6. Check for plan content that looks like it was generated but empty
    if content_len > 0 and not content.strip().startswith("#"):
        warnings.append(
            "Plan does not appear to start with a markdown heading. "
            "Consider adding a title."
        )

    is_valid = len(errors) == 0

    return PlanValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        missing_sections=missing_sections,
        estimated_file_count=estimated_file_count,
        content_length=content_len,
    )


def validate_plan_before_execution() -> PlanValidationResult:
    """Validate the plan before execution begins (post-approval guard).

    Additional checks specific to execution readiness:
    - Plan is approved
    - Plan content is non-empty
    - No unresolved interview questions
    """
    errors: list[str] = []
    warnings: list[str] = []

    with _plan_lock:
        if not is_plan_approved():
            errors.append("Plan has not been approved.")

        if not _plan_state.plan_content.strip():
            errors.append("Plan content is empty.")

        # Warn if plan was approved but no files were explored
        if not _plan_state.explored_files:
            warnings.append(
                "No files were explored before plan was approved. "
                "The plan may miss important context."
            )

        # Check for stale plan content (approved long ago)
        elapsed_ms = get_plan_elapsed_ms()
        if elapsed_ms > DEFAULT_PLAN_MODE_TIMEOUT_MS:
            warnings.append(
                f"Plan mode has been active for {elapsed_ms / 1000 / 60:.1f} minutes. "
                "Consider re-validating the plan."
            )

        content_len = len(_plan_state.plan_content)

    return PlanValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        missing_sections=[],
        estimated_file_count=0,
        content_length=content_len,
    )


# ============================================================================
# Exploration context building
# ============================================================================


def build_exploration_context(
    max_files: int = 50,
    include_previews: bool = False,
    max_preview_bytes: int = 500,
) -> dict[str, Any]:
    """Build a context object from the exploration phase results.

    Aggregates explored file paths and optionally includes snippet previews
    for use in generating the system prompt or plan content.

    Args:
        max_files: Maximum number of file paths to include.
        include_previews: Whether to read and include file previews.
        max_preview_bytes: Max bytes per file preview.

    Returns:
        Dict with exploration context suitable for prompt injection.
    """
    with _plan_lock:
        files = _plan_state.explored_files[:max_files]
        topic = _plan_state.plan_topic

    context: dict[str, Any] = {
        "topic": topic,
        "files_explored": len(_plan_state.explored_files),
        "file_paths": files,
        "exploration_count": len(_plan_state.explored_files),
    }

    if include_previews and files:
        previews: dict[str, str] = {}
        for fpath in files[:20]:  # Preview at most 20 files
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read(max_preview_bytes)
                if len(content) >= max_preview_bytes:
                    content += "\n... (truncated)"
                previews[fpath] = content
            except Exception:
                previews[fpath] = "[unable to read]"
        context["file_previews"] = previews

    return context


def build_exploration_summary_for_prompt() -> str:
    """Build a human-readable summary of the exploration phase for the prompt."""
    with _plan_lock:
        files = _plan_state.explored_files
        topic = _plan_state.plan_topic

    if not files:
        if topic:
            return f"No files were explored for: {topic}."
        return "No files were explored during this plan session."

    lines = [
        f"## Exploration Summary: {topic}" if topic else "## Exploration Summary",
        "",
        f"**{len(files)} files** were explored:",
        "",
    ]

    # Group files by directory for readability
    by_dir: dict[str, list[str]] = {}
    for f in files:
        d = os.path.dirname(f) or "."
        by_dir.setdefault(d, []).append(os.path.basename(f))

    for directory, names in sorted(by_dir.items()):
        lines.append(f"### {directory}/")
        for name in sorted(names)[:10]:
            lines.append(f"  - {name}")
        if len(names) > 10:
            lines.append(f"  - ... and {len(names) - 10} more files")

    return "\n".join(lines)


# ============================================================================
# Plan mode timeout
# ============================================================================


def get_plan_mode_timeout_ms() -> int:
    """Get the configured plan mode timeout in milliseconds.

    Priority:
    1. CLAUDE_CODE_PLAN_MODE_TIMEOUT_MS env var
    2. Default: 30 minutes
    """
    raw = os.environ.get("CLAUDE_CODE_PLAN_MODE_TIMEOUT_MS")
    if raw:
        try:
            timeout = int(raw, 10)
            if timeout > 0:
                return timeout
        except ValueError:
            pass
    return DEFAULT_PLAN_MODE_TIMEOUT_MS


def is_plan_mode_timed_out() -> bool:
    """Check if the plan mode session has exceeded its timeout."""
    with _plan_lock:
        if not _plan_state.is_active:
            return False
        if _plan_state.entered_at == 0.0:
            return False
    elapsed_ms = get_plan_elapsed_ms()
    timeout_ms = get_plan_mode_timeout_ms()
    return elapsed_ms > timeout_ms


def get_plan_mode_remaining_ms() -> float:
    """Get the remaining time in plan mode before timeout.

    Returns a negative value if already timed out.
    """
    elapsed_ms = get_plan_elapsed_ms()
    timeout_ms = get_plan_mode_timeout_ms()
    return timeout_ms - elapsed_ms


def get_plan_mode_remaining_str() -> str:
    """Get a human-readable remaining time string."""
    remaining_ms = get_plan_mode_remaining_ms()
    if remaining_ms <= 0:
        return "Plan mode timeout reached."
    total_seconds = int(remaining_ms / 1000)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    if minutes > 0:
        return f"{minutes}m {seconds}s remaining"
    return f"{seconds}s remaining"


# ============================================================================
# Plan file I/O
# ============================================================================


def _default_plan_file_path() -> str:
    """Derive the default plan file path.

    Uses CLAUDE_CODE_PLAN_FILE env var, otherwise defaults to
    .claude/plans/plan.md relative to the project root.
    """
    env_path = os.environ.get("CLAUDE_CODE_PLAN_FILE")
    if env_path:
        return os.path.normpath(env_path)

    from hare.bootstrap.state import get_project_root

    root = get_project_root()
    return os.path.join(root, DEFAULT_PLAN_FILE)


def read_plan_file(plan_file_path: str = "") -> tuple[str, str | None]:
    """Read the plan file from disk.

    Returns (content, error_message). On success, error_message is None.
    """
    path = plan_file_path or get_plan_file_path()
    path = os.path.normpath(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # Enforce size limit
        if len(content) > MAX_PLAN_FILE_SIZE:
            return "", f"Plan file exceeds maximum size of {MAX_PLAN_FILE_SIZE} bytes"
        return content, None
    except FileNotFoundError:
        return "", f"Plan file not found: {path}"
    except PermissionError:
        return "", f"Permission denied reading plan file: {path}"
    except IsADirectoryError:
        return "", f"Plan file path is a directory: {path}"
    except UnicodeDecodeError as e:
        return "", f"Plan file has encoding errors: {e}"
    except OSError as e:
        return "", f"Error reading plan file: {e}"
    except Exception as e:
        return "", f"Unexpected error reading plan file: {e}"


def write_plan_file(content: str, plan_file_path: str = "") -> str | None:
    """Write the plan content to the plan file on disk.

    Creates parent directories if needed. Returns an error message on failure,
    or None on success.
    """
    path = os.path.normpath(plan_file_path or get_plan_file_path())

    # Validate content size before writing
    if len(content) > MAX_PLAN_FILE_SIZE:
        return (
            f"Plan content exceeds maximum size of "
            f"{MAX_PLAN_FILE_SIZE} bytes ({len(content)} bytes)"
        )

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        set_plan_content(content)
        # Auto-persist state when plan is written to disk
        with _plan_lock:
            if _plan_state.is_active:
                _persist_plan_state()
        return None
    except PermissionError:
        return f"Permission denied writing plan file: {path}"
    except IsADirectoryError:
        return f"Plan file path is a directory: {path}"
    except OSError as e:
        return f"Error writing plan file: {e}"
    except Exception as e:
        return f"Unexpected error writing plan file: {e}"


def plan_file_exists(plan_file_path: str = "") -> bool:
    """Check if the plan file exists on disk."""
    path = os.path.normpath(plan_file_path or get_plan_file_path())
    return os.path.isfile(path)


def delete_plan_file(plan_file_path: str = "") -> str | None:
    """Delete the plan file from disk.

    Returns an error message on failure, or None on success.
    """
    path = os.path.normpath(plan_file_path or get_plan_file_path())
    if not os.path.isfile(path):
        return None  # Already gone, not an error
    try:
        os.remove(path)
        return None
    except PermissionError:
        return f"Permission denied deleting plan file: {path}"
    except OSError as e:
        return f"Error deleting plan file: {e}"


def get_plan_file_size(plan_file_path: str = "") -> int:
    """Get the plan file size in bytes. Returns -1 if the file does not exist."""
    path = os.path.normpath(plan_file_path or get_plan_file_path())
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


# ============================================================================
# Plan content parsing
# ============================================================================


def parse_plan_sections(plan_content: str = "") -> dict[str, str]:
    """Parse a plan markdown document into named sections.

    Splits on `## Section Name` headings and returns a dict mapping
    section names (lowercase, stripped) to their content.

    Example:
        "## Summary\nThis is the summary.\n\n## Approach\nDo this."
        -> {"summary": "This is the summary.", "approach": "Do this."}
    """
    content = plan_content or get_plan_content()
    if not content:
        return {}

    sections: dict[str, str] = {}
    current_section = "_preamble"
    current_content: list[str] = []

    for line in content.split("\n"):
        m = re.match(r"^#{1,3}\s+(.+)$", line)
        if m:
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
                current_content = []
            current_section = m.group(1).strip().lower()
        else:
            current_content.append(line)

    if current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections


def plan_has_section(section_name: str, plan_content: str = "") -> bool:
    """Check if a named section exists in the plan content."""
    sections = parse_plan_sections(plan_content)
    return section_name.lower() in sections


def get_plan_section(section_name: str, plan_content: str = "") -> str:
    """Get the content of a named section from the plan.

    Returns empty string if the section is not found.
    """
    sections = parse_plan_sections(plan_content)
    return sections.get(section_name.lower(), "")


def extract_file_paths_from_plan(plan_content: str = "") -> list[str]:
    """Extract file paths mentioned in backticks from the plan content.

    Only extracts paths that look like real filenames (have an extension).
    """
    content = plan_content or get_plan_content()
    if not content:
        return []

    # Match backtick-wrapped paths with known code extensions
    pattern = re.findall(
        r"`([^`]+\.(?:py|ts|tsx|js|jsx|rs|go|java|rb|swift|kt|scala|sh|bash|yaml|yml|json|toml|md|css|html|sql))`",
        content,
    )
    # Deduplicate and sort
    return sorted(set(pattern))


def generate_plan_template(topic: str = "") -> str:
    """Generate a markdown plan template with required sections.

    Args:
        topic: A description of what the plan is for.

    Returns:
        A markdown string with section headers for the user/model to fill in.
    """
    title_line = f"# Plan: {topic}" if topic else "# Implementation Plan"
    return f"""{title_line}

## Summary

<!-- Brief description of what this plan aims to accomplish. -->

## Approach

<!-- High-level approach and architecture decisions. -->

## Implementation

<!-- Step-by-step implementation details. -->

### Step 1

### Step 2

### Step 3

## Testing

<!-- How the changes should be tested. -->

## Risks and Mitigations

<!-- Potential risks and how they are mitigated. -->

## Alternatives Considered

<!-- Alternative approaches that were evaluated and why they were not chosen. -->

## Dependencies

<!-- External dependencies, file changes, or system requirements. -->

## Rollback Plan

<!-- How to revert these changes if something goes wrong. -->
"""


# ============================================================================
# Multi-agent plan orchestration
# ============================================================================


@dataclass
class PlanAgentSpec:
    """Specification for spawning a plan-mode sub-agent."""

    agent_type: PlanModeAgentType
    agent_id: str = ""
    prompt: str = ""
    max_turns: int = 15
    model: str | None = None
    plan_mode_required: bool = False
    permission_mode: str = "plan"


def build_explore_agent_specs(
    topic: str,
    files_to_explore: list[str] | None = None,
) -> list[PlanAgentSpec]:
    """Build agent specifications for the exploration phase.

    Creates one explore agent spec per file group (or one general spec
    if no specific files are targeted).
    """
    count = get_plan_mode_v2_explore_agent_count()
    specs: list[PlanAgentSpec] = []

    if files_to_explore:
        # Distribute files across agents
        chunk_size = max(1, len(files_to_explore) // count)
        for i in range(count):
            chunk = files_to_explore[i * chunk_size : (i + 1) * chunk_size]
            if not chunk:
                break
            specs.append(
                PlanAgentSpec(
                    agent_type="explore",
                    agent_id=f"plan-explore-{i}",
                    prompt=_build_explore_prompt(topic, chunk),
                    max_turns=get_max_plan_exploration_turns(),
                    plan_mode_required=True,
                    permission_mode="plan",
                )
            )
    else:
        # Single general explore agent
        for i in range(min(count, 1)):  # At most 1 general explore agent
            specs.append(
                PlanAgentSpec(
                    agent_type="explore",
                    agent_id=f"plan-explore-{i}",
                    prompt=_build_explore_prompt(topic, None),
                    max_turns=get_max_plan_exploration_turns(),
                    plan_mode_required=True,
                    permission_mode="plan",
                )
            )

    return specs


def build_architect_agent_spec(
    topic: str,
    constraints: list[str] | None = None,
) -> PlanAgentSpec | None:
    """Build an architect agent specification if multi-agent is enabled.

    The architect agent focuses on high-level design and trade-off analysis
    before detailed implementation planning.
    """
    if not is_plan_mode_multi_agent_enabled():
        return None

    constraint_text = ""
    if constraints:
        constraint_text = (
            "Architectural constraints:\n"
            + "\n".join(f"  - {c}" for c in constraints)
            + "\n\n"
        )

    architect_prompt = (
        f"Design a high-level architecture for: {topic}\n\n"
        f"{constraint_text}"
        f"Your task:\n"
        f"1. Identify the key components and their responsibilities.\n"
        f"2. Analyze trade-offs between possible approaches.\n"
        f"3. Recommend a concrete architecture with rationale.\n"
        f"4. Highlight integration points and dependencies.\n"
        f"5. Flag any performance, security, or maintainability concerns.\n\n"
        f"Do NOT write implementation code. Focus on design decisions."
    )

    return PlanAgentSpec(
        agent_type="architect",
        agent_id="plan-architect",
        prompt=architect_prompt,
        max_turns=PLAN_MODE_AGENT_ROLES["architect"]["max_turns"],
        plan_mode_required=True,
        permission_mode="plan",
    )


def build_review_agent_spec(plan_content: str) -> PlanAgentSpec | None:
    """Build a review agent specification if multi-agent is enabled."""
    if not is_plan_mode_multi_agent_enabled():
        return None

    return PlanAgentSpec(
        agent_type="code_review",
        agent_id="plan-review",
        prompt=_build_review_prompt(plan_content),
        max_turns=PLAN_MODE_AGENT_ROLES["code_review"]["max_turns"],
        plan_mode_required=True,
        permission_mode="plan",
    )


def build_review_agent_specs(
    plan_content: str,
    review_count: int | None = None,
) -> list[PlanAgentSpec]:
    """Build multiple review agent specifications for parallel review.

    Each reviewer gets a slightly different perspective angle.

    Args:
        plan_content: The plan content to review.
        review_count: Number of reviewers. Defaults to get_plan_mode_review_agent_count().

    Returns:
        List of review agent specs (empty if multi-agent is disabled).
    """
    if not is_plan_mode_multi_agent_enabled():
        return []

    count = review_count or get_plan_mode_review_agent_count()
    if count <= 0:
        return []

    review_angles = [
        ("correctness", "Verify logic correctness and edge case handling"),
        (
            "security",
            "Review for security vulnerabilities and unsafe patterns",
        ),
        (
            "performance",
            "Analyze performance implications and potential bottlenecks",
        ),
        (
            "maintainability",
            "Evaluate code organization, naming, and long-term maintainability",
        ),
        (
            "consistency",
            "Check alignment with existing codebase patterns and conventions",
        ),
    ]

    specs: list[PlanAgentSpec] = []
    for i in range(min(count, len(review_angles))):
        angle_name, angle_focus = review_angles[i]
        specs.append(
            PlanAgentSpec(
                agent_type="code_review",
                agent_id=f"plan-review-{angle_name}",
                prompt=_build_focused_review_prompt(
                    plan_content, angle_name, angle_focus
                ),
                max_turns=PLAN_MODE_AGENT_ROLES["code_review"]["max_turns"],
                plan_mode_required=True,
                permission_mode="plan",
            )
        )
    return specs


def _build_explore_prompt(topic: str, files: list[str] | None) -> str:
    """Build the prompt for an explore agent."""
    if files:
        if not topic:
            topic = "the codebase"
        file_list = "\n".join(f"  - {f}" for f in files)
        return (
            f"Explore the following files to understand implementation context "
            f"for: {topic}\n\n"
            f"Files to explore:\n{file_list}\n\n"
            f"Focus on understanding patterns, dependencies, and integration points. "
            f"Report your findings including relevant code snippets, architectural "
            f"patterns, and any potential issues or constraints."
        )
    return (
        f"Explore the codebase to understand the implementation context for: {topic}\n\n"
        f"Use Glob, Grep, and Read tools to find relevant files and understand "
        f"the current architecture. Report your findings including relevant code "
        f"snippets, architectural patterns, and any potential issues or constraints."
    )


def _build_review_prompt(plan_content: str) -> str:
    """Build the prompt for a code review agent."""
    return (
        f"Review the following implementation plan for correctness, completeness, "
        f"and potential issues:\n\n"
        f"---\n{plan_content}\n---\n\n"
        f"Evaluate the plan against these criteria:\n"
        f"1. Correctness: Will the proposed changes work as described?\n"
        f"2. Completeness: Are edge cases, error handling, and testing covered?\n"
        f"3. Consistency: Does the plan align with existing codebase patterns?\n"
        f"4. Risks: What could go wrong with this approach?\n\n"
        f"Provide specific, actionable feedback. If you find issues, suggest "
        f"concrete improvements."
    )


def _build_focused_review_prompt(
    plan_content: str, angle_name: str, angle_focus: str
) -> str:
    """Build a focused review prompt for a specific perspective."""
    return (
        f"Review the following implementation plan with a focus on "
        f"**{angle_name}**: {angle_focus}.\n\n"
        f"---\n{plan_content}\n---\n\n"
        f"Provide specific, actionable feedback from this perspective only. "
        f"Rate your confidence (low/medium/high) for each finding. "
        f"Suggest concrete improvements where applicable."
    )


# ============================================================================
# Interview phase
# ============================================================================


def complete_interview_phase() -> PlanState:
    """Mark the interview phase as complete and transition to exploration.

    Validates that at least one question has been answered before completing.
    Returns the current plan state.
    """
    with _plan_lock:
        if not is_plan_mode_interview_phase_enabled():
            return _plan_state

        _plan_state.interview_complete = True

        # If still in entering phase, auto-transition to exploring
        if _plan_state.phase == "entering":
            _plan_state.phase = "exploring"

        _persist_plan_state()
        return _plan_state


def is_interview_complete() -> bool:
    """Check if the interview phase has been completed."""
    with _plan_lock:
        if not is_plan_mode_interview_phase_enabled():
            return True  # Not required, so considered complete
        return _plan_state.interview_complete


def get_interview_completion_status() -> dict[str, Any]:
    """Get detailed interview completion status.

    Returns:
        Dict with 'is_complete', 'questions_asked', 'questions_answered',
        and 'pending_questions'.
    """
    questions = get_interview_questions()
    with _plan_lock:
        answers = dict(_plan_state.interview_answers)

    pending = []
    for q in questions:
        q_id = q.get("id", "")
        if q_id not in answers:
            pending.append(q)

    return {
        "is_complete": is_interview_complete(),
        "questions_total": len(questions),
        "questions_answered": len(answers),
        "pending_questions": pending,
        "answers": answers,
    }


# ============================================================================
# Pewter ledger — cost tracking variants for plan mode
# ============================================================================


def apply_pewter_ledger_trim(usage: dict[str, Any]) -> dict[str, Any]:
    """Remove cost data from usage stats (trim variant)."""
    trimmed = dict(usage)
    trimmed.pop("cost", None)
    trimmed.pop("costUsd", None)
    trimmed.pop("cost_usd", None)
    return trimmed


def apply_pewter_ledger_cut(usage: dict[str, Any]) -> dict[str, Any]:
    """Zero out cost display (cut variant)."""
    cut = dict(usage)
    cut["cost"] = 0
    cut["costUsd"] = 0.0
    cut["cost_usd"] = 0.0
    return cut


def apply_pewter_ledger_cap(
    usage: dict[str, Any],
    cap_usd: float = 5.0,
) -> dict[str, Any]:
    """Cap cost display at threshold (cap variant)."""
    capped = dict(usage)
    for key in ("costUsd", "cost_usd", "cost"):
        current = capped.get(key, 0.0)
        if isinstance(current, (int, float)) and current > cap_usd:
            capped[key] = cap_usd
    return capped


def transform_usage_for_plan_mode(usage: dict[str, Any]) -> dict[str, Any]:
    """Apply the active pewter ledger variant to usage stats.

    Returns transformed usage dict based on the current pewter variant.
    """
    if not usage:
        return {}
    variant = get_pewter_ledger_variant()
    if variant == "trim":
        return apply_pewter_ledger_trim(usage)
    elif variant == "cut":
        return apply_pewter_ledger_cut(usage)
    elif variant == "cap":
        cap_value = float(os.environ.get("CLAUDE_CODE_PEWTER_CAP_USD", "5.0"))
        return apply_pewter_ledger_cap(usage, cap_usd=cap_value)
    return usage


def is_plan_budget_exceeded(budget_limit_usd: float | None = None) -> bool:
    """Check if the plan mode budget has been exceeded.

    Requires the caller to provide current cumulative cost.
    Returns False if no budget limit is set.
    """
    with _plan_lock:
        limit = budget_limit_usd or _plan_state.budget_limit_usd

    if limit <= 0:
        return False

    # Budget checking must be done by the caller with actual usage data.
    # This is a helper that validates the limit is configured.
    return False  # Consumer must provide actual usage


# ============================================================================
# Plan mode system prompt customization
# ============================================================================


def get_plan_mode_system_prompt_context() -> dict[str, Any]:
    """Build context dict for system prompt template customization.

    Returns values that control plan mode prompt sections:
    - agent_count: Number of parallel agents
    - multi_agent_enabled: Whether multi-agent execution is on
    - interview_phase_enabled: Whether interview Q&A is on
    - pewter_variant: Current cost tracking variant
    - allowed_tools: Tool name list for the prompt
    - plan_file_path: Where the plan should be written
    """
    return {
        "agent_count": get_plan_mode_v2_agent_count(),
        "explore_agent_count": get_plan_mode_v2_explore_agent_count(),
        "multi_agent_enabled": is_plan_mode_multi_agent_enabled(),
        "interview_phase_enabled": is_plan_mode_interview_phase_enabled(),
        "plan_mode_v2_enabled": is_plan_mode_v2_enabled(),
        "pewter_variant": get_pewter_ledger_variant(),
        "allowed_tools": get_plan_mode_allowed_tools(),
        "plan_file_path": get_plan_file_path(),
        "max_explore_turns": get_max_plan_exploration_turns(),
        "rejection_count": get_rejection_count(),
        "remaining_time": get_plan_mode_remaining_str(),
    }


def build_plan_mode_tool_allowlist_block() -> str:
    """Build a human-readable tool allowlist block for the system prompt."""
    tools = get_plan_mode_allowed_tools()
    blocked = get_plan_mode_blocked_tools()

    lines = [
        "## Plan Mode Tool Restrictions",
        "",
        "**Available tools (read-only operations):**",
    ]
    for t in sorted(tools):
        lines.append(f"  - {t}")
    lines.append("")
    lines.append("**Blocked tools (write/modify operations):**")
    for t in sorted(blocked):
        lines.append(f"  - {t}")
    return "\n".join(lines)


# ============================================================================
# Interview phase conversation management
# ============================================================================


# Interview questions to ask before exploration begins
DEFAULT_INTERVIEW_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "plan_v2_scope",
        "question": "What is the scope of this change? (single file, multiple files, system-wide)",
        "options": '["Single file", "Multiple files (2-5)", "Many files (5+)", "Not sure yet"]',
    },
    {
        "id": "plan_v2_constraints",
        "question": "Are there any constraints or requirements you want me to keep in mind?",
        "options": '["Must maintain backward compatibility", "Must follow existing patterns strictly", "Performance is critical", "No specific constraints"]',
    },
    {
        "id": "plan_v2_testing",
        "question": "What level of testing do you expect?",
        "options": '["No tests needed", "Unit tests only", "Full test coverage", "Not sure — recommend what fits"]',
    },
]


def get_interview_questions() -> list[dict[str, str]]:
    """Get the default interview phase questions.

    These may be customized via environment or settings in the future.
    """
    return list(DEFAULT_INTERVIEW_QUESTIONS)


def get_interview_phase_prompt() -> str:
    """Build the system prompt section for the interview phase.

    Only includes content when interview phase is enabled.
    """
    if not is_plan_mode_interview_phase_enabled():
        return ""

    questions = get_interview_questions()
    q_lines = []
    for q in questions:
        q_lines.append(f"  - {q['question']}")

    return (
        "## Plan Mode Interview Phase\n\n"
        "Before exploring the codebase, ask clarifying questions to ensure "
        "the plan aligns with user expectations. Use AskUserQuestion to present "
        "these questions:\n\n"
        + "\n".join(q_lines)
        + "\n\n"
        "Collect all answers before proceeding to exploration.\n"
    )


# ============================================================================
# Plan mode teleportation support
# ============================================================================


def can_teleport_plan() -> bool:
    """Check if the current plan can be teleported to a remote session.

    Teleportation requires:
    - Plan is approved or modified
    - Plan content is non-empty
    - Plan mode v2 is enabled
    """
    return (
        is_plan_mode_v2_enabled()
        and is_plan_approved()
        and bool(get_plan_content().strip())
    )


def build_teleport_payload() -> dict[str, Any]:
    """Build a teleport payload from the current plan state.

    Used to send plan context to a remote CCR session.
    """
    with _plan_lock:
        return {
            "plan_content": _plan_state.plan_content,
            "plan_topic": _plan_state.plan_topic,
            "explored_files": list(_plan_state.explored_files),
            "allowed_prompts": list(_plan_state.allowed_prompts),
            "interview_answers": dict(_plan_state.interview_answers),
            "pewter_variant": _plan_state.pewter_variant,
            "agent_ids": list(_plan_state.agent_ids),
            "plan_mode_v2_context": get_plan_mode_system_prompt_context(),
            "revision_count": _plan_state.revision_count,
            "approved_at": _plan_state.approved_at,
        }


def apply_teleport_payload(payload: dict[str, Any]) -> None:
    """Apply a teleport payload to restore plan mode state on the receiving side.

    Args:
        payload: The teleport payload from build_teleport_payload().
    """
    with _plan_lock:
        _plan_state.reset()
        _plan_state.is_active = True
        _plan_state.phase = "approved"
        _plan_state.plan_topic = payload.get("plan_topic", "")
        _plan_state.plan_content = payload.get("plan_content", "")
        _plan_state.approval_status = "approved"
        _plan_state.approved_at = payload.get("approved_at", time.time() * 1000)
        _plan_state.explored_files = list(payload.get("explored_files", []))
        _plan_state.allowed_prompts = list(payload.get("allowed_prompts", []))
        _plan_state.interview_answers = dict(payload.get("interview_answers", {}))
        _plan_state.agent_ids = list(payload.get("agent_ids", []))
        _plan_state.pewter_variant = payload.get("pewter_variant")


# ============================================================================
# Session bootstrap integration
# ============================================================================


def on_session_enter_plan_mode() -> None:
    """Called when the session enters plan mode.

    Syncs plan mode state to the bootstrap state tracking flags.
    """
    from hare.bootstrap.state import (
        set_has_exited_plan_mode,
        set_needs_plan_mode_exit_attachment,
    )

    # Mark that we haven't exited yet (entering plan mode)
    set_has_exited_plan_mode(False)
    set_needs_plan_mode_exit_attachment(False)

    # Initialize plan state
    enter_plan_mode()


def on_session_exit_plan_mode() -> None:
    """Called when the session exits plan mode.

    Syncs plan mode state to the bootstrap state tracking flags.
    """
    from hare.bootstrap.state import (
        set_has_exited_plan_mode,
        set_needs_plan_mode_exit_attachment,
        handle_plan_mode_transition,
    )

    set_has_exited_plan_mode(True)
    set_needs_plan_mode_exit_attachment(True)
    handle_plan_mode_transition("plan", "default")

    # Finalize plan state
    exit_plan_mode()


# ============================================================================
# Plan mode health check / diagnostic
# ============================================================================


def get_plan_mode_health() -> dict[str, Any]:
    """Return a diagnostic snapshot of the current plan mode session.

    Useful for debugging, observability, and admin dashboards.
    """
    with _plan_lock:
        return {
            "is_active": _plan_state.is_active,
            "phase": _plan_state.phase,
            "topic": _plan_state.plan_topic,
            "approval_status": _plan_state.approval_status,
            "elapsed_ms": get_plan_elapsed_ms(),
            "remaining_ms": get_plan_mode_remaining_ms(),
            "is_timed_out": is_plan_mode_timed_out(),
            "revision_count": _plan_state.revision_count,
            "rejection_count": _plan_state.rejection_count,
            "explored_file_count": len(_plan_state.explored_files),
            "agent_count": len(_plan_state.agent_ids),
            "interview_answers_count": len(_plan_state.interview_answers),
            "interview_complete": _plan_state.interview_complete,
            "pewter_variant": _plan_state.pewter_variant,
            "plan_content_length": len(_plan_state.plan_content),
            "session_id": _plan_state.session_id,
            "budget_limit_usd": _plan_state.budget_limit_usd,
            "plan_file_exists": plan_file_exists(_plan_state.plan_file_path),
        }


def get_plan_mode_summary() -> str:
    """Get a human-readable summary of the current plan mode session."""
    health = get_plan_mode_health()

    elapsed_sec = health["elapsed_ms"] / 1000 if health["elapsed_ms"] else 0
    elapsed_min = int(elapsed_sec // 60)
    elapsed_sec_remainder = int(elapsed_sec % 60)

    lines = [
        "=== Plan Mode v2 Session Summary ===",
        f"  Active:        {health['is_active']}",
        f"  Phase:         {health['phase']}",
        f"  Approval:      {health['approval_status']}",
        f"  Topic:         {health['topic'] or '(none)'}",
        f"  Elapsed:       {elapsed_min}m {elapsed_sec_remainder}s",
        f"  Remaining:     {health['remaining_ms']:.0f}ms",
        f"  Timed out:     {health['is_timed_out']}",
        f"  Revisions:     {health['revision_count']}",
        f"  Rejections:    {health['rejection_count']}",
        f"  Files explored:{health['explored_file_count']}",
        f"  Agents:        {health['agent_count']}",
        f"  Interview:     {health['interview_answers_count']} answers "
        f"({'complete' if health['interview_complete'] else 'pending'})",
        f"  Pewter:        {health['pewter_variant'] or 'none'}",
        f"  Plan size:     {health['plan_content_length']} chars",
        f"  Plan file:     {'exists' if health['plan_file_exists'] else 'not found'}",
        "===================================",
    ]
    return "\n".join(lines)


# ============================================================================
# Budget and cost awareness
# ============================================================================


def set_plan_budget_limit(limit_usd: float) -> None:
    """Set a cost budget limit for the plan mode session.

    When the cumulative cost exceeds this limit, a warning is emitted
    and plan execution may be paused.
    """
    with _plan_lock:
        _plan_state.budget_limit_usd = max(0.0, limit_usd)
        _persist_plan_state()


def get_plan_budget_limit() -> float:
    """Get the current plan mode budget limit. 0.0 means no limit."""
    with _plan_lock:
        return _plan_state.budget_limit_usd


def should_pause_for_budget(current_cost_usd: float) -> bool:
    """Check if plan execution should pause due to budget constraints.

    Args:
        current_cost_usd: The cumulative cost of the session so far.

    Returns:
        True if budget limit is set and current cost exceeds it.
    """
    limit = get_plan_budget_limit()
    if limit <= 0:
        return False
    return current_cost_usd >= limit


# ============================================================================
# Plan recovery helpers
# ============================================================================


def revise_plan_after_rejection(feedback: str = "") -> PlanState:
    """Handle going back to exploration after a plan is rejected.

    Resets approval status and transitions back to exploring phase
    so the model can incorporate feedback and regenerate.

    Args:
        feedback: User feedback explaining the rejection reason.

    Returns:
        Current plan state after transition.
    """
    with _plan_lock:
        # Record rejection first
        _plan_state.approval_status = "rejected"
        _plan_state.rejected_at = time.time() * 1000

        # Reset to exploring if under rejection limit
        if _plan_state.rejection_count < MAX_PLAN_REJECTIONS:
            _plan_state.phase = "exploring"
            _plan_state.approval_status = "pending"
        else:
            _plan_state.phase = "exiting"

        return _plan_state


def abandon_plan() -> PlanState:
    """Abandon the current plan and exit plan mode immediately.

    Cleans up state without going through the normal exit flow.
    """
    with _plan_lock:
        _plan_state.phase = "exiting"
        _plan_state.is_active = False
        _plan_state.approval_status = "rejected"
        _delete_persisted_plan_state()
        return _plan_state


# ============================================================================
# Reset (for testing)
# ============================================================================


def reset_plan_mode_state() -> None:
    """Reset all plan mode v2 state (for test isolation)."""
    with _plan_lock:
        _plan_state.reset()
        _delete_persisted_plan_state()
