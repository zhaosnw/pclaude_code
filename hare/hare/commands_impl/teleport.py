"""Port of: src/commands/teleport/. Teleport to or list available remote sessions."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "teleport"
DESCRIPTION = "Jump between sessions — teleport to a remote or previous session"
ALIASES: list[str] = ["tp"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Teleport to a session by ID, or list available teleport targets."""
    get_app_state = context.get("get_app_state")
    get_teleport_targets = context.get("get_teleport_targets")
    get_session_id = context.get("get_session_id")
    teleport = context.get("teleport")

    current_id = get_session_id() if get_session_id else "unknown"

    # If a target session ID was provided, attempt the teleport
    target = (args[0] if args else "").strip() if isinstance(args, list) else str(args or "").strip()
    if target and teleport:
        try:
            result = await teleport(target)
            if result.get("ok"):
                return {
                    "type": "text",
                    "value": (
                        f"**Teleported** `{current_id}` → `{target}`\n\n"
                        f"{result.get('message', 'Session ready.')}"
                    ),
                }
            return {
                "type": "text",
                "value": (
                    f"Could not teleport to `{target}`.\n"
                    f"Reason: {result.get('error', 'unknown error')}\n\n"
                    "Verify the session ID is correct and the session is still active."
                ),
                "display": "system",
            }
        except Exception as exc:
            return {"type": "text", "value": f"Teleport failed: {exc}", "display": "system"}

    # Build status display and list available sessions
    lines: list[str] = ["## Teleport", "", f"**Current session:** `{current_id}`"]

    # Remote / local mode
    app_state = get_app_state() if get_app_state else {}
    remote_url = app_state.get("remoteSessionUrl") if app_state else None
    if remote_url:
        lines.append(f"**Mode:** Remote  —  {remote_url}")
    else:
        lines.append("**Mode:** Local")

    # Available teleport targets
    targets: list[dict[str, Any]] = []
    if get_teleport_targets:
        try:
            targets = await get_teleport_targets()
        except Exception:
            pass

    lines.append("")
    lines.append("### Available sessions")
    if not targets:
        lines.append("_(No other sessions available to teleport to.)_")
    else:
        for t in targets:
            sid = t.get("session_id", t.get("id", "unknown"))
            model = t.get("model", "?")
            age = t.get("age", t.get("last_active", "?"))
            label = t.get("name") or t.get("cwd") or sid
            lines.append(f"- `{sid}` — {label}  ")
            lines.append(f"  Model: {model} | Last active: {age}")

    lines.extend([
        "",
        "### Usage",
        "- `/teleport <session-id>` — jump to that session",
        "- `/tp` — alias for `/teleport`",
        "",
        "Teleport suspends your current session and resumes the target "
        "session with its full conversation history.",
    ])

    return {"type": "text", "value": "\n".join(lines)}
