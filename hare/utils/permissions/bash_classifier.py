"""Classify bash commands for permission routing. Port of bashClassifier.ts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class BashClassification:
    risk: Literal["low", "medium", "high"]
    reason: str


def classify_bash_command(cmd: str) -> BashClassification:
    from hare.utils.permissions.dangerous_patterns import looks_dangerous_command

    if looks_dangerous_command(cmd):
        return BashClassification(risk="high", reason="dangerous pattern")
    return BashClassification(risk="low", reason="ok")
