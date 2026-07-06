"""
Bash security — classify command risk levels and detect dangerous patterns.

Port of: src/tools/BashTool/bashSecurity.ts

This module determines risk levels for shell commands, identifying
dangerous patterns and potential injection vectors beyond simple regex matching.
"""

from __future__ import annotations

import re
from typing import Literal

RiskLevel = Literal["safe", "low", "medium", "high", "critical"]

# ---------------------------------------------------------------------------
# Critical patterns — likely destructive and irreversible
# ---------------------------------------------------------------------------

CRITICAL_PATTERNS: list[str] = [
    # Force-remove system directories
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|--recursive\s+--force)\s+[/~]",
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/etc",
    r"rm\s+-rf\s+/var",
    r"rm\s+-rf\s+/usr",
    r"rm\s+-rf\s+/home",
    r"rm\s+-rf\s+/boot",
    # Filesystem destruction
    r"mkfs\.",
    r"dd\s+if=\S+\s+of=/dev/sd",
    r"dd\s+if=\S+\s+of=/dev/nvme",
    r"dd\s+.*of=/dev/",
    # Fork bomb
    r":\(\)\s*\{\s*:\|\s*:\s*&\s*\};:",
    r":\s*\|\s*:\s*&\s*:",
    # System-level chmod/chown
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+\S+\s+/",
    # Redirect to block devices
    r">\s*/dev/sd[a-z]",
    r">\s*/dev/nvme",
    # Systemctl disable critical services
    r"systemctl\s+disable\s+(sshd?|network|firewalld|ufw)",
    r"systemctl\s+stop\s+(sshd?|network|firewalld|ufw)",
    # Mount/umount operations
    r"mount\s+.*\s+/",
    r"umount\s+/",
    # IPTables modifications
    r"iptables\s+-[ADIL]\s+INPUT\s+-j\s+DROP",
    r"iptables\s+-F\b",
    # Crontab overwrites
    r"echo\s+.*\|\s+crontab\s+-",
    r"crontab\s+-r\b",
]

# ---------------------------------------------------------------------------
# High risk patterns — potentially destructive, needs confirmation
# ---------------------------------------------------------------------------

HIGH_RISK_PATTERNS: list[str] = [
    # Force git operations
    r"git\s+push\s+.*--force",
    r"git\s+push\s+-f\b",
    r"git\s+push\s+--delete\s+origin",
    r"git\s+reset\s+--hard",
    r"git\s+clean\s+-[a-zA-Z]*f",
    # Force remove (non-system)
    r"rm\s+-rf\b",
    r"sudo\s+rm\b",
    # Pipe to shell
    r"curl\s+.*\|\s*(ba)?sh",
    r"curl\s+.*\|\s*sudo\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*sudo\s*(ba)?sh",
    # Eval with command substitution
    r"eval\s+.*\$",
    r"eval\s+.*`",
    # Wide-open permissions
    r"chmod\s+777\b",
    r"chmod\s+-R\s+777\b",
    # Docker dangerous
    r"docker\s+rm\s+-f\b",
    r"docker\s+system\s+prune",
    # Kubernetes destructive
    r"kubectl\s+delete\b",
    r"kubectl\s+drain\b",
    # Package uninstall
    r"npm\s+unpublish\b",
    r"pip\s+uninstall\s+-y\b(?!\s+\S+)",  # -y without specific package
    # Find delete
    r"find\s+.*\s+-delete\b(?!\s+\S*\s+-name\s+)",  # -delete without explicit path
]

# ---------------------------------------------------------------------------
# Medium risk patterns — could cause issues, usually safe with confirmation
# ---------------------------------------------------------------------------

MEDIUM_RISK_PATTERNS: list[str] = [
    r"git\s+checkout\s+--",  # Discard unstaged changes
    r"git\s+stash\s+drop",
    r"git\s+rebase\b",
    r"rm\s+-[a-zA-Z]*[rf]",
    r"mv\s+.*\s+/dev/null",
    r"kill\s+-9\b",
    r"pkill\b",
    r"pip\s+install\b",
    r"npm\s+install\s+-g",
    r"apt\s+(install|remove|purge)",
    r"brew\s+(install|uninstall|remove)",
    r"chmod\s+\d{3}",
    r"chown\b",
    r"shutdown\b",
    r"reboot\b",
]

# ---------------------------------------------------------------------------
# Low risk commands — commonly used safe commands
# ---------------------------------------------------------------------------

LOW_RISK_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "rg",
        "find",
        "echo",
        "pwd",
        "whoami",
        "date",
        "wc",
        "sort",
        "uniq",
        "diff",
        "file",
        "which",
        "type",
        "mkdir",
        "touch",
        "cp",
        "df",
        "du",
        "env",
        "printenv",
        "uname",
        "hostname",
        "git status",
        "git log",
        "git diff",
        "git branch",
        "git tag",
        "git remote",
        "python --version",
        "node --version",
        "npm --version",
        "pip --version",
        "ps",
        "top",
        "htop",
        "uptime",
        "free",
        "vm_stat",
    }
)


