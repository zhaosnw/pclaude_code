"""Sandbox execution wiring (2.1.88 shouldUseSandbox + sandbox-adapter).

hare had the sandbox decision skeleton (BashTool/sandbox.py) and a SandboxManager
config holder, but nothing consulted the real config and BashTool never applied a
sandbox. These tests pin:

  * should_use_sandbox_for_input mirrors TS shouldUseSandbox(input) — gated on
    SandboxManager.is_sandboxing_enabled(), the dangerouslyDisableSandbox escape
    hatch, and user-excluded commands.
  * wrap_command_for_sandbox builds a real macOS seatbelt invocation when
    sandboxing is on, and is a strict no-op otherwise (zero regression for the
    default, sandbox-off path).
  * BashTool advertises dangerouslyDisableSandbox and runs unsandboxed by default.

Sandboxing is OFF by default, so normal Bash is completely unaffected.
"""

import asyncio
import os
import shutil
import sys

import pytest

from hare.utils.sandbox.sandbox_adapter import (
    SandboxConfig,
    SandboxManager,
    build_seatbelt_profile,
    wrap_command_for_sandbox,
)
from hare.tools_impl.BashTool.sandbox import should_use_sandbox_for_input


@pytest.fixture(autouse=True)
def _reset_sandbox_state(monkeypatch):
    """Sandboxing is global state — always restore after each test. Also pin the
    settings-derived enablement to OFF so enablement is controlled purely by the
    global flag (deterministic regardless of the user's real settings.json)."""
    import hare.utils.sandbox.sandbox_adapter as adapter

    monkeypatch.setattr(adapter, "_settings_sandbox_enabled", lambda: False)
    prev = SandboxManager._global_sandbox_enabled
    prev_unsb = SandboxManager._global_unsandboxed_commands_allowed
    SandboxManager.set_sandboxing_enabled(False)
    yield
    SandboxManager.set_sandboxing_enabled(prev)
    SandboxManager.set_unsandboxed_commands_allowed(prev_unsb)


# ---------------------------------------------------------------------------
# decision: should_use_sandbox_for_input
# ---------------------------------------------------------------------------

def test_disabled_by_default():
    assert SandboxManager.is_sandboxing_enabled() is False
    assert should_use_sandbox_for_input({"command": "ls"}) is False


def test_enabled_sandboxes_a_command():
    SandboxManager.set_sandboxing_enabled(True)
    assert should_use_sandbox_for_input({"command": "ls"}) is True


def test_no_command_not_sandboxed():
    SandboxManager.set_sandboxing_enabled(True)
    assert should_use_sandbox_for_input({}) is False


def test_disable_flag_only_when_unsandboxed_allowed():
    SandboxManager.set_sandboxing_enabled(True)
    # disable requested but policy does NOT allow unsandboxed → still sandboxed
    SandboxManager.set_unsandboxed_commands_allowed(False)
    assert (
        should_use_sandbox_for_input(
            {"command": "ls", "dangerously_disable_sandbox": True}
        )
        is True
    )
    # disable requested AND policy allows it → not sandboxed
    SandboxManager.set_unsandboxed_commands_allowed(True)
    assert (
        should_use_sandbox_for_input(
            {"command": "ls", "dangerously_disable_sandbox": True}
        )
        is False
    )


def test_excluded_command_not_sandboxed(monkeypatch):
    SandboxManager.set_sandboxing_enabled(True)
    # user-configured excluded command from settings
    import hare.tools_impl.BashTool.sandbox as sb

    # ":*" prefix syntax now works (parser fixed to detect prefix before wildcard).
    monkeypatch.setattr(sb, "_get_user_excluded_commands", lambda: ["docker:*"])
    assert should_use_sandbox_for_input({"command": "docker ps"}) is False
    assert should_use_sandbox_for_input({"command": "ls -la"}) is True


def test_bash_permission_rule_prefix_before_wildcard():
    """':*' is a prefix rule (matches 'docker ps'), not a literal wildcard."""
    from hare.tools_impl.BashTool.bash_permissions import bash_permission_rule
    from hare.tools_impl.BashTool.sandbox import _contains_excluded_command

    r = bash_permission_rule("docker:*")
    assert getattr(r, "type", None) == "prefix"
    assert _contains_excluded_command("docker ps", ["docker:*"]) is True
    assert _contains_excluded_command("ls", ["docker:*"]) is False


def test_bare_colon_star_is_not_a_prefix():
    """TS /^(.+):\\*$/ requires a non-empty prefix, so a bare ':*' is NOT a
    prefix rule (would otherwise match everything)."""
    from hare.tools_impl.BashTool.bash_permissions import bash_permission_rule

    r = bash_permission_rule(":*")
    assert getattr(r, "type", None) != "prefix"


