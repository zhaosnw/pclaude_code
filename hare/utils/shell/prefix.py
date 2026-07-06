"""
Shared command prefix extraction (LLM-assisted in TS).

Port of: src/utils/shell/prefix.ts (public API + constants).
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


DANGEROUS_SHELL_PREFIXES: frozenset[str] = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "fish",
        "csh",
        "tcsh",
        "ksh",
        "dash",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "bash.exe",
    }
)


@dataclass
class CommandPrefixResult:
    command_prefix: str | None


@dataclass
class CommandSubcommandPrefixResult:
    command_prefix: str | None
    subcommand_prefixes: dict[str, CommandPrefixResult] = field(default_factory=dict)


@dataclass
class PrefixExtractorConfig:
    tool_name: str
    policy_spec: str
    event_name: str
    query_source: str
    pre_check: Callable[[str], CommandPrefixResult | None] | None = None


class PrefixExtractorFn(Protocol):
    async def __call__(
        self,
        command: str,
        abort_signal: Any,
        is_non_interactive_session: bool,
    ) -> CommandPrefixResult | None: ...


async def _default_prefix_impl(
    command: str,
    _abort: Any,
    _non_interactive: bool,
    _cfg: PrefixExtractorConfig,
) -> CommandPrefixResult | None:
    if os.environ.get("PYTHON_ENV") == "test":
        return None
    first = command.strip().split()[0] if command.strip() else ""
    if first.lower() in DANGEROUS_SHELL_PREFIXES:
        return CommandPrefixResult(command_prefix=None)
    return CommandPrefixResult(command_prefix=first or None)


def create_command_prefix_extractor(config: PrefixExtractorConfig) -> PrefixExtractorFn:
    async def memoized(
        command: str,
        abort_signal: Any,
        is_non_interactive_session: bool,
    ) -> CommandPrefixResult | None:
        if config.pre_check:
            pre = config.pre_check(command)
            if pre is not None:
                return pre
        return await _default_prefix_impl(
            command, abort_signal, is_non_interactive_session, config
        )

    return memoized


def create_subcommand_prefix_extractor(
    get_prefix: PrefixExtractorFn,
    split_command: Callable[[str], list[str] | Awaitable[list[str]]],
) -> Any:
    async def memoized(
        command: str,
        abort_signal: Any,
        is_non_interactive_session: bool,
    ) -> CommandSubcommandPrefixResult | None:
        main = await get_prefix(command, abort_signal, is_non_interactive_session)
        raw = split_command(command)
        parts = await raw if inspect.isawaitable(raw) else raw
        subs: dict[str, CommandPrefixResult] = {}
        for part in parts:
            r = await get_prefix(part, abort_signal, is_non_interactive_session)
            if r:
                subs[part] = r
        return CommandSubcommandPrefixResult(
            command_prefix=main.command_prefix if main else None,
            subcommand_prefixes=subs,
        )

    return memoized