# ---------------------------------------------------------------------------
# Quote-aware content extraction
# ---------------------------------------------------------------------------


def extract_unquoted_segments(command: str) -> list[str]:
    """Parse shell command and return only segments outside quotes.

    This helps pattern-match against actual commands rather than
    quoted literal strings that happen to contain dangerous-looking text.

    Returns a list of unquoted content segments.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0

    while i < len(command):
        ch = command[i]

        if ch == "\\" and not in_single:
            # Backslash escapes next char in double quotes or outside quotes
            if in_double:
                if i + 1 < len(command) and command[i + 1] in ('"', '\\', '$', '`', '\n'):
                    i += 2
                    continue
            else:
                if i + 1 < len(command):
                    current.append(command[i + 1])
                    i += 2
                    continue
        elif ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        elif ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        elif ch in (";", "|", "&", "\n") and not in_single and not in_double:
            # Command separator — flush current segment
            if current:
                segments.append("".join(current).strip())
                current = []
            i += 1
            continue

        if not in_single:
            current.append(ch)
        i += 1

    if current:
        segments.append("".join(current).strip())

    return [s for s in segments if s]


# ---------------------------------------------------------------------------
# Misparsing validators — detect shell argument parsing ambiguities
# ---------------------------------------------------------------------------


def detect_command_injection(command: str) -> bool:
    """Check for command injection patterns.

    Detects semicolon-separated commands, backtick substitution,
    and $() inside potentially quoted strings that bypass simple regex matching.
    """
    # Check unquoted segments for injection patterns
    segments = extract_unquoted_segments(command)
    for seg in segments:
        # Semicolons in a single segment suggest command chaining
        if ";" in seg and not seg.strip().startswith(";"):
            # But allow find -exec ... \; patterns
            if not re.search(r"-exec\s+.*\\;", seg):
                return True
        # Backtick substitution
        if "`" in seg:
            return True
        # $() command substitution (allow $(cat <<'EOF' ... EOF) safe heredoc)
        if "$(" in seg:
            if not re.search(r"\$\(cat\s+<<'[^']+'\s*\n", seg):
                return True

    return False


def detect_path_traversal(command: str) -> bool:
    """Check for path traversal patterns in destructive commands.

    Detects '../' segments combined with rm, mv, cp -R, chmod, chown.
    """
    destructive_ops = r"(rm|mv|cp\s+-R|chmod|chown|chgrp)"
    traversal = r"\.\.[\\/]"

    if re.search(destructive_ops, command) and re.search(traversal, command):
        # Flag if it's not a safely anchored operation
        if not re.search(r"--?(cwd|path|dir)\s+\S+", command):
            return True
    return False


def detect_redirect_to_system(command: str) -> bool:
    """Check for output redirection to system-critical paths.

    Detects patterns like '> /etc/passwd', '>> /etc/crontab', '> /dev/sda'.
    """
    system_targets = [
        r"/etc/(passwd|shadow|group|sudoers|crontab|hosts|resolv\.conf|fstab)",
        r"/dev/sd[a-z]",
        r"/dev/nvme",
        r"/boot/",
        r"/proc/",
        r"/sys/",
    ]

    for target in system_targets:
        if re.search(rf">\s*{target}", command):
            return True
    return False


# ---------------------------------------------------------------------------
# Main classification
# ---------------------------------------------------------------------------


def classify_command_risk(command: str) -> RiskLevel:
    """Classify the risk level of a shell command.

    Checks in order: critical → high → medium → safe → low.
    """
    cmd = command.strip()

    for pattern in CRITICAL_PATTERNS:
        if re.search(pattern, cmd):
            return "critical"

    for pattern in HIGH_RISK_PATTERNS:
        if re.search(pattern, cmd):
            return "high"

    for pattern in MEDIUM_RISK_PATTERNS:
        if re.search(pattern, cmd):
            return "medium"

    # Check for injection patterns that bypass regex
    if detect_command_injection(cmd):
        return "high"

    first_word = cmd.split()[0] if cmd.split() else ""
    if first_word in LOW_RISK_COMMANDS or cmd in LOW_RISK_COMMANDS:
        return "safe"

    return "low"


def is_command_safe_for_auto_approve(
    command: str,
    allow_rules: list[str],
    deny_rules: list[str],
) -> bool:
    """Check if a command is safe for automatic approval.

    Checks deny rules first, then allow rules, then risk classification.
    """
    from hare.tools_impl.BashTool.bash_permissions import check_bash_permission

    deny_result = check_bash_permission(command, deny_rules, is_allow=False)
    if deny_result["matched"]:
        return False

    allow_result = check_bash_permission(command, allow_rules, is_allow=True)
    if allow_result["matched"]:
        return True

    risk = classify_command_risk(command)
    return risk in ("safe",)