def test_unsandboxed_lockdown_via_settings(monkeypatch):
    """When sandboxing is on and settings.sandbox.allowUnsandboxedCommands=false,
    dangerouslyDisableSandbox must NOT escape the sandbox (TS reads it live)."""
    import hare.utils.sandbox.sandbox_adapter as adapter

    SandboxManager.set_sandboxing_enabled(True)
    SandboxManager._global_unsandboxed_commands_allowed = None  # defer to settings
    monkeypatch.setattr(
        adapter,
        "_settings_sandbox_bool",
        lambda key, default: (False if key == "allowUnsandboxedCommands" else default),
    )
    assert SandboxManager.are_unsandboxed_commands_allowed() is False
    assert (
        should_use_sandbox_for_input(
            {"command": "ls", "dangerously_disable_sandbox": True}
        )
        is True
    )


def test_unsandboxed_default_true_when_no_setting(monkeypatch):
    import hare.utils.sandbox.sandbox_adapter as adapter

    SandboxManager._global_unsandboxed_commands_allowed = None
    monkeypatch.setattr(adapter, "_settings_sandbox_bool", lambda key, default: default)
    assert SandboxManager.are_unsandboxed_commands_allowed() is True


def test_profile_allows_dev_fd():
    """Process substitution / tee to /dev/fd/N must be writable under sandbox."""
    prof = build_seatbelt_profile(cwd="/Users/x/proj", allow_write=["/Users/x/proj"])
    assert '(subpath "/dev/fd")' in prof


# ---------------------------------------------------------------------------
# wrapper: wrap_command_for_sandbox
# ---------------------------------------------------------------------------

def test_wrap_is_noop_when_disabled():
    argv = ["bash", "-c", "echo hi"]
    out = wrap_command_for_sandbox(argv, cwd="/tmp", config=SandboxConfig())
    assert out == argv  # unchanged → zero regression


def test_seatbelt_profile_allows_cwd_denies_default():
    prof = build_seatbelt_profile(cwd="/Users/x/proj", allow_write=["/Users/x/proj"])
    assert "(version 1)" in prof
    assert "(allow default)" in prof
    assert "(deny file-write*)" in prof
    assert "/Users/x/proj" in prof


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="seatbelt sandbox-exec only available on macOS",
)
def test_wrap_builds_sandbox_exec_argv_on_darwin():
    cfg = SandboxConfig(enabled=True, filesystem_allow_write=["/Users/x/proj"])
    argv = ["bash", "-c", "echo hi"]
    out = wrap_command_for_sandbox(argv, cwd="/Users/x/proj", config=cfg)
    assert out[0] == "sandbox-exec"
    assert "-p" in out
    assert out[-3:] == argv


# ---------------------------------------------------------------------------
# enforcement (darwin only): the seatbelt profile actually restricts writes
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="seatbelt enforcement only testable on macOS with sandbox-exec",
)
def test_seatbelt_enforces_write_restriction(tmp_path):
    import subprocess

    allowed = tmp_path / "allowed"
    denied = tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()
    cfg = SandboxConfig(
        enabled=True,
        filesystem_allow_write=[os.path.realpath(str(allowed)), "/dev"],
    )

    def run(target):
        argv = wrap_command_for_sandbox(
            ["bash", "-c", f"echo hi > {target}/f.txt"],
            cwd=os.path.realpath(str(allowed)),
            config=cfg,
        )
        return subprocess.run(argv, capture_output=True)

    ok = run(os.path.realpath(str(allowed)))
    if (
        ok.returncode == 71
        and b"sandbox_apply: Operation not permitted" in ok.stderr
    ):
        pytest.skip(
            "sandbox-exec is present but sandbox_apply is disallowed in this environment"
        )
    assert ok.returncode == 0, ok.stderr
    assert (allowed / "f.txt").exists()

    bad = run(os.path.realpath(str(denied)))
    assert bad.returncode != 0
    assert not (denied / "f.txt").exists()


# ---------------------------------------------------------------------------
# BashTool wiring
# ---------------------------------------------------------------------------

def test_bash_schema_advertises_disable_sandbox():
    from hare.tools_impl.BashTool.bash_tool import BashTool

    props = BashTool.input_schema()["properties"]
    assert "dangerously_disable_sandbox" in props


def test_bash_runs_unsandboxed_by_default():
    from hare.tools_impl.BashTool.bash_tool import BashTool

    async def go():
        res = await BashTool.call({"command": "echo SANDBOX-OFF"}, None)
        data = res.data if hasattr(res, "data") else str(res)
        assert "SANDBOX-OFF" in data

    asyncio.run(go())
