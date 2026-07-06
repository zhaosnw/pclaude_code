"""Port of: src/utils/telemetry/events.ts"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class TrackingEvent:
    name: str
    properties: dict[str, Any] | None = None
    timestamp: float = 0.0


_events: list[TrackingEvent] = []


def track_event(name: str, properties: dict[str, Any] | None = None) -> None:
    import time

    _events.append(
        TrackingEvent(name=name, properties=properties, timestamp=time.time())
    )


def get_tracked_events() -> list[TrackingEvent]:
    return list(_events)


def clear_events() -> None:
    _events.clear()
