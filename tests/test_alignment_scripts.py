from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFY = PROJECT_ROOT / "hare" / "scripts" / "verify_alignment.py"
GEN_PRIORITY = PROJECT_ROOT / "hare" / "scripts" / "gen_alignment_priority.py"


def test_alignment_data_uses_canonical_python_paths() -> None:
    data = json.loads(
        (PROJECT_ROOT / "alignment_data.json").read_text(encoding="utf-8")
    )
    bad = []
    for row in data.get("rows", []):
        if not isinstance(row, dict):
            continue
        py_path = str(row.get("py", ""))
        if py_path and " | " not in py_path and not py_path.startswith("hare/hare/"):
            bad.append(py_path)
    assert not bad, f"Found non-canonical python paths: {bad[:10]}"


def test_verify_alignment_script_passes() -> None:
    proc = subprocess.run(
        [sys.executable, str(VERIFY)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_gen_alignment_priority_is_idempotent() -> None:
    before = (PROJECT_ROOT / "alignment_data.json").read_text(encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(GEN_PRIORITY)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = (PROJECT_ROOT / "alignment_data.json").read_text(encoding="utf-8")
    assert before == after
