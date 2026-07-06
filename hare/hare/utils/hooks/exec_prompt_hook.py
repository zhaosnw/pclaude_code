"""Prompt-type hook execution.

Port of: src/utils/hooks/execPromptHook.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptHookOutcome:
    rendered: str


async def exec_prompt_hook(
    prompt: str, *, system: str | None = None
) -> PromptHookOutcome:
    del system
    return PromptHookOutcome(rendered=prompt)
