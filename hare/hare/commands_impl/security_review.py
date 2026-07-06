"""
/security-review command - run a focused security review.

Port of: src/commands/security-review.ts

Triggers a security-focused analysis of current changes.
Scans the working-tree diff against HEAD for common vulnerability patterns
and insecure coding practices, then returns a structured report with
severity-classified findings and remediation guidance.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any

from hare.utils.git_diff import fetch_git_diff

COMMAND_NAME = "security-review"
DESCRIPTION = "Run a focused security review of pending changes"
ALIASES: list[str] = []

# ---------------------------------------------------------------------------
# Severity / finding model
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class Finding:
    """A single security finding detected in the diff."""

    severity: str
    category: str
    file: str
    line: int
    snippet: str
    description: str
    remediation: str = ""
    cwe: str = ""


# ---------------------------------------------------------------------------
# Pattern matchers  (each returns a list of Finding)
# ---------------------------------------------------------------------------

# Patterns loosely aligned with OWASP Top 10 + common CWEs.
_SECRET_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, category, description, cwe)
    (
        r"(?i)(?:api[_-]?key|apikey|secret[_-]?key|secret_key|access[_-]?key)\s*[:=]\s*[\"'`][^\"'`\n]{8,}[\"'`]",
        "Secrets / Hardcoded Credentials",
        "Hardcoded API key or secret value detected.",
        "CWE-798",
    ),
    (
        r"(?i)(?:password|passwd|pwd)\s*[:=]\s*[\"'`][^\"'`\n]{4,}[\"'`]",
        "Secrets / Hardcoded Credentials",
        "Hardcoded password detected.",
        "CWE-798",
    ),
    (
        r"(?i)(?:token|jwt|bearer)\s*[:=]\s*[\"'`][A-Za-z0-9_\-.]{16,}[\"'`]",
        "Secrets / Hardcoded Credentials",
        "Hardcoded authentication token detected.",
        "CWE-798",
    ),
    (
        r"(?i)private[_-]?key\s*[:=]\s*[\"'`]?-----BEGIN",
        "Secrets / Hardcoded Credentials",
        "Hardcoded private key material detected.",
        "CWE-321",
    ),
    (
        r"(?i)(?:db[_-]?(?:url|uri|connection)|DATABASE_URL|MONGO_URI)\s*[:=]\s*[\"'`][^\"'`\n]{20,}[\"'`]",
        "Secrets / Hardcoded Credentials",
        "Database connection string may contain hardcoded credentials.",
        "CWE-798",
    ),
]

_INJECTION_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        r"(?i)(?:cursor\.)?execute\s*\(\s*f[\"'][\s\S]*?\{.*?\}",
        "SQL / NoSQL Injection",
        "String formatting in SQL query — potential SQL injection.",
        "CWE-89",
    ),
    (
        r"(?i)f?[\"'].*SELECT\s+.*FROM\s+.*\+.*",
        "SQL / NoSQL Injection",
        "String concatenation in SQL statement — possible SQL injection.",
        "CWE-89",
    ),
    (
        r"(?i)(?:innerHTML|outerHTML|insertAdjacentHTML|document\.write)\s*\(\s*",
        "Cross-Site Scripting (XSS)",
        "Use of innerHTML or similar DOM sink without sanitization — potential XSS.",
        "CWE-79",
    ),
    (
        r"(?i)dangerouslySetInnerHTML\s*=",
        "Cross-Site Scripting (XSS)",
        "React dangerouslySetInnerHTML — possible XSS vector.",
        "CWE-79",
    ),
    (
        r"(?i)(?:eval|exec|Function)\s*\(\s*",
        "Code / Command Injection",
        "Use of eval/exec/Function with possible user-controlled input.",
        "CWE-95",
    ),
    (
        r"(?i)(?:os\.system|subprocess\.(?:call|Popen|run)|shell\s*=\s*True)\s*\(\s*",
        "Code / Command Injection",
        "Shell command execution with possible unsanitized input.",
        "CWE-78",
    ),
    (
        r"(?i)(?:child_process\.exec|child_process\.spawn)\s*\(\s*",
        "Code / Command Injection",
        "Node.js child_process exec/spawn — verify input sanitization.",
        "CWE-78",
    ),
]

_PATH_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        r"(?i)(?:open|read|write)\s*\(\s*.*\.\./",
        "Path Traversal",
        "File operation with '..' path segment — possible path traversal.",
        "CWE-22",
    ),
    (
        r"(?i)(?:require|import)\s*\(\s*.*\+.*\)",
        "Dynamic Imports",
        "Dynamic import with string concatenation — verify input is not user-controlled.",
        "CWE-706",
    ),
]

_AUTH_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        r"(?i)if\s+True\s*(?::|==)\s*True",
        "Authentication Bypass",
        "Trivially-bypassed authentication check (if True == True).",
        "CWE-287",
    ),
    (
        r"(?i)(?:@login_required|@require_auth|isAuthenticated)\s*==\s*False",
        "Authentication Bypass",
        "Suspicious inversion of authentication check — verify intent.",
        "CWE-287",
    ),
]

_CRYPTO_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        r"(?i)(?:md5|sha1)\s*\(\s*",
        "Weak Cryptography",
        "Use of MD5 or SHA1 — these are cryptographically broken.",
        "CWE-328",
    ),
    (
        r"(?i)(?:DES|RC4|RC2)\b",
        "Weak Cryptography",
        "Use of a deprecated / broken cipher algorithm.",
        "CWE-327",
    ),
    (
        r"(?i)(?:Math\.random|random\.randint|random\.choice)\s*\(\s*",
        "Weak Randomness",
        "Non-cryptographic random source — not suitable for security-sensitive tokens.",
        "CWE-338",
    ),
    (
        r"(?i)(?:ssl\._create_unverified_context|verify\s*=\s*False|rejectUnauthorized\s*:\s*false)",
        "Insecure TLS",
        "TLS certificate verification disabled — man-in-the-middle risk.",
        "CWE-295",
    ),
]

_DEBUG_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        r"(?i)(?:console\.(?:log|debug|dir|trace)|print|echo|var_dump|debugger)\s*\(\s*",
        "Debug Code Left Behind",
        "Debug/logging statement — may leak information in production.",
        "CWE-489",
    ),
    (
        r"(?i)DEBUG\s*=\s*True",
        "Debug Mode Enabled",
        "Debug flag set to True — should not be active in production.",
        "CWE-489",
    ),
]

# Grouped for ordered scanning
_RULE_GROUPS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    ("critical", _SECRET_PATTERNS),
    ("high", _CRYPTO_PATTERNS),
    ("high", _AUTH_PATTERNS),
    ("medium", _INJECTION_PATTERNS),
    ("medium", _PATH_PATTERNS),
    ("low", _DEBUG_PATTERNS),
]


# ---------------------------------------------------------------------------
# Diff content extraction (slimmer than the full utility)
# ---------------------------------------------------------------------------

async def _get_diff_text(cwd: str | None = None) -> tuple[str, str]:
    """Run ``git diff HEAD`` and return (stdout, git_root)."""
    git_exe = os.environ.get("GIT_EXECUTABLE", "git")
    try:
        proc = await asyncio.create_subprocess_exec(
            git_exe,
            "diff",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        diff_text = stdout.decode("utf-8", errors="replace")
    except (asyncio.TimeoutError, FileNotFoundError, OSError):
        return "", ""

    # Resolve git root for relative paths
    try:
        proc = await asyncio.create_subprocess_exec(
            git_exe,
            "rev-parse",
            "--show-toplevel",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        git_root = out.decode("utf-8", errors="replace").strip()
    except (asyncio.TimeoutError, FileNotFoundError, OSError):
        git_root = ""

    return diff_text, git_root


def _parse_diff_for_findings(
    diff_text: str, git_root: str, focus: str
) -> list[Finding]:
    """Walk the unified diff and run every pattern group against added lines."""
    findings: list[Finding] = []
    current_file = ""
    current_line_new = 0

    lines = diff_text.split("\n")

    for raw in lines:
        # Track file header
        file_match = re.match(r"^\+\+\+\s+b/(.+)", raw)
        if file_match:
            current_file = file_match.group(1)
            current_line_new = 0
            continue

        # Track hunk header  @@ -a,b +c,d @@
        hunk = re.match(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@", raw)
        if hunk:
            current_line_new = int(hunk.group(1)) - 1
            continue

        # Only scan added lines
        if raw.startswith("+") and not raw.startswith("+++"):
            current_line_new += 1
            line_content = raw[1:]  # strip the '+' prefix

            # Apply all rule groups
            for severity, patterns in _RULE_GROUPS:
                for pattern, category, desc, cwe in patterns:
                    if re.search(pattern, line_content):
                        # Filter by focus if requested
                        if focus and focus.lower() not in category.lower() and focus.lower() not in desc.lower():
                            continue
                        snippet = line_content.strip()
                        findings.append(
                            Finding(
                                severity=severity,
                                category=category,
                                file=current_file,
                                line=current_line_new,
                                snippet=snippet[:120],
                                description=desc,
                                cwe=cwe,
                            )
                        )

    return findings


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_report(findings: list[Finding], files_changed: int, focus: str) -> str:
    """Produce a markdown security review report."""

    parts: list[str] = []

    # Header
    parts.append("## Security Review")
    if focus:
        parts.append(f"**Focus area:** {focus}")
    parts.append("")
    parts.append(f"**Files changed:** {files_changed}  |  **Findings:** {len(findings)}")
    parts.append("")

    if not findings:
        parts.append("No security issues detected in the pending changes.")
        parts.append("")
        parts.append(
            "> Tip: this scanner checks for common patterns only. "
            "Always review authentication, authorization, data validation, "
            "and cryptography changes manually."
        )
        return "\n".join(parts)

    # Sort by severity then file
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 99), f.file, f.line))

    # Summary table
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    parts.append("### Summary by Severity")
    parts.append("")
    parts.append("| Severity | Count |")
    parts.append("|----------|-------|")
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev in counts:
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(sev, "")
            parts.append(f"| {emoji} **{sev.title()}** | {counts[sev]} |")
    parts.append("")

    # Category breakdown
    cat_counts: dict[str, int] = {}
    for f in findings:
        cat_counts[f.category] = cat_counts.get(f.category, 0) + 1
    parts.append("### Categories Affected")
    parts.append("")
    for cat, cnt in cat_counts.items():
        parts.append(f"- **{cat}** — {cnt} finding{'s' if cnt > 1 else ''}")
    parts.append("")

    # Individual findings
    parts.append("---")
    parts.append("")
    parts.append("### Detailed Findings")
    parts.append("")

    for i, f in enumerate(findings, 1):
        emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(f.severity, "")
        parts.append(f"#### {i}. {emoji} [{f.severity.upper()}] {f.category}")
        parts.append("")
        parts.append(f"- **File:** `{f.file}:{f.line}`")
        parts.append(f"- **Description:** {f.description}")
        if f.cwe:
            parts.append(f"- **CWE:** {f.cwe}")
        parts.append(f"- **Snippet:**")
        parts.append(f"  ```")
        parts.append(f"  {f.snippet}")
        parts.append(f"  ```")
        if f.remediation:
            parts.append(f"- **Remediation:** {f.remediation}")
        else:
            parts.append(f"- **Remediation:** Remove hardcoded values, use environment variables or a secrets manager. Review the surrounding code to ensure proper input validation, output encoding, and access control.")
        parts.append("")

    # Mitigation guidance
    parts.append("---")
    parts.append("")
    parts.append("### Mitigation Guidance")
    parts.append("")
    parts.append("- **Secrets:** Use environment variables or a secrets manager (AWS Secrets Manager, HashiCorp Vault, GitHub Secrets). Never commit secrets to source control.")
    parts.append("- **Injection:** Use parameterized queries / prepared statements. Apply context-aware output encoding for XSS prevention.")
    parts.append("- **Command execution:** Avoid shell=True. Use subprocess with list arguments or libraries like `shlex.quote`.")
    parts.append("- **Cryptography:** Use modern algorithms (AES-256-GCM, SHA-256+, bcrypt/argon2 for passwords).")
    parts.append("- **Authentication:** Enforce at the framework/middleware level. Never bypass checks via feature flags in production paths.")
    parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Command entry-points
# ---------------------------------------------------------------------------

_HELP_TEXT = (
    "## Security Review\n\n"
    "Analyzes pending changes for OWASP Top 10 vulnerabilities and "
    "common security issues.\n\n"
    "**Usage:**\n"
    "- `/security-review` — scan all changed files\n"
    "- `/security-review focus on XSS` — filter to a specific concern\n"
    "- `/security-review help` — show this help\n\n"
    "**Checks include:**\n"
    "- Secrets & hardcoded credentials\n"
    "- SQL / NoSQL injection\n"
    "- Cross-Site Scripting (XSS)\n"
    "- Code & command injection\n"
    "- Path traversal\n"
    "- Weak cryptography & randomness\n"
    "- Insecure TLS configuration\n"
    "- Authentication bypass patterns\n"
    "- Debug code left behind\n"
)


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Run a security review of the current changes.

    Returns a structured report: summary table, category breakdown,
    per-finding details, and remediation guidance.
    """
    payload = (args or "").strip()

    # Help shortcut
    if payload.lower() in ("help", "--help", "-h"):
        return {"type": "text", "value": _HELP_TEXT}

    # Extract focus area
    focus = ""
    for prefix in ("focus on ", "focus:", "focus "):
        lower = payload.lower()
        idx = lower.find(prefix)
        if idx >= 0:
            focus = payload[idx + len(prefix):].strip()
            break

    # Fetch diff
    git_diff_result = await fetch_git_diff()
    if git_diff_result is None:
        return {
            "type": "text",
            "value": (
                "## Security Review\n\n"
                "No git repository detected, or not in a working tree.\n"
                "Run this command inside a git repository with uncommitted changes."
            ),
        }

    files_changed = git_diff_result.stats.files_changed

    if files_changed == 0:
        return {
            "type": "text",
            "value": (
                "## Security Review\n\n"
                "No changes detected in the working tree. Nothing to review.\n\n"
                "Make changes and run `/security-review` to analyze them."
            ),
        }

    # Get raw diff text for line-level scanning
    diff_text, git_root = await _get_diff_text()

    # Scan
    findings = _parse_diff_for_findings(diff_text, git_root, focus)

    # Render
    report = _render_report(findings, files_changed, focus)

    return {"type": "text", "value": report}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[focus area]",
        "call": call,
    }
