"""Stub: port of authFileDescriptor.ts — token paths and persistence helpers."""

from __future__ import annotations

from pathlib import Path

# Well-known default; TS resolves under config home — keep compatible string.
CCR_SESSION_INGRESS_TOKEN_PATH = str(
    Path.home() / ".hare" / "remote" / ".session_ingress_token"
)


def read_token_from_well_known_file(path: str, _label: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def maybe_persist_token_for_subprocesses(_path: str, _token: str, _label: str) -> None:
    """Optional disk persistence for subprocess visibility — stubbed."""
    pass
