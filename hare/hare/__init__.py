"""Compatibility shim for ``cd hare`` workflows.

This inner package used to mirror the full source tree. It now just extends
``hare.__path__`` so nested workflows resolve submodules from the canonical
repo-root ``hare/`` package.
"""

from __future__ import annotations

from pathlib import Path

_CANONICAL_PKG = Path(__file__).resolve().parents[2] / "hare"
_canonical_pkg_str = str(_CANONICAL_PKG)
if _canonical_pkg_str not in __path__:
    __path__.insert(0, _canonical_pkg_str)

_globals = globals()
exec((_CANONICAL_PKG / "__init__.py").read_text(encoding="utf-8"), _globals, _globals)
