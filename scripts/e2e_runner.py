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
      # or, for session scenarios, a sequential list of entrypoints. Later
      # invocations may reference ${session_id[N]} from an earlier JSON result.
      "invocations": [{"argv": [...], "stdin": str|None}, ...],
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
import re
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
SESSION_ID_REFERENCE_RE = re.compile(r"\$\{session_id\[(\d+)\]\}")

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
    # Hare emits pretty-printed JSON for --output-format json, while
    # stream-json is line-delimited. Prefer the complete document so a normal
    # JSON result does not get misclassified as a sequence of raw lines.
    if stdout_kind == "json":
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            pass
        else:
            return parsed if isinstance(parsed, list) else [parsed]
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
    for entry in case.get("fs", {}).get("seed", []):
        # String entries copy seeds/<rel> to sandbox/<rel>. Dict entries
        # ({"src": ..., "dst": ...}) let cases place a shared seed at a
        # case-specific sandbox path (e.g. a settings file at
        # .claude/settings.json) without colliding in the seeds root.
        if isinstance(entry, dict):
            rel_src, rel_dst = entry["src"], entry["dst"]
        else:
            rel_src = rel_dst = entry
        src = SEEDS_ROOT / rel_src
        dst = sandbox / rel_dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dst)
    return sandbox


def _snapshot(root: Path) -> list[dict[str, Any]]:
    return snapshot_files(root)


def _invocations(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize legacy single-entrypoint and multi-invocation case schemas."""
    has_entrypoint = "entrypoint" in case
    has_invocations = "invocations" in case
    if has_entrypoint == has_invocations:
        raise ValueError("case must define exactly one of 'entrypoint' or 'invocations'")
    if has_entrypoint:
        entrypoint = case["entrypoint"]
        if not isinstance(entrypoint, dict):
            raise ValueError("case.entrypoint must be an object")
        return [entrypoint]

    invocations = case["invocations"]
    if not isinstance(invocations, list) or not invocations:
        raise ValueError("case.invocations must be a non-empty array")
    if not all(isinstance(invocation, dict) for invocation in invocations):
        raise ValueError("every case invocation must be an object")
    return invocations


def _substitute_session_ids(value: str, session_ids: list[str | None]) -> str:
    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(session_ids) or not session_ids[index]:
            raise ValueError(f"session_id[{index}] is unavailable for this invocation")
        return session_ids[index] or ""

    return SESSION_ID_REFERENCE_RE.sub(replace, value)


def _session_id_from_stdout(stdout: str, stdout_kind: str) -> str | None:
    for event in _parse_stdout(stdout, stdout_kind):
        if isinstance(event, dict) and isinstance(event.get("session_id"), str):
            return event["session_id"]
    return None


def run_case(case: dict[str, Any], *, base_url: str | None = None) -> dict[str, Any]:
    """Run one CLI E2E case. If base_url is set, drives the real HTTP path
    (Layer B) instead of the in-process fixture (Layer A)."""
    expected = case.get("expected", {})
    sandbox = _make_sandbox(case)
    env = _prepare_env(case, base_url=base_url, sandbox_root=sandbox)
    invocation_results: list[dict[str, Any]] = []
    session_ids: list[str | None] = []

    for invocation in _invocations(case):
        argv = invocation.get("argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("every invocation argv must be an array of strings")
        invocation_expected = {**expected, **invocation.get("expected", {})}
        stdout_kind = invocation_expected.get("stdout_kind", "text")
        rendered_argv = [_substitute_session_ids(arg, session_ids) for arg in argv]
        stdin_text = invocation.get("stdin")
        if isinstance(stdin_text, str):
            stdin_text = _substitute_session_ids(stdin_text, session_ids)

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "hare", *rendered_argv],
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
                cwd=str(sandbox),
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            status = "ok" if exit_code == invocation_expected.get("exit_code", 0) else "error"
        except subprocess.TimeoutExpired:
            exit_code, stdout, stderr, status = -1, "", "Timeout", "timeout"
        except Exception as exc:  # pragma: no cover - defensive
            exit_code, stdout, stderr, status = -1, "", str(exc), "error"

        session_id = _session_id_from_stdout(stdout, stdout_kind)
        session_ids.append(session_id)
        invocation_results.append(
            {
                "argv": rendered_argv,
                "status": status,
                "events": _parse_stdout(stdout, stdout_kind),
                "stdout": stdout,
                "stderr": stderr,
                "state": {"exit_code": exit_code},
                "session_id": session_id,
            }
        )

    final = invocation_results[-1]
    status = "ok" if all(result["status"] == "ok" for result in invocation_results) else "error"

    files_snapshot = _snapshot(sandbox)
    sandbox_root = str(sandbox)
    shutil.rmtree(sandbox, ignore_errors=True)

    stdout_kind = expected.get("stdout_kind", "text")
    return {
        "case_id": case["case_id"],
        "priority": case.get("priority", "P2"),
        "status": status,
        "events": final["events"],
        "stdout": final["stdout"],
        "stderr": final["stderr"],
        "files": files_snapshot,
        "state": final["state"],
        "invocations": invocation_results,
        "sandbox_root": sandbox_root,
    }


if __name__ == "__main__":
    case_path = Path(sys.argv[1])
    result = run_case(json.loads(case_path.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False))
