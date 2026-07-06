"""Beta / experimental session tracing.

Port of: src/utils/telemetry/betaSessionTracing.ts
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


@asynccontextmanager
async def beta_session_trace(_label: str, **_kw: Any) -> AsyncIterator[None]:
    yield
