import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from hare.main import _parse_stream_json_input


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "hare" / "alignment" / "fixtures" / "single_turn_hello.json"


def _user_message(content: object) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
            "session_id": "",
        }
    )


def test_parse_stream_json_input_preserves_multiple_user_turns() -> None:
    data = "\n".join([_user_message("first"), _user_message("second")])

    assert _parse_stream_json_input(data) == ["first", "second"]


def test_parse_stream_json_input_accepts_content_blocks() -> None:
    blocks = [{"type": "text", "text": "hello"}]

    assert _parse_stream_json_input(_user_message(blocks)) == [blocks]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("not-json", "line 1"),
        (json.dumps({"type": "control_request"}), "expected a user message"),
        (_user_message("   "), "No user messages received"),
    ],
)
def test_parse_stream_json_input_rejects_invalid_input(data: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_stream_json_input(data)


def test_stream_json_input_requires_stream_json_output() -> None:
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="hare-stream-input-") as tmpdir:
        config_dir = Path(tmpdir) / ".hare"
        env["HARE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "hare",
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "text",
            ],
            input=_user_message("hello") + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=str(REPO_ROOT),
        )

    assert proc.returncode == 1
    assert (
        "--input-format=stream-json requires output-format=stream-json"
        in proc.stderr
    )


@pytest.mark.integration
def test_stream_json_input_responds_before_stdin_eof() -> None:
    env = dict(os.environ)
    env["HARE_MODEL_FIXTURE"] = str(FIXTURE)
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"
    with tempfile.TemporaryDirectory(prefix="hare-stream-live-") as tmpdir:
        config_dir = Path(tmpdir) / ".hare"
        env["HARE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hare",
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        try:
            proc.stdin.write(_user_message("say hello") + "\n")
            proc.stdin.flush()

            deadline = time.monotonic() + 20
            events: list[dict[str, object]] = []
            while time.monotonic() < deadline:
                readable, _, _ = select.select(
                    [proc.stdout], [], [], max(0.0, deadline - time.monotonic())
                )
                if not readable:
                    break
                line = proc.stdout.readline()
                if not line:
                    break
                event = json.loads(line)
                events.append(event)
                if event.get("type") == "result":
                    break

            result = next(
                (event for event in events if event.get("type") == "result"), None
            )
            assert result is not None, events
            assert result["result"] == "Hello from fixture."
            assert proc.poll() is None, "process exited before the SDK input stream closed"

            proc.stdin.close()
            assert proc.wait(timeout=20) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
