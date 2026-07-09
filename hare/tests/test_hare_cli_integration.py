"""Integration tests for CLI entrypoints — subprocess, flag handling, module health."""

from __future__ import annotations

import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Version fast-path (entrypoints/cli.py:main)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_hare_module_version_flag() -> None:
    """python -m hare --version should print version and exit 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "hare", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "2.1.88" in proc.stdout


@pytest.mark.integration
def test_hare_version_short_flag() -> None:
    """python -m hare -v should print version and exit 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "hare", "-v"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "2.1.88" in proc.stdout


@pytest.mark.integration
def test_hare_help_flag_through_main() -> None:
    """cli_main([\"--help\"]) exercises argparse help."""
    import asyncio
    from hare.main import cli_main

    with pytest.raises(SystemExit):
        asyncio.run(cli_main(["--help"]))


@pytest.mark.integration
def test_cli_main_version_exits() -> None:
    """cli_main with -v triggers argparse version action (SystemExit)."""
    import asyncio
    from hare.main import cli_main

    with pytest.raises(SystemExit):
        asyncio.run(cli_main(["-v"]))


# ---------------------------------------------------------------------------
# Key module import health
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_can_import_query_engine() -> None:
    """QueryEngine and QueryEngineConfig should be importable."""
    from hare.query_engine import QueryEngine, QueryEngineConfig

    assert QueryEngine is not None
    assert QueryEngineConfig is not None


@pytest.mark.integration
def test_can_import_bridge_api() -> None:
    """Bridge API symbols should be importable."""
    from hare.bridge import (
        BridgeApiClient,
        BridgeConfig,
        create_bridge_api_client,
    )

    assert BridgeApiClient is not None
    assert BridgeConfig is not None
    assert create_bridge_api_client is not None


@pytest.mark.integration
def test_can_import_cli_main() -> None:
    """cli_main should be importable."""
    from hare.main import cli_main

    assert callable(cli_main)


@pytest.mark.integration
def test_can_import_commands() -> None:
    """Command system should be importable."""
    from hare.commands import get_commands

    assert callable(get_commands)
