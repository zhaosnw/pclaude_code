import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from e2e_runner import run_case  # noqa: E402
import e2e_runner  # noqa: E402


FIXTURE = "hare/alignment/fixtures/single_turn_hello.json"


def test_run_case_injects_fixture_and_snapshots_files():
    case = {
        "case_id": "smoke.hello",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": FIXTURE,
        "expected": {"exit_code": 0, "stdout_kind": "text"},
        "policy": {},
    }
    result = run_case(case)
    assert result["state"]["exit_code"] == 0, result["stderr"]
    assert "Hello from fixture." in result["stdout"]
    # files 是 (相对路径, sha256) 的快照列表,不再恒为空
    assert isinstance(result["files"], list)
    # sandbox_root 被透出供 normalizer 抹路径
    assert result["sandbox_root"]


def test_run_case_rejects_legacy_fixture_prefix():
    case = {
        "case_id": "smoke.legacy-fixture",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": "alignment/fixtures/single_turn_hello.json",
        "expected": {"exit_code": 0, "stdout_kind": "text"},
        "policy": {},
    }
    with pytest.raises(ValueError, match="hare/alignment"):
        run_case(case)


def test_run_case_seeds_files_into_sandbox():
    # seed README.md 进沙箱;由于 fixture 不读它,这里只验证种子机制不报错且文件被快照
    case = {
        "case_id": "smoke.seed",
        "priority": "P1",
        "entrypoint": {"argv": ["-p", "hi"]},
        "fixture": FIXTURE,
        "fs": {"seed": ["README.md"]},
        "expected": {"exit_code": 0},
        "policy": {},
    }
    result = run_case(case)
    snap_paths = {f["path"] for f in result["files"]}
    assert "README.md" in snap_paths


def test_run_case_reuses_previous_json_session_id_for_later_invocation(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        session_id = "first-session" if len(calls) == 1 else "second-session"
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"session_id": session_id}), stderr="")

    monkeypatch.setattr(e2e_runner.subprocess, "run", fake_run)
    case = {
        "case_id": "session.resume-smoke",
        "priority": "P1",
        "invocations": [
            {
                "argv": ["-p", "remember pineapple", "--output-format", "json"],
                "expected": {"stdout_kind": "json"},
            },
            {
                "argv": ["--resume", "${session_id[0]}", "-p", "what word?", "--output-format", "json"],
                "expected": {"stdout_kind": "json"},
            },
        ],
        "expected": {"exit_code": 0, "stdout_kind": "json"},
        "policy": {},
    }

    result = run_case(case)

    assert calls[1][-6:] == ["--resume", "first-session", "-p", "what word?", "--output-format", "json"]
    assert result["state"]["exit_code"] == 0
    assert result["invocations"][0]["session_id"] == "first-session"


def test_parse_stdout_accepts_pretty_printed_json_result():
    parsed = e2e_runner._parse_stdout(
        '{\n  "type": "result",\n  "session_id": "session-1"\n}\n', "json"
    )

    assert parsed == [{"type": "result", "session_id": "session-1"}]
