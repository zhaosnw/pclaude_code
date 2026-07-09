import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from e2e_runner import run_case  # noqa: E402


FIXTURE = "hare/alignment/fixtures/single_turn_hello.json"


def test_run_case_injects_fixture_and_snapshots_files():
    case = {
        "case_id": "smoke.hello",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": FIXTURE,
        "expected": {"exit_code": 0, "stdout_kind": "text"},
        "policy": {},
    }
    result = run_case(case)
    assert result["state"]["exit_code"] == 0, result["stderr"]
    assert "Hello from fixture." in result["stdout"]
    assert isinstance(result["files"], list)
    assert result["sandbox_root"]


def test_run_case_rejects_legacy_fixture_prefix():
    case = {
        "case_id": "smoke.legacy-fixture",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": "alignment/fixtures/single_turn_hello.json",
        "expected": {"exit_code": 0, "stdout_kind": "text"},
        "policy": {},
    }
    with pytest.raises(ValueError, match="hare/alignment"):
        run_case(case)


def test_run_case_seeds_files_into_sandbox():
    case = {
        "case_id": "smoke.seed",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": FIXTURE,
        "fs": {"seed": ["README.md"]},
        "expected": {"exit_code": 0},
        "policy": {},
    }
    result = run_case(case)
    snap_paths = {f["path"] for f in result["files"]}
    assert "README.md" in snap_paths
