"""Port of: src/cli/exit.ts

Exit handling — processes exit codes and cleanup.
"""

from __future__ import annotations

import atexit
import signal
import sys
from typing import Any

# Exit code constants
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 2
EXIT_AUTH_ERROR = 4
EXIT_USAGE_ERROR = 64


def handle_exit(code: int = EXIT_OK) -> None:
    """Exit the process with the given code."""
    sys.exit(code)


def handle_interrupt(signum: int = signal.SIGINT, frame: Any = None) -> None:
    """Handle SIGINT (Ctrl+C) gracefully."""
    print("\nInterrupted.", file=sys.stderr)
    sys.exit(EXIT_INTERRUPTED)


def setup_exit_handlers() -> None:
    """Register signal handlers and atexit cleanup."""
    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    atexit.register(_cleanup)


def _cleanup() -> None:
    """Cleanup function called on normal exit."""
    try:
        from hare.utils.cleanup_registry import run_cleanup

        run_cleanup()
    except ImportError:
        pass


def exit_with_usage_error(message: str) -> None:
    """Print usage error and exit."""
    print(f"Error: {message}", file=sys.stderr)
    print("Usage: hare [options] [prompt]", file=sys.stderr)
    sys.exit(EXIT_USAGE_ERROR)


def exit_with_auth_error(message: str) -> None:
    """Print auth error and exit."""
    print(f"Authentication error: {message}", file=sys.stderr)
    print(
        "Set ANTHROPIC_API_KEY environment variable or run: hare login", file=sys.stderr
    )
    sys.exit(EXIT_AUTH_ERROR)
