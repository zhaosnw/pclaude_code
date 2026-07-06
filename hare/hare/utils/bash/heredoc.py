"""Port of: src/utils/bash/heredoc.ts"""

from __future__ import annotations
import re


def extract_heredoc(command: str) -> tuple[str, str]:
    m = re.search(r'<<[\-]?[\'"]?(\w+)[\'"]?', command)
    if not m:
        return ("", "")
    delimiter = m.group(1)
    pattern = re.compile(rf"^{re.escape(delimiter)}$", re.MULTILINE)
    rest = command[m.end() :]
    end = pattern.search(rest)
    if not end:
        return (delimiter, rest)
    return (delimiter, rest[: end.start()])


def has_heredoc(command: str) -> bool:
    return bool(re.search(r'<<[\-]?[\'"]?\w+[\'"]?', command))
