from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOTS = [
    PROJECT_ROOT / "alignment" / "cases",
    PROJECT_ROOT / "hare" / "alignment" / "cases",
]
CANONICAL_PREFIX = "hare/alignment/fixtures/"


def test_e2e_case_fixtures_use_canonical_hare_alignment_prefix() -> None:
    noncanonical: list[str] = []

    for root in CASE_ROOTS:
        for case_path in sorted(root.glob("**/case.json")):
            case = json.loads(case_path.read_text(encoding="utf-8"))
            fixture = case.get("fixture")
            if fixture and not fixture.startswith(CANONICAL_PREFIX):
                rel = case_path.relative_to(PROJECT_ROOT).as_posix()
                noncanonical.append(f"{rel}: {fixture}")

    assert not noncanonical, (
        "E2E case fixtures must use the canonical "
        f"{CANONICAL_PREFIX!r} prefix:\n" + "\n".join(noncanonical)
    )
