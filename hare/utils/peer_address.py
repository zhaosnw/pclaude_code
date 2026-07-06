"""
Parse peer / routing addresses (UDS vs bridge). Port of src/utils/peerAddress.ts.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class ParsedAddress(TypedDict):
    scheme: Literal["uds", "bridge", "other"]
    target: str


def parse_address(to: str) -> ParsedAddress:
    if to.startswith("uds:"):
        return {"scheme": "uds", "target": to[4:]}
    if to.startswith("bridge:"):
        return {"scheme": "bridge", "target": to[7:]}
    if to.startswith("/"):
        return {"scheme": "uds", "target": to}
    return {"scheme": "other", "target": to}
