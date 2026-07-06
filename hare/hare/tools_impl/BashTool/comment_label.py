"""Port of: src/tools/BashTool/commentLabel.ts"""

from __future__ import annotations


def extract_bash_comment_label(command: str) -> str | None:
    nl = command.find("\n")
    first_line = (command if nl == -1 else command[:nl]).strip()
    if not first_line.startswith("#") or first_line.startswith("#!"):
        return None
    stripped = first_line.lstrip("#").lstrip()
    return stripped or None
