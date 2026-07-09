from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPARE = PROJECT_ROOT / "hare" / "scripts" / "compare_alignment.py"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_compare_alignment_hard_fails_p0(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    case_dir = cases_root / "P0" / "sample" / "case"
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "sample.case",
                "priority": "P0",
                "entrypoint": {"kind": "cli", "argv": ["--version"], "stdin": None},
                "mocks": {"model": {}},
                "expected": {},
                "policy": {
                    "ignore_fields": [],
                    "tolerance": {},
                    "blocking": True,
                    "allow_delta": [],
                },
            }
        ),
        encoding="utf-8",
    )
    ts_path = tmp_path / "ts.jsonl"
    py_path = tmp_path / "py.jsonl"
    _write_jsonl(
        ts_path,
        [
            {
                "case_id": "sample.case",
                "priority": "P0",
                "status": "ok",
                "events": [],
                "stdout": "a",
                "stderr": "",
                "files": [],
                "state": {},
                "error": None,
                "duration_ms": 1,
            }
        ],
    )
    _write_jsonl(
        py_path,
        [
            {
                "case_id": "sample.case",
                "priority": "P0",
                "status": "ok",
                "events": [],
                "stdout": "b",
                "stderr": "",
                "files": [],
                "state": {},
                "error": None,
                "duration_ms": 1,
            }
        ],
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(COMPARE),
            "--ts",
            str(ts_path),
            "--py",
            str(py_path),
            "--cases-dir",
            str(cases_root),
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 1
    assert "HARD FAIL" in proc.stdout


def test_compare_alignment_respects_allow_delta(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    case_dir = cases_root / "P1" / "sample" / "case"
    case_dir.mkdir(parents=True)
    (case_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "sample.allow",
                "priority": "P1",
                "entrypoint": {"kind": "cli", "argv": ["--version"], "stdin": None},
                "mocks": {"model": {}},
                "expected": {},
                "policy": {
                    "ignore_fields": [],
                    "tolerance": {},
                    "blocking": True,
                    "allow_delta": [
                        {
                            "path": "stdout",
                            "reason": "phase1 accepted delta",
                            "expires_at": "2099-01-01",
                            "kind": "text",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    ts_path = tmp_path / "ts.jsonl"
    py_path = tmp_path / "py.jsonl"
    base = {
        "status": "ok",
        "events": [],
        "stderr": "",
        "files": [],
        "state": {},
        "error": None,
        "duration_ms": 1,
        "priority": "P1",
    }
    _write_jsonl(ts_path, [{"case_id": "sample.allow", "stdout": "alpha", **base}])
    _write_jsonl(py_path, [{"case_id": "sample.allow", "stdout": "beta", **base}])

    proc = subprocess.run(
        [
            sys.executable,
            str(COMPARE),
            "--ts",
            str(ts_path),
            "--py",
            str(py_path),
            "--cases-dir",
            str(cases_root),
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0
    assert "FAIL" not in proc.stdout
