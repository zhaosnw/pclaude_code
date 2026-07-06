"""Doctor /context warnings (`doctorContextWarnings.ts`).

Detects context-window pressure from:
- Large CLAUDE.md files
- Bloated agent descriptions
- MCP tools exceeding token budget
- Unreachable permission rules (ask/deny shadowed)

All four checks run in parallel (asyncio.gather), matching the TS Promise.all.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (matching TS status notices and existing patterns)
# ---------------------------------------------------------------------------

MCP_TOOLS_THRESHOLD = 25_000  # tokens

# Re-export for callers that need the threshold constants
from hare.utils.status_notice_helpers import (  # noqa: E402
    AGENT_DESCRIPTIONS_THRESHOLD,
)
from hare.utils.claudemd import MAX_MEMORY_CHARACTER_COUNT  # noqa: E402

# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------

ContextWarningType = Literal[
    "claudemd_files", "agent_descriptions", "mcp_tools", "unreachable_rules"
]
ContextWarningSeverity = Literal["warning", "error"]


@dataclass
class ContextWarning:
    """A single context-window warning detected by the doctor."""

    type: ContextWarningType
    severity: ContextWarningSeverity
    message: str
    details: list[str]
    current_value: int
    threshold: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for IPC / JSON transport."""
        return {
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "current_value": self.current_value,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextWarning:
        """Deserialize from a plain dict."""
        return cls(
            type=data["type"],
            severity=data.get("severity", "warning"),
            message=data.get("message", ""),
            details=data.get("details", []),
            current_value=data.get("current_value", 0),
            threshold=data.get("threshold", 0),
        )


@dataclass
class ContextWarnings:
    """Aggregate of all context warnings the doctor can produce."""

    claude_md_warning: ContextWarning | None = None
    agent_warning: ContextWarning | None = None
    mcp_warning: ContextWarning | None = None
    unreachable_rules_warning: ContextWarning | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for IPC / JSON transport."""
        return {
            "claude_md_warning": (
                self.claude_md_warning.to_dict() if self.claude_md_warning else None
            ),
            "agent_warning": (
                self.agent_warning.to_dict() if self.agent_warning else None
            ),
            "mcp_warning": (
                self.mcp_warning.to_dict() if self.mcp_warning else None
            ),
            "unreachable_rules_warning": (
                self.unreachable_rules_warning.to_dict()
                if self.unreachable_rules_warning
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextWarnings:
        """Deserialize from a plain dict."""
        return cls(
            claude_md_warning=(
                ContextWarning.from_dict(data["claude_md_warning"])
                if data.get("claude_md_warning")
                else None
            ),
            agent_warning=(
                ContextWarning.from_dict(data["agent_warning"])
                if data.get("agent_warning")
                else None
            ),
            mcp_warning=(
                ContextWarning.from_dict(data["mcp_warning"])
                if data.get("mcp_warning")
                else None
            ),
            unreachable_rules_warning=(
                ContextWarning.from_dict(data["unreachable_rules_warning"])
                if data.get("unreachable_rules_warning")
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Individual checkers
# ---------------------------------------------------------------------------


async def check_claude_md_files() -> ContextWarning | None:
    """Check for CLAUDE.md files exceeding MAX_MEMORY_CHARACTER_COUNT.

    Each file > 40 000 chars contributes to context pressure independently.
    Only large files are reported; the UI already shows total memory size.
    """
    try:
        from hare.utils.claudemd import get_large_memory_files, get_memory_files
    except ImportError as exc:
        logger.warning("Cannot load claudemd helpers: %s", exc)
        return None

    try:
        memory_files = await get_memory_files()
    except Exception as exc:
        logger.warning("Failed to load memory files: %s", exc)
        return None

    # Guard against None return from get_memory_files
    if not memory_files:
        return None

    large_files = get_large_memory_files(memory_files)

    if not large_files:
        return None

    # Sort descending by content length for display
    sorted_files = sorted(large_files, key=lambda f: len(f.content), reverse=True)

    details: list[str] = []
    for file in sorted_files:
        details.append(f"{file.path}: {len(file.content):,} chars")

    count = len(sorted_files)
    if count == 1:
        message = (
            f"Large CLAUDE.md file detected "
            f"({len(sorted_files[0].content):,} chars > "
            f"{MAX_MEMORY_CHARACTER_COUNT:,})"
        )
    else:
        message = (
            f"{count} large CLAUDE.md files detected "
            f"(each > {MAX_MEMORY_CHARACTER_COUNT:,} chars)"
        )

    return ContextWarning(
        type="claudemd_files",
        severity="warning",
        message=message,
        details=details,
        current_value=count,
        threshold=MAX_MEMORY_CHARACTER_COUNT,
    )


async def check_agent_descriptions(
    agent_info: Any | None,
) -> ContextWarning | None:
    """Check whether agent descriptions exceed the token threshold.

    Only non-built-in agents are counted (built-in agents are pre-loaded
    in the system prompt and not extra overhead).
    """
    if agent_info is None:
        return None

    try:
        from hare.services.token_estimation import rough_token_count_estimation
    except ImportError:
        def rough_token_count_estimation(s: str) -> int:
            return max(1, len(s) // 4)

    try:
        from hare.utils.status_notice_helpers import (
            AGENT_DESCRIPTIONS_THRESHOLD,
            get_agent_descriptions_total_tokens,
        )
    except ImportError as exc:
        logger.warning("Cannot load status notice helpers: %s", exc)
        return None

    try:
        total_tokens = get_agent_descriptions_total_tokens(agent_info)
    except Exception as exc:
        logger.warning("Failed to compute agent description tokens: %s", exc)
        return None

    if total_tokens <= AGENT_DESCRIPTIONS_THRESHOLD:
        return None

    # Break down token cost per custom agent (sorted high to low)
    active = getattr(agent_info, "active_agents", None) or []
    agent_tokens: list[dict[str, Any]] = []
    for agent in active:
        source = getattr(agent, "source", None)
        if source == "built-in":
            continue
        agent_type = getattr(agent, "agent_type", "")
        # Also handle snake_case variants from Python dataclasses
        if not agent_type:
            agent_type = getattr(agent, "agentType", "")
        when = getattr(agent, "when_to_use", "")
        if not when:
            when = getattr(agent, "whenToUse", "")
        desc = f"{agent_type}: {when}"
        try:
            tokens = rough_token_count_estimation(desc)
        except Exception:
            tokens = max(1, len(desc) // 4)
        agent_tokens.append({"name": agent_type or "unknown", "tokens": tokens})

    agent_tokens.sort(key=lambda a: a["tokens"], reverse=True)

    details: list[str] = []
    for a in agent_tokens[:5]:
        details.append(f"{a['name']}: ~{a['tokens']:,} tokens")

    overflow = len(agent_tokens) - 5
    if overflow > 0:
        from hare.utils.string_utils import pluralize

        details.append(f"({overflow} more custom {pluralize(overflow, 'agent')})")

    return ContextWarning(
        type="agent_descriptions",
        severity="warning",
        message=(
            f"Large agent descriptions "
            f"(~{total_tokens:,} tokens > "
            f"{AGENT_DESCRIPTIONS_THRESHOLD:,})"
        ),
        details=details,
        current_value=total_tokens,
        threshold=AGENT_DESCRIPTIONS_THRESHOLD,
    )


def _is_mcp_tool(tool: Any) -> bool:
    """Check whether a tool is an MCP tool.

    Matches TS `tool.isMcp` by checking for `mcp__` prefix on the name
    or a non-null `mcp_info` attribute. Also checks for `is_mcp` /
    `isMcp` boolean attributes if present.
    """
    # Direct is_mcp flag (closest to TS `tool.isMcp`)
    is_mcp = getattr(tool, "is_mcp", None)
    if is_mcp is None:
        is_mcp = getattr(tool, "isMcp", None)
    if isinstance(is_mcp, bool):
        return is_mcp

    # Fallback: name-based detection
    name = getattr(tool, "name", "")
    if isinstance(name, str) and name.startswith("mcp__"):
        return True

    # MCP info object present
    mcp_info = getattr(tool, "mcp_info", None)
    if mcp_info is not None:
        return True

    return False


async def check_mcp_tools(
    tools: list[Any],
    get_tool_permission_context: Callable[..., Any],
    agent_info: Any | None,
) -> ContextWarning | None:
    """Check whether MCP tool definitions consume too many tokens.

    MCP tools are loaded asynchronously and may not be available when the
    doctor command runs (it executes before MCP connections are established).
    Falls back to character-based estimation when the bulk API call fails.
    """
    if not tools:
        return None

    mcp_tools = [t for t in tools if _is_mcp_tool(t)]

    if not mcp_tools:
        return None

    # --- primary: use token-counting from analyze_context --------------------
    try:
        from hare.utils.analyze_context import count_mcp_tool_tokens

        # Attempt to resolve the active model for more accurate token counting.
        model: str | None = None
        try:
            from hare.utils.model.model import get_main_loop_model
            model_obj = get_main_loop_model()
            if model_obj and hasattr(model_obj, "id"):
                model = getattr(model_obj, "id", str(model_obj))
            elif model_obj:
                model = str(model_obj)
        except Exception:
            pass

        result = await count_mcp_tool_tokens(
            tools,
            get_tool_permission_context,
            agent_info,
            model=model,
        )
        mcp_tool_tokens: int = result.get("mcp_tool_tokens", 0)
        mcp_tool_details: list[dict[str, Any]] = result.get("mcp_tool_details", [])

        if mcp_tool_tokens <= MCP_TOOLS_THRESHOLD:
            return None

        # Group tools by server for concise display
        by_server: dict[str, dict[str, int]] = {}
        for td in mcp_tool_details:
            name = td.get("name", "")
            server = td.get("server_name", "unknown")
            # If server_name wasn't set by count_mcp_tool_tokens, extract from name
            if server == "unknown" and "__" in name:
                parts = name.split("__")
                server = parts[1] if len(parts) > 1 else "unknown"
            entry = by_server.setdefault(server, {"count": 0, "tokens": 0})
            entry["count"] += 1
            entry["tokens"] += td.get("tokens", 0)

        sorted_servers = sorted(
            by_server.items(), key=lambda kv: kv[1]["tokens"], reverse=True
        )

        details: list[str] = []
        for srv_name, info in sorted_servers[:5]:
            details.append(
                f"{srv_name}: {info['count']} tools "
                f"(~{info['tokens']:,} tokens)"
            )

        if len(sorted_servers) > 5:
            details.append(f"({len(sorted_servers) - 5} more servers)")

        return ContextWarning(
            type="mcp_tools",
            severity="warning",
            message=(
                f"Large MCP tools context "
                f"(~{mcp_tool_tokens:,} tokens > "
                f"{MCP_TOOLS_THRESHOLD:,})"
            ),
            details=details,
            current_value=mcp_tool_tokens,
            threshold=MCP_TOOLS_THRESHOLD,
        )

    except Exception as exc:
        logger.debug(
            "count_mcp_tool_tokens failed, falling back to character estimation: %s",
            exc,
        )
        # --- fallback: character-based estimation ----------------------------
        try:
            from hare.services.token_estimation import rough_token_count_estimation
        except ImportError:

            def rough_token_count_estimation(s: str) -> int:
                return max(1, len(s) // 4)

        estimated = 0
        for tool in mcp_tools:
            name = getattr(tool, "name", "") or ""
            desc = ""
            try:
                if hasattr(tool, "description"):
                    raw_desc = tool.description
                    if callable(raw_desc):
                        raw = raw_desc({}, {})
                        if asyncio.iscoroutine(raw):
                            raw = await raw
                        desc = raw if isinstance(raw, str) else str(raw)
                    else:
                        desc = str(raw_desc) if raw_desc else ""
            except Exception:
                desc = name
            # Use combined text length for estimation (matching TS fallback)
            combined = f"{name}{desc}"
            estimated += rough_token_count_estimation(combined)

        if estimated <= MCP_TOOLS_THRESHOLD:
            return None

        return ContextWarning(
            type="mcp_tools",
            severity="warning",
            message=(
                f"Large MCP tools context "
                f"(~{estimated:,} tokens estimated > "
                f"{MCP_TOOLS_THRESHOLD:,})"
            ),
            details=[f"{len(mcp_tools)} MCP tools detected (token count estimated)"],
            current_value=estimated,
            threshold=MCP_TOOLS_THRESHOLD,
        )


async def check_unreachable_rules(
    get_tool_permission_context: Callable[..., Any],
) -> ContextWarning | None:
    """Detect permission rules that can never be reached.

    Two patterns are detected:
    - Allow rule shadowed by a tool-wide deny rule (completely blocked)
    - Allow rule shadowed by a tool-wide ask rule (will always prompt)
    """
    try:
        from hare.utils.permissions.permission_rule import (
            permission_rule_value_to_string,
        )
        from hare.utils.permissions.shadowed_rule_detection import (
            DetectUnreachableRulesOptions,
            detect_unreachable_rules,
        )
        from hare.utils.sandbox.sandbox_adapter import SandboxManager
    except ImportError as exc:
        logger.warning("Cannot load permission inspection modules: %s", exc)
        return None

    try:
        context = await get_tool_permission_context()
    except Exception as exc:
        logger.warning("Failed to get tool permission context: %s", exc)
        return None

    if context is None:
        return None

    try:
        sandbox_auto_allow = (
            SandboxManager.is_sandboxing_enabled()
            and SandboxManager.is_auto_allow_bash_if_sandboxed_enabled()
        )
    except Exception:
        sandbox_auto_allow = False

    try:
        unreachable = detect_unreachable_rules(
            context,
            DetectUnreachableRulesOptions(
                sandbox_auto_allow_enabled=sandbox_auto_allow,
            ),
        )
    except Exception as exc:
        logger.warning("Failed to detect unreachable rules: %s", exc)
        return None

    if not unreachable:
        return None

    from hare.utils.string_utils import pluralize

    details: list[str] = []
    for r in unreachable:
        rule_str = permission_rule_value_to_string(r.rule.rule_value)
        details.append(f"{rule_str}: {r.reason}")
        details.append(f"  Fix: {r.fix}")

    count = len(unreachable)
    return ContextWarning(
        type="unreachable_rules",
        severity="warning",
        message=(
            f"{count} {pluralize(count, 'unreachable permission rule')} detected"
        ),
        details=details,
        current_value=count,
        threshold=0,
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


async def check_context_warnings(
    tools: list[Any],
    agent_info: Any,
    get_tool_permission_context: Callable[..., Any],
) -> ContextWarnings:
    """Run all context-warning checks in parallel (matching TS Promise.all).

    Args:
        tools: Current tool pool (built-in + MCP).
        agent_info: AgentDefinitionsResult from the agent loader.
        get_tool_permission_context: Async callback returning the current
            ToolPermissionContext.

    Returns:
        ContextWarnings with a nullable warning for each category.
    """
    results = await asyncio.gather(
        check_claude_md_files(),
        check_agent_descriptions(agent_info),
        check_mcp_tools(tools, get_tool_permission_context, agent_info),
        check_unreachable_rules(get_tool_permission_context),
        return_exceptions=True,
    )

    def _unpack(idx: int) -> ContextWarning | None:
        val = results[idx]
        if isinstance(val, BaseException):
            # Swallow errors in individual checks so one failure doesn't
            # prevent the whole doctor from returning results.
            logger.debug(
                "Context-warning check %d failed: %s",
                idx,
                val,
                exc_info=val,
            )
            return None
        return val

    return ContextWarnings(
        claude_md_warning=_unpack(0),
        agent_warning=_unpack(1),
        mcp_warning=_unpack(2),
        unreachable_rules_warning=_unpack(3),
    )


# ---------------------------------------------------------------------------
# Helper functions for working with warnings
# ---------------------------------------------------------------------------


def has_any_warnings(warnings: ContextWarnings) -> bool:
    """Return True if at least one warning is present."""
    return (
        warnings.claude_md_warning is not None
        or warnings.agent_warning is not None
        or warnings.mcp_warning is not None
        or warnings.unreachable_rules_warning is not None
    )


def get_active_warnings(warnings: ContextWarnings) -> list[ContextWarning]:
    """Return a flat list of all non-null warnings."""
    active: list[ContextWarning] = []
    for attr in (
        "claude_md_warning",
        "agent_warning",
        "mcp_warning",
        "unreachable_rules_warning",
    ):
        w = getattr(warnings, attr, None)
        if w is not None:
            active.append(w)
    return active


def get_warning_count(warnings: ContextWarnings) -> int:
    """Return the number of active (non-null) warnings."""
    return len(get_active_warnings(warnings))


def get_warnings_by_type(
    warnings: ContextWarnings,
) -> dict[str, ContextWarning | None]:
    """Return a dict mapping warning type keys to their (possibly None) warnings."""
    return {
        "claudemd_files": warnings.claude_md_warning,
        "agent_descriptions": warnings.agent_warning,
        "mcp_tools": warnings.mcp_warning,
        "unreachable_rules": warnings.unreachable_rules_warning,
    }


def get_warnings_by_severity(
    warnings: ContextWarnings,
) -> dict[ContextWarningSeverity, list[ContextWarning]]:
    """Group active warnings by their severity level."""
    grouped: dict[ContextWarningSeverity, list[ContextWarning]] = {
        "warning": [],
        "error": [],
    }
    for w in get_active_warnings(warnings):
        grouped.setdefault(w.severity, []).append(w)
    return grouped


def has_critical_warnings(warnings: ContextWarnings) -> bool:
    """Return True if any warning has 'error' severity."""
    for w in get_active_warnings(warnings):
        if w.severity == "error":
            return True
    return False


def get_total_context_overhead_estimate(
    warnings: ContextWarnings,
) -> int:
    """Sum the current_value across all active warnings to estimate total overhead.

    This is a rough heuristic — different warning types measure different
    things (count of files, token counts, etc.).
    """
    total = 0
    for w in get_active_warnings(warnings):
        if w.type in ("agent_descriptions", "mcp_tools"):
            # Token-based warnings contribute directly
            total += w.current_value
        elif w.type == "claudemd_files":
            # File count; estimate character cost
            char_estimate = w.current_value * MAX_MEMORY_CHARACTER_COUNT
            total += char_estimate // 4  # rough token conversion
        # unreachable_rules doesn't contribute tokens directly
    return total


def format_warning_for_display(warning: ContextWarning) -> str:
    """Format a single ContextWarning as a display string with details."""
    lines: list[str] = []
    # Severity indicator
    sev_marker = "!!" if warning.severity == "error" else "! "
    lines.append(f"{sev_marker} {warning.message}")

    # Details
    if warning.details:
        for detail in warning.details:
            lines.append(f"     {detail}")

    return "\n".join(lines)


def format_warnings_summary(warnings: ContextWarnings) -> str:
    """Generate a one-line summary of all active context warnings.

    Returns empty string if no warnings are present.
    """
    active = get_active_warnings(warnings)
    if not active:
        return ""

    count = len(active)
    errors = sum(1 for w in active if w.severity == "error")
    from hare.utils.string_utils import pluralize

    parts: list[str] = [f"{count} context {pluralize(count, 'warning')}"]
    if errors > 0:
        parts.append(f"({errors} {pluralize(errors, 'error')})")

    # Categorize
    categories: list[str] = []
    if warnings.claude_md_warning:
        categories.append("CLAUDE.md files")
    if warnings.agent_warning:
        categories.append("agent descriptions")
    if warnings.mcp_warning:
        categories.append("MCP tools")
    if warnings.unreachable_rules_warning:
        categories.append("unreachable rules")

    if categories:
        parts.append(f"[{', '.join(categories)}]")

    return " ".join(parts)


def format_warnings_for_cli(warnings: ContextWarnings) -> str:
    """Format all context warnings for CLI doctor output.

    Produces a human-readable block suitable for terminal display.
    Returns empty string if no warnings are present.
    """
    active = get_active_warnings(warnings)
    if not active:
        return ""

    lines: list[str] = []
    lines.append("─" * 40)
    lines.append("Context Window Warnings")
    lines.append("─" * 40)

    for w in active:
        lines.append("")
        lines.append(format_warning_for_display(w))

    # Footer with summary counts
    errors = sum(1 for w in active if w.severity == "error")
    warnings_count = len(active) - errors
    from hare.utils.string_utils import pluralize

    footer_parts: list[str] = []
    if errors:
        footer_parts.append(f"{errors} {pluralize(errors, 'critical issue')}")
    if warnings_count:
        footer_parts.append(f"{warnings_count} {pluralize(warnings_count, 'advisory')}")
    if footer_parts:
        lines.append("")
        lines.append(f"  Summary: {', '.join(footer_parts)}")

    return "\n".join(lines)
