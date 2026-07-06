"""Normalize `requestId` → `request_id` on control messages (`controlMessageCompat.ts`)."""

from __future__ import annotations

from typing import Any


def normalize_control_message_keys(obj: Any) -> Any:
    if obj is None or not isinstance(obj, dict):
        return obj
    record: dict[str, Any] = obj
    if "requestId" in record and "request_id" not in record:
        record["request_id"] = record["requestId"]
        del record["requestId"]
    response = record.get("response")
    if response is not None and isinstance(response, dict):
        r = response
        if "requestId" in r and "request_id" not in r:
            r["request_id"] = r["requestId"]
            del r["requestId"]
    return obj
