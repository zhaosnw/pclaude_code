"""Live end-to-end smoke against a REAL model backend (e.g. DeepSeek via the
user's ~/.claude.json / ANTHROPIC_* env).

Unlike the fixture-replay E2E suite (which fixes the model's output), this runs
`python -m hare` against a real, NON-deterministic model — the only thing that
proves hare's real HTTP client, streaming parse, system prompt, tool advertising
and agentic loop actually work together against a live backend.

Opt-in (nondeterministic, costs tokens, needs network + creds): runs only when
HARE_LIVE_TESTS=1. Assertions are loose (properties, not exact text).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not os.environ.get("HARE_LIVE_TESTS"),
    reason="live model test — set HARE_LIVE_TESTS=1 (and a working ANTHROPIC_* backend) to run",
)


def _run(argv, cwd, timeout=180):
    env = dict(os.environ)
    env.setdefault("API_TIMEOUT_MS", "120000")
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "hare", *argv],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
    )


@pytest.mark.live
@pytest.mark.integration
def test_live_basic_qa():
    """A plain prompt completes against the live model and prints a non-empty
    answer (real HTTP + streaming + system prompt path)."""
    proc = _run(["-p", "Reply with exactly the word: PONG"], cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "empty answer from live model"
    assert "PONG" in proc.stdout.upper()


@pytest.mark.live
@pytest.mark.integration
def test_live_agentic_tool_loop():
    """The real agentic loop: the model is asked to read a file, must call the
    Read tool, get the content back, and answer from it. Proves tool advertising
    (registry) + tool-use guidance (system prompt) + the multi-turn loop work
    end-to-end with a live model. Loose check: the answer reflects file content."""
    with tempfile.TemporaryDirectory() as d:
        marker = "ZESTYQUOKKA"
        Path(d, "README.md").write_text(
            f"This project's secret codename is {marker}.\n", encoding="utf-8"
        )
        proc = _run(
            [
                "-p",
                "Read README.md in the current directory and tell me the secret codename.",
                "--permission-mode", "bypassPermissions",
                "--output-format", "json",
            ],
            cwd=d,
        )
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj.get("is_error") is False, obj
    assert obj.get("num_turns", 0) >= 2, f"expected a tool turn; got {obj.get('num_turns')}"
    assert marker in (obj.get("result") or ""), (
        f"model didn't read the file via the tool; result={obj.get('result')!r}"
    )


@pytest.mark.live
@pytest.mark.integration
def test_live_bash_tool_loop():
    """Real agentic loop over the Bash tool: model runs a command and reports its
    output (a second, distinct tool path beyond Read)."""
    token = "SQUIRREL9173"
    with tempfile.TemporaryDirectory() as d:
        proc = _run(
            [
                "-p",
                f"Use the Bash tool to run: echo {token}  — then tell me the exact token it printed.",
                "--permission-mode", "bypassPermissions",
                "--output-format", "json",
            ],
            cwd=d,
        )
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)
    assert obj.get("is_error") is False, obj
    assert obj.get("num_turns", 0) >= 2, f"expected a tool turn; got {obj.get('num_turns')}"
    assert token in (obj.get("result") or ""), (
        f"model didn't run/report the command; result={obj.get('result')!r}"
    )
