#!/usr/bin/env python3
"""Dogfood: drive hare for real in an isolated git repo with isolated config.

Scenarios: file change, resume, permission denial, MCP tool call, hook block.
The model is the deterministic mock (no network), but everything else — CLI,
settings, MCP subprocess, hook subprocess, session storage — is the real path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path("/Users/midea/midea/pclaude_code")
sys.path.insert(0, str(REPO / "scripts"))
from mock_anthropic_server import make_server  # noqa: E402

BASE = Path("/private/tmp/claude-501/-Users-midea-midea-pclaude-code/"
            "feebeb80-625a-4f63-85e4-cf7951fd1452/scratchpad/dogfood-run")
SEEDS = REPO / "hare" / "alignment" / "seeds"
results = []


def hare(fixture, argv, sandbox, cfg, extra_env=None):
    fx = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(fixture, fx)
    fx.close()
    server = make_server(fx.name, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    env = dict(os.environ)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.update(
        PYTHONPATH=str(REPO),
        ANTHROPIC_API_KEY="sk-test-dummy",
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}",
        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
        HOME=str(cfg), HARE_CONFIG_DIR=str(cfg), CLAUDE_CONFIG_DIR=str(cfg),
    )
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-m", "hare", *argv],
        capture_output=True, text=True, timeout=120, env=env, cwd=sandbox,
        stdin=subprocess.DEVNULL,
    )
    server.shutdown()
    os.unlink(fx.name)
    return proc


def new_repo(name):
    sbx = BASE / name / "repo"
    cfg = BASE / name / "cfg"
    sbx.mkdir(parents=True)
    cfg.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=sbx, check=True)
    return sbx, cfg


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True)

# --- 1. File change: read then edit a real file -----------------------------
sbx, cfg = new_repo("filechange")
(sbx / "greeting.txt").write_text("hello world\n")
fx = {"kind": "scripted", "responses": [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "greeting.txt"}}],
     "usage": {"input_tokens": 40, "output_tokens": 10}},
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t2", "name": "Edit", "input": {
            "file_path": "greeting.txt", "old_string": "world", "new_string": "dogfood"}}],
     "usage": {"input_tokens": 70, "output_tokens": 12}},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Updated the greeting."}],
     "usage": {"input_tokens": 95, "output_tokens": 6}}]}
p = hare(fx, ["-p", "update the greeting", "--permission-mode", "bypassPermissions"], sbx, cfg)
content = (sbx / "greeting.txt").read_text().strip()
record("file change", p.returncode == 0 and content == "hello dogfood",
       f"exit={p.returncode} content={content!r}")

# --- 2. Resume: turn 1 creates, turn 2 (--resume) edits ----------------------
sbx, cfg = new_repo("resume")
fx1 = {"kind": "scripted", "responses": [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {
            "file_path": "notes.txt", "content": "first turn\n"}}],
     "usage": {"input_tokens": 40, "output_tokens": 10}},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Created notes.txt."}],
     "usage": {"input_tokens": 70, "output_tokens": 6}}]}
p1 = hare(fx1, ["-p", "create notes", "--output-format", "json",
                "--permission-mode", "bypassPermissions"], sbx, cfg)
sid = None
try:
    sid = json.loads(p1.stdout).get("session_id")
except json.JSONDecodeError:
    pass
fx2 = {"kind": "scripted", "responses": [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t2", "name": "Edit", "input": {
            "file_path": "notes.txt", "old_string": "first", "new_string": "second"}}],
     "usage": {"input_tokens": 60, "output_tokens": 10}},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Updated notes.txt."}],
     "usage": {"input_tokens": 90, "output_tokens": 6}}]}
p2 = hare(fx2, ["-p", "update it", "--resume", sid or "missing", "--output-format", "json",
                "--permission-mode", "bypassPermissions"], sbx, cfg) if sid else None
content = (sbx / "notes.txt").read_text().strip() if (sbx / "notes.txt").exists() else None
same_session = False
if p2:
    try:
        same_session = json.loads(p2.stdout).get("session_id") == sid
    except json.JSONDecodeError:
        pass
record("resumed task",
       bool(sid) and p2 is not None and p2.returncode == 0
       and content == "second turn" and same_session,
       f"session={sid} exit2={p2.returncode if p2 else 'n/a'} content={content!r} "
       f"same_session={same_session}")

# --- 3. Permission denial: deny rule blocks the mutation ---------------------
sbx, cfg = new_repo("denial")
(sbx / ".hare").mkdir()
(sbx / ".hare" / "settings.json").write_text(
    json.dumps({"permissions": {"deny": ["Bash(rm *)"]}}))
(sbx / "precious.txt").write_text("do not delete\n")
fx = {"kind": "scripted", "responses": [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {
            "command": "rm precious.txt", "description": "delete the file"}}],
     "usage": {"input_tokens": 40, "output_tokens": 12}},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "I could not delete it."}],
     "usage": {"input_tokens": 80, "output_tokens": 7}}]}
p = hare(fx, ["-p", "delete precious.txt", "--output-format", "json"], sbx, cfg)
survived = (sbx / "precious.txt").exists()
denials = []
try:
    denials = json.loads(p.stdout).get("permission_denials", [])
except json.JSONDecodeError:
    pass
record("permission denial", survived and len(denials) == 1,
       f"file_survived={survived} denials={len(denials)}")

# --- 4. MCP tool: real stdio subprocess -------------------------------------
sbx, cfg = new_repo("mcp")
shutil.copy2(SEEDS / "mcp_echo_server.py", sbx / "mcp_echo_server.py")
(sbx / "mcp.json").write_text(json.dumps(
    {"mcpServers": {"echo": {"command": sys.executable, "args": ["mcp_echo_server.py"]}}}))
fx = {"kind": "scripted", "responses": [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "mcp__echo__echo",
         "input": {"text": "dogfood-ping"}}],
     "usage": {"input_tokens": 45, "output_tokens": 12}},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "The echo server replied."}],
     "usage": {"input_tokens": 90, "output_tokens": 6}}]}
p = hare(fx, ["-p", "echo dogfood-ping", "--mcp-config", "mcp.json",
              "--permission-mode", "bypassPermissions"], sbx, cfg)
marker = sbx / "mcp_echo_called.txt"
called = marker.exists() and "dogfood-ping" in marker.read_text()
record("MCP tool", p.returncode == 0 and called,
       f"exit={p.returncode} server_invoked={called} "
       f"marker={marker.read_text().strip() if marker.exists() else None}")

# --- 5. Hook block: PreToolUse hook stops a real command ---------------------
sbx, cfg = new_repo("hook")
shutil.copy2(SEEDS / "hook_block_bash.py", sbx / "hook_block_bash.py")
(sbx / ".hare").mkdir()
(sbx / ".hare" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 hook_block_bash.py"}]}]}}))
fx = {"kind": "scripted", "responses": [
    {"stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {
            "command": "touch should_not_exist.txt", "description": "create a file"}}],
     "usage": {"input_tokens": 40, "output_tokens": 12}},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "The hook blocked it."}],
     "usage": {"input_tokens": 80, "output_tokens": 7}}]}
p = hare(fx, ["-p", "touch a file", "--output-format", "json"], sbx, cfg)
hook_ran = (sbx / "hook_pretool_ran.txt").exists()
blocked = not (sbx / "should_not_exist.txt").exists()
record("hook block", p.returncode == 0 and hook_ran and blocked,
       f"exit={p.returncode} hook_ran={hook_ran} command_blocked={blocked}")

print()
failed = [n for n, ok, _ in results if not ok]
print(f"{len(results) - len(failed)}/{len(results)} scenarios passed"
      + (f"; FAILED: {failed}" if failed else ""))
# Exit non-zero so `make dogfood` actually gates on the result.
sys.exit(1 if failed else 0)
