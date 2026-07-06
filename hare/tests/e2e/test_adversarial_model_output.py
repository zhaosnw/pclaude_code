"""Deterministic robustness tests: feed hare ADVERSARIAL/malformed model output
via a Layer-A fixture and assert hare degrades gracefully (no crash, no REPL,
a clean result) — real models occasionally emit such output, and fixtures let us
test it deterministically without a live model.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _run_fixture(responses, argv_extra=None):
    """Write a scripted fixture and run `python -m hare -p ... --output-format json`."""
    fx = {"kind": "scripted", "responses": responses}
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d, "adv.json")
        fp.write_text(json.dumps(fx), encoding="utf-8")
        env = dict(os.environ)
        env["HARE_MODEL_FIXTURE"] = str(fp)
        env["ANTHROPIC_API_KEY"] = "test-key-not-used"
        proc = subprocess.run(
            [sys.executable, "-m", "hare", "-p", "go",
             "--permission-mode", "bypassPermissions",
             "--output-format", "json", *(argv_extra or [])],
            capture_output=True, text=True, timeout=60, env=env, cwd=d,
            stdin=subprocess.DEVNULL,
        )
        return proc


def _assert_graceful(proc):
    # no crash, no REPL fallback, parseable result
    assert "Traceback (most recent" not in proc.stderr, proc.stderr
    assert "Hare Python Port" not in proc.stdout, "dropped to REPL"
    assert proc.stdout.strip(), "no output"
    obj = json.loads(proc.stdout)  # must still be valid result JSON
    assert obj.get("type") == "result"
    return obj


@pytest.mark.integration
def test_unknown_tool_is_handled_gracefully():
    """Model calls a tool that doesn't exist, then answers. hare must not crash."""
    proc = _run_fixture([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "NoSuchToolXYZ", "input": {"a": 1}}],
         "usage": {"input_tokens": 5, "output_tokens": 5}},
        {"stop_reason": "end_turn",
         "content": [{"type": "text", "text": "Handled the missing tool."}],
         "usage": {"input_tokens": 8, "output_tokens": 4}},
    ])
    _assert_graceful(proc)


@pytest.mark.integration
def test_tool_use_missing_required_input_handled():
    """Model calls Read with no file_path (missing required arg). hare must
    surface a tool error and keep going, not crash."""
    proc = _run_fixture([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
         "usage": {"input_tokens": 5, "output_tokens": 5}},
        {"stop_reason": "end_turn",
         "content": [{"type": "text", "text": "Could not read."}],
         "usage": {"input_tokens": 8, "output_tokens": 4}},
    ])
    _assert_graceful(proc)


@pytest.mark.integration
def test_bash_missing_command_handled():
    """Model calls Bash with no command. hare must not crash."""
    proc = _run_fixture([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}],
         "usage": {"input_tokens": 5, "output_tokens": 5}},
        {"stop_reason": "end_turn",
         "content": [{"type": "text", "text": "No command given."}],
         "usage": {"input_tokens": 8, "output_tokens": 4}},
    ])
    _assert_graceful(proc)


@pytest.mark.integration
def test_empty_content_response_handled():
    """Model returns a response with empty content list. hare must not crash."""
    proc = _run_fixture([
        {"stop_reason": "end_turn", "content": [],
         "usage": {"input_tokens": 5, "output_tokens": 0}},
    ])
    _assert_graceful(proc)
