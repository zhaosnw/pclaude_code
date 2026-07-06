"""
Module entrypoint for the Hare Python stdio backend.
"""

from __future__ import annotations

import asyncio

from hare.backend_stdio import run_stdio_backend


def main() -> None:
    raise SystemExit(asyncio.run(run_stdio_backend()))


if __name__ == "__main__":
    main()
