"""Port of: src/utils/mcp/dateTimeParser.ts"""

from __future__ import annotations
from datetime import datetime


def parse_date_time_string(s: str) -> datetime | None:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
