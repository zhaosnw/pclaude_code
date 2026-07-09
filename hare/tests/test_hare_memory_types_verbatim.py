"""Verbatim-fidelity check: hare's memory prompt-section constants must match
the 2.1.88 TS source word-for-word (they are literal system-prompt text).

This re-extracts the canonical arrays from src/memdir/memoryTypes.ts via node and
compares them element-by-element to hare's constants. Skipped when the recovered
TS source or node is unavailable (e.g. minimal CI) — the differential e2e suite
covers behavior; this is the prose-fidelity guard.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

from hare.memdir import memory_types as M

_HERE = os.path.dirname(__file__)
_TS = os.path.normpath(
    os.path.join(
        _HERE, "..", "..", "recovered-from-cli-js-map", "src", "memdir", "memoryTypes.ts"
    )
)

pytestmark = pytest.mark.skipif(
    not os.path.isfile(_TS) or shutil.which("node") is None,
    reason="recovered TS source or node not available",
)


def _extract():
    ts = open(_TS, encoding="utf-8").read()
    body = ts[ts.index("export const TYPES_SECTION_COMBINED"):]
    body = body.replace("export const ", "const ")
    body = re.sub(r":\s*readonly string\[\]", "", body)
    js = (
        "const MEMORY_TYPES = ['user','feedback','project','reference'];\n"
        + body
        + "\nconsole.log(JSON.stringify({TYPES_SECTION_COMBINED,"
        "TYPES_SECTION_INDIVIDUAL,WHAT_NOT_TO_SAVE_SECTION,MEMORY_DRIFT_CAVEAT,"
        "WHEN_TO_ACCESS_SECTION,TRUSTING_RECALL_SECTION,MEMORY_FRONTMATTER_EXAMPLE}));"
    )
    out = subprocess.check_output(["node", "-e", js], text=True)
    return json.loads(out)


@pytest.fixture(scope="module")
def ts_consts():
    return _extract()


@pytest.mark.parametrize(
    "name",
    [
        "TYPES_SECTION_COMBINED",
        "TYPES_SECTION_INDIVIDUAL",
        "WHAT_NOT_TO_SAVE_SECTION",
        "WHEN_TO_ACCESS_SECTION",
        "TRUSTING_RECALL_SECTION",
        "MEMORY_FRONTMATTER_EXAMPLE",
    ],
)
def test_section_matches_ts_verbatim(ts_consts, name):
    hare_val = list(getattr(M, name))
    ts_val = ts_consts[name]
    assert hare_val == ts_val, (
        f"{name} diverges from TS at element(s): "
        + ", ".join(
            str(i)
            for i in range(max(len(hare_val), len(ts_val)))
            if (hare_val[i] if i < len(hare_val) else None)
            != (ts_val[i] if i < len(ts_val) else None)
        )
    )


def test_drift_caveat_matches_ts(ts_consts):
    assert M.MEMORY_DRIFT_CAVEAT == ts_consts["MEMORY_DRIFT_CAVEAT"]


_MEMDIR_TS = os.path.normpath(
    os.path.join(
        _HERE, "..", "..", "recovered-from-cli-js-map", "src", "memdir", "memdir.ts"
    )
)


@pytest.mark.skipif(not os.path.isfile(_MEMDIR_TS), reason="memdir.ts not available")
def test_build_memory_lines_matches_ts_verbatim():
    """The ENTIRE assembled default memory section (not just the constants) must
    match TS buildMemoryLines('auto memory', dir, undefined, false) word-for-word.
    Evaluates the TS function with stubbed constants and compares to hare's."""
    from hare.memdir.memdir import build_memory_lines

    ts = open(_MEMDIR_TS, encoding="utf-8").read()
    fn = ts[ts.index("export function buildMemoryLines"):]
    fn = fn[: fn.index("\n}\n") + 2].replace("export function", "function")
    fn = re.sub(r":\s*string(\[\])?", "", fn)
    fn = fn.replace("extraGuidelines?", "extraGuidelines")
    fn = fn.replace("skipIndex = false", "skipIndex")
    fn = fn.replace(
        "lines.push(...buildSearchingPastContextSection(memoryDir))", ""
    )  # tengu_coral_fern off by default → []
    mt = open(_TS, encoding="utf-8").read()
    consts = re.sub(
        r":\s*readonly string\[\]",
        "",
        mt[mt.index("export const TYPES_SECTION_COMBINED"):].replace(
            "export const ", "const "
        ),
    )
    dir_guidance = (
        "This directory already exists — write to it directly with the Write "
        "tool (do not run mkdir or check for its existence)."
    )
    prelude = (
        "const MEMORY_TYPES=['user','feedback','project','reference'];\n"
        + consts
        + "\nconst ENTRYPOINT_NAME='MEMORY.md';\nconst MAX_ENTRYPOINT_LINES=200;\n"
        + f"const DIR_EXISTS_GUIDANCE={json.dumps(dir_guidance)};\n"
    )
    js = (
        prelude
        + fn
        + "\nconsole.log(JSON.stringify("
        "buildMemoryLines('auto memory','MEMDIR',undefined,false)));"
    )
    ts_lines = json.loads(subprocess.check_output(["node", "-e", js], text=True))
    assert build_memory_lines("auto memory", "MEMDIR", None, False) == ts_lines
