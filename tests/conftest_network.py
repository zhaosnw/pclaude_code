"""
Network safety fixture — blocks real outbound connections in non-integration tests.

Port of: ALIGNMENT_EVALUATION_AND_CI_PLAN.md §6.9

Autouse fixture that patches socket.connect and common HTTP libraries to
fail fast on any real outbound connection during unit/alignment tests.
Integration tests (marked @pytest.mark.integration) are exempt.
"""

from __future__ import annotations

import pytest


class NetworkBlockedError(Exception):
    """Raised when a test attempts a real network call without @integration marker."""

    pass


def _block_network(*args, **kwargs):  # type: ignore
    raise NetworkBlockedError(
        "Real network call blocked. Use @pytest.mark.integration to allow network access."
    )


@pytest.fixture(autouse=True)
def _block_real_network(request, monkeypatch):
    """Block all real outbound connections in non-integration tests.

    Skips blocking for tests marked with @pytest.mark.integration.
    """
    # Skip if integration test
    if request.node.get_closest_marker("integration"):
        return

    import socket

    # Block socket connections
    monkeypatch.setattr(socket, "create_connection", _block_network)

    # Block common HTTP libraries
    try:
        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", _block_network)
    except ImportError:
        pass

    try:
        import http.client

        monkeypatch.setattr(http.client.HTTPConnection, "request", _block_network)
    except ImportError:
        pass
