"""
Post-sampling hook registration (REPL / SDK).

Port of: src/utils/hooks/postSamplingHooks.ts
"""

from __future__ import annotations

from typing import Any, Protocol

ReplHookContext = dict[str, Any]

# Alias preserving TS PascalCase + acronym casing (`REPLHookContext`).
REPLHookContext = ReplHookContext


class ReplHook(Protocol):
    async def __call__(self, ctx: ReplHookContext) -> Any: ...


_hooks: list[ReplHook] = []


def register_post_sampling_hook(hook: ReplHook) -> None:
    _hooks.append(hook)


async def run_post_sampling_hooks(ctx: ReplHookContext) -> list[Any]:
    results: list[Any] = []
    for h in _hooks:
        results.append(await h(ctx))
    return results


def clear_post_sampling_hooks() -> None:
    _hooks.clear()
