from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOTS = [
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "hare" / "scripts",
    PROJECT_ROOT / "hare" / "tests",
]
ROOT_ALIGNMENT_RE = re.compile(
    r"(?<!hare/)(?<!legacy_)alignment/(cases|golden|seeds)/"
)
ALLOWED_FILES: set[str] = set()


def test_root_alignment_string_refs_are_confined_to_allowlist() -> None:
    offenders: list[str] = []

    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*")):
            if path.is_dir() or path.suffix == ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if not ROOT_ALIGNMENT_RE.search(text):
                continue
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel not in ALLOWED_FILES:
                offenders.append(rel)

    assert not offenders, (
        "repo-root alignment string refs escaped the allowlist:\n"
        + "\n".join(offenders)
    )
