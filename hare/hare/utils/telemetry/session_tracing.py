"""Session-level tracing helpers.

Port of: src/utils/telemetry/sessionTracing.ts
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


@asynccontextmanager
async def session_trace(_name: str, **_attrs: Any) -> AsyncIterator[None]:
    yield


def set_session_trace_metadata(_key: str, _value: Any) -> None:
    pass
