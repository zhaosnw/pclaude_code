from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOTS = [
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "hare" / "scripts",
    PROJECT_ROOT / "hare" / "tests",
]
LEGACY_FIXTURE_PREFIX = "alignment/fixtures/"
ALLOWED_FILES = {
    "scripts/e2e_runner.py",
    "tests/test_alignment_case_fixture_paths.py",
    "tests/test_e2e_runner.py",
    "tests/test_alignment_legacy_fixture_ref_allowlist.py",
    "tests/test_record_golden_paths.py",
    "hare/scripts/e2e_runner.py",
    "hare/tests/test_hare_alignment_case_fixture_paths.py",
    "hare/tests/test_hare_alignment_legacy_fixture_ref_allowlist.py",
    "hare/tests/test_hare_e2e_runner.py",
    "hare/tests/test_hare_record_golden_paths.py",
}


def test_legacy_fixture_prefix_is_confined_to_explicit_allowlist() -> None:
    offenders: list[str] = []

    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*")):
            if path.is_dir() or path.suffix == ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if LEGACY_FIXTURE_PREFIX not in text:
                continue
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel not in ALLOWED_FILES:
                offenders.append(rel)

    assert not offenders, (
        "legacy fixture-prefix references escaped the allowlist:\n"
        + "\n".join(offenders)
    )
