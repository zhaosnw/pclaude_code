"""
YOLO / fast-path classifier for auto-approval of safe operations.

Port of: src/utils/permissions/yoloClassifier.ts

Provides heuristics-based auto-approval for operations that are
clearly safe based on pattern matching and risk classification.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

YOLO_CLASSIFIER_TOOL_NAME = "YoloClassifier"

# ---------------------------------------------------------------------------
# Read-only commands that are safe on their own when they only inspect state
# ---------------------------------------------------------------------------
READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "less", "grep", "echo", "pwd", "whoami",
    "date", "wc", "sort", "uniq", "find", "file", "which", "whereis",
    "git status", "git log", "git diff", "git show", "git branch", "git tag",
    "git stash list", "git remote -v", "git config --get", "git config --list",
    "python --version", "python -c", "python3 --version", "node --version",
    "npm --version", "npm ls", "pip list", "pip show",
    "df", "du", "env", "printenv", "uname", "hostname", "uptime",
    "ps", "pgrep", "top -l", "lsof -p",
})

# Substrings that make an otherwise-safe command dangerous
DANGEROUS_SUBSTRINGS: tuple[re.Pattern, ...] = (
    re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)?-?r[rf]?\b"),
    re.compile(r"\bmkfs\."),
    re.compile(r"\bdd\s+if="),
    re.compile(r">\s*/dev/(sd|hd|nvme|xvd|disk|mapper|dm-)"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bchmod\s+-R\s+777\b"),
    re.compile(r"\bchown\s+-R\b"),
    re.compile(r"\bcurl\s+.*\|\s*(bash|sh|zsh|fish|dash)\b"),
    re.compile(r"\bwget\s+.*\|\s*(bash|sh|zsh|fish|dash)\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bfork\s*bomb\b|\b:\(\)\s*\{"),
    re.compile(r"\bgit\s+push\s+--force\b"),
    re.compile(r"\bgit\s+push\s+-f\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\s+-[f]*[d]*[x]*\b"),
    re.compile(r"\bdocker\s+rm\b.*-f"),
    re.compile(r"\bdocker\s+system\s+prune\b"),
    re.compile(r"\bmv\s+.*\s+/(etc|usr|bin|sbin|boot|dev|System)\b"),
    re.compile(r"\bcp\s+.*\s+/(etc|usr|bin|sbin|boot|dev|System)\b"),
    re.compile(r"\b>\s*/(etc|usr|bin|sbin|boot|dev|System)\b"),
    re.compile(r"&\s*>\s*/dev/null\s*&\s*disown"),
    re.compile(r"\bxargs\s+rm\b"),
    re.compile(r"\bfind\s+.*-exec\s+rm\b"),
    re.compile(r"\bfind\s+.*-delete\b"),
)

# Redirection operators that may write to the filesystem
REDIRECTION_WRITE_PATTERN = re.compile(r"[12]?>>?[&]?\s*\S")

# Pipelines that transform output (read-only but more complex)
TRANSFORM_PIPELINE_PATTERN = re.compile(
    r"^(cat|head|tail|grep|sort|uniq|wc|awk|sed|cut|tr|column|jq)\s+.*\|"
)

# Protected files that should never be auto-written
PROTECTED_FILES = frozenset({
    "authorized_keys", "known_hosts", "id_rsa", "id_ed25519", "id_ecdsa",
    ".env", "credentials.json", "secret", ".secrets", "key.pem", "cert.pem",
})

PROTECTED_DIRECTORIES = frozenset({
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/dev",
    "/System", "/Library/System",
})

SAFE_FILE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".csv", ".cfg", ".ini", ".env.example",
    ".html", ".css", ".scss", ".less", ".xml", ".svg",
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp",
    ".sh", ".bash", ".zsh", ".fish", ".rb", ".php", ".lua", ".sql",
})

# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------
RiskLevel = str  # "safe" | "low" | "medium" | "high" | "critical" | "blocked" | "disabled" | "unknown"


@dataclass
class YoloClassifierResult:
    should_block: bool
    reason: str
    model: str = "heuristic"
    risk_level: RiskLevel = "unknown"
    matched_rule: str = ""


@dataclass
class YoloClassifierConfig:
    enable_auto_approve: bool = True
    enable_bash_auto_approve: bool = False
    max_risk_level: RiskLevel = "low"
    _safe_patterns: set[str] = field(default_factory=set)
    _blocked_patterns: set[str] = field(default_factory=set)

    def add_safe_pattern(self, pattern: str) -> None:
        self._safe_patterns.add(pattern)

    def add_blocked_pattern(self, pattern: str) -> None:
        self._blocked_patterns.add(pattern)

    def is_safe_pattern(self, command: str) -> Optional[str]:
        """Return the first matching safe pattern, or None."""
        for pat in self._safe_patterns:
            if pat in command:
                return pat
        return None

    def is_blocked_pattern(self, command: str) -> Optional[str]:
        """Return the first matching blocked pattern, or None."""
        for pat in self._blocked_patterns:
            if pat in command:
                return pat
        return None


# Global config instance
_classifier_config = YoloClassifierConfig()


def get_yolo_classifier_config() -> YoloClassifierConfig:
    return _classifier_config


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_yolo_classifier(tool_name: str, tool_input: dict[str, Any]) -> YoloClassifierResult:
    """Run the YOLO classifier to determine if an operation can be auto-approved.

    Returns a YoloClassifierResult indicating whether to block or allow
    the operation based on heuristics analysis.
    """
    config = _classifier_config
    if not config.enable_auto_approve:
        return YoloClassifierResult(
            should_block=True, reason="Auto-approve disabled", risk_level="disabled"
        )

    if tool_name in ("Bash", "BashTool"):
        return _classify_bash(tool_input, config)
    elif tool_name in ("FileRead", "FileReadTool", "Read", "ReadTool"):
        return _classify_file_read(tool_input, config)
    elif tool_name in ("Glob", "GlobTool", "Grep", "GrepTool"):
        return _classify_read_only(tool_input, config)
    elif tool_name in ("FileEdit", "FileEditTool", "FileWrite", "FileWriteTool", "Write", "WriteTool", "Edit", "EditTool"):
        return _classify_file_write(tool_input, config)
    elif tool_name in ("NotebookEdit", "NotebookRead"):
        return _classify_notebook(tool_input, config)
    else:
        return YoloClassifierResult(
            should_block=True, reason=f"No classifier for {tool_name}", risk_level="unknown"
        )


# ---------------------------------------------------------------------------
# Bash classification
# ---------------------------------------------------------------------------

def _classify_bash(tool_input: dict[str, Any], config: YoloClassifierConfig) -> YoloClassifierResult:
    """Classify a bash command for auto-approval using layered heuristics."""
    if not config.enable_bash_auto_approve:
        return YoloClassifierResult(
            should_block=True, reason="Bash auto-approve disabled", risk_level="disabled"
        )

    command = str(tool_input.get("command", "")).strip()
    if not command:
        return YoloClassifierResult(should_block=True, reason="Empty command", risk_level="unknown")

    # Layer 1: Check custom safe patterns (user-configured allowlist)
    safe_match = config.is_safe_pattern(command)
    if safe_match:
        return YoloClassifierResult(
            should_block=False, reason=f"Matched safe pattern: {safe_match}",
            risk_level="safe", matched_rule=safe_match,
        )

    # Layer 2: Check custom blocked patterns (user-configured denylist)
    blocked_match = config.is_blocked_pattern(command)
    if blocked_match:
        return YoloClassifierResult(
            should_block=True, reason=f"Matched blocked pattern: {blocked_match}",
            risk_level="blocked", matched_rule=blocked_match,
        )

    # Layer 3: Scan for dangerous substrings via regex
    dangerous_find = _scan_dangerous_patterns(command)
    if dangerous_find:
        return YoloClassifierResult(
            should_block=True, reason=f"Dangerous pattern: {dangerous_find}",
            risk_level="critical", matched_rule=dangerous_find,
        )

    # Layer 4: Check built-in read-only command allowlist
    safe_cmd = _match_read_only_command(command)
    if safe_cmd:
        return YoloClassifierResult(
            should_block=False, reason=f"Safe command: {safe_cmd}",
            risk_level="safe", matched_rule=safe_cmd,
        )

    # Layer 5: Check if command writes to protected directories via redirection
    if _redirects_to_protected_path(command):
        return YoloClassifierResult(
            should_block=True, reason="Redirect writes to protected directory",
            risk_level="critical", matched_rule="redirect-protected",
        )

    # Layer 6: If max_risk_level is safe-only, block everything else
    if config.max_risk_level == "safe":
        return YoloClassifierResult(
            should_block=True, reason="Not in read-only allowlist", risk_level="unknown"
        )

    # Layer 7: If command is a non-destructive shell builtin or common tool
    if _is_likely_safe_command(command):
        return YoloClassifierResult(
            should_block=False, reason="Likely safe — common tool", risk_level="low"
        )

    return YoloClassifierResult(should_block=True, reason="Requires review", risk_level="unknown")


# ---------------------------------------------------------------------------
# File read classification
# ---------------------------------------------------------------------------

def _classify_file_read(tool_input: dict[str, Any], config: YoloClassifierConfig) -> YoloClassifierResult:
    """Classify a file read operation with path traversal detection."""
    file_path = str(tool_input.get("file_path", tool_input.get("filePath", "")))
    if not file_path:
        return YoloClassifierResult(should_block=False, reason="Unknown file path", risk_level="low")

    # Block reads of protected directories
    blocked_dir = _resolve_protected_directory(file_path)
    if blocked_dir:
        return YoloClassifierResult(
            should_block=True, reason=f"Protected directory: {blocked_dir}", risk_level="high"
        )

    # Block reads from protected files (secrets, keys)
    if _matches_protected_file(file_path):
        return YoloClassifierResult(
            should_block=True, reason=f"Protected file: {os.path.basename(file_path)}",
            risk_level="high"
        )

    ext = os.path.splitext(file_path)[1].lower()
    if ext in SAFE_FILE_EXTENSIONS:
        return YoloClassifierResult(
            should_block=False, reason=f"Safe extension: {ext}", risk_level="safe"
        )

    return YoloClassifierResult(should_block=False, reason="File read — low risk", risk_level="low")


# ---------------------------------------------------------------------------
# Read-only tool classification (Glob, Grep)
# ---------------------------------------------------------------------------

def _classify_read_only(tool_input: dict[str, Any], config: YoloClassifierConfig) -> YoloClassifierResult:
    """Classify read-only operations like Glob and Grep."""
    # Check if the pattern targets a protected directory
    pattern = str(tool_input.get("pattern", tool_input.get("path", "")))
    if pattern:
        for prot_dir in PROTECTED_DIRECTORIES:
            if pattern.startswith(prot_dir):
                return YoloClassifierResult(
                    should_block=True, reason=f"Pattern targets protected dir: {prot_dir}",
                    risk_level="high",
                )
    return YoloClassifierResult(should_block=False, reason="Read-only operation", risk_level="safe")


# ---------------------------------------------------------------------------
# File write classification
# ---------------------------------------------------------------------------

def _classify_file_write(tool_input: dict[str, Any], config: YoloClassifierConfig) -> YoloClassifierResult:
    """Classify a file write operation with path traversal and override detection."""
    file_path = str(tool_input.get("file_path", tool_input.get("filePath", "")))
    if not file_path:
        return YoloClassifierResult(should_block=True, reason="Unknown file path", risk_level="unknown")

    # Block writes to protected directories
    blocked_dir = _resolve_protected_directory(file_path)
    if blocked_dir:
        return YoloClassifierResult(
            should_block=True, reason=f"Protected directory: {blocked_dir}", risk_level="critical"
        )

    # Block writes to protected files
    if _matches_protected_file(file_path):
        return YoloClassifierResult(
            should_block=True, reason=f"Protected file: {os.path.basename(file_path)}",
            risk_level="high"
        )

    # Detect path traversal (e.g. ../../etc/passwd)
    if _has_path_traversal(file_path):
        return YoloClassifierResult(
            should_block=True, reason="Path traversal detected", risk_level="high"
        )

    # Check if overwriting an existing file (higher risk)
    expanded = os.path.expanduser(file_path)
    if os.path.exists(expanded) and os.path.getsize(expanded) > 0:
        if config.max_risk_level in ("safe",):
            return YoloClassifierResult(
                should_block=True, reason="Would overwrite existing file", risk_level="medium"
            )

    return YoloClassifierResult(
        should_block=False, reason="File write — acceptable risk", risk_level="medium"
    )


# ---------------------------------------------------------------------------
# Notebook classification
# ---------------------------------------------------------------------------

def _classify_notebook(tool_input: dict[str, Any], config: YoloClassifierConfig) -> YoloClassifierResult:
    """Classify notebook operations (always read-only in practice via this classifier)."""
    notebook_path = str(tool_input.get("notebook_path", ""))
    if notebook_path:
        blocked_dir = _resolve_protected_directory(notebook_path)
        if blocked_dir:
            return YoloClassifierResult(
                should_block=True, reason=f"Protected directory: {blocked_dir}", risk_level="high"
            )
    return YoloClassifierResult(should_block=False, reason="Notebook operation", risk_level="low")


# ---------------------------------------------------------------------------
# Shared heuristics
# ---------------------------------------------------------------------------

@lru_cache(maxsize=128)
def _scan_dangerous_patterns(command: str) -> Optional[str]:
    """Scan a command string for known-dangerous regex patterns.

    Uses lru_cache to avoid re-scanning identical commands.
    """
    stripped = command.strip()
    for pattern in DANGEROUS_SUBSTRINGS:
        m = pattern.search(stripped)
        if m:
            return m.group(0).strip()
    return None


def _match_read_only_command(command: str) -> Optional[str]:
    """Check whether a command starts with a known-safe read-only command.

    Handles multi-word prefixes (e.g. 'git status'), bare commands,
    and piped pipelines that begin with a read-only filter.
    """
    stripped = command.strip()

    # Exact match against allowlist
    if stripped in READ_ONLY_COMMANDS:
        return stripped

    # Prefix match — command starts with an allowlisted prefix followed by a space
    for safe in sorted(READ_ONLY_COMMANDS, key=len, reverse=True):
        if stripped.startswith(safe + " ") or stripped == safe:
            return safe

    return None


def _tokenize_first_command(command: str) -> Optional[str]:
    """Extract the first command name from a (possibly piped) command string."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return None
    return tokens[0]


