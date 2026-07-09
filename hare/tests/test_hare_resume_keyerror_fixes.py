"""Regression tests for confirmed bugs found by the differential bug-hunt workflow.

Each test encodes a real, confirmed, high-severity bug that was missed by the
existing test suite and found by adversarial review vs the TS reference."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


# ── leafUuids KeyError crash ────────────────────────────────────────────────

def test_enrich_log_from_data_no_keyerror():
    """_enrich_log_from_data reads 'leafUuids' (camelCase) but
    load_transcript_file returns 'leaf_uuids' (snake_case). This KeyError
    crashed every call to get_last_session_log / load_full_log."""
    from hare.utils.session_storage import _enrich_log_from_data

    data: dict = {
        "messages": {
            "u1": {
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "hi"},
                "isSidechain": False,
            }
        },
        "leaf_uuids": {"u1"},
        # Required entries from load_transcript_file return shape
        "summaries": {},
        "custom_titles": {},
        "tags": {},
        "agent_names": {},
        "agent_colors": {},
        "agent_settings": {},
        "modes": {},
        "pr_numbers": {},
        "pr_urls": {},
        "pr_repositories": {},
        "worktree_states": {},
        "file_history_snapshots": {},
        "attribution_snapshots": {},
        "content_replacements": {},
        "context_collapse_commits": [],
        "context_collapse_snapshot": None,
    }
    result = _enrich_log_from_data(data, "sess-test", "/nonexistent/sess.jsonl")
    assert result is not None
    assert result["sessionId"] == "sess-test"
    assert result["messageCount"] == 1


# ── Compact env truthiness ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "env_val,expected",
    [
        ("1", False),
        ("true", False),
        ("yes", False),
        ("on", False),
        ("false", True),
        ("0", True),
        ("no", True),
        ("off", True),
        ("garbage", True),
    ],
)
def test_compact_disabled_env_is_truthy(monkeypatch, env_val, expected):
    """DISABLE_COMPACT must use TS isEnvTruthy semantics: only 1/true/yes/on
    disables; bare os.environ.get truthiness was treating 'false' as true."""
    monkeypatch.setenv("DISABLE_COMPACT", env_val)
    monkeypatch.delenv("DISABLE_AUTO_COMPACT", raising=False)
    from hare.services.compact.auto_compact import is_auto_compact_enabled
    from hare.query.core import _is_auto_compact_enabled

    assert is_auto_compact_enabled() is expected, (
        f"is_auto_compact_enabled() returned {not expected} for DISABLE_COMPACT={env_val!r}"
    )
    assert _is_auto_compact_enabled() is expected, (
        f"_is_auto_compact_enabled() returned {not expected} for DISABLE_COMPACT={env_val!r}"
    )


# ── McpError NameError ──────────────────────────────────────────────────────

def test_mcperror_alias_available():
    """MCPError is defined at module top-level, but all 7 raise sites use the
    bare name 'McpError' (wrong case). This test locks the fix: the module
    must export an 'McpError' alias so all raise sites resolve correctly,
    and MCPError must remain the canonical name."""
    from hare.services.mcp import client as mcp_client

    # Canonical class exists
    assert mcp_client.MCPError.__name__ == "MCPError"
    # Alias must exist so raise McpError(...) resolves everywhere
    assert mcp_client.McpError is mcp_client.MCPError, (
        "McpError alias missing — all 7 SSE/http error paths raise NameError"
    )
