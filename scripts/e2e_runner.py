#!/usr/bin/env python3
"""Self-contained CLI E2E runner for the nested ``hare/`` project.

Distinct from ``scripts/alignment_runner.py`` (which targets the outer repo's
module-level alignment corpus). This one runs the *real* CLI as a subprocess
(``python -m hare``) under a deterministic fixture model + filesystem sandbox,
and is the substrate for both Layer A (fixture replay) and Layer B (mock
Anthropic server) E2E.

A case dict:
    {
      "case_id": str,
      "entrypoint": {"argv": [...], "stdin": str|None},
      "fixture": "hare/alignment/fixtures/<name>.json"  # optional canonical form
      "fs": {"seed": ["README.md", ...]},           # optional; copied from hare/alignment/seeds/
      "env": {...},                                  # optional extra env
      "expected": {"exit_code": int, "stdout_kind": "text"|"json"|"ndjson"},
      "policy": {...},
    }
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HARE_ROOT = REPO_ROOT / "hare"
FIXTURES_ROOT = HARE_ROOT / "alignment" / "fixtures"
SEEDS_ROOT = HARE_ROOT / "alignment" / "seeds"

sys.path.insert(0, str(HARE_ROOT / "alignment"))
from golden_normalize import snapshot_files  # noqa: E402


def _resolve_hare_fixture_path(fixture: str) -> Path:
    path = Path(fixture)
    if path.is_absolute():
        return path
    if path.parts[:2] == ("hare", "alignment"):
        return (REPO_ROOT / path).resolve()
    raise ValueError(
        "fixture path must use the canonical 'hare/alignment/...' prefix: "
        f"{fixture}"
    )


def _parse_stdout(stdout: str, stdout_kind: str) -> list[Any]:
    if stdout_kind not in {"json", "ndjson"}:
        return []
    events: list[Any] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"raw": line})
    return events


def _prepare_env(
    case: dict[str, Any],
    *,
    base_url: str | None,
    sandbox_root: Path,
) -> dict[str, str]:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_entries = [str(REPO_ROOT)]
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    for key, value in case.get("env", {}).items():
        env[key] = value
    # Layer A: deterministic in-process fixture replay
    fixture = case.get("fixture")
    if fixture and base_url is None:
        env["HARE_MODEL_FIXTURE"] = str(_resolve_hare_fixture_path(fixture))
    config_root = str((sandbox_root / ".hare").resolve())
    env["HARE_CONFIG_DIR"] = config_root
    env["CLAUDE_CONFIG_DIR"] = config_root
    # Layer B: real HTTP path against a mock Anthropic server
    if base_url is not None:
        env["ANTHROPIC_BASE_URL"] = base_url
        env.pop("HARE_MODEL_FIXTURE", None)
        # The SDK (httpx) honors proxy env vars — force a direct connection to
        # the local mock, else 127.0.0.1 gets routed through an ambient proxy.
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
    env.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
    env["TERM"] = "dumb"
    return env


def _make_sandbox(case: dict[str, Any]) -> Path:
    sandbox = Path(tempfile.mkdtemp(prefix="hare-e2e-"))
    for rel in case.get("fs", {}).get("seed", []):
        src = SEEDS_ROOT / rel
        dst = sandbox / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dst)
    return sandbox


def _snapshot(root: Path) -> list[dict[str, Any]]:
    return snapshot_files(root)


def run_case(case: dict[str, Any], *, base_url: str | None = None) -> dict[str, Any]:
    """Run one CLI E2E case. If base_url is set, drives the real HTTP path
    (Layer B) instead of the in-process fixture (Layer A)."""
    entrypoint = case["entrypoint"]
    expected = case.get("expected", {})
    sandbox = _make_sandbox(case)
    env = _prepare_env(case, base_url=base_url, sandbox_root=sandbox)
    stdin_text = entrypoint.get("stdin")

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "hare", *entrypoint["argv"]],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(sandbox),
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
        status = "ok" if exit_code == expected.get("exit_code", 0) else "error"
    except subprocess.TimeoutExpired:
        exit_code, stdout, stderr, status = -1, "", "Timeout", "timeout"
    except Exception as exc:  # pragma: no cover - defensive
        exit_code, stdout, stderr, status = -1, "", str(exc), "error"

    files_snapshot = _snapshot(sandbox)
    sandbox_root = str(sandbox)
    shutil.rmtree(sandbox, ignore_errors=True)

    stdout_kind = expected.get("stdout_kind", "text")
    return {
        "case_id": case["case_id"],
        "priority": case.get("priority", "P2"),
        "status": status,
        "events": _parse_stdout(stdout, stdout_kind),
        "stdout": stdout,
        "stderr": stderr,
        "files": files_snapshot,
        "state": {"exit_code": exit_code},
        "sandbox_root": sandbox_root,
    }


if __name__ == "__main__":
    case_path = Path(sys.argv[1])
    result = run_case(json.loads(case_path.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False))
