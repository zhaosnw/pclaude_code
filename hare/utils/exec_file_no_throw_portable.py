"""
Synchronous shell exec with defaults (deprecated).

Port of: src/utils/execFileNoThrowPortable.ts
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from hare.utils.cwd import get_cwd

MS_IN_SECOND = 1000
SECONDS_IN_MINUTE = 60
_DEFAULT_TIMEOUT = 10 * SECONDS_IN_MINUTE * MS_IN_SECOND


def exec_sync_with_defaults_deprecated(
    command: str,
    options_or_abort: Any | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str | None:
    """Deprecated: prefer async `exec_file_no_throw`."""
    if options_or_abort is None:
        opts: dict[str, Any] = {}
    elif hasattr(options_or_abort, "throw_if_aborted"):
        opts = {"abort_signal": options_or_abort, "timeout": timeout}
    else:
        opts = dict(options_or_abort)
        timeout = int(opts.get("timeout", timeout))

    abort_signal = opts.get("abort_signal")
    if abort_signal is not None and hasattr(abort_signal, "throw_if_aborted"):
        abort_signal.throw_if_aborted()

    final_timeout_ms = int(opts.get("timeout", timeout))
    input_s = opts.get("input")
    try:
        r = subprocess.run(
            command,
            shell=True,  # nosec B602
            cwd=get_cwd(),
            env=os.environ,
            timeout=final_timeout_ms / 1000.0,
            capture_output=True,
            text=True,
            input=input_s,
        )
        out = (r.stdout or "").strip()
        return out or None
    except (subprocess.SubprocessError, OSError):
        return None
