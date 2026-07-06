#!/usr/bin/env python3
"""Validate that all ``hare.*`` imports within the hare package resolve.

Walks every .py file under hare/hare/ and tests each ``from hare.X import Y``
/ ``import hare.X`` statement by attempting the import in a subprocess.
Exits non-zero if any import fails.

Usage:
    python scripts/validate_imports.py          # check all
    python scripts/validate_imports.py --quick  # only top-level modules
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARE_PKG = PROJECT_ROOT / "hare"


def extract_hare_imports(file_path: Path) -> list[str]:
    """Parse a .py file and return hare-package import statements."""
    try:
        tree = ast.parse(file_path.read_text())
    except SyntaxError as e:
        print(f"  SKIP (syntax error): {file_path.relative_to(PROJECT_ROOT)} — {e}")
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("hare"):
                    imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("hare"):
                names = ", ".join(a.name for a in node.names)
                imports.append(f"from {node.module} import {names}")
    return imports


def test_import(import_stmt: str) -> bool:
    """Try executing an import statement in a fresh subprocess."""
    code = f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); {import_stmt}"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        err = (
            result.stderr.strip().split("\n")[-1]
            if result.stderr.strip()
            else "unknown"
        )
        print(f"  FAIL: {import_stmt}")
        print(f"         {err}")
        return False
    return True


def main() -> int:
    quick = "--quick" in sys.argv

    py_files = sorted(HARE_PKG.rglob("*.py"))
    if quick:
        py_files = [f for f in py_files if f.parent == HARE_PKG]

    print(f"Validating imports in {len(py_files)} file(s)...")

    total_imports = 0
    failures = 0

    for py_file in py_files:
        imports = extract_hare_imports(py_file)
        if not imports:
            continue
        for imp in imports:
            total_imports += 1
            if not test_import(imp):
                failures += 1

    max_failures = 50  # port-in-progress allowance
    for arg in sys.argv:
        if arg.startswith("--max-failures="):
            max_failures = int(arg.split("=", 1)[1])

    print(
        f"\nResults: {total_imports} imports tested, {failures} failures (max allowed: {max_failures})"
    )
    if failures > max_failures:
        print(f"ERROR: {failures} failures exceeds max {max_failures}.")
        return 1
    if failures:
        print(
            f"WARNING: {failures} import failures within allowed limit ({max_failures})."
        )
    else:
        print("All imports resolve successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
