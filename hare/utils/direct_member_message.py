"""Direct `@name message` routing (`directMemberMessage.ts`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Protocol


def parse_direct_member_message(input_str: str) -> tuple[str, str] | None:
    m = re.match(r"^@([\w-]+)\s+(.+)$", input_str, re.DOTALL)
    if not m:
        return None
    recipient_name, message = m.group(1), m.group(2).strip()
    if not message:
        return None
    return recipient_name, message


class _TeamContext(Protocol):
    team_name: str
    teammates: dict[str, Any]


WriteFn = Callable[
    [str, dict[str, str], str],
    Awaitable[None],
]


@dataclass
class DirectMessageOk:
    success: Literal[True]
    recipient_name: str


@dataclass
class DirectMessageErr:
    success: Literal[False]
    error: Literal["no_team_context", "unknown_recipient"]
    recipient_name: str | None = None


DirectMessageResult = DirectMessageOk | DirectMessageErr


async def send_direct_member_message(
    recipient_name: str,
    message: str,
    team_context: _TeamContext | None,
    write_to_mailbox: WriteFn | None = None,
) -> DirectMessageResult:
    if not team_context or write_to_mailbox is None:
        return DirectMessageErr(False, "no_team_context")

    teammates = getattr(team_context, "teammates", None) or {}
    member = None
    for t in teammates.values():
        if getattr(t, "name", None) == recipient_name:
            member = t
            break
    if member is None:
        return DirectMessageErr(False, "unknown_recipient", recipient_name)

    await write_to_mailbox(
        recipient_name,
        {
            "from": "user",
            "text": message,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        },
        team_context.team_name,
    )
    return DirectMessageOk(True, recipient_name)