def _is_likely_safe_command(command: str) -> bool:
    """Heuristic: is this command likely safe even though it is not in the
    read-only allowlist?

    Covers common developer tools that read but do not mutate the filesystem
    or system state in destructive ways.
    """
    first = _tokenize_first_command(command)
    if first is None:
        return False

    safe_binaries = frozenset({
        "man", "info", "whatis", "apropos", "type", "command", "hash",
        "printf", "test", "[", "true", "false",
        "basename", "dirname", "realpath", "readlink",
        "id", "groups", "logname", "tty",
        "nproc", "arch", "getconf",
        "clear", "reset",
        "cargo", "make", "cmake", "meson",
        "tree", "direnv", "asdf",
        "brew", "port",
    })
    return first in safe_binaries


def _resolve_protected_directory(file_path: str) -> Optional[str]:
    """Return the matching protected directory prefix, or None.

    Resolves ~ and symlinks so that e.g. /usr/local/bin/foo still matches /usr.
    Handles macOS /private prefix normalization (/private/etc -> /etc).
    """
    resolved = os.path.realpath(os.path.expanduser(file_path))
    # macOS: strip the /private prefix when the target path is under a
    # symlinked system directory.  We check each protected dir rather than
    # the specific file, since the file may not exist yet (e.g. a write).
    if resolved.startswith("/private/"):
        for prot_dir in PROTECTED_DIRECTORIES:
            # If the non-/private form of the protected dir exists as a
            # symlink, the /private prefix is likely a macOS artefact.
            if os.path.islink(prot_dir):
                shortened = resolved[len("/private") :]
                if shortened.startswith(prot_dir + os.sep) or shortened == prot_dir:
                    return prot_dir
    for prot_dir in PROTECTED_DIRECTORIES:
        if resolved.startswith(prot_dir + os.sep) or resolved == prot_dir:
            return prot_dir
    return None


