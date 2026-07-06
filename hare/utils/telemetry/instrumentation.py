"""Port of: src/utils/telemetry/instrumentation.ts"""

from __future__ import annotations
import functools
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T", bound=Callable[..., Any])


def instrument_function(name: str = "") -> Callable[[T], T]:
    def decorator(fn: T) -> T:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            try:
                return await fn(*args, **kwargs)
            finally:
                elapsed = time.monotonic() - start
                from hare.utils.telemetry.events import track_event

                track_event(
                    f"fn_{name or fn.__name__}", {"duration_ms": elapsed * 1000}
                )

        return wrapper  # type: ignore

    return decorator
