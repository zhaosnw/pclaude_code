from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE2 = PROJECT_ROOT / "hare" / "scripts" / "run_alignment_phase2.py"
TS_RUNNER = (
    PROJECT_ROOT / "recovered-from-cli-js-map" / "alignment-harness" / "runner.ts"
)
CASE_FILE = (
    PROJECT_ROOT
    / "legacy_alignment"
    / "cases"
    / "P1"
    / "module"
    / "history_parse"
    / "case.json"
)


def test_ts_alignment_runner_history_case() -> None:
    proc = subprocess.run(
        ["bun", str(TS_RUNNER), "--case-file", str(CASE_FILE)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    result = json.loads(proc.stdout)
    assert result["case_id"] == "module.history_parse"
    assert result["status"] == "ok"
    assert result["events"][0]["id"] == 1  # "[Pasted text #1]" → id=1


def test_phase2_dual_run_produces_report(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(PHASE2),
            "--priority",
            "P1",
            "--report-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(
        (tmp_path / "alignment-report.json").read_text(encoding="utf-8")
    )
    assert report["summary"]["gates_pass"] is True
