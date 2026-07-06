"""File-based teammate inboxes (port of teammateMailbox.ts)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.slow_operations import json_parse, json_stringify
from hare.utils.tasks import sanitize_path_component
from hare.utils.teammate import get_team_name

TEAMMATE_MESSAGE_TAG = "teammate-message"


def get_teams_dir() -> str:
    return str(Path(get_hare_config_home_dir()) / "teams")


def get_inbox_path(agent_name: str, team_name: str | None = None) -> str:
    team = team_name or get_team_name() or "default"
    safe_team = sanitize_path_component(team)
    safe_agent = sanitize_path_component(agent_name)
    inbox_dir = Path(get_teams_dir()) / safe_team / "inboxes"
    return str(inbox_dir / f"{safe_agent}.json")


async def read_mailbox(
    agent_name: str, team_name: str | None = None
) -> list[dict[str, Any]]:
    p = Path(get_inbox_path(agent_name, team_name))
    if not p.exists():
        return []
    try:
        data = json_parse(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except OSError as e:
        log_for_debugging(f"read_mailbox: {e}")
        return []


async def write_to_mailbox(
    recipient: str,
    message: dict[str, Any],
    team_name: str | None = None,
) -> None:
    Path(get_inbox_path(recipient, team_name)).parent.mkdir(parents=True, exist_ok=True)
    msgs = await read_mailbox(recipient, team_name)
    entry = {**message, "read": False}
    msgs.append(entry)
    Path(get_inbox_path(recipient, team_name)).write_text(
        json_stringify(msgs, indent=2), encoding="utf-8"
    )


def format_teammate_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        sender = m.get("from") or m.get("from_") or ""
        color_attr = f' color="{m["color"]}"' if m.get("color") else ""
        summary_attr = f' summary="{m["summary"]}"' if m.get("summary") else ""
        parts.append(
            f'<{TEAMMATE_MESSAGE_TAG} teammate_id="{sender}"{color_attr}{summary_attr}>\n'
            f"{m.get('text', '')}\n</{TEAMMATE_MESSAGE_TAG}>"
        )
    return "\n\n".join(parts)
