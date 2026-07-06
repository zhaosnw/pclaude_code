"""Port of: src/utils/bash/shellQuote.ts + shellQuoting.ts"""

from __future__ import annotations
import re
import shlex


def shell_quote(s: str) -> str:
    if not s:
        return "''"
    return shlex.quote(s)


def shell_quote_list(args: list[str]) -> str:
    return " ".join(shell_quote(a) for a in args)


def shell_join(args: list[str]) -> str:
    return shell_quote_list(args)


def needs_quoting(s: str) -> bool:
    return bool(re.search(r"[^a-zA-Z0-9_./:-]", s))
