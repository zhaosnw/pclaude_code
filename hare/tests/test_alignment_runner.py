from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT_ROOT / "hare" / "scripts" / "alignment_runner.py"
CASES_ROOT = PROJECT_ROOT / "alignment" / "cases"


def _run_case(case_id: str) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--cases-dir",
            str(CASES_ROOT),
            "--priority",
            "P0,P1",
            "--case",
            case_id,
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


def test_alignment_runner_cli_case_executes() -> None:
    result = _run_case("cli.version_flag")
    assert result["case_id"] == "cli.version_flag"
    assert result["status"] == "ok"
    assert isinstance(result["stdout"], str)
    assert "not_implemented_in_phase1:files_state_snapshot" in result["phase1_notes"]


def test_alignment_runner_module_case_executes_real_function() -> None:
    result = _run_case("module.history_parse")
    assert result["status"] == "ok"
    assert result["events"]
    assert result["events"][0]["id"] == 1  # "[Pasted text #1]" → id=1


def test_alignment_runner_sdk_case_is_skipped() -> None:
    result = _run_case("sdk.deferred")
    assert result["status"] == "skipped"
    assert result["error"]["code"] == "PHASE2_SDK"
