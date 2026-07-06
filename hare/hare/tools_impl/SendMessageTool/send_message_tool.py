"""
Send Message Tool - send a message to another agent.

Port of: src/tools/SendMessageTool/SendMessageTool.ts

Routes messages between agents via file-based inboxes (teammate mailbox).
Supports sending to named teammates, broadcast to all teammates, and legacy
protocol messages (shutdown_request/response, plan_approval_request/response).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from hare.tool import ToolBase, ToolResult, ToolUseContext
from hare.app_types.permissions import PermissionAllowDecision, PermissionResult
SEND_MESSAGE_TOOL_NAME = "SendMessage"
TEAMMATE_MESSAGE_TAG = "teammate-message"
MAX_MESSAGE_LENGTH = 100_000


class _SendMessageTool(ToolBase):
    name = SEND_MESSAGE_TOOL_NAME
    aliases = ["send_message", "sendMsg", "notify"]
    search_hint = "send a message to another agent or resume a paused agent"
    max_result_size_chars = 50_000

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Agent name to send to, or '*' to broadcast to all teammates."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "The message text to send. Can also be a JSON-encoded object "
                        "for protocol messages (shutdown_response, plan_approval_response)."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "Optional short (3-6 word) summary for the recipient's inbox listing.",
                },
                "color": {
                    "type": "string",
                    "description": "Optional color hint for the message display.",
                },
            },
            "required": ["to", "message"],
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False  # Writes to filesystem inboxes

    def is_concurrency_safe(self, input: dict[str, Any]) -> bool:
        return True  # Each recipient has its own inbox file

    async def check_permissions(
        self, input: dict[str, Any], context: ToolUseContext
    ) -> PermissionResult:
        return PermissionAllowDecision(behavior="allow", updated_input=input)

    async def prompt(self, options: dict[str, Any]) -> str:
        return (
            "Send a message to another agent or resume a paused agent. "
            "Use this to communicate with teammates, assign tasks, or "
            "respond to protocol messages (shutdown, plan approval). "
            "Messages arrive in the recipient's inbox and are delivered "
            "at their next tool-processing round."
        )

    async def description(self, input: dict[str, Any], options: dict[str, Any]) -> str:
        recipient = input.get("to", "")
        summary = input.get("summary", "")
        if summary:
            return f"Send message to {recipient}: {summary}"
        return f"Send message to {recipient}"

    def user_facing_name(self, input: Optional[dict[str, Any]] = None) -> str:
        return SEND_MESSAGE_TOOL_NAME

    def to_auto_classifier_input(self, input: dict[str, Any]) -> Any:
        return f"to={input.get('to', '')}: {input.get('message', '')}"

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        recipient: str = args.get("to", "").strip()
        message_raw: str = args.get("message", "")
        summary: str = args.get("summary", "").strip()
        color: Optional[str] = args.get("color")

        # --- validation --------------------------------------------------
        if not recipient:
            return ToolResult(data="Error: 'to' field is required.")
        if not message_raw:
            return ToolResult(data="Error: 'message' field is required.")
        if len(message_raw) > MAX_MESSAGE_LENGTH:
            return ToolResult(
                data=f"Error: message too long ({len(message_raw)} chars). "
                f"Maximum is {MAX_MESSAGE_LENGTH}."
            )

        # Resolve the sender identity from environment or context.
        sender_name = self._resolve_sender_name(context)

        # Normalise the message payload: if it looks like JSON, keep the
        # original text but also flag it so the recipient can parse it.
        message_text = message_raw

        # Detect legacy protocol messages.
        message_meta: dict[str, Any] = {}
        try:
            parsed = json.loads(message_raw)
            if isinstance(parsed, dict):
                msg_type = parsed.get("type", "")
                if msg_type in (
                    "shutdown_request",
                    "shutdown_response",
                    "plan_approval_request",
                    "plan_approval_response",
                ):
                    message_meta["protocol_type"] = msg_type
        except (json.JSONDecodeError, TypeError):
            pass

        # --- broadcast ---------------------------------------------------
        if recipient == "*":
            return await self._broadcast(
                sender_name, message_text, summary, color, message_meta
            )

        # --- direct send -------------------------------------------------
        return await self._send_to_one(
            recipient, sender_name, message_text, summary, color, message_meta
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_sender_name(self, context: ToolUseContext) -> str:
        """Resolve the sending agent's name."""
        if context.agent_id:
            name = context.agent_id.split("@")[0] if "@" in context.agent_id else context.agent_id
            if name:
                return name
        # Fallback: use the environment variable.
        env_agent_id = os.environ.get("CLAUDE_CODE_AGENT_ID", "")
        if "@" in env_agent_id:
            return env_agent_id.split("@")[0]
        if env_agent_id:
            return env_agent_id
        return "agent"

    async def _send_to_one(
        self,
        recipient: str,
        sender: str,
        text: str,
        summary: str,
        color: Optional[str],
        meta: dict[str, Any],
    ) -> ToolResult:
        """Write a message into a single recipient's inbox."""
        try:
            from hare.utils.teammate_mailbox import write_to_mailbox

            envelope: dict[str, Any] = {
                "from_": sender,
                "from": sender,
                "text": text,
                "summary": summary or self._truncate_summary(text),
                "timestamp": "",  # mailbox reader stamps on read
            }
            if color:
                envelope["color"] = color
            if meta:
                envelope["meta"] = meta

            await write_to_mailbox(recipient, envelope)

            return ToolResult(
                data=(
                    f"Message sent to '{recipient}'. "
                    f"Delivered {len(text)} chars from '{sender}'."
                )
            )

        except OSError as exc:
            return ToolResult(data=f"Error sending message to '{recipient}': {exc}")
        except ImportError:
            return ToolResult(
                data="Error: teammate mailbox module not available."
            )

    async def _broadcast(
        self,
        sender: str,
        text: str,
        summary: str,
        color: Optional[str],
        meta: dict[str, Any],
    ) -> ToolResult:
        """Broadcast a message to every known teammate except ourselves."""
        try:
            from hare.utils.teammate_mailbox import write_to_mailbox
            from hare.utils.teammate import get_agent_name, get_team_name
            from pathlib import Path

            team_name = get_team_name() or "default"
            self_name = get_agent_name() or sender

            # Discover teammates by scanning inbox files.
            teams_dir = Path(
                _get_hare_config_home_dir() / "teams" / _sanitize(team_name) / "inboxes"
            )
            teammates: set[str] = set()
            if teams_dir.exists():
                for fpath in teams_dir.glob("*.json"):
                    name = fpath.stem
                    if name and name != self_name:
                        teammates.add(name)

            if not teammates:
                return ToolResult(
                    data="Broadcast: no other teammates found in the current team."
                )

            envelope: dict[str, Any] = {
                "from_": sender,
                "from": sender,
                "text": text,
                "summary": summary or f"[broadcast] {self._truncate_summary(text)}",
                "timestamp": "",
            }
            if color:
                envelope["color"] = color
            if meta:
                envelope["meta"] = meta

            sent: list[str] = []
            failed: list[str] = []
            for name in sorted(teammates):
                try:
                    await write_to_mailbox(name, envelope)
                    sent.append(name)
                except OSError:
                    failed.append(name)

            parts = [f"Broadcast sent to {len(sent)} teammate(s): {', '.join(sent)}."]
            if failed:
                parts.append(f"Failed for {len(failed)}: {', '.join(failed)}.")
            return ToolResult(data=" ".join(parts))

        except OSError as exc:
            return ToolResult(data=f"Broadcast error: {exc}")
        except ImportError:
            return ToolResult(data="Error: teammate mailbox module not available.")

    @staticmethod
    def _truncate_summary(text: str, max_len: int = 80) -> str:
        """Create a short summary from the message text."""
        first_line = text.split("\n")[0].strip()
        if len(first_line) <= max_len:
            return first_line
        return first_line[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Module-level helpers imported by broadcast path
# ---------------------------------------------------------------------------

def _get_hare_config_home_dir() -> Path:
    """Lightweight inline fallback — mirrors hare.utils.env_utils."""
    from pathlib import Path

    try:
        from hare.utils.env_utils import get_hare_config_home_dir as _fn

        return Path(_fn())
    except ImportError:
        pass
    # Fallback
    return Path.home() / ".claude"


def _sanitize(name: str) -> str:
    """Filesystem-safe path component."""
    import re

    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

SendMessageTool = _SendMessageTool()
