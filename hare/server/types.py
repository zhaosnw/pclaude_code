"""Direct-connect server types (port of src/server/types.ts)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SessionState = Literal["starting", "running", "detached", "stopping", "stopped"]


@dataclass
class ServerConfig:
    port: int
    host: str
    auth_token: str
    unix: str | None = None
    idle_timeout_ms: int = 0
    max_sessions: int | None = None
    workspace: str | None = None


@dataclass
class SessionInfo:
    id: str
    status: SessionState
    created_at: float
    work_dir: str
    process: object | None = None
    session_key: str | None = None


@dataclass
class SessionIndexEntry:
    session_id: str
    transcript_session_id: str
    cwd: str
    permission_mode: str | None = None
    created_at: float = 0.0
    last_active_at: float = 0.0


SessionIndex = dict[str, SessionIndexEntry]
