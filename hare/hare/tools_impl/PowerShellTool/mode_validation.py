"""Validate PowerShell invocation mode vs permission policy.

Port of: src/tools/PowerShellTool/modeValidation.ts
Follows the same pattern as: src/tools/BashTool/modeValidation.ts
"""

from __future__ import annotations

from typing import Any

# Commands allowed in acceptEdits mode: build/test/VCS tools that are
# safe to run when the agent is applying code changes automatically.
# Includes both cross-platform commands (git, npm, etc.) and
# PowerShell-native / Windows-specific equivalents.
_ACCEPT_EDITS_ALLOWED_COMMANDS = frozenset(
    {
        # ---------- VCS ----------
        "git",
        # ---------- Node.js / JS ecosystem ----------
        "npm",
        "yarn",
        "pnpm",
        "node",
        "npx",
        # ---------- Python ----------
        "pip",
        "pip3",
        "python",
        "python3",
        # ---------- .NET / MSBuild ----------
        "dotnet",
        "msbuild",
        "nuget",
        # ---------- Rust ----------
        "cargo",
        "rustc",
        # ---------- Go ----------
        "go",
        # ---------- C / C++ ----------
        "make",
        "cmake",
        "gcc",
        "g++",
        "clang",
        "clang++",
        # ---------- Java / JVM ----------
        "mvn",
        "gradle",
        "javac",
        # ---------- Ruby ----------
        "ruby",
        "bundle",
        "gem",
        # ---------- PHP ----------
        "php",
        "composer",
        # ---------- Windows package managers ----------
        "choco",
        "winget",
        "scoop",
        # ---------- PowerShell-native build / test cmdlets ----------
        "invoke-pester",
        "pester",
    }
)

# PowerShell cmdlets that should NEVER be allowed in restricted modes
# (plan / acceptEdits) because they are destructive or administrative.
_RESTRICTED_POWERSHELL_CMDLETS = frozenset(
    {
        "remove-item",
        "delete-item",
        "clear-content",
        "stop-process",
        "stop-service",
        "restart-computer",
        "stop-computer",
        "format-volume",
        "disable-computerrestore",
    }
)


def check_permission_mode(
    command: str,
    mode: str,
) -> dict[str, Any]:
    """Check if a PowerShell command is allowed in the given permission mode.

    Modes:
      - ``"default"``   : all commands allowed
      - ``"plan"``      : only read-only commands allowed
      - ``"acceptEdits"``: only build / test / VCS commands allowed
    """
    if mode == "default":
        return {"allowed": True}

    if mode == "plan":
        return {
            "allowed": False,
            "reason": "Cannot execute PowerShell commands in plan mode",
        }

    if mode == "acceptEdits":
        first_word = _extract_first_word(command)
        if not first_word:
            return {
                "allowed": False,
                "reason": "No command found",
            }
        if first_word in _ACCEPT_EDITS_ALLOWED_COMMANDS:
            return {"allowed": True}
        return {
            "allowed": False,
            "reason": (
                f"Command '{first_word}' not allowed in acceptEdits mode. "
                f"Allowed: {', '.join(sorted(_ACCEPT_EDITS_ALLOWED_COMMANDS))}"
            ),
        }

    # Unknown mode → be safe, deny.
    return {
        "allowed": False,
        "reason": f"Unknown permission mode: {mode!r}",
    }


def get_auto_allowed_commands(mode: str) -> frozenset[str]:
    """Return the set of commands auto-allowed for *mode*."""
    if mode == "acceptEdits":
        return _ACCEPT_EDITS_ALLOWED_COMMANDS
    return frozenset()


def violates_plan_mode(command: str) -> bool:
    """Return True if *command* would violate read-only / plan-mode restrictions.

    Checks both PowerShell cmdlets and common destructive shell patterns.
    """
    low = command.lower().strip()
    if not low:
        return False
    # Check restricted PowerShell cmdlets
    for cmdlet in _RESTRICTED_POWERSHELL_CMDLETS:
        if cmdlet in low:
            return True
    # Check for destructive patterns (out-file, set-content, redirections)
    if "set-content" in low or "out-file" in low or ">> " in low or " > " in low:
        return True
    return False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _extract_first_word(command: str) -> str:
    """Extract the first "word" (command / cmdlet name) from a PowerShell command string.

    Always returns a lowercased token so lookups against the allowed/restricted
    sets are case-insensitive.
    """
    stripped = command.strip()
    if not stripped:
        return ""
    # PowerShell can be invoked via `powershell -Command "..."`; handle that
    # by peeking inside the quoted expression.
    low = stripped.lower()
    if low.startswith("powershell") or low.startswith("pwsh "):
        # Try to pull the -Command argument
        if "-command " in low:
            idx = low.index("-command ") + len("-command ")
            rest = stripped[idx:].strip()
            if rest.startswith(('"', "'")):
                rest = rest[1:]
            return rest.split()[0].strip('"').strip("'").lower()
        return stripped.split()[1].lower() if " " in stripped else stripped.lower()
    # Simple case: grab first whitespace-delimited token
    return stripped.split()[0].strip('"').strip("'").lower()


def is_powershell_restricted_cmdlet(command: str) -> bool:
    """Return True if *command* starts with a known restricted / destructive cmdlet."""
    low = command.strip().lower()
    if not low:
        return False
    first = _extract_first_word(low)
    return first in _RESTRICTED_POWERSHELL_CMDLETS
