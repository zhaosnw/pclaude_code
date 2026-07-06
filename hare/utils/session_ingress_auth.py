"""Session ingress authentication (port of sessionIngressAuth.ts)."""

from __future__ import annotations

import os

from hare.utils.auth_file_descriptor import (
    CCR_SESSION_INGRESS_TOKEN_PATH,
    maybe_persist_token_for_subprocesses,
    read_token_from_well_known_file,
)
from hare.utils.debug import log_for_debugging
from hare.utils.errors import error_message
from hare.utils.fs_operations import get_fs_implementation

_UNSET: object = object()
_cached_ingress_token: str | None | object = _UNSET


def get_token_from_file_descriptor() -> str | None:
    """Read token from FD or well-known file; result is cached (including None)."""
    global _cached_ingress_token
    if _cached_ingress_token is not _UNSET:
        return _cached_ingress_token  # type: ignore[return-value]

    fd_env = os.environ.get("CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR")
    if not fd_env:
        path = (
            os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE")
            or CCR_SESSION_INGRESS_TOKEN_PATH
        )
        _cached_ingress_token = read_token_from_well_known_file(
            path, "session ingress token"
        )
        return _cached_ingress_token  # type: ignore[return-value]

    try:
        fd = int(fd_env, 10)
    except ValueError:
        log_for_debugging(
            f"CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR must be a valid integer, got: {fd_env}",
            level="error",
        )
        _cached_ingress_token = None
        return None

    import sys

    if sys.platform in ("darwin", "freebsd"):
        fd_path = f"/dev/fd/{fd}"
    else:
        fd_path = f"/proc/self/fd/{fd}"

    try:
        fs = get_fs_implementation()
        token = fs.read_file_sync(fd_path, encoding="utf-8").strip()
        if not token:
            log_for_debugging("File descriptor contained empty token", level="error")
            _cached_ingress_token = None
            return None
        log_for_debugging(f"Successfully read token from file descriptor {fd}")
        _cached_ingress_token = token
        maybe_persist_token_for_subprocesses(
            CCR_SESSION_INGRESS_TOKEN_PATH, token, "session ingress token"
        )
        return token
    except OSError as error:
        log_for_debugging(
            f"Failed to read token from file descriptor {fd}: {error_message(error)}",
            level="error",
        )
        path = (
            os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE")
            or CCR_SESSION_INGRESS_TOKEN_PATH
        )
        _cached_ingress_token = read_token_from_well_known_file(
            path, "session ingress token"
        )
        return _cached_ingress_token


def get_session_ingress_auth_token() -> str | None:
    env_token = os.environ.get("CLAUDE_CODE_SESSION_ACCESS_TOKEN")
    if env_token:
        return env_token
    return get_token_from_file_descriptor()


def get_session_ingress_auth_headers() -> dict[str, str]:
    token = get_session_ingress_auth_token()
    if not token:
        return {}
    if token.startswith("sk-ant-sid"):
        headers: dict[str, str] = {"Cookie": f"sessionKey={token}"}
        org = os.environ.get("CLAUDE_CODE_ORGANIZATION_UUID")
        if org:
            headers["X-Organization-Uuid"] = org
        return headers
    return {"Authorization": f"Bearer {token}"}


def update_session_ingress_auth_token(token: str) -> None:
    os.environ["CLAUDE_CODE_SESSION_ACCESS_TOKEN"] = token
