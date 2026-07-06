"""CLI exit helpers (port of src/cli/exit.ts)."""

from __future__ import annotations

import sys
from typing import Optional


def cli_error(msg: Optional[str] = None) -> None:
    if msg:
        print(msg, file=sys.stderr)
    sys.exit(1)


def cli_ok(msg: Optional[str] = None) -> None:
    if msg:
        sys.stdout.write(msg + "\n")
    sys.exit(0)
