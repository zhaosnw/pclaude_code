from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_TREE = PROJECT_ROOT / "hare" / "hare"


def test_hare_hare_tree_has_no_python_files() -> None:
    if not LEGACY_TREE.exists():
        return

    offenders = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in LEGACY_TREE.rglob("*.py")
    )
    assert not offenders, (
        "legacy hare/hare tree should be fully removed:\n" + "\n".join(offenders[:20])
    )
