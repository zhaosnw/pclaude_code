"""Port of: src/commands/perf-issue/. Create a performance issue template with profiling guidance."""

from __future__ import annotations

import asyncio
import platform
import time
from typing import Any

from hare.constants.product import VERSION

COMMAND_NAME = "perf-issue"
DESCRIPTION = "Create a performance issue template with profiling guidance"
ALIASES: list[str] = ["perf", "slow"]


_TEMPLATE = """## Performance Issue Report

### What is slow?
<!-- Describe the operation that feels slow, e.g. "prompt response time", "tool execution", "startup" -->

{description}

### When did it start?
<!-- Was it always slow, or after a specific update / configuration change? -->

{since}

### Steps to reproduce
1.
2.
3.

### Observed timing
- **Observed duration:** {observed}
- **Expected duration:** {expected}
- **Reproducibility:** [ ] Always  [ ] Sometimes  [ ] Once

### Environment
- **Hare version:** {version}
- **Python version:** {python_version}
- **Platform:** {platform_info}
- **Model:** {model}
- **Session mode:** {mode}

### Profiling data
<!-- Attach any of the following if available:
  - `` hare --profile `` output
  - py-spy or pyinstrument traces
  - Chrome DevTools Performance trace (for UI)
  - `` ps aux `` or `` htop `` screenshot showing CPU/memory
-->

```
(Paste profiling output here)
```

### Possible causes
<!-- Any hunches about what might be causing the slowness? -->

### Additional context
<!-- Screenshots, logs, related issues, or anything else helpful -->
"""


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Generate a performance issue template.

    Provides a fill-in-the-blanks markdown template with environment
    details pre-populated from the current session.  Accepts optional
    ``--text`` / ``--clipboard`` flags to output plain text or attempt
    a system clipboard copy.
    """
    arg = args[0].strip().lower() if args else ""

    if arg in ("--help", "-h", "help"):
        return {
            "type": "text",
            "value": (
                f"Hare {VERSION}  —  /{COMMAND_NAME}\n\n"
                "Generate a performance issue template with profiling guidance.\n\n"
                "Usage: /perf-issue [--text | --clipboard]\n\n"
                "  (no flag)   Render the template as markdown.\n"
                "  --text      Output plain text (no markdown formatting).\n"
                "  --clipboard Copy the template to the system clipboard.\n\n"
                "The template includes sections for reproduction steps, timing,\n"
                "environment info, profiling data, and diagnostic context."
            ),
        }

    # Gather session context for pre-population
    get_session_id = context.get("get_session_id")
    get_app_state = context.get("get_app_state")
    options = context.get("options", {})

    session_id = get_session_id() if get_session_id else "unknown"
    model = options.get("mainLoopModel", options.get("model", "unknown"))

    mode = "Local"
    if get_app_state:
        app_state = get_app_state()
        if app_state.get("remoteSessionUrl"):
            mode = f"Remote ({app_state['remoteSessionUrl']})"

    platform_info = f"{platform.system()} {platform.release()} ({platform.machine()})"

    # Pre-fill known fields
    body = _TEMPLATE.format(
        description="(describe what is slow)",
        since="(e.g. since upgrade to vX.Y.Z, or always)",
        observed="(e.g. 12 seconds)",
        expected="(e.g. under 2 seconds)",
        version=VERSION,
        python_version=platform.python_version(),
        platform_info=platform_info,
        model=model,
        mode=mode,
    )

    # Output mode
    if arg == "--clipboard":
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbcopy",
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(body.encode("utf-8"))
            if proc.returncode == 0:
                return {
                    "type": "text",
                    "value": "Performance issue template copied to clipboard.",
                }
            return {
                "type": "text",
                "value": (
                    "Failed to copy to clipboard. Template:\n\n" + body
                ),
            }
        except FileNotFoundError:
            return {
                "type": "text",
                "value": (
                    "Clipboard tool (`pbcopy`) not available on this platform.\n\n"
                    + body
                ),
            }

    if arg == "--text":
        return {"type": "text", "value": body}

    return {"type": "text", "value": body}
