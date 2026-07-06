"""Port of: src/commands/ultraplan.tsx — Launch remote multi-agent planning in Claude Code on the web."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "ultraplan"
DESCRIPTION = "Advanced multi-agent plan mode (Opus) — runs in Claude Code on the web"
ALIASES: list[str] = []

CCR_TERMS_URL = "https://code.claude.com/docs/en/claude-code-on-the-web"


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show ultraplan usage info and, when available, current session state.

    In the full TypeScript CLI this command launches a remote CCR (Claude Code
    on the web) session that runs advanced multi-agent planning with Opus while
    keeping the local terminal free.  The headless Python port surfaces usage
    instructions and any active-session metadata.
    """
    get_app_state = context.get("get_app_state") if isinstance(context, dict) else None
    options = context.get("options", {}) if isinstance(context, dict) else {}
    blurb = " ".join(args).strip() if args else ""

    # --- current state -------------------------------------------------------
    state_lines: list[str] = []
    if get_app_state:
        try:
            app_state = get_app_state()
        except Exception:
            app_state = {}
        session_url = app_state.get("ultraplanSessionUrl")
        launching = app_state.get("ultraplanLaunching")
        pending = app_state.get("ultraplanPendingChoice")
        if session_url:
            state_lines.append(f"**Active session:** {session_url}")
            state_lines.append("")
        elif launching:
            state_lines.append("**Status:** launching remote session, please wait…")
            state_lines.append("")
        elif pending:
            state_lines.append("**Status:** plan ready — awaiting your choice (execute remotely / teleport here).")
            state_lines.append("")
    else:
        state_lines.append("**Mode:** local (no bridge state available)")
        state_lines.append("")

    # --- usage ---------------------------------------------------------------
    if blurb:
        # User provided a prompt — in the full CLI this would launch a remote
        # planning session.  Here we show what would happen.
        usage_lines: list[str] = [
            "## ultraplan — launch requested",
            "",
            f"**Prompt:** {blurb}",
            "",
            "In the full TypeScript CLI this would:",
            "",
            "1. Check eligibility (login, billing, model availability)",
            "2. Launch a remote **Opus** session in Claude Code on the web",
            "3. Run multi-agent exploration & planning (up to 30 min)",
            "4. Return the validated plan so you can execute it remotely or teleport it here",
            "",
            "The headless Python port does not run remote sessions — use the",
            "interactive CLI (`claude`) for full ultraplan support.",
            "",
            f"Terms: {CCR_TERMS_URL}",
        ]
        return {"type": "text", "value": "\n".join(usage_lines)}

    # Bare `/ultraplan` — show help / feature overview.
    help_lines: list[str] = [
        "## ultraplan",
        "",
        "Advanced **multi-agent** plan mode with our most powerful model (Opus).",
        "Runs in Claude Code on the web so your terminal stays free while the",
        "remote agents explore, plan, and validate.",
        "",
        "### Usage",
        "",
        "```",
        "/ultraplan <prompt>",
        "```",
        "or include **ultraplan** anywhere in your prompt.",
        "",
        "### Requirements",
        "",
        "- `/login` — an active account is required",
        "- Opus model availability on the remote session",
        "",
        "### What happens",
        "",
        "1. Your goal + context are sent to a **remote CCR session**",
        "2. Opus explores the problem from multiple angles (multi-agent)",
        "3. A structured, validated plan lands in your local session",
        "4. You choose: execute remotely, or teleport the plan here",
        "",
        "Timeout: 30 minutes.  You can keep working locally while it plans.",
        "",
        f"Terms: {CCR_TERMS_URL}",
    ]
    return {"type": "text", "value": "\n".join(help_lines)}
