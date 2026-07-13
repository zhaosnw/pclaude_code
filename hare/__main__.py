"""Allow running Hare through the same entrypoint as the ``hare`` command."""

from __future__ import annotations

def main() -> None:
    from hare.entrypoints.cli import main as cli_entrypoint_main

    cli_entrypoint_main()


if __name__ == "__main__":
    main()
