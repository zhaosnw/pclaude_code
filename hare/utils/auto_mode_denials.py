"""Recent auto-mode classifier denials (`autoModeDenials.ts`)."""

from __future__ import annotations

import os
from dataclasses import dataclass

_MAX = 20
_denials: list["AutoModeDenial"] = []


@dataclass
class AutoModeDenial:
    tool_name: str
    display: str
    reason: str
    timestamp: float


def _feature_transcript_classifier() -> bool:
    return os.environ.get("TRANSCRIPT_CLASSIFIER", "") == "1"


def record_auto_mode_denial(denial: AutoModeDenial) -> None:
    if not _feature_transcript_classifier():
        return
    global _denials
    _denials = [denial, *_denials[: _MAX - 1]]


def get_auto_mode_denials() -> tuple[AutoModeDenial, ...]:
    return tuple(_denials)
