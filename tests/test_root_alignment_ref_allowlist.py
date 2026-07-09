from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = [
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "tests",
]
ROOT_ALIGNMENT_PATTERNS = [
    'PROJECT_ROOT / "alignment"',
    'REPO_ROOT / "alignment"',
    'ROOT_ALIGNMENT = PROJECT_ROOT / "alignment"',
]
ALLOWED_FILES = {
    "tests/test_alignment_case_fixture_paths.py",
    "tests/test_alignment_e2e_mirror.py",
    "tests/test_root_alignment_ref_allowlist.py",
}


def test_root_alignment_tree_refs_are_confined_to_allowlist() -> None:
    offenders: list[str] = []

    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*")):
            if path.is_dir() or path.suffix == ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if not any(pattern in text for pattern in ROOT_ALIGNMENT_PATTERNS):
                continue
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel not in ALLOWED_FILES:
                offenders.append(rel)

    assert not offenders, (
        "repo-root alignment tree references escaped the allowlist:\n"
        + "\n".join(offenders)
    )
