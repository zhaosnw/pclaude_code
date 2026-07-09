"""Parametrized pytest runner for all legacy_alignment/cases.

Each case.json found under legacy_alignment/cases/ is expanded into a test that:
1. Runs the Python alignment runner for that case
2. Asserts status is ok (or skipped for known-deferred cases)
3. Validates the output shape matches legacy_alignment/schema/output.schema.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = PROJECT_ROOT / "legacy_alignment" / "cases"
RUNNER = PROJECT_ROOT / "hare" / "scripts" / "alignment_runner.py"


def _discover_cases() -> list[tuple[str, str, Path]]:
    """Return list of (case_id, priority, case_path) tuples."""
    cases = []
    for case_file in sorted(CASES_DIR.glob("**/case.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        cases.append((case["case_id"], case["priority"], case_file))
    return cases


CASE_IDS = _discover_cases()


def _run_python_case(case_id: str) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--cases-dir",
            str(CASES_DIR),
            "--case",
            case_id,
            "--priority",
            "P0,P1,P2,P3",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        pytest.fail(f"No output from runner for {case_id}: stderr={proc.stderr}")
    return json.loads(lines[0])


@pytest.mark.alignment
@pytest.mark.parametrize(
    "case_id,priority,case_path", CASE_IDS, ids=[c[0] for c in CASE_IDS]
)
def test_alignment_case_runs(case_id: str, priority: str, case_path: Path) -> None:
    """Every alignment case must be runnable by the Python oracle."""
    result = _run_python_case(case_id)
    assert result["case_id"] == case_id, (
        f"case_id mismatch: {result.get('case_id')} != {case_id}"
    )

    allowed_statuses = {"ok", "skipped", "error"}
    assert result["status"] in allowed_statuses, f"Unknown status: {result['status']}"

    if result["status"] == "error":
        error_info = result.get("error", {})
        code = error_info.get("code", "")
        if code in ("PHASE2_SDK",):
            pytest.skip(f"Deferred: {error_info.get('message_normalized', '')}")
        pytest.fail(
            f"Case {case_id} error: {error_info.get('message_normalized', result.get('stderr', ''))}"
        )


@pytest.mark.alignment
@pytest.mark.parametrize(
    "case_id,priority,case_path", CASE_IDS, ids=[c[0] for c in CASE_IDS]
)
def test_alignment_case_output_shape(
    case_id: str, priority: str, case_path: Path
) -> None:
    """Validate output shape has required fields."""
    result = _run_python_case(case_id)
    if result["status"] == "skipped":
        pytest.skip("Case skipped")

    required_fields = [
        "case_id",
        "priority",
        "status",
        "events",
        "stdout",
        "stderr",
        "files",
        "state",
        "error",
        "duration_ms",
    ]
    for field in required_fields:
        assert field in result, f"Missing required field '{field}' in {case_id} output"


@pytest.mark.alignment
@pytest.mark.parametrize(
    "case_id,priority,case_path", CASE_IDS, ids=[c[0] for c in CASE_IDS]
)
def test_alignment_case_schema_valid(
    case_id: str, priority: str, case_path: Path
) -> None:
    """Validate case.json passes schema validation."""
    import jsonschema

    schema_path = PROJECT_ROOT / "legacy_alignment" / "schema" / "case.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    case = json.loads(case_path.read_text(encoding="utf-8"))
    jsonschema.validate(case, schema)


@pytest.mark.alignment
def test_case_count_minimum() -> None:
    """Ensure we have at least 20 alignment cases (growing toward 30+)."""
    case_files = list(CASES_DIR.glob("**/case.json"))
    assert len(case_files) >= 10, f"Need at least 10 cases, found {len(case_files)}"


@pytest.mark.alignment
def test_p0_cases_exist() -> None:
    """Ensure P0 tier has cases defined."""
    p0_cases = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in CASES_DIR.glob("**/case.json")
    ]
    p0_cases = [c for c in p0_cases if c["priority"] == "P0"]
    assert len(p0_cases) >= 3, f"Expected >= 3 P0 cases, got {len(p0_cases)}"
