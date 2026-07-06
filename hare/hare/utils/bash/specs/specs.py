"""
Bash command specifications – defines which commands are safe/unsafe.

Port of: src/utils/bash/specs/index.ts + individual spec files
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CommandSpec:
    name: str
    safe: bool = True
    description: str = ""
    read_only: bool = False
    prefix_args: list[str] = field(default_factory=list)


COMMAND_SPECS: dict[str, CommandSpec] = {
    "alias": CommandSpec(
        name="alias", safe=True, read_only=True, description="Define shell aliases"
    ),
    "nohup": CommandSpec(
        name="nohup", safe=False, description="Run command immune to hangups"
    ),
    "sleep": CommandSpec(
        name="sleep", safe=True, read_only=True, description="Delay execution"
    ),
    "time": CommandSpec(name="time", safe=True, description="Time command execution"),
    "timeout": CommandSpec(
        name="timeout", safe=True, description="Run command with time limit"
    ),
    "srun": CommandSpec(name="srun", safe=False, description="SLURM job runner"),
    "pyright": CommandSpec(
        name="pyright", safe=True, read_only=True, description="Python type checker"
    ),
    "cat": CommandSpec(name="cat", safe=True, read_only=True),
    "head": CommandSpec(name="head", safe=True, read_only=True),
    "tail": CommandSpec(name="tail", safe=True, read_only=True),
    "less": CommandSpec(name="less", safe=True, read_only=True),
    "more": CommandSpec(name="more", safe=True, read_only=True),
    "grep": CommandSpec(name="grep", safe=True, read_only=True),
    "rg": CommandSpec(name="rg", safe=True, read_only=True),
    "find": CommandSpec(name="find", safe=True, read_only=True),
    "ls": CommandSpec(name="ls", safe=True, read_only=True),
    "dir": CommandSpec(name="dir", safe=True, read_only=True),
    "pwd": CommandSpec(name="pwd", safe=True, read_only=True),
    "echo": CommandSpec(name="echo", safe=True, read_only=True),
    "printf": CommandSpec(name="printf", safe=True, read_only=True),
    "wc": CommandSpec(name="wc", safe=True, read_only=True),
    "sort": CommandSpec(name="sort", safe=True, read_only=True),
    "uniq": CommandSpec(name="uniq", safe=True, read_only=True),
    "diff": CommandSpec(name="diff", safe=True, read_only=True),
    "which": CommandSpec(name="which", safe=True, read_only=True),
    "whoami": CommandSpec(name="whoami", safe=True, read_only=True),
    "date": CommandSpec(name="date", safe=True, read_only=True),
    "env": CommandSpec(name="env", safe=True, read_only=True),
    "printenv": CommandSpec(name="printenv", safe=True, read_only=True),
    "uname": CommandSpec(name="uname", safe=True, read_only=True),
    "id": CommandSpec(name="id", safe=True, read_only=True),
    "file": CommandSpec(name="file", safe=True, read_only=True),
    "stat": CommandSpec(name="stat", safe=True, read_only=True),
    "tree": CommandSpec(name="tree", safe=True, read_only=True),
    "du": CommandSpec(name="du", safe=True, read_only=True),
    "df": CommandSpec(name="df", safe=True, read_only=True),
    "free": CommandSpec(name="free", safe=True, read_only=True),
    "top": CommandSpec(name="top", safe=True, read_only=True),
    "ps": CommandSpec(name="ps", safe=True, read_only=True),
    "git": CommandSpec(name="git", safe=False, description="Git version control"),
    "npm": CommandSpec(name="npm", safe=False, description="Node package manager"),
    "yarn": CommandSpec(name="yarn", safe=False),
    "pnpm": CommandSpec(name="pnpm", safe=False),
    "pip": CommandSpec(name="pip", safe=False),
    "pip3": CommandSpec(name="pip3", safe=False),
    "docker": CommandSpec(name="docker", safe=False),
    "kubectl": CommandSpec(name="kubectl", safe=False),
    "make": CommandSpec(name="make", safe=False),
    "cargo": CommandSpec(name="cargo", safe=False),
    "go": CommandSpec(name="go", safe=False),
    "python": CommandSpec(name="python", safe=False),
    "python3": CommandSpec(name="python3", safe=False),
    "node": CommandSpec(name="node", safe=False),
    "bun": CommandSpec(name="bun", safe=False),
    "deno": CommandSpec(name="deno", safe=False),
    "ruby": CommandSpec(name="ruby", safe=False),
    "java": CommandSpec(name="java", safe=False),
    "javac": CommandSpec(name="javac", safe=False),
    "gcc": CommandSpec(name="gcc", safe=False),
    "g++": CommandSpec(name="g++", safe=False),
    "clang": CommandSpec(name="clang", safe=False),
    "rustc": CommandSpec(name="rustc", safe=False),
    "rm": CommandSpec(name="rm", safe=False, description="Remove files"),
    "rmdir": CommandSpec(name="rmdir", safe=False),
    "mv": CommandSpec(name="mv", safe=False),
    "cp": CommandSpec(name="cp", safe=False),
    "mkdir": CommandSpec(name="mkdir", safe=False),
    "touch": CommandSpec(name="touch", safe=False),
    "chmod": CommandSpec(name="chmod", safe=False),
    "chown": CommandSpec(name="chown", safe=False),
    "ln": CommandSpec(name="ln", safe=False),
    "sed": CommandSpec(name="sed", safe=False),
    "awk": CommandSpec(name="awk", safe=False),
    "curl": CommandSpec(name="curl", safe=False),
    "wget": CommandSpec(name="wget", safe=False),
    "ssh": CommandSpec(name="ssh", safe=False),
    "scp": CommandSpec(name="scp", safe=False),
    "rsync": CommandSpec(name="rsync", safe=False),
    "tar": CommandSpec(name="tar", safe=False),
    "zip": CommandSpec(name="zip", safe=False),
    "unzip": CommandSpec(name="unzip", safe=False),
    "kill": CommandSpec(name="kill", safe=False),
    "killall": CommandSpec(name="killall", safe=False),
    "sudo": CommandSpec(name="sudo", safe=False),
    "su": CommandSpec(name="su", safe=False),
    "apt": CommandSpec(name="apt", safe=False),
    "brew": CommandSpec(name="brew", safe=False),
    "systemctl": CommandSpec(name="systemctl", safe=False),
    "service": CommandSpec(name="service", safe=False),
}


def get_spec(command_name: str) -> CommandSpec | None:
    return COMMAND_SPECS.get(command_name)


def get_all_specs() -> dict[str, CommandSpec]:
    return dict(COMMAND_SPECS)
