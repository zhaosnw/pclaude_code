import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HARE_ALIGNMENT = REPO / "alignment"
FIXTURE = HARE_ALIGNMENT / "fixtures" / "single_turn_hello.json"


@pytest.mark.integration
def test_print_mode_uses_fixture_and_is_deterministic():
    env = dict(os.environ)
    env["HARE_MODEL_FIXTURE"] = str(FIXTURE)
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"
    # 跑两次,输出必须逐字节一致,且包含 fixture 文本
    outs = []
    with tempfile.TemporaryDirectory(prefix="hare-smoke-subprocess-") as tmpdir:
        env["HOME"] = tmpdir
        config_dir = Path(tmpdir) / ".hare"
        env["HARE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        for _ in range(2):
            proc = subprocess.run(
                [sys.executable, "-m", "hare", "-p", "say hi"],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
                cwd=str(REPO),
            )
            assert proc.returncode == 0, proc.stderr
            outs.append(proc.stdout)
    assert "Hello from fixture." in outs[0], outs[0]
    assert outs[0] == outs[1], "subprocess output not deterministic"


@pytest.mark.integration
@pytest.mark.parametrize("empty_prompt", ["", "   "])
def test_empty_or_whitespace_print_prompt_errors_without_model_call(empty_prompt):
    """An empty/whitespace `-p` must (1) stay non-interactive (no REPL), and
    (2) error cleanly WITHOUT calling the model — matching Claude Code, which
    rejects empty/whitespace prompts. Found via live testing: hare dropped to the
    REPL on "" and sent "   " to the model (13 turns / occasional timeout)."""
    env = dict(os.environ)
    # Point the fixture model at a path that would raise if invoked, to prove no
    # model call happens (the error must come before any model interaction).
    env["HARE_MODEL_FIXTURE"] = str(FIXTURE)
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"
    with tempfile.TemporaryDirectory(prefix="hare-smoke-subprocess-") as tmpdir:
        env["HOME"] = tmpdir
        config_dir = Path(tmpdir) / ".hare"
        env["HARE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        proc = subprocess.run(
            [sys.executable, "-m", "hare", "-p", empty_prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=30,
            env=env, cwd=str(REPO), stdin=subprocess.DEVNULL,
        )
    combined = proc.stdout + proc.stderr
    assert "Hare Python Port" not in combined, f"dropped to REPL:\n{combined}"
    assert "Type /help" not in combined, f"dropped to REPL:\n{combined}"
    assert proc.returncode != 0, "empty/whitespace prompt should be an error"
    assert "Hello from fixture." not in proc.stdout, "model was called for an empty prompt"
    assert "rror" in combined or "input" in combined.lower(), f"no clean error:\n{combined}"


@pytest.mark.integration
def test_prompt_read_from_stdin_when_piped():
    """`echo "..." | hare` (no -p, piped stdin) must read stdin as the prompt and
    run non-interactively — matching Claude Code. (Was a defect: hare ignored
    stdin and dropped to the REPL.)"""
    env = dict(os.environ)
    env["HARE_MODEL_FIXTURE"] = str(FIXTURE)
    env["ANTHROPIC_API_KEY"] = "test-key-not-used"
    with tempfile.TemporaryDirectory(prefix="hare-smoke-subprocess-") as tmpdir:
        env["HOME"] = tmpdir
        config_dir = Path(tmpdir) / ".hare"
        env["HARE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        proc = subprocess.run(
            [sys.executable, "-m", "hare", "--output-format", "json"],
            input="what is this project?",
            capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO),
        )
    assert "Hare Python Port" not in proc.stdout, f"dropped to REPL:\n{proc.stdout}"
    assert proc.returncode == 0, proc.stderr
    import json as _json
    obj = _json.loads(proc.stdout)
    assert obj["type"] == "result" and obj["result"] == "Hello from fixture."
