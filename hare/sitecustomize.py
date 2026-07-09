"""Prefer the repo-root package layout when Python starts inside ``hare/``.

This keeps ``cd hare && python ...`` workflows pointed at the canonical
top-level ``hare/`` package while the inner mirror tree is being removed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

repo_root_str = str(REPO_ROOT)
if repo_root_str in sys.path:
    sys.path.remove(repo_root_str)
sys.path.insert(0, repo_root_str)
