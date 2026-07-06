"""CCR session polling for /ultraplan. Port of: src/utils/ultraplan/ccrSession.ts

Waits for an approved ExitPlanMode tool_result, then extracts the plan text.
Uses pollRemoteSessionEvents (shared with RemoteAgentTask) for pagination.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from hare.tools_impl.ExitPlanModeTool import EXIT_PLAN_MODE_TOOL_NAME

logger = logging.getLogger("hare.ultraplan")

POLL_INTERVAL_MS = 3000
MAX_CONSECUTIVE_FAILURES = 5
ULTRAPLAN_TELEPORT_SENTINEL = "__ULTRAPLAN_TELEPORT_LOCAL__"
APPROVED_PLAN_MARKERS = ("## Approved Plan (edited by user):\n", "## Approved Plan:\n")

# Minimum plan quality thresholds
MIN_PLAN_LENGTH_CHARS = 50
MIN_PLAN_LINES = 3
MIN_FILE_REFERENCES = 1
MIN_ACTION_ITEMS = 1

# Graduated timeout phases (seconds)
PHASE_TIMEOUTS = {
    "initial": 300,         # time to enter plan mode + draft plan
    "plan_pending": 1200,   # time after ExitPlanMode called, waiting for approval
    "teleport": 600,        # time to complete teleport handoff
}


class PollFailReason(str, Enum):
    TERMINATED = "terminated"
    TIMEOUT_PENDING = "timeout_pending"
    TIMEOUT_NO_PLAN = "timeout_no_plan"
    EXTRACT_MARKER_MISSING = "extract_marker_missing"
    NETWORK_OR_UNKNOWN = "network_or_unknown"
    STOPPED = "stopped"
    PLAN_TOO_SHORT = "plan_too_short"
    PLAN_NO_ACTION = "plan_no_action"


class PlanQuality(str, Enum):
    GOOD = "good"
    SHORT = "short"
    NO_FILES = "no_files"
    NO_ACTIONS = "no_actions"
    TRUNCATED = "truncated"
    EMPTY = "empty"


class UltraplanPollError(Exception):
    def __init__(self, message: str, reason: PollFailReason, reject_count: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.reject_count = reject_count


@dataclass
class PollResult:
    plan: str
    reject_count: int
    execution_target: str  # "local" (teleport) or "remote" (in-CCR)


@dataclass
class PollProgress:
    """Detailed polling session progress for telemetry and debugging."""

    started_at: float = 0.0
    plan_seen_at: float = 0.0
    approved_at: float = 0.0
    phase: str = "initial"
    total_events: int = 0
    total_batches: int = 0
    network_failures: int = 0

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at if self.started_at else 0.0

    @property
    def plan_wait_s(self) -> float:
        if self.plan_seen_at and self.started_at:
            return self.plan_seen_at - self.started_at
        return 0.0

    @property
    def approval_wait_s(self) -> float:
        if self.approved_at and self.plan_seen_at:
            return self.approved_at - self.plan_seen_at
        return 0.0

    def summary(self) -> str:
        return (
            f"phase={self.phase} batches={self.total_batches} events={self.total_events} "
            f"failures={self.network_failures} plan_wait={self.plan_wait_s:.0f}s "
            f"approval_wait={self.approval_wait_s:.0f}s total={self.elapsed_s:.0f}s"
        )


@dataclass
class PlanRejectionRecord:
    """Tracks what was rejected so the next plan can avoid the same pitfalls."""

    attempt: int
    plan_snippet: str  # first 200 chars for logging
    rejected_at: float
    reason_hint: str = ""  # optional classification of why it was rejected


class PlanRejectionHistory:
    """Accumulates rejection records across polling attempts."""

    def __init__(self) -> None:
        self._records: list[PlanRejectionRecord] = []

    def record(self, plan_text: str, reason_hint: str = "") -> None:
        self._records.append(PlanRejectionRecord(
            attempt=len(self._records) + 1,
            plan_snippet=plan_text[:200],
            rejected_at=time.monotonic(),
            reason_hint=reason_hint,
        ))
        logger.info(
            "[ultraplan] rejection #%d recorded (hint=%s, snippet=%.80s...)",
            len(self._records), reason_hint or "unspecified", plan_text,
        )

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def last_rejection(self) -> PlanRejectionRecord | None:
        return self._records[-1] if self._records else None

    def patterns(self) -> list[str]:
        """Heuristic: surface common rejection reasons."""
        hints: list[str] = []
        if self.count >= 3:
            hints.append("persistent_rejection")
        if any("vague" in r.reason_hint.lower() for r in self._records):
            hints.append("plan_too_vague")
        if any("scope" in r.reason_hint.lower() for r in self._records):
            hints.append("scope_mismatch")
        return hints


# ============================================================================
# Plan quality validation & normalization
# ============================================================================

_TRUNCATION_CLUES = re.compile(
    r"(\.\.\.\s*$|truncat|content cut|message exceed|token limit)",
    re.IGNORECASE,
)
_FILE_REF_PATTERN = re.compile(
    r"""(?:`([^`]+\.(?:py|ts|js|tsx|jsx|rs|go|java|rb|php|c|h|cpp|hpp|cs|swift|kt|scala|sh|bash|yaml|yml|json|toml|cfg|ini|md|txt|sql|css|html|vue|svelte))`|  # backtick-quoted path
         (?:^|\s)((?:[a-zA-Z]:[/\\]|/|\.{1,2}/)[^\s`"'[\]]+\.(?:py|ts|js|tsx|jsx|rs|go|java|rb|php|c|h|cpp|hpp|cs|swift|kt|scala|sh|bash|yaml|yml|json|toml|cfg|ini|md|txt|sql|css|html|vue|svelte))  # absolute/relative path
    )""",
    re.MULTILINE | re.VERBOSE,
)
_ACTION_LINE_PATTERN = re.compile(
    r"^\s*(?:[-*+]|\d+[.)]\s)(?!\s*$)", re.MULTILINE,
)


def validate_plan(plan: str) -> PlanQuality:
    """Validate extracted plan for minimum usable quality.

    Returns PlanQuality classification — callers should reject plans
    that are EMPTY, SHORT, or TRUNCATED before handing off to execution.
    """
    stripped = plan.strip()
    if not stripped:
        return PlanQuality.EMPTY
    if len(stripped) < MIN_PLAN_LENGTH_CHARS:
        return PlanQuality.SHORT

    lines = [ln for ln in stripped.split("\n") if ln.strip()]
    if len(lines) < MIN_PLAN_LINES:
        return PlanQuality.SHORT

    if _TRUNCATION_CLUES.search(stripped):
        return PlanQuality.TRUNCATED

    file_refs = len(_FILE_REF_PATTERN.findall(stripped))
    if file_refs < MIN_FILE_REFERENCES:
        # Relaxed: plans can describe conceptual changes without file paths.
        # But if it also has no action items, flag it.
        action_count = len(_ACTION_LINE_PATTERN.findall(stripped))
        if action_count < MIN_ACTION_ITEMS:
            return PlanQuality.NO_ACTIONS

    return PlanQuality.GOOD


def normalize_plan(plan: str) -> str:
    """Post-process extracted plan text for downstream consumption.

    - Strip leading chatty preamble (common in Claude's plan output)
    - Collapse excessive blank lines
    - Remove trailing "---" or "***" separators
    - Ensure the plan ends with a newline for consistent concatenation
    """
    lines = plan.strip().split("\n")

    # Strip chatty preamble lines (common patterns in Claude output)
    preamble_patterns = (
        "here is", "here's", "below is", "i've", "i have",
        "let me", "okay", "sure", "certainly", "absolutely",
    )
    while lines and any(
        lines[0].lower().lstrip("*- #").strip().startswith(p) for p in preamble_patterns
    ):
        lines.pop(0)

    # Strip trailing separators
    while lines and lines[-1].strip() in ("---", "***", "===", "..."):
        lines.pop()

    # Collapse 3+ consecutive blank lines into 2
    collapsed: list[str] = []
    blanks = 0
    for ln in lines:
        if ln.strip() == "":
            blanks += 1
            if blanks <= 2:
                collapsed.append(ln)
        else:
            blanks = 0
            collapsed.append(ln)

    # Strip trailing blank lines
    while collapsed and collapsed[-1].strip() == "":
        collapsed.pop()

    result = "\n".join(collapsed).rstrip() + "\n"
    return result


def extract_file_paths_from_plan(plan: str) -> list[str]:
    """Extract all file paths referenced in a plan for pre-flight checks."""
    matches = _FILE_REF_PATTERN.findall(plan)
    paths: set[str] = set()
    for match in matches:
        # Each findall on a pattern with multiple groups returns a tuple
        p = match[0] or match[1]
        if p:
            paths.add(p)
    return sorted(paths)


# ============================================================================
# Content parsing helpers
# ============================================================================


def _content_to_text(content: Any) -> str:
    """Tool_result content -> string. Supports str or [{type:'text',text}...]."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else "" for b in content)
    return ""


def _extract_teleport_plan(content: Any) -> str | None:
    text = _content_to_text(content)
    idx = text.find(ULTRAPLAN_TELEPORT_SENTINEL + "\n")
    return text[idx + len(ULTRAPLAN_TELEPORT_SENTINEL) + 1:].rstrip() if idx != -1 else None


def _extract_approved_plan(content: Any) -> str:
    text = _content_to_text(content)
    for marker in APPROVED_PLAN_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            return text[idx + len(marker):].rstrip()
    raise ValueError(f"ExitPlanMode approved but no plan marker. Preview: {text[:200]}")


# ============================================================================
# ExitPlanModeScanner — pure stateful event classifier
# ============================================================================


class ExitPlanModeScanner:
    """Pure stateful classifier for CCR event stream.

    Ingests SDKMessage[] batches from pollRemoteSessionEvents, returns
    {kind, plan?, id?, subtype?}. Tracks tool_use calls and tool_results
    across batches. Precedence: approved > terminated > rejected > pending
    > unchanged. No I/O, no timers — unit-testable.
    """

    def __init__(self) -> None:
        self._calls: list[str] = []
        self._results: dict[str, dict[str, Any]] = {}
        self._rejected: set[str] = set()
        self._terminated: dict[str, str] | None = None
        self._rescan = False
        self.ever_seen_pending = False

    @property
    def reject_count(self) -> int:
        return len(self._rejected)

    @property
    def has_pending_plan(self) -> bool:
        for cid in reversed(self._calls):
            if cid not in self._rejected:
                return cid not in self._results
        return False

    def ingest(self, new_events: list[dict[str, Any]]) -> dict[str, Any]:
        """Classify a batch of events. Returns dict with 'kind' key."""
        for msg in new_events:
            mt = msg.get("type", "")
            if mt == "assistant":
                for b in msg.get("message", {}).get("content", []):
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == EXIT_PLAN_MODE_TOOL_NAME:
                        self._calls.append(b["id"])
            elif mt == "user":
                content = msg.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            self._results[b["tool_use_id"]] = b
            elif mt == "result" and msg.get("subtype") != "success":
                self._terminated = {"subtype": msg.get("subtype", "unknown")}

        should_scan = len(new_events) > 0 or self._rescan
        self._rescan = False
        found: dict[str, Any] | None = None

        if should_scan:
            for cid in reversed(self._calls):
                if cid in self._rejected:
                    continue
                tr = self._results.get(cid)
                if tr is None:
                    found = {"kind": "pending"}
                elif tr.get("is_error") is True:
                    tp = _extract_teleport_plan(tr.get("content"))
                    found = {"kind": "teleport", "plan": tp} if tp else {"kind": "rejected", "id": cid}
                else:
                    found = {"kind": "approved", "plan": _extract_approved_plan(tr.get("content"))}
                break
            if found and found["kind"] in ("approved", "teleport"):
                return found

        if found and found["kind"] == "rejected":
            self._rejected.add(found["id"])
            self._rescan = True
        if self._terminated:
            return {"kind": "terminated", "subtype": self._terminated["subtype"]}
        if found:
            if found["kind"] == "pending":
                self.ever_seen_pending = True
            return found
        return {"kind": "unchanged"}


# ============================================================================
# Polling orchestration
# ============================================================================


def _determine_phase(scanner: ExitPlanModeScanner, session_status: str, idle: bool) -> str:
    """Derive human-readable phase from scanner state + session status."""
    if scanner.has_pending_plan:
        return "plan_pending"
    if idle:
        if scanner.ever_seen_pending and not scanner.has_pending_plan:
            return "needs_input"
        return "running"
    return "running"


def _resolve_timeout(context: str, configured_ms: int, scanner: ExitPlanModeScanner) -> int:
    """Resolve effective timeout from config, falling back to graduated defaults."""
    if configured_ms and configured_ms > 0:
        return configured_ms
    if scanner.ever_seen_pending:
        return PHASE_TIMEOUTS["plan_pending"] * 1000
    return PHASE_TIMEOUTS["initial"] * 1000


async def poll_for_approved_exit_plan_mode(
    session_id: str,
    timeout_ms: int,
    *,
    poll_remote_events: Callable[[str, str | None], Awaitable[dict[str, Any]]] | None = None,
    is_transient_network_error: Callable[[Exception], bool] | None = None,
    on_phase_change: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> PollResult:
    """Poll remote CCR session until ExitPlanMode approved or timeout.

    Returns plan + execution_target ("local"=teleport, "remote"=in-CCR).
    Raises UltraplanPollError on terminal failure, timeout, or caller stop.
    """
    if poll_remote_events is None:
        raise ValueError("poll_remote_events callback is required")

    loop = asyncio.get_event_loop()
    progress = PollProgress(started_at=time.monotonic())
    rejection_history = PlanRejectionHistory()
    scanner = ExitPlanModeScanner()
    cursor: str | None = None
    failures = 0
    last_phase = "initial"

    # Resolve effective timeout
    effective_timeout_ms = _resolve_timeout("poll", timeout_ms, scanner)
    deadline = loop.time() + effective_timeout_ms / 1000.0

    while loop.time() < deadline:
        if should_stop and should_stop():
            logger.info("[ultraplan] caller requested stop after %s", progress.summary())
            raise UltraplanPollError("poll stopped", PollFailReason.STOPPED, scanner.reject_count)

        # --- fetch next batch ---
        try:
            resp = await poll_remote_events(session_id, cursor)
            new_events: list[dict[str, Any]] = resp.get("newEvents", [])
            cursor = resp.get("lastEventId")
            session_status: str = resp.get("sessionStatus", "")
            progress.total_batches += 1
            progress.total_events += len(new_events)
            failures = 0
        except Exception as e:
            transient = is_transient_network_error(e) if is_transient_network_error else False
            failures += 1
            progress.network_failures += 1
            if not transient or failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error("[ultraplan] poll failed permanently after %d failures", failures)
                raise UltraplanPollError(str(e), PollFailReason.NETWORK_OR_UNKNOWN, scanner.reject_count) from e
            await asyncio.sleep(POLL_INTERVAL_MS / 1000.0)
            continue

        # --- classify ---
        try:
            result = scanner.ingest(new_events)
        except Exception as e:
            logger.exception("[ultraplan] scanner ingest raised")
            raise UltraplanPollError(str(e), PollFailReason.EXTRACT_MARKER_MISSING, scanner.reject_count) from e

        if result["kind"] == "approved":
            progress.approved_at = time.monotonic()
            plan_text = result["plan"]
            quality = validate_plan(plan_text)
            if quality == PlanQuality.EMPTY:
                logger.warning("[ultraplan] approved plan was empty — treating as rejection")
                rejection_history.record(plan_text, "empty_plan")
                raise UltraplanPollError("approved plan was empty", PollFailReason.PLAN_TOO_SHORT, scanner.reject_count)
            if quality == PlanQuality.TRUNCATED:
                logger.warning("[ultraplan] approved plan appears truncated — proceeding with caution")
            if quality in (PlanQuality.SHORT, PlanQuality.NO_ACTIONS):
                logger.warning(
                    "[ultraplan] approved plan quality=%s, reject_count=%d — proceeding anyway",
                    quality.value, scanner.reject_count,
                )
            normalized = normalize_plan(plan_text)
            logger.info("[ultraplan] plan approved, %s", progress.summary())
            return PollResult(normalized, scanner.reject_count, "remote")

        if result["kind"] == "teleport":
            progress.approved_at = time.monotonic()
            plan_text = result["plan"] or ""
            quality = validate_plan(plan_text)
            if quality == PlanQuality.EMPTY:
                logger.warning("[ultraplan] teleport plan was empty")
                raise UltraplanPollError("teleport plan was empty", PollFailReason.PLAN_TOO_SHORT, scanner.reject_count)
            normalized = normalize_plan(plan_text)
            logger.info("[ultraplan] teleport plan received, %s", progress.summary())
            return PollResult(normalized, scanner.reject_count, "local")

        if result["kind"] == "terminated":
            logger.error("[ultraplan] session terminated: subtype=%s, %s", result.get("subtype"), progress.summary())
            raise UltraplanPollError(
                f"session ended ({result['subtype']}) before plan approval",
                PollFailReason.TERMINATED, scanner.reject_count,
            )

        if result["kind"] == "rejected":
            # Capture rejection for learning
            tr = scanner._results.get(result["id"])
            plan_text = _content_to_text(tr.get("content", "")) if tr else ""
            rejection_history.record(plan_text, "tool_rejected")
            logger.info(
                "[ultraplan] plan rejected (#%d), %s",
                rejection_history.count, progress.summary(),
            )

        # --- phase transition ---
        quiet_idle = session_status in ("idle", "requires_action") and len(new_events) == 0
        phase = _determine_phase(scanner, session_status, quiet_idle)
        progress.phase = phase
        if phase != last_phase:
            if phase == "plan_pending" and not progress.plan_seen_at:
                progress.plan_seen_at = time.monotonic()
            logger.debug("[ultraplan] phase %s -> %s", last_phase, phase)
            last_phase = phase
            if on_phase_change:
                on_phase_change(phase)

            # Extend deadline when plan becomes pending (give user time to review)
            if phase == "plan_pending":
                new_deadline = loop.time() + PHASE_TIMEOUTS["plan_pending"]
                if new_deadline > deadline:
                    deadline = new_deadline
                    logger.debug("[ultraplan] extended deadline to +%ds for plan review", PHASE_TIMEOUTS["plan_pending"])

        await asyncio.sleep(POLL_INTERVAL_MS / 1000.0)

    t = effective_timeout_ms / 1000
    if scanner.ever_seen_pending:
        raise UltraplanPollError(f"no approval after {t}s", PollFailReason.TIMEOUT_PENDING, scanner.reject_count)
    raise UltraplanPollError(
        f"ExitPlanMode never reached after {t}s (container failed or session ID mismatch?)",
        PollFailReason.TIMEOUT_NO_PLAN, scanner.reject_count,
    )


# ============================================================================
# Session lifecycle management
# ============================================================================


@dataclass
class CCRSessionConfig:
    """Structured config for a CCR ultraplan session.

    All callbacks follow the TS dependency-injection pattern — the caller
    provides the actual I/O functions so the core logic stays testable.
    """

    session_id: str = ""
    prompt: str = ""
    timeout_ms: int = 1_800_000  # 30 min default
    poll_remote_events: Callable[[str, str | None], Awaitable[dict[str, Any]]] | None = None
    is_transient_network_error: Callable[[Exception], bool] | None = None
    on_phase_change: Callable[[str], None] | None = None
    should_stop: Callable[[], bool] | None = None
    max_rejections: int = 10  # stop polling after this many rejections


def _build_config(raw: dict[str, Any] | None) -> CCRSessionConfig:
    """Parse raw dict config into a CCRSessionConfig, with sensible defaults."""
    if not raw:
        return CCRSessionConfig()
    return CCRSessionConfig(
        session_id=raw.get("session_id", ""),
        prompt=raw.get("prompt", ""),
        timeout_ms=raw.get("timeout_ms", 1_800_000),
        poll_remote_events=raw.get("poll_remote_events"),
        is_transient_network_error=raw.get("is_transient_network_error"),
        on_phase_change=raw.get("on_phase_change"),
        should_stop=raw.get("should_stop"),
        max_rejections=raw.get("max_rejections", 10),
    )


async def start_ccr_session(
    prompt: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a remote CCR session, poll for ExitPlanMode approval.

    Callbacks (poll_remote_events, is_transient_network_error, on_phase_change,
    should_stop) injected via config dict per the TS dependency-injection pattern.
    Returns plan text, reject_count, execution_target, session_id — or error
    info dict on failure.
    """
    cfg = _build_config(config)
    sid = cfg.session_id
    poll = cfg.poll_remote_events
    if not sid or not poll:
        logger.error("start_ccr_session: session_id and poll_remote_events required")
        return {
            "plan": "", "reject_count": 0, "execution_target": "none",
            "session_id": sid, "error": "missing session_id or poll_remote_events",
        }

    try:
        r = await poll_for_approved_exit_plan_mode(
            session_id=sid,
            timeout_ms=cfg.timeout_ms,
            poll_remote_events=poll,
            is_transient_network_error=cfg.is_transient_network_error,
            on_phase_change=cfg.on_phase_change,
            should_stop=cfg.should_stop,
        )
        result: dict[str, Any] = {
            "plan": r.plan, "reject_count": r.reject_count,
            "execution_target": r.execution_target, "session_id": sid,
            "file_paths": extract_file_paths_from_plan(r.plan),
        }
        logger.info(
            "start_ccr_session success: target=%s rejects=%d files=%d plan_len=%d",
            r.execution_target, r.reject_count,
            len(result["file_paths"]), len(r.plan),
        )
        return result
    except UltraplanPollError as e:
        logger.error("start_ccr_session failed: reason=%s reject=%d", e.reason.value, e.reject_count)
        return {
            "plan": "", "reject_count": e.reject_count,
            "execution_target": "none", "session_id": sid,
            "error": str(e), "error_reason": e.reason.value,
        }
