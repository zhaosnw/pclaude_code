"""
Error utilities.

Port of: src/utils/errors.ts
"""

from __future__ import annotations

import errno
from typing import Any


def get_errno_code(error: Any) -> str | None:
    """Return symbolic errno name (e.g. ENOENT) for OSError, else None."""
    if isinstance(error, OSError) and error.errno is not None:
        return errno.errorcode.get(error.errno)  # type: ignore[arg-type]
    return None


def error_message(error: Any) -> str:
    """Extract an error message from any error type."""
    if isinstance(error, Exception):
        return str(error)
    return str(error)


def is_enoent(error: Any) -> bool:
    """Check if an error is a FileNotFoundError."""
    return isinstance(error, FileNotFoundError)


def is_abort_error(error: Any) -> bool:
    """Check if an error is an abort/cancellation error."""
    if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)):
        return True
    msg = error_message(error).lower()
    return "aborted" in msg or "cancelled" in msg


# Import asyncio only when needed
import asyncio


class HareError(Exception):
    """Base error class for Hare."""

    pass


class MalformedCommandError(HareError):
    """Raised when a command is malformed."""

    pass


class ShellError(HareError):
    """Raised when a shell command fails."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def is_fs_inaccessible(error: Any) -> bool:
    """Check if error indicates filesystem is inaccessible or path is missing.

    TS: isFsInaccessible — catches ENOENT (dir doesn't exist) in addition to
    permission/IO errors so that directory traversals can continue gracefully
    when expected paths are absent.
    """
    if isinstance(error, OSError):
        return error.errno in (
            errno.EACCES,
            errno.EPERM,
            errno.EROFS,
            errno.ENOSPC,
            errno.EIO,
            errno.ENOENT,
            errno.ENOTDIR,
        )
    return False
