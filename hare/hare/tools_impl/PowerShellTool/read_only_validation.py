"""
Read-only session validation for PowerShell. Port of: src/tools/PowerShellTool/readOnlyValidation.ts

Determines whether a PowerShell command is safe to run in read-only mode
by checking against an allowlist of known read-only cmdlets with safe-flag
constraints, and detecting security-concerning patterns (subexpressions,
splatting, assignments, member invocations, stop-parsing, UNC paths).
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Security-concerning pattern detection
# ---------------------------------------------------------------------------

# Subexpressions: $(...) can execute arbitrary code
_RE_SUBEXPRESSION = re.compile(r"\$\(")

# Splatting: @variable passes arbitrary parameters (token-start only,
# excluding email-style @ in mid-word)
_RE_SPLATTING = re.compile(r"(?:^|[^\w.])@\w+")

# Member invocations: .Method() can call arbitrary .NET methods
_RE_MEMBER_INVOCATION = re.compile(r"\.\w+\s*\(")

# Assignments: $var = ... can modify state
_RE_ASSIGNMENT = re.compile(r"\$\w+\s*[+\-*/]?=")

# Stop-parsing symbol: --% passes everything raw to native commands
_RE_STOP_PARSING = re.compile(r"--%")

# UNC paths: \\server\share can trigger network requests
_RE_UNC = re.compile(r"\\\\")

# Protocol-relative paths (not preceded by colon): //server/share
_RE_PROTO_RELATIVE = re.compile(r"(?<!:)//")

# Static method calls: [Type]::Method() can invoke arbitrary .NET methods
_RE_STATIC_METHOD = re.compile(r"::")


def _has_sync_security_concerns(command: str) -> bool:
    """Check for security-concerning patterns that disqualify a command
    from being considered read-only, even if the cmdlet is in the allowlist.
    Mirrors hasSyncSecurityConcerns in the TypeScript source."""
    trimmed = command.strip()
    if not trimmed:
        return False
    if _RE_SUBEXPRESSION.search(trimmed):
        return True
    if _RE_SPLATTING.search(trimmed):
        return True
    if _RE_MEMBER_INVOCATION.search(trimmed):
        return True
    if _RE_ASSIGNMENT.search(trimmed):
        return True
    if _RE_STOP_PARSING.search(trimmed):
        return True
    if _RE_UNC.search(trimmed):
        return True
    if _RE_PROTO_RELATIVE.search(trimmed):
        return True
    if _RE_STATIC_METHOD.search(trimmed):
        return True
    return False


# ---------------------------------------------------------------------------
# Writers (destructive cmdlets / aliases)
# ---------------------------------------------------------------------------

_WRITERS = frozenset({
    "set-content", "out-file", "move-item", "remove-item",
    "ni ", "new-item", "copy-item", "rename-item", "delete-item",
    "mkdir", "rmdir", "del", "erase", "rd", "ren", "move",
    "add-content", "clear-content", "export-csv", "export-clixml",
    "set-item", "set-itemproperty", "set-acl",
})

# ---------------------------------------------------------------------------
# Read-only cmdlet allowlist: cmdlet name -> frozenset of safe flags
# Keys are lowercase; cmdlet matching is case-insensitive.
# ---------------------------------------------------------------------------

_READ_ONLY_CMDLETS: dict[str, frozenset[str] | bool] = {
    # ── Filesystem (read-only) ──
    "get-childitem": frozenset({
        "-path", "-literalpath", "-filter", "-include", "-exclude",
        "-recurse", "-depth", "-name", "-force", "-attributes",
        "-directory", "-file", "-hidden", "-readonly", "-system",
    }),
    "get-content": frozenset({
        "-path", "-literalpath", "-totalcount", "-head", "-tail",
        "-raw", "-encoding", "-delimiter", "-readcount",
    }),
    "get-item": frozenset({"-path", "-literalpath", "-force", "-stream"}),
    "get-itemproperty": frozenset({"-path", "-literalpath", "-name"}),
    "test-path": frozenset({
        "-path", "-literalpath", "-pathtype", "-filter",
        "-include", "-exclude", "-isvalid", "-newerthan", "-olderthan",
    }),
    "resolve-path": frozenset({"-path", "-literalpath", "-relative"}),
    "get-filehash": frozenset({"-path", "-literalpath", "-algorithm", "-inputstream"}),
    "get-acl": frozenset({"-path", "-literalpath", "-audit", "-filter", "-include", "-exclude"}),

    # ── Navigation (working-directory changes only) ──
    "set-location": frozenset({"-path", "-literalpath", "-passthru", "-stackname"}),
    "push-location": frozenset({"-path", "-literalpath", "-passthru", "-stackname"}),
    "pop-location": frozenset({"-passthru", "-stackname"}),

    # ── Text searching / filtering (read-only) ──
    "select-string": frozenset({
        "-path", "-literalpath", "-pattern", "-inputobject",
        "-simplematch", "-casesensitive", "-quiet", "-list",
        "-notmatch", "-allmatches", "-encoding", "-context", "-raw", "-noemphasis",
    }),

    # ── Data conversion (pure transforms, no side effects) ──
    "convertto-json": frozenset({
        "-inputobject", "-depth", "-compress", "-enumsasstrings", "-asarray",
    }),
    "convertfrom-json": frozenset({
        "-inputobject", "-depth", "-ashashtable", "-noenumerate",
    }),
    "convertto-csv": frozenset({
        "-inputobject", "-delimiter", "-notypeinformation", "-noheader", "-usequotes",
    }),
    "convertfrom-csv": frozenset({"-inputobject", "-delimiter", "-header", "-useculture"}),
    "format-hex": frozenset({
        "-path", "-literalpath", "-inputobject", "-encoding", "-count", "-offset",
    }),

    # ── Object inspection (read-only) ──
    "get-member": frozenset({
        "-inputobject", "-membertype", "-name", "-static", "-view", "-force",
    }),
    "get-unique": frozenset({"-inputobject", "-asstring", "-caseinsensitive", "-ontype"}),
    "compare-object": frozenset({
        "-referenceobject", "-differenceobject", "-property",
        "-syncwindow", "-casesensitive", "-culture",
        "-excludedifferent", "-includeequal", "-passthru",
    }),
    "join-string": frozenset({
        "-inputobject", "-property", "-separator", "-outputprefix",
        "-outputsuffix", "-singlequote", "-doublequote", "-formatstring",
    }),
    "get-random": frozenset({
        "-inputobject", "-minimum", "-maximum", "-count", "-setseed", "-shuffle",
    }),

    # ── Path utilities (read-only) ──
    "convert-path": frozenset({"-path", "-literalpath"}),
    "join-path": frozenset({"-path", "-childpath", "-additionalchildpath"}),
    "split-path": frozenset({
        "-path", "-literalpath", "-qualifier", "-noqualifier",
        "-parent", "-leaf", "-leafbase", "-extension", "-isabsolute",
    }),

    # ── Process / System info ──
    "get-process": frozenset({
        "-name", "-id", "-module", "-fileversioninfo", "-includeusername",
    }),
    "get-service": frozenset({
        "-name", "-displayname", "-dependentservices",
        "-requiredservices", "-include", "-exclude",
    }),
    "get-computerinfo": True,   # all flags read-only
    "get-host": True,
    "get-date": frozenset({"-date", "-format", "-uformat", "-displayhint", "-asutc"}),
    "get-location": frozenset({"-psprovider", "-psdrive", "-stack", "-stackname"}),
    "get-psdrive": frozenset({"-name", "-psprovider", "-scope"}),
    "get-module": frozenset({
        "-name", "-listavailable", "-all", "-fullyqualifiedname", "-psedition",
    }),
    "get-alias": frozenset({"-name", "-definition", "-scope", "-exclude"}),
    "get-history": frozenset({"-id", "-count"}),
    "get-culture": True,
    "get-uiculture": True,
    "get-timezone": frozenset({"-name", "-id", "-listavailable"}),
    "get-uptime": True,
    "get-hotfix": frozenset({"-id", "-description"}),
    "get-itempropertyvalue": frozenset({"-path", "-literalpath", "-name"}),
    "get-psprovider": frozenset({"-psprovider"}),

    # ── Output / display (allowAllFlags with arg validation) ──
    "write-output": frozenset({"-inputobject", "-noenumerate"}),
    "write-host": frozenset({
        "-object", "-nonewline", "-separator",
        "-foregroundcolor", "-backgroundcolor",
    }),
    "start-sleep": frozenset({"-seconds", "-milliseconds", "-duration"}),
    "format-table": True,
    "format-list": True,
    "format-wide": True,
    "format-custom": True,
    "measure-object": True,
    "select-object": True,
    "sort-object": True,
    "group-object": True,
    "where-object": True,
    "out-string": True,
    "out-host": True,

    # ── Network info ──
    "get-netadapter": frozenset({
        "-name", "-interfacedescription", "-interfaceindex", "-physical",
    }),
    "get-netipaddress": frozenset({
        "-interfaceindex", "-interfacealias", "-addressfamily", "-type",
    }),
    "get-netipconfiguration": frozenset({
        "-interfaceindex", "-interfacealias", "-detailed", "-all",
    }),
    "get-netroute": frozenset({
        "-interfaceindex", "-interfacealias", "-addressfamily", "-destinationprefix",
    }),
    "get-dnsclientcache": frozenset({
        "-entry", "-name", "-type", "-status", "-section", "-data",
    }),
    "get-dnsclient": frozenset({"-interfaceindex", "-interfacealias"}),

    # ── Event log (read-only) ──
    "get-eventlog": frozenset({
        "-logname", "-newest", "-after", "-before", "-entrytype",
        "-index", "-instanceid", "-message", "-source", "-username",
        "-asbaseobject", "-list",
    }),
    "get-winevent": frozenset({
        "-logname", "-listlog", "-listprovider", "-providername",
        "-path", "-maxevents", "-filterxpath", "-force", "-oldest",
    }),

    # ── CIM ──
    "get-cimclass": frozenset({
        "-classname", "-namespace", "-methodname", "-propertyname", "-qualifiername",
    }),
}

# Known aliases that map to read-only cmdlets (canonical, lowercase).
_READ_ONLY_ALIASES: dict[str, str] = {
    "ls": "get-childitem",
    "dir": "get-childitem",
    "gci": "get-childitem",
    "gc": "get-content",
    "cat": "get-content",
    "type": "get-content",
    "gi": "get-item",
    "gp": "get-itemproperty",
    "sls": "select-string",
    "gm": "get-member",
    "gu": "get-unique",
    "diff": "compare-object",
    "compare": "compare-object",
    "gps": "get-process",
    "ps": "get-process",
    "gsv": "get-service",
    "gl": "get-location",
    "pwd": "get-location",
    "gal": "get-alias",
    "h": "get-history",
    "history": "get-history",
    "measure": "measure-object",
    "select": "select-object",
    "sort": "sort-object",
    "group": "group-object",
    "where": "where-object",
    "ft": "format-table",
    "fl": "format-list",
    "fw": "format-wide",
    "fc": "format-custom",
    "sleep": "start-sleep",
    "cd": "set-location",
    "chdir": "set-location",
    "sl": "set-location",
    "pushd": "push-location",
    "popd": "pop-location",
    "resolve": "resolve-path",
    "rvpa": "resolve-path",
    "echo": "write-output",
    "write": "write-output",
    "gcm": "get-module",
    "sc": "set-content",   # maps to a WRITER — caught by _WRITERS
}


def _resolve_canonical(name: str) -> str:
    """Resolve a command name to canonical form via known aliases."""
    lower = name.lower()
    return _READ_ONLY_ALIASES.get(lower, lower)


def _is_cmdlet(name: str) -> bool:
    """True if the name looks like a PowerShell cmdlet (Verb-Noun pattern)."""
    return "-" in name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def violates_read_only_mode(command: str) -> bool:
    """Fast check: does the command contain a known destructive writer?
    Used as a first-pass gate before the full allowlist check."""
    low = command.lower()
    if _has_sync_security_concerns(command):
        return True
    return any(w in low for w in _WRITERS)


def check_read_only_constraints(command: str) -> dict[str, Any]:
    """Check if a PowerShell command is allowed in read-only mode.

    Returns a dict with:
        allowed: bool — whether the command is safe to run read-only
        reason: str (optional) — explanation when disallowed
    """
    cmd = command.strip()
    if not cmd:
        return {"allowed": False, "reason": "empty command"}

    # ---------- security-concerning patterns: fail-closed ----------
    if _has_sync_security_concerns(cmd):
        return {
            "allowed": False,
            "reason": "Contains security-concerning patterns (subexpressions, "
                      "splatting, assignments, member invocations, stop-parsing, "
                      "static method calls, or UNC paths)",
        }

    # ---------- extract first token as command name ----------
    tokens = cmd.split()
    if not tokens:
        return {"allowed": False, "reason": "no tokens in command"}

    raw_name = tokens[0]
    canonical = _resolve_canonical(raw_name)

    # ---------- check destructive writers ----------
    if canonical in _WRITERS:
        return {
            "allowed": False,
            "reason": f"'{raw_name}' is a destructive writer, not allowed in read-only mode",
        }

    # ---------- look up in read-only cmdlet allowlist ----------
    config = _READ_ONLY_CMDLETS.get(canonical)
    if config is None:
        return {
            "allowed": False,
            "reason": f"'{raw_name}' is not in the read-only cmdlet allowlist",
        }

    # ---------- flag validation ----------
    if config is True:
        # allowAllFlags — command's entire flag surface is read-only
        return {"allowed": True}

    safe_flags: frozenset[str] = config  # type: ignore[assignment]
    args = tokens[1:]
    is_cmdlet = _is_cmdlet(canonical)

    for arg in args:
        # Determine if this arg is a flag/parameter
        is_flag = False
        param_name = ""
        if is_cmdlet:
            # PowerShell cmdlet parameters use -Prefix (ASCII hyphen; also
            # Unicode en-dash/em-dash reach this path in the TS parser)
            if arg.startswith("-"):
                is_flag = True
                param_name = "-" + arg[1:]
        else:
            # Native commands: -flag (all platforms) or /flag (Windows)
            if arg.startswith("-") or arg.startswith("/"):
                is_flag = True
                param_name = arg

        if is_flag:
            # Strip colon-bound value for comparison: -Flag:value → -flag
            colon_idx = param_name.find(":")
            if colon_idx > 0:
                param_name = param_name[:colon_idx]

            param_lower = param_name.lower()
            if param_lower not in safe_flags:
                return {
                    "allowed": False,
                    "reason": f"Flag '{arg}' is not in the safe-flag set for '{raw_name}'",
                }

    return {"allowed": True}
