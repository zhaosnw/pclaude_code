"""Compatibility entrypoint for ``python -m hare`` inside ``hare/``."""

from __future__ import annotations

import asyncio
import sys


def main() -> None:
    from hare.main import cli_main

    try:
        asyncio.run(cli_main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
