"""Port of: src/utils/systemPromptType.ts"""

from __future__ import annotations
from typing import NewType

SystemPrompt = NewType("SystemPrompt", str)


def as_system_prompt(text: str) -> SystemPrompt:
    return SystemPrompt(text)
