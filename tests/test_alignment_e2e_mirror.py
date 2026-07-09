from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_ALIGNMENT = PROJECT_ROOT / "alignment"
HARE_ALIGNMENT = PROJECT_ROOT / "hare" / "alignment"
IGNORED_REL_PATHS = {"README.md"}
IGNORED_PARTS = {"__pycache__"}


def _file_map(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in rel.parts):
            continue
        rel_str = rel.as_posix()
        if rel_str in IGNORED_REL_PATHS:
            continue
        files[rel_str] = path
    return files


def test_root_alignment_mirrors_hare_alignment() -> None:
    """The repo-root alignment tree is a compatibility mirror of hare/alignment.

    We intentionally allow README drift because the two directories explain
    different roles, but the underlying E2E assets should stay byte-identical.
    """

    root_files = _file_map(ROOT_ALIGNMENT)
    hare_files = _file_map(HARE_ALIGNMENT)

    assert root_files.keys() == hare_files.keys(), (
        "alignment/ and hare/alignment/ differ in tracked E2E asset paths:\n"
        f"root-only={sorted(root_files.keys() - hare_files.keys())}\n"
        f"hare-only={sorted(hare_files.keys() - root_files.keys())}"
    )

    mismatches: list[str] = []
    for rel in sorted(root_files):
        if root_files[rel].read_bytes() != hare_files[rel].read_bytes():
            mismatches.append(rel)

    assert not mismatches, (
        "alignment/ and hare/alignment/ drifted in E2E asset contents:\n"
        + "\n".join(mismatches)
    )
