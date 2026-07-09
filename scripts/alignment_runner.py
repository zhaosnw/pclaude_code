#!/usr/bin/env python3
"""Phase 1 Python alignment oracle runner."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT_ROOT = PROJECT_ROOT / "legacy_alignment"
CASES_ROOT = ALIGNMENT_ROOT / "cases"


def _ensure_import_paths() -> None:
    project_root = str(PROJECT_ROOT)
    scripts_root = str(Path(__file__).resolve().parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)


def load_case(case_path: Path) -> dict[str, Any]:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    required = ["case_id", "priority", "entrypoint", "mocks", "expected", "policy"]
    for key in required:
        if key not in case:
            raise ValueError(f"Missing required field '{key}' in {case_path}")
    return case


def _phase1_notes(case: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    fs_cfg = case.get("fs", {})
    if fs_cfg.get("writes_allowed_under"):
        notes.append("not_implemented_in_phase1:writes_allowed_under")
    if case.get("mocks", {}).get("network") == "deny":
        notes.append("not_implemented_in_phase1:network_deny")
    if case.get("mocks", {}).get("subprocess") == "deny":
        notes.append("not_implemented_in_phase1:subprocess_deny")
    notes.append("not_implemented_in_phase1:files_state_snapshot")
    return notes


def _prepare_env(case: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_entries = [str(PROJECT_ROOT)]
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    for key, value in case.get("env", {}).items():
        env[key] = value
    return env


def _prepare_cwd(case: dict[str, Any]) -> Path:
    fs_cfg = case.get("fs", {})
    cwd_template = fs_cfg.get("cwd_template")
    if cwd_template:
        candidate = ALIGNMENT_ROOT / cwd_template
        if candidate.exists():
            return candidate
    return PROJECT_ROOT / "hare"


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


def run_case_cli(case: dict[str, Any]) -> dict[str, Any]:
    entrypoint = case["entrypoint"]
    expected = case.get("expected", {})
    env = _prepare_env(case)
    cwd = _prepare_cwd(case)
    stdin_text = entrypoint.get("stdin")

    start = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "hare", *entrypoint["argv"]],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(cwd),
        )
        exit_code = proc.returncode
        expected_code = expected.get("exit_code", 0)
        status = "ok" if exit_code == expected_code else "error"
        stdout = proc.stdout
        stderr = proc.stderr
        error = (
            {
                "kind": "cli_exit",
                "code": str(exit_code),
                "message_normalized": f"expected {expected_code}, got {exit_code}",
            }
            if status == "error"
            else None
        )
    except subprocess.TimeoutExpired:
        status = "timeout"
        exit_code = -1
        stdout = ""
        stderr = "Timeout"
        error = {
            "kind": "timeout",
            "code": "TIMEOUT",
            "message_normalized": "case timed out",
        }
    except Exception as exc:
        status = "error"
        exit_code = -1
        stdout = ""
        stderr = str(exc)
        error = {"kind": "runner_error", "code": "ECLI", "message_normalized": str(exc)}

    stdout_kind = expected.get("stdout_kind", "text")
    return {
        "case_id": case["case_id"],
        "priority": case["priority"],
        "status": status,
        "events": _parse_stdout(stdout, stdout_kind),
        "stdout": stdout,
        "stderr": stderr,
        "files": [],
        "state": {"exit_code": exit_code},
        "error": error,
        "duration_ms": (time.time() - start) * 1000,
        "phase1_notes": _phase1_notes(case),
    }


async def _maybe_await(result: Any) -> Any:
    if asyncio.iscoroutine(result):
        return await result
    return result


def _import_target(target: str) -> Any:
    module_name, _, attr = target.rpartition(".")
    if not module_name or not attr:
        raise ValueError(
            f"module_func must be 'package.module.callable', got '{target}'"
        )
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _to_json_safe(obj: Any) -> Any:
    """Convert object to JSON-serializable form."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return {
            k: _to_json_safe(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    return str(obj)


def run_case_module(case: dict[str, Any]) -> dict[str, Any]:
    entrypoint = case["entrypoint"]
    target = entrypoint["module_func"]
    kwargs = dict(entrypoint.get("module_kwargs", {}))

    # Try generic module dispatch first (handles history.*, task.*, etc.)
    _ensure_import_paths()
    try:
        from alignment_mocks import run_generic_module_case as _generic
    except ImportError:
        _generic = None

    # Check if this is a known generic module_func
    _known_generic_prefixes = (
        "history.", "task.", "token_budget.", "permission.",
        "settings.", "mcp.", "hooks.", "compact.", "cost.",
    )
    if _generic and any(target.startswith(p) for p in _known_generic_prefixes):
        return _generic(case)

    # Legacy path: import target directly
    start = time.time()
    try:
        callable_obj = _import_target(target)
        result = asyncio.run(_maybe_await(callable_obj(**kwargs)))
        status = "ok"
        error = None
        safe_result = _to_json_safe(result)
        events = safe_result if isinstance(safe_result, list) else [safe_result]
    except Exception as exc:
        status = "error"
        error = {
            "kind": "execution_error",
            "code": "EMODULE",
            "message_normalized": str(exc),
        }
        events = []
    return {
        "case_id": case["case_id"],
        "priority": case["priority"],
        "status": status,
        "events": events,
        "stdout": "",
        "stderr": "" if error is None else error["message_normalized"],
        "files": [],
        "state": {},
        "error": error,
        "duration_ms": (time.time() - start) * 1000,
        "phase1_notes": _phase1_notes(case),
    }


def run_case_sdk(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "priority": case["priority"],
        "status": "skipped",
        "events": [],
        "stdout": "",
        "stderr": "SDK harness deferred to Phase 2",
        "files": [],
        "state": {},
        "error": {
            "kind": "skipped",
            "code": "PHASE2_SDK",
            "message_normalized": "SDK harness deferred to Phase 2",
        },
        "duration_ms": 0.0,
        "phase1_notes": ["not_implemented_in_phase1:sdk_harness"],
    }


def run_case_query(case: dict[str, Any]) -> dict[str, Any]:
    """Run a query loop case with scripted model via alignment_mocks."""
    # scripts/ is sibling to the hare/ package, not inside it
    _ensure_import_paths()
    try:
        from alignment_mocks import run_query_case as _run
    except ImportError:
        return {
            "case_id": case["case_id"],
            "priority": case["priority"],
            "status": "error",
            "events": [],
            "stdout": "",
            "stderr": "alignment_mocks not importable",
            "files": [],
            "state": {},
            "error": {
                "kind": "runner_error",
                "code": "EMOCK",
                "message_normalized": "alignment_mocks not importable",
            },
            "duration_ms": 0.0,
            "phase1_notes": ["not_implemented_in_phase1:query_harness"],
        }
    return asyncio.run(_run(case))


def main() -> int:
    import argparse

    _ensure_import_paths()

    parser = argparse.ArgumentParser(description="Run Python alignment cases")
    parser.add_argument("--priority", default="P0")
    parser.add_argument("--out", default=None)
    parser.add_argument("--cases-dir", default=str(CASES_ROOT))
    parser.add_argument("--case", default=None)
    args = parser.parse_args()

    priorities = {item.strip() for item in args.priority.split(",") if item.strip()}
    cases_dir = Path(args.cases_dir)

    if args.case:
        case_paths = [
            path
            for path in sorted(cases_dir.glob("**/case.json"))
            if json.loads(path.read_text(encoding="utf-8")).get("case_id") == args.case
        ]
    else:
        case_paths = sorted(cases_dir.glob("**/case.json"))

    results: list[dict[str, Any]] = []
    for case_path in case_paths:
        case = load_case(case_path)
        if case["priority"] not in priorities:
            continue
        kind = case["entrypoint"]["kind"]
        if kind == "cli":
            result = run_case_cli(case)
        elif kind in {"unit", "module"}:
            result = run_case_module(case)
        elif kind == "sdk":
            result = run_case_sdk(case)
        elif kind == "query":
            result = run_case_query(case)
        else:
            raise ValueError(f"Unsupported entrypoint kind: {kind}")
        results.append(result)

    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    try:
        for result in results:
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
    finally:
        if args.out:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
