"""Port of: src/utils/uuid.ts"""

from __future__ import annotations
import uuid


def create_agent_id() -> str:
    return str(uuid.uuid4())[:8]


def create_session_id() -> str:
    return str(uuid.uuid4())


def create_message_id() -> str:
    return str(uuid.uuid4())
