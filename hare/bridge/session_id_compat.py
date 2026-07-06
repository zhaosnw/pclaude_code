"""
Session ID tag translation helpers for the CCR v2 compat layer.

Port of: src/bridge/sessionIdCompat.ts

CRITICAL: The transformation directions matter:
  - toCompatSessionId: cse_* -> session_* (for compat API layer)
  - toInfraSessionId:  session_* -> cse_* (for infrastructure layer)

Previously these were INVERTED in the Python port — that was a hard bug
that would cause 404s on all session API calls.
"""

from __future__ import annotations

from typing import Callable, Optional

_is_cse_shim_enabled: Optional[Callable[[], bool]] = None


def set_cse_shim_gate(gate: Callable[[], bool]) -> None:
    """Register the GrowthBook gate for the cse_ shim."""
    global _is_cse_shim_enabled
    _is_cse_shim_enabled = gate


def to_compat_session_id(session_id: str) -> str:
    """Re-tag cse_* -> session_* for use with the v1 compat API.

    Worker endpoints want cse_*; client-facing compat endpoints want session_*.
    Same UUID, different tag prefix.
    """
    if not session_id.startswith("cse_"):
        return session_id
    if _is_cse_shim_enabled and not _is_cse_shim_enabled():
        return session_id
    return "session_" + session_id[len("cse_") :]


def to_infra_session_id(session_id: str) -> str:
    """Re-tag session_* -> cse_* for infrastructure-layer calls.

    Inverse of toCompatSessionId. POST /v1/environments/{id}/bridge/reconnect
    looks sessions up by their infra tag (cse_*).
    """
    if not session_id.startswith("session_"):
        return session_id
    return "cse_" + session_id[len("session_") :]
