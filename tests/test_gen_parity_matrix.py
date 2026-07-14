from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "gen_parity_matrix.py"


def run_matrix(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def test_generator_extracts_all_four_dimensions(tmp_path: Path) -> None:
    matrix = tmp_path / "parity-matrix.md"

    proc = run_matrix("--output", str(matrix))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    content = matrix.read_text(encoding="utf-8")
    assert "`cli.--print`" in content
    assert "`cli.command.mcp`" in content
    assert "`tool.BashTool`" in content
    # Hook events come from the reference HOOK_EVENTS list, settings keys from
    # the tracked behavior-gating set; a regression in either extractor drops
    # the dimension silently otherwise.
    assert "`hook.PreCompact`" in content
    assert "`settings.permissions.ask`" in content
    assert "implemented-unverified" in content
    assert "| `cli.--continue` | `aligned` | `session.continue_basic` | `P1` |" in content
    assert "| `cli.--resume` | `aligned` | `session.resume_basic` | `P1` |" in content
    assert "| `cli.--mcp-config` | `aligned` | `mcp.stdio_tool_call` | `P1` |" in content
    assert "| `hook.Stop` | `aligned` | `hooks.stop_hook` | `P1` |" in content
    # A flag with no golden yet still reports as unverified.
    assert "| `cli.--debug` | `implemented-unverified` | `-` | `P1` |" in content


def test_check_rejects_aligned_feature_without_a_golden_case(tmp_path: Path) -> None:
    matrix = tmp_path / "parity-matrix.md"
    assert run_matrix("--output", str(matrix)).returncode == 0

    matrix.write_text(
        matrix.read_text(encoding="utf-8").replace(
            "| `cli.--print` | `implemented-unverified` | `-` | `P0` |",
            "| `cli.--print` | `aligned` | `missing.golden.case` | `P0` |",
            1,
        ),
        encoding="utf-8",
    )

    proc = run_matrix("--check", "--matrix", str(matrix))

    assert proc.returncode == 1
    assert "missing.golden.case" in proc.stdout
