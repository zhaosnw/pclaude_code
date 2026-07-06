"""Substitute ``$ARGUMENTS`` placeholders in skill prompts (`argumentSubstitution.ts`)."""

from __future__ import annotations

import re


def parse_arguments(args: str) -> list[str]:
    if not args or not args.strip():
        return []
    try:
        from hare.utils.bash.shell_quote import (
            ShellParseSuccess,
            try_parse_shell_command,
        )

        result = try_parse_shell_command(args)
        if isinstance(result, ShellParseSuccess):
            return [t for t in result.tokens if isinstance(t, str)]
    except Exception:
        pass
    return [x for x in args.split() if x]


def parse_argument_names(argument_names: str | list[str] | None) -> list[str]:
    if not argument_names:
        return []

    def valid(name: str) -> bool:
        return bool(name.strip()) and not name.isdigit()

    if isinstance(argument_names, list):
        return [n for n in argument_names if valid(str(n))]
    return [n for n in argument_names.split() if valid(n)]


def generate_progressive_argument_hint(
    arg_names: list[str], typed_args: list[str]
) -> str | None:
    remaining = arg_names[len(typed_args) :]
    if not remaining:
        return None
    return " ".join(f"[{n}]" for n in remaining)


def substitute_arguments(
    content: str,
    args: str | None,
    append_if_no_placeholder: bool = True,
    argument_names: list[str] | None = None,
) -> str:
    if args is None:
        return content
    argument_names = argument_names or []
    parsed = parse_arguments(args)
    original = content
    for i, name in enumerate(argument_names):
        if not name:
            continue
        content = re.sub(
            rf"\${re.escape(name)}(?![\[\w])",
            parsed[i] if i < len(parsed) else "",
            content,
        )

    def repl_idx(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return parsed[idx] if idx < len(parsed) else ""

    content = re.sub(r"\$ARGUMENTS\[(\d+)\]", repl_idx, content)
    content = re.sub(r"\$(\d+)(?!\w)", repl_idx, content)
    content = content.replace("$ARGUMENTS", args)
    if content == original and append_if_no_placeholder and args:
        content = content + f"\n\nARGUMENTS: {args}"
    return content
