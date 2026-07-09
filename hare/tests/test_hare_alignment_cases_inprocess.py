"""Run P0/P1 query alignment cases in-process under pytest-cov coverage.

This exercises query_engine.submit_message, stop_hooks.handle_stop_hooks,
auto_compact, and config pipelines, covering ~30-40 remaining conditions.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so alignment_mocks import works
_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

_ALIGNMENT_CASES = str(
    Path(__file__).resolve().parents[2] / "legacy_alignment" / "cases"
)


def _discover_query_cases():
    """Find all P0/P1 query cases."""
    cases = []
    for priority in ("P0", "P1"):
        root = os.path.join(_ALIGNMENT_CASES, priority)
        for dirpath, dirnames, filenames in os.walk(root):
            if "case.json" in filenames:
                cf = os.path.join(dirpath, "case.json")
                try:
                    case = json.loads(open(cf).read())
                    if case["entrypoint"].get("kind") == "query":
                        cases.append((case["case_id"], cf))
                except Exception:
                    pass
    return cases


_QUERY_CASES = _discover_query_cases()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id,case_file", _QUERY_CASES, ids=[c[0] for c in _QUERY_CASES]
)
async def test_alignment_query_case_inprocess(case_id: str, case_file: str):
    """Run a query alignment case in-process to build branch coverage."""
    from alignment_mocks import run_query_case

    case = json.loads(open(case_file).read())
    result = await run_query_case(case)
    assert result["status"] == "ok", (
        f"Query case {case_id} failed: {result.get('error', result.get('stderr', ''))}"
    )
