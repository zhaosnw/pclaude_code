"""
Execute shell snippets embedded in slash-command / skill text. Port of src/utils/promptShellExecution.ts.
"""

from __future__ import annotations

import re
from typing import Any

from hare.utils.errors import MalformedCommandError, ShellError, error_message

BLOCK_PATTERN = re.compile(r"```!\s*\n?([\s\S]*?)\n?```")
INLINE_PATTERN = re.compile(r"(?:^|\s)!`([^`]+)`", re.MULTILINE)


async def execute_shell_commands_in_prompt(
    text: str,
    context: Any,
    slash_command_name: str,
    shell: str | None = None,
) -> str:
    """Replace ```! ... ``` and !`cmd` with shell tool output."""
    result = text
    from hare.tools_impl.BashTool.bash_tool import BashTool as bash_singleton

    shell_tool: Any = bash_singleton
    if shell == "powershell":
        try:
            from hare.utils.shell.shell_tool_utils import is_powershell_tool_enabled

            if is_powershell_tool_enabled():
                from hare.tools_impl.PowerShellTool.powershell_tool import (
                    PowerShellTool,
                )

                shell_tool = PowerShellTool
        except ImportError:
            pass

    matches: list[tuple[str, str]] = []
    for m in BLOCK_PATTERN.finditer(text):
        if m.group(1):
            matches.append((m.group(0), m.group(1).strip()))
    if "!`" in text:
        for m in INLINE_PATTERN.finditer(text):
            if m.group(1):
                matches.append((m.group(0), m.group(1).strip()))

    for pattern, command in matches:
        if not command:
            continue
        try:
            out = await shell_tool.call({"command": command}, context)
            data = getattr(out, "data", out)
            output = _format_bash_output(data)
            result = result.replace(pattern, output)
        except MalformedCommandError:
            raise
        except Exception as e:
            _raise_bash_error(e, pattern)

    return result


def _format_bash_output(data: Any) -> str:
    if isinstance(data, dict):
        stdout = str(data.get("stdout", ""))
        stderr = str(data.get("stderr", ""))
    else:
        stdout = str(getattr(data, "stdout", "") or data)
        stderr = str(getattr(data, "stderr", ""))
    parts: list[str] = []
    if stdout.strip():
        parts.append(stdout.strip())
    if stderr.strip():
        parts.append(f"[stderr]\n{stderr.strip()}")
    return "\n".join(parts)


def _raise_bash_error(e: BaseException, pattern: str) -> None:
    if isinstance(e, ShellError):
        if getattr(e, "interrupted", False):
            raise MalformedCommandError(
                f'Shell command interrupted for pattern "{pattern}": [Command interrupted]'
            ) from e
        out = _format_bash_error_output(e)
        raise MalformedCommandError(
            f'Shell command failed for pattern "{pattern}": {out}'
        ) from e
    raise MalformedCommandError(f"[Error]\n{error_message(e)}") from e


def _format_bash_error_output(e: ShellError) -> str:
    return _format_bash_output(
        {"stdout": getattr(e, "stdout", ""), "stderr": getattr(e, "stderr", "")},
    )
