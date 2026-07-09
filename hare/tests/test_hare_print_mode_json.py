"""TS-differential-driven: hare must honor --output-format json/stream-json.

Before this, hare ignored the flag and emitted plain text. Claude Code emits a
structured result object. These tests pin the core schema (stable fields) that
hare must produce; volatile fields (duration/cost/session_id) are not asserted
here.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARE_ALIGNMENT = REPO / "alignment"
FIXTURE = HARE_ALIGNMENT / "fixtures" / "single_turn_hello.json"


def _run(argv):
    env = dict(os.environ)
    env["HARE_MODEL_FIXTURE"] = str(FIXTURE)
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"
    with tempfile.TemporaryDirectory(prefix="hare-print-json-") as tmpdir:
        env["HOME"] = tmpdir
        config_dir = Path(tmpdir) / ".hare"
        env["HARE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        return subprocess.run(
            [sys.executable, "-m", "hare", *argv],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=str(REPO),
        )


@pytest.mark.integration
def test_output_format_json_emits_structured_result():
    proc = _run(["-p", "say hello", "--output-format", "json"])
    assert proc.returncode == 0, proc.stderr
    obj = json.loads(proc.stdout)  # must be valid JSON, not plain text
    assert obj["type"] == "result"
    assert obj["subtype"] == "success"
    assert obj["is_error"] is False
    assert obj["result"] == "Hello from fixture."
    assert obj["stop_reason"] == "end_turn"
    # usage must be a JSON object, not a stringified dataclass
    assert isinstance(obj["usage"], dict), repr(obj["usage"])
    assert "input_tokens" in obj["usage"]
    assert "output_tokens" in obj["usage"]


def test_align_result_schema_matches_claude_contract():
    from hare.main import _align_result_schema

    aligned = _align_result_schema(
        {"type": "result", "subtype": "success", "result": "x", "model_usage": {}}
    )
    # renamed and contract keys present
    assert "modelUsage" in aligned and "model_usage" not in aligned
    for k in ("api_error_status", "ttft_ms", "time_to_request_ms",
              "fast_mode_state", "terminal_reason"):
        assert k in aligned, k
    assert aligned["terminal_reason"] == "completed"
    # non-result objects pass through untouched
    assert _align_result_schema({"type": "system"}) == {"type": "system"}


@pytest.mark.integration
def test_output_format_stream_json_emits_ndjson_with_result():
    proc = _run(["-p", "say hello", "--output-format", "stream-json", "--verbose"])
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    objs = [json.loads(ln) for ln in lines]  # every line is valid JSON
    types = [o.get("type") for o in objs]
    assert "result" in types
    result = next(o for o in objs if o.get("type") == "result")
    assert result["result"] == "Hello from fixture."
