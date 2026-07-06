#!/usr/bin/env python3
"""Record golden output by driving the TS reference Claude Code against the
mock Anthropic server, so hare can be diffed against the *real* reference.

Usage:
    CLAUDE_TS_CLI="node /path/to/cli.js" python scripts/record_golden.py <case_id>

Reads alignment/cases/**/case.json (matching case_id), boots the mock server
with case.fixture, runs the TS CLI with ANTHROPIC_BASE_URL pointed at it,
normalizes the captured stdout/exit, writes alignment/golden/<...>/golden.json.

The recorded golden is then what hare (driven by the SAME fixture via Layer A)
is compared against in tests/e2e/test_e2e_cases.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]  # hare/
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "alignment"))

from mock_anthropic_server import make_server  # noqa: E402
from golden_normalize import normalize_result, snapshot_files  # noqa: E402

CASES_DIR = REPO / "alignment" / "cases"
GOLDEN_DIR = REPO / "alignment" / "golden"


def find_case(case_id: str) -> Path:
    for p in CASES_DIR.glob("**/case.json"):
        if json.loads(p.read_text(encoding="utf-8"))["case_id"] == case_id:
            return p
    raise SystemExit(f"case not found: {case_id}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: record_golden.py <case_id>")
    ts_cli = os.environ.get("CLAUDE_TS_CLI")
    if not ts_cli:
        raise SystemExit(
            "CLAUDE_TS_CLI is not set. Point it at the TS reference CLI entry, "
            'e.g. CLAUDE_TS_CLI="node /path/to/cli.js" or CLAUDE_TS_CLI=claude'
        )

    case_path = find_case(sys.argv[1])
    case = json.loads(case_path.read_text(encoding="utf-8"))
    fixture = case.get("fixture")
    if not fixture:
        raise SystemExit(
            f"{case['case_id']}: no 'fixture' to drive the TS reference. "
            f"Deterministic CLI cases (kind=deterministic) are frozen by hand, "
            f"not recorded from TS."
        )

    server = make_server(REPO / fixture, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    env = dict(os.environ)
    # Force the reference CLI onto the mock via API-key auth: a stray
    # ANTHROPIC_AUTH_TOKEN (e.g. a DeepSeek token in the shell) would route to a
    # real endpoint and ignore the mock base_url.
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["ANTHROPIC_API_KEY"] = "sk-test-dummy"
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    # Isolated, throwaway config so onboarding/trust never block headless print.
    cfg_dir = tempfile.mkdtemp(prefix="ts-ref-cfg-")
    env["CLAUDE_CONFIG_DIR"] = cfg_dir
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env.setdefault("API_TIMEOUT_MS", "30000")

    # Same seed filesystem the e2e_runner gives hare, so fs-dependent tools
    # (Read/etc.) see identical inputs on both sides of the differential.
    sandbox = tempfile.mkdtemp(prefix="ts-ref-sbx-")
    seeds_root = REPO / "alignment" / "seeds"
    for rel in case.get("fs", {}).get("seed", []):
        src = seeds_root / rel
        dst = Path(sandbox) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dst)

    # Cases may declare a reference-specific argv when CLI flags differ between
    # hare and the TS reference (e.g. hare's --permission-mode bypassPermissions
    # vs claude's --dangerously-skip-permissions).
    argv = case["entrypoint"].get("ts_argv", case["entrypoint"]["argv"])
    files_snapshot: list = []
    try:
        proc = subprocess.run(
            ts_cli.split() + argv,
            input=case["entrypoint"].get("stdin"),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=sandbox,
        )
        # Capture the reference's post-run filesystem before the sandbox is
        # torn down, so file-mutating tools (Write/Edit/...) can be diffed.
        if case.get("policy", {}).get("check_files"):
            files_snapshot = snapshot_files(Path(sandbox))
    finally:
        server.shutdown()
        shutil.rmtree(cfg_dir, ignore_errors=True)
        shutil.rmtree(sandbox, ignore_errors=True)

    expected_code = case.get("expected", {}).get("exit_code", 0)
    golden = {
        "case_id": case["case_id"],
        "status": "ok" if proc.returncode == expected_code else "error",
        "state": {"exit_code": proc.returncode},
        "stdout": proc.stdout,
    }
    if case.get("policy", {}).get("check_files"):
        golden["files"] = files_snapshot
    golden = normalize_result(golden)

    rel = case_path.parent.relative_to(CASES_DIR)
    out = GOLDEN_DIR / rel / "golden.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    if golden["status"] != "ok":
        print(
            f"WARNING: TS exit {proc.returncode} (expected {expected_code}); "
            f"stderr:\n{proc.stderr}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
