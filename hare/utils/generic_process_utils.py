"""
Cross-platform process tree helpers (ps-style).

Port of: src/utils/genericProcessUtils.ts
"""

from __future__ import annotations

import os
import platform
import re

from hare.utils.exec_file_no_throw import exec_file_no_throw_with_cwd
from hare.utils.exec_file_no_throw_portable import exec_sync_with_defaults_deprecated


def is_process_running(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


async def get_ancestor_pids_async(pid: str | int, max_depth: int = 10) -> list[int]:
    pid_s = str(pid)
    if platform.system() == "Windows":
        script = f"""
$pid = {pid_s}
$ancestors = @()
for ($i = 0; $i -lt {max_depth}; $i++) {{
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid" -ErrorAction SilentlyContinue
  if (-not $proc -or -not $proc.ParentProcessId -or $proc.ParentProcessId -eq 0) {{ break }}
  $pid = $proc.ParentProcessId
  $ancestors += $pid
}}
$ancestors -join ','
""".strip()
        r = await exec_file_no_throw_with_cwd(
            "powershell.exe",
            ["-NoProfile", "-Command", script],
            timeout=3000,
            cwd=None,
            preserve_output_on_error=False,
        )
        if r["code"] != 0 or not (r["stdout"] or "").strip():
            return []
        return [int(x) for x in re.split(r",+", r["stdout"].strip()) if x.isdigit()]

    script = (
        f"pid={pid_s}; for i in $(seq 1 {max_depth}); do "
        f'ppid=$(ps -o ppid= -p $pid 2>/dev/null | tr -d " "); '
        f'if [ -z "$ppid" ] || [ "$ppid" = "0" ] || [ "$ppid" = "1" ]; then break; fi; '
        f"echo $ppid; pid=$ppid; done"
    )
    r = await exec_file_no_throw_with_cwd(
        "sh", ["-c", script], timeout=3000, cwd=None, preserve_output_on_error=False
    )
    if r["code"] != 0 or not (r["stdout"] or "").strip():
        return []
    return [int(x) for x in r["stdout"].strip().split() if x.isdigit()]


def get_process_command(pid: str | int) -> str | None:
    pid_str = str(pid)
    try:
        if platform.system() == "Windows":
            cmd = (
                "powershell.exe -NoProfile -Command "
                f'"(Get-CimInstance Win32_Process -Filter \\"ProcessId={pid_str}\\").CommandLine"'
            )
        else:
            cmd = f"ps -o command= -p {pid_str}"
        return exec_sync_with_defaults_deprecated(cmd, {"timeout": 1000})
    except OSError:
        return None


async def get_ancestor_commands_async(pid: str | int, max_depth: int = 10) -> list[str]:
    pid_s = str(pid)
    if platform.system() == "Windows":
        script = f"""
$currentPid = {pid_s}
$commands = @()
for ($i = 0; $i -lt {max_depth}; $i++) {{
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$currentPid" -ErrorAction SilentlyContinue
  if (-not $proc) {{ break }}
  if ($proc.CommandLine) {{ $commands += $proc.CommandLine }}
  if (-not $proc.ParentProcessId -or $proc.ParentProcessId -eq 0) {{ break }}
  $currentPid = $proc.ParentProcessId
}}
$commands -join [char]0
""".strip()
        r = await exec_file_no_throw_with_cwd(
            "powershell.exe",
            ["-NoProfile", "-Command", script],
            timeout=3000,
            cwd=None,
            preserve_output_on_error=False,
        )
        if r["code"] != 0 or not (r["stdout"] or "").strip():
            return []
        return [x for x in r["stdout"].split("\0") if x]

    script = (
        f"currentpid={pid_s}; for i in $(seq 1 {max_depth}); do "
        "cmd=$(ps -o command= -p $currentpid 2>/dev/null); "
        'if [ -n "$cmd" ]; then printf \'%s\\0\' "$cmd"; fi; '
        f'ppid=$(ps -o ppid= -p $currentpid 2>/dev/null | tr -d " "); '
        'if [ -z "$ppid" ] || [ "$ppid" = "0" ] || [ "$ppid" = "1" ]; then break; fi; '
        "currentpid=$ppid; done"
    )
    r = await exec_file_no_throw_with_cwd(
        "sh", ["-c", script], timeout=3000, cwd=None, preserve_output_on_error=False
    )
    if r["code"] != 0 or not (r["stdout"] or "").strip():
        return []
    return [x for x in r["stdout"].split("\0") if x]


def get_child_pids(pid: str | int) -> list[int]:
    pid_str = str(pid)
    try:
        if platform.system() == "Windows":
            cmd = (
                "powershell.exe -NoProfile -Command "
                f'"(Get-CimInstance Win32_Process -Filter \\"ParentProcessId={pid_str}\\").ProcessId"'
            )
        else:
            cmd = f"pgrep -P {pid_str}"
        result = exec_sync_with_defaults_deprecated(cmd, {"timeout": 1000})
        if not result:
            return []
        return [int(x) for x in result.strip().splitlines() if x.strip().isdigit()]
    except OSError:
        return []