def _matches_protected_file(file_path: str) -> bool:
    """Check whether the basename of file_path is a known protected file.

    Handles dotfiles (e.g. .env, .env.production) and bare filenames.
    Matches both exact basenames and dotfile prefixes.
    """
    basename = os.path.basename(file_path)
    if basename in PROTECTED_FILES:
        return True
    if basename.startswith("."):
        # For dotfiles like .env.production, the protected entry ".env"
        # should match anything of the form ".env" or ".env.*"
        for prot in PROTECTED_FILES:
            if prot.startswith(".") and (
                basename == prot or basename.startswith(prot + ".")
            ):
                return True
        return False
    # Bare filename without dot prefix: check the stem before extension
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return stem in PROTECTED_FILES


def _has_path_traversal(file_path: str) -> bool:
    """Detect path traversal attempts like ../ or encoded variants."""
    # Standard traversal
    if ".." in file_path.split(os.sep):
        # Check if the traversal escapes past a reasonable depth
        segments = [s for s in file_path.replace("\\", "/").split("/") if s]
        up_count = sum(1 for s in segments if s == "..")
        if up_count >= 3:
            return True
    # Symlink-based traversal
    if file_path.startswith("/proc/self/root") or file_path.startswith("/dev/fd/"):
        return True
    return False


def _redirects_to_protected_path(command: str) -> bool:
    """Check if a shell command uses > or >> to write into a protected directory."""
    m = REDIRECTION_WRITE_PATTERN.search(command)
    if not m:
        return False
    # Extract the target path after the redirection operator
    match_end = m.end()
    tail = command[match_end:].strip()
    # Grab the first whitespace-delimited token as the path
    target = tail.split()[0] if tail.split() else ""
    if not target:
        return False
    resolved = os.path.realpath(os.path.expanduser(target))
    for prot_dir in PROTECTED_DIRECTORIES:
        if resolved.startswith(prot_dir + os.sep) or resolved == prot_dir:
            return True
    return False
