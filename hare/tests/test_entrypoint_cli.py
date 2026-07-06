from __future__ import annotations

from pathlib import Path


def test_run_ts_frontend_uses_callers_cwd(monkeypatch) -> None:
    from hare.entrypoints import cli as entry_cli

    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def fake_run(argv, cwd=None, env=None):
        seen["argv"] = argv
        seen["cwd"] = cwd
        seen["env"] = env
        return _Result()

    monkeypatch.setattr(entry_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(entry_cli, "_project_root", lambda: Path("/repo"))
    monkeypatch.setattr(entry_cli.os, "getcwd", lambda: "/workspace/project")
    monkeypatch.setattr(Path, "exists", lambda self: True)

    exit_code = entry_cli._run_ts_frontend(["--debug"])

    assert exit_code == 0
    assert seen["cwd"] == "/workspace/project"


def test_run_ts_frontend_enables_python_backend(monkeypatch) -> None:
    from hare.entrypoints import cli as entry_cli

    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def fake_run(argv, cwd=None, env=None):
        seen["env"] = env
        return _Result()

    monkeypatch.setattr(entry_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(entry_cli, "_project_root", lambda: Path("/repo"))
    monkeypatch.setattr(entry_cli.os, "getcwd", lambda: "/workspace/project")
    monkeypatch.setattr(Path, "exists", lambda self: True)

    exit_code = entry_cli._run_ts_frontend([])

    assert exit_code == 0
    assert seen["env"]["HARE_PYTHON_BACKEND"] == "1"
