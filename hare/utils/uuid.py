"""UUID validation and agent id helpers. Port of: src/utils/uuid.ts"""

from __future__ import annotations

import re
import secrets

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_uuid(maybe_uuid: object) -> str | None:
    """Return the string if it is a valid UUID, else None."""
    if not isinstance(maybe_uuid, str):
        return None
    return maybe_uuid if _UUID_RE.match(maybe_uuid) else None


def create_agent_id(label: str | None = None) -> str:
    """Format: a{label-}{16 hex chars} (matches TS createAgentId)."""
    suffix = secrets.token_hex(8)
    if label:
        return f"a{label}-{suffix}"
    return f"a{suffix}"
