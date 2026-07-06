"""
Port of: src/utils/bash/specs/pyright.ts

Expanded with:
- Typed data classes (OptionArg, Option, CommandSpec) matching the TypeScript types
- Pyright exit codes (0=ok, 1=errors, 2=fatal, 3=config)
- Read-only safe-flag mapping (mirrors frontend PYRIGHT_READ_ONLY_COMMANDS)
- Known Python version / platform enum helpers
- Query helpers: get_pyright_option, classify_pyright_command, build_pyright_command
- Output-format utilities for JSON diagnostics
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# ============================================================================
# 0. Custom exception classes
# ============================================================================


class PyrightError(Exception):
    """Base exception for pyright-spec errors (validation, parsing, safety)."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class PyrightValidationError(PyrightError):
    """Raised when a pyright argument / flag value fails domain validation."""


class PyrightParseError(PyrightError):
    """Raised when pyright JSON output cannot be parsed or is malformed."""


class PyrightSafetyError(PyrightError):
    """Raised when a pyright invocation contains unsafe / dangerous arguments."""


# ============================================================================
# 1. Dataclasses — typed equivalents of TypeScript Option / Argument / CommandSpec
# ============================================================================


@dataclass
class OptionArg:
    """Metadata for an option's value argument."""

    name: str
    description: str = ""
    isOptional: bool = False
    isVariadic: bool = False
    isCommand: bool = False
    isDangerous: bool = False
    isModule: str | bool = False
    isScript: bool = False


@dataclass
class Option:
    """Metadata for a single CLI option / flag."""

    name: str | list[str]
    description: str = ""
    args: OptionArg | list[OptionArg] | None = None
    isRequired: bool = False

    @property
    def primary_name(self) -> str:
        """Return the canonical long-form name, or the first short name."""
        if isinstance(self.name, list):
            for n in self.name:
                if n.startswith("--"):
                    return n
            return self.name[0]
        return self.name

    @property
    def all_names(self) -> list[str]:
        """Return all aliases for this option as a flat list."""
        if isinstance(self.name, list):
            return self.name
        return [self.name]

    def arg_count(self) -> int:
        """Return how many value arguments this option expects."""
        if self.args is None:
            return 0
        if isinstance(self.args, list):
            return len(self.args)
        return 1

    def is_flag(self) -> bool:
        """Return True when this option takes no value argument."""
        return self.arg_count() == 0


@dataclass
class CommandSpec:
    """Full typed specification for a CLI command."""

    name: str
    description: str = ""
    options: list[Option] = field(default_factory=list)
    args: OptionArg | None = None
    subcommands: list[CommandSpec] = field(default_factory=list)


# ============================================================================
# 2. Pyright exit codes
# ============================================================================


class PyrightExitCode:
    """Exit codes returned by the ``pyright`` CLI.

    Source: https://github.com/microsoft/pyright/blob/main/docs/command-line.md#exit-codes
    """

    SUCCESS = 0       # No errors (or --warnings used and no warnings)
    ERRORS = 1        # Type errors or warnings found
    FATAL = 2         # Fatal error (crash, I/O, parser failure)
    CONFIG = 3        # Configuration file not found or invalid


PYRIGHT_EXIT_CODES: dict[int, str] = {
    0: "success — no type errors",
    1: "errors — type errors or warnings found",
    2: "fatal — internal error, I/O failure, or parser crash",
    3: "config — configuration file missing or invalid",
}


def pyright_exit_code_description(code: int) -> str:
    """Return a human-readable description for a pyright exit code."""
    return PYRIGHT_EXIT_CODES.get(code, f"unknown pyright exit code ({code})")


# ============================================================================
# 3. Known Python versions / platforms (argument value domains)
# ============================================================================

PYRIGHT_PYTHON_VERSIONS: frozenset[str] = frozenset(
    {
        "3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14",
        "3.15", "3.16",
    }
)

PYRIGHT_PYTHON_PLATFORMS: frozenset[str] = frozenset(
    {"Linux", "Windows", "Darwin", "macOS", "All"}
)

PYRIGHT_DIAGNOSTIC_LEVELS: frozenset[str] = frozenset(
    {"error", "warning", "information", "none"}
)


# ============================================================================
# 4. Structured PYRIGHT_SPEC (preserved from original port)
# ============================================================================

# Legacy dict-style spec — kept for backward compatibility with index.py consumers.
PYRIGHT_SPEC: dict[str, Any] = {
    "name": "pyright",
    "description": "Type checker for Python",
    "options": [
        {"name": ["--help", "-h"], "description": "Show help message"},
        {"name": "--version", "description": "Print pyright version and exit"},
        {
            "name": ["--watch", "-w"],
            "description": "Continue to run and watch for changes",
        },
        {
            "name": ["--project", "-p"],
            "description": "Use the configuration file at this location",
            "args": {"name": "FILE OR DIRECTORY"},
        },
        {"name": "-", "description": "Read file or directory list from stdin"},
        {
            "name": "--createstub",
            "description": "Create type stub file(s) for import",
            "args": {"name": "IMPORT"},
        },
        {
            "name": ["--typeshedpath", "-t"],
            "description": "Use typeshed type stubs at this location",
            "args": {"name": "DIRECTORY"},
        },
        {
            "name": "--verifytypes",
            "description": "Verify completeness of types in py.typed package",
            "args": {"name": "IMPORT"},
        },
        {
            "name": "--ignoreexternal",
            "description": "Ignore external imports for --verifytypes",
        },
        {
            "name": "--pythonpath",
            "description": "Path to the Python interpreter",
            "args": {"name": "FILE"},
        },
        {
            "name": "--pythonplatform",
            "description": "Analyze for platform",
            "args": {"name": "PLATFORM"},
        },
        {
            "name": "--pythonversion",
            "description": "Analyze for Python version",
            "args": {"name": "VERSION"},
        },
        {
            "name": ["--venvpath", "-v"],
            "description": "Directory that contains virtual environments",
            "args": {"name": "DIRECTORY"},
        },
        {"name": "--outputjson", "description": "Output results in JSON format"},
        {"name": "--verbose", "description": "Emit verbose diagnostics"},
        {"name": "--stats", "description": "Print detailed performance stats"},
        {
            "name": "--dependencies",
            "description": "Emit import dependency information",
        },
        {
            "name": "--level",
            "description": "Minimum diagnostic level",
            "args": {"name": "LEVEL"},
        },
        {
            "name": "--skipunannotated",
            "description": "Skip type analysis of unannotated functions",
        },
        {
            "name": "--warnings",
            "description": "Use exit code of 1 if warnings are reported",
        },
        {
            "name": "--threads",
            "description": "Use up to N threads to parallelize type checking",
            "args": {"name": "N", "isOptional": True},
        },
    ],
    "args": {
        "name": "files",
        "description": "Specify files or directories to analyze (overrides config file)",
        "isVariadic": True,
        "isOptional": True,
    },
}


# ============================================================================
# 5. Typed command spec (new)
# ============================================================================

PYRIGHT_COMMAND_SPEC = CommandSpec(
    name="pyright",
    description="Static type checker for Python",
    options=[
        Option(name=["--help", "-h"], description="Show help message"),
        Option(name="--version", description="Print pyright version and exit"),
        Option(
            name=["--watch", "-w"],
            description="Continue to run and watch for changes",
        ),
        Option(
            name=["--project", "-p"],
            description="Use the configuration file at this location",
            args=OptionArg(name="file_or_directory", description="Path to pyproject.toml or directory"),
        ),
        Option(
            name="-",
            description="Read file or directory list from stdin",
        ),
        Option(
            name="--createstub",
            description="Create type stub file(s) for import",
            args=OptionArg(name="import_name", description="Dotted import path (e.g. 'os.path')"),
        ),
        Option(
            name=["--typeshedpath", "-t"],
            description="Use typeshed type stubs at this location",
            args=OptionArg(name="directory", description="Path to typeshed directory"),
        ),
        Option(
            name="--verifytypes",
            description="Verify completeness of types in py.typed package",
            args=OptionArg(name="import_name", description="Package to verify"),
        ),
        Option(
            name="--ignoreexternal",
            description="Ignore external imports for --verifytypes",
        ),
        Option(
            name="--pythonpath",
            description="Path to the Python interpreter",
            args=OptionArg(name="file", description="Path to python executable"),
        ),
        Option(
            name="--pythonplatform",
            description="Analyze for platform",
            args=OptionArg(name="platform", description="One of: Linux, Windows, Darwin, macOS, All"),
        ),
        Option(
            name="--pythonversion",
            description="Analyze for Python version",
            args=OptionArg(name="version", description="Python version (e.g. '3.11', '3.12')"),
        ),
        Option(
            name=["--venvpath", "-v"],
            description="Directory that contains virtual environments",
            args=OptionArg(name="directory", description="Path to venvs directory"),
        ),
        Option(name="--outputjson", description="Output results in JSON format"),
        Option(name="--verbose", description="Emit verbose diagnostics"),
        Option(name="--stats", description="Print detailed performance stats"),
        Option(
            name="--dependencies",
            description="Emit import dependency information",
        ),
        Option(
            name="--level",
            description="Minimum diagnostic level",
            args=OptionArg(name="level", description="One of: error, warning, information, none"),
        ),
        Option(
            name="--skipunannotated",
            description="Skip type analysis of unannotated functions",
        ),
        Option(
            name="--warnings",
            description="Use exit code of 1 if warnings are reported",
        ),
        Option(
            name="--threads",
            description="Use up to N threads to parallelize type checking",
            args=OptionArg(name="N", description="Thread count (default: all cores)", isOptional=True),
        ),
    ],
    args=OptionArg(
        name="files",
        description="Specify files or directories to analyze (overrides config file)",
        isVariadic=True,
        isOptional=True,
    ),
)


# ============================================================================
# 6. Safe / read-only flag mapping (mirrors frontend PYRIGHT_READ_ONLY_COMMANDS)
# ============================================================================

# Flags that are considered safe for read-only execution.
# "none" = boolean flag (no arg); "string" = flag takes a string arg that must
# be validated.
PyrightArgKind = Literal["none", "string"]

PYRIGHT_SAFE_FLAGS: dict[str, PyrightArgKind] = {
    "--outputjson":     "none",
    "--project":        "string",
    "-p":               "string",
    "--pythonversion":  "string",
    "--pythonplatform": "string",
    "--typeshedpath":   "string",
    "-t":               "string",
    "--venvpath":       "string",
    "-v":               "string",
    "--level":          "string",
    "--stats":          "none",
    "--verbose":        "none",
    "--version":        "none",
    "--dependencies":   "none",
    "--warnings":       "none",
}

# Flags that are ALWAYS dangerous and never auto-approvable.
PYRIGHT_DANGEROUS_FLAGS: frozenset[str] = frozenset(
    {
        "--createstub",
        "--verifytypes",
        "--ignoreexternal",  # paired with --verifytypes
        "--watch",
        "-w",
        "--pythonpath",      # can point to arbitrary interpreter
    }
)

# pyright treats "--" as a file path, NOT as end-of-options marker.
# This is a critical security distinction.
PYRIGHT_RESPECTS_DOUBLE_DASH: bool = False


# ============================================================================
# 7. Option query helpers
# ============================================================================


def get_pyright_option(flag: str) -> Option | None:
    """Look up a pyright option by any of its names (e.g. ``'-p'`` or ``'--project'``)."""
    for opt in PYRIGHT_COMMAND_SPEC.options:
        if flag in opt.all_names:
            return opt
    return None


def get_pyright_option_by_name(name: str) -> Option | None:
    """Look up a pyright option by canonical long name only."""
    for opt in PYRIGHT_COMMAND_SPEC.options:
        if opt.primary_name == name:
            return opt
    return None


def get_pyright_flag_names() -> frozenset[str]:
    """Return the set of all flag/alias strings pyright recognizes."""
    names: set[str] = set()
    for opt in PYRIGHT_COMMAND_SPEC.options:
        names.update(opt.all_names)
    return frozenset(names)


def is_pyright_flag(token: str) -> bool:
    """Return True when *token* is a recognized pyright flag/option name."""
    return token in get_pyright_flag_names()


# ============================================================================
# 8. Command safety classification
# ============================================================================


@dataclass
class PyrightCommandClassification:
    """Result of classifying a pyright command for safety/permissions."""

    is_safe: bool
    """True when every flag and positional arg passes safety checks."""

    flags: list[str] = field(default_factory=list)
    """Recognised flags found in the command."""

    dangerous_flags: list[str] = field(default_factory=list)
    """Any dangerous flags that trigger a deny."""

    unknown_flags: list[str] = field(default_factory=list)
    """Flags that are not in the known-safe set."""

    positional_args: list[str] = field(default_factory=list)
    """Non-flag positional arguments (usually file/directory paths)."""

    has_watch: bool = False
    """True when --watch or -w is present (long-running side effect)."""

    reason: str = ""
    """Human-readable explanation when is_safe is False."""

    missing_args: list[str] = field(default_factory=list)
    """Flags that require a value argument but none was provided."""

    invalid_args: list[tuple[str, str, str]] = field(default_factory=list)
    """(flag, value, reason) tuples for flags whose value failed validation."""

    suspicious_paths: list[str] = field(default_factory=list)
    """Positional arguments that look like path-traversal attempts."""


def classify_pyright_command(
    argv: list[str],
    *,
    allowed_dirs: list[str] | None = None,
    validate_flag_args: bool = True,
) -> PyrightCommandClassification:
    """Analyze a pyright argument vector and classify its safety.

    Parameters
    ----------
    argv:
        The full argument list *excluding* the ``pyright`` executable name
        (i.e. ``sys.argv[1:]`` as split by the shell).
    allowed_dirs:
        Optional list of allowed directories.  When provided, positional
        file/directory arguments are checked against this allowlist and any
        paths outside these directories or containing traversal patterns
        are flagged as suspicious.
    validate_flag_args:
        When True (default), validate the *values* of safe string flags
        against their known domains (e.g. ``--pythonversion 9.99`` would
        be flagged as invalid).

    Returns
    -------
    PyrightCommandClassification
        Structured result with ``is_safe`` and detailed breakdown.
    """
    result = PyrightCommandClassification(is_safe=True)
    flag_names = get_pyright_flag_names()

    i = 0
    while i < len(argv):
        token = argv[i]

        # -- Detect --watch / -w (always dangerous: long-running process) --
        if token in ("--watch", "-w"):
            result.has_watch = True
            result.flags.append(token)
            result.dangerous_flags.append(token)
            result.is_safe = False
            if not result.reason:
                result.reason = (
                    "--watch / -w turns pyright into a long-running watcher"
                )
            i += 1
            continue

        # -- Detect other dangerous flags --
        if token in PYRIGHT_DANGEROUS_FLAGS:
            result.flags.append(token)
            result.dangerous_flags.append(token)
            result.is_safe = False
            # Consume argument for dangerous flags that take one
            opt = get_pyright_option(token)
            if opt is not None and opt.arg_count() > 0:
                i += 1  # skip the dangerous flag's argument
            if not result.reason:
                if token == "--createstub":
                    result.reason = "--createstub writes type stubs to disk"
                elif token == "--verifytypes":
                    result.reason = "--verifytypes performs package analysis"
                elif token == "--pythonpath":
                    result.reason = (
                        "--pythonpath can point to arbitrary interpreter"
                    )
                else:
                    result.reason = f"flag {token} is not read-only safe"
            i += 1
            continue

        # -- Check known flag --
        if token in flag_names:
            kind = PYRIGHT_SAFE_FLAGS.get(token)
            if kind is None:
                # Recognized flag name but not in the read-only safe set
                result.flags.append(token)
                result.unknown_flags.append(token)
                result.is_safe = False
                if not result.reason:
                    result.reason = (
                        f"flag {token} is not in the read-only safe set"
                    )
                # Still need to consume an argument if this flag takes one
                opt = get_pyright_option(token)
                if opt is not None and opt.arg_count() > 0 and not opt.args.isOptional:  # type: ignore[union-attr]
                    i += 1  # skip past the unknown flag's arg
                i += 1
                continue

            result.flags.append(token)
            if kind == "string":
                i += 1
                if i >= len(argv):
                    # Missing required argument for a string-valued flag
                    result.missing_args.append(token)
                    result.is_safe = False
                    if not result.reason:
                        result.reason = (
                            f"flag {token} expects a value argument but "
                            "none was provided"
                        )
                    i += 1  # advance past end (will exit loop)
                    continue

                arg_value = argv[i]

                # Validate the argument value against known domains
                if validate_flag_args:
                    valid, err_msg = _validate_flag_arg(token, arg_value)
                    if not valid:
                        result.invalid_args.append((token, arg_value, err_msg))
                        result.is_safe = False
                        if not result.reason:
                            result.reason = (
                                f"invalid value for {token}: {err_msg}"
                            )

            i += 1
            continue

        # -- Token not in flag_names --
        # If it looks like a flag (starts with -) but pyright doesn't
        # recognize it, treat it as unknown/suspicious rather than a
        # positional file argument.
        if token.startswith("-"):
            result.unknown_flags.append(token)
            result.is_safe = False
            if not result.reason:
                result.reason = (
                    f"unrecognized flag '{token}' is not a known pyright option"
                )
            i += 1
            continue

        # -- Not a flag: positional argument (file/directory path) --
        result.positional_args.append(token)

        # Check for path-traversal patterns
        if _is_path_suspicious(token):
            result.suspicious_paths.append(token)
            result.is_safe = False
            if not result.reason:
                result.reason = (
                    f"positional argument '{token}' appears to contain "
                    "path-traversal patterns"
                )

        # Check against allowed directories if provided
        if allowed_dirs is not None and not _is_path_in_allowed_dirs(
            token, allowed_dirs
        ):
            if token not in result.suspicious_paths:
                result.suspicious_paths.append(token)
            result.is_safe = False
            if not result.reason:
                allowed = ", ".join(allowed_dirs)
                result.reason = (
                    f"'{token}' is not within allowed directories: "
                    f"[{allowed}]"
                )

        i += 1

    # Aggregate reasons when multiple issues exist
    if not result.is_safe and not result.reason:
        parts: list[str] = []
        if result.dangerous_flags:
            parts.append(
                f"dangerous flags: {', '.join(result.dangerous_flags)}"
            )
        if result.unknown_flags:
            parts.append(
                f"unknown flags: {', '.join(result.unknown_flags)}"
            )
        if result.missing_args:
            parts.append(
                f"missing arguments for: {', '.join(result.missing_args)}"
            )
        if result.invalid_args:
            parts.append(
                "invalid arguments: "
                + "; ".join(
                    f"{f}={v} ({r})" for f, v, r in result.invalid_args
                )
            )
        if result.suspicious_paths:
            parts.append(
                f"suspicious paths: {', '.join(result.suspicious_paths)}"
            )
        result.reason = "; ".join(parts)

    return result


def is_pyright_command_safe(
    argv: list[str],
    *,
    allowed_dirs: list[str] | None = None,
    validate_flag_args: bool = True,
) -> bool:
    """Convenience: return True when *argv* is a read-only safe pyright invocation."""
    return classify_pyright_command(
        argv,
        allowed_dirs=allowed_dirs,
        validate_flag_args=validate_flag_args,
    ).is_safe


# ============================================================================
# 9. Command builder
# ============================================================================


def build_pyright_command(
    files: list[str] | None = None,
    *,
    project: str | None = None,
    python_version: str | None = None,
    python_platform: str | None = None,
    venv_path: str | None = None,
    typeshed_path: str | None = None,
    level: str | None = None,
    output_json: bool = False,
    verbose: bool = False,
    stats: bool = False,
    dependencies: bool = False,
    warnings: bool = False,
    threads: int | None = None,
) -> list[str]:
    """Build a read-only-safe pyright command-line argument list.

    Only flags from the ``PYRIGHT_SAFE_FLAGS`` set are exposed.  Dangerous
    flags (``--createstub``, ``--verifytypes``, ``--watch``, ``--pythonpath``)
    are deliberately excluded.

    Parameters
    ----------
    files:
        One or more files/directories to analyze.
    project:
        Path to pyproject.toml or config directory (``--project``).
    python_version:
        Python version string e.g. ``"3.12"`` (``--pythonversion``).
    python_platform:
        Target platform e.g. ``"Linux"`` (``--pythonplatform``).
    venv_path:
        Path to virtual-environments directory (``--venvpath``).
    typeshed_path:
        Path to typeshed stubs (``--typeshedpath``).
    level:
        Minimum diagnostic level (``--level``).
    output_json:
        Emit JSON diagnostics (``--outputjson``).
    verbose:
        Emit verbose diagnostics (``--verbose``).
    stats:
        Print performance stats (``--stats``).
    dependencies:
        Emit import dependency info (``--dependencies``).
    warnings:
        Exit 1 on warnings (``--warnings``).
    threads:
        Thread count (``--threads``).  ``None`` = use all cores.

    Returns
    -------
    list[str]
        Argument vector suitable for ``subprocess.run(['pyright'] + args, …)``.

    Raises
    ------
    PyrightValidationError
        When any input value fails domain validation (unknown version,
        invalid platform, non-positive thread count, etc.).
    """
    errors: list[str] = []

    # -- Validate domain-specific values --
    if python_version is not None and python_version not in PYRIGHT_PYTHON_VERSIONS:
        versions = ", ".join(sorted(PYRIGHT_PYTHON_VERSIONS))
        errors.append(
            f"--pythonversion '{python_version}' is not a known version; "
            f"expected one of: {versions}"
        )
    if python_platform is not None and python_platform not in PYRIGHT_PYTHON_PLATFORMS:
        platforms = ", ".join(sorted(PYRIGHT_PYTHON_PLATFORMS))
        errors.append(
            f"--pythonplatform '{python_platform}' is not a known platform; "
            f"expected one of: {platforms}"
        )
    if level is not None and level not in PYRIGHT_DIAGNOSTIC_LEVELS:
        levels = ", ".join(sorted(PYRIGHT_DIAGNOSTIC_LEVELS))
        errors.append(
            f"--level '{level}' is not a valid diagnostic level; "
            f"expected one of: {levels}"
        )
    if threads is not None and threads < 1:
        errors.append(
            f"--threads must be >= 1, got {threads}"
        )

    if errors:
        raise PyrightValidationError(
            "invalid pyright arguments: " + "; ".join(errors),
            code="INVALID_ARG",
        )

    # -- Build the argument vector --
    args: list[str] = []

    if project is not None:
        args.extend(["--project", project])
    if python_version is not None:
        args.extend(["--pythonversion", python_version])
    if python_platform is not None:
        args.extend(["--pythonplatform", python_platform])
    if venv_path is not None:
        args.extend(["--venvpath", venv_path])
    if typeshed_path is not None:
        args.extend(["--typeshedpath", typeshed_path])
    if level is not None:
        args.extend(["--level", level])
    if output_json:
        args.append("--outputjson")
    if verbose:
        args.append("--verbose")
    if stats:
        args.append("--stats")
    if dependencies:
        args.append("--dependencies")
    if warnings:
        args.append("--warnings")
    if threads is not None:
        args.extend(["--threads", str(threads)])

    if files:
        # Basic defense: reject any file arg that contains suspicious
        # path-traversal patterns.
        for f in files:
            if _is_path_suspicious(f):
                raise PyrightSafetyError(
                    f"file argument '{f}' contains path-traversal patterns",
                    code="UNSAFE_PATH",
                )
        args.extend(files)

    return args


# ============================================================================
# 10. Output-format utilities
# ============================================================================

# Schema for a single pyright JSON diagnostic (summary of known fields).
PYRIGHT_DIAGNOSTIC_KEYS: frozenset[str] = frozenset(
    {
        "file",          # source file path
        "severity",      # "error" | "warning" | "information"
        "message",       # human-readable diagnostic message
        "rule",          # rule identifier (e.g. "reportUnknownMemberType")
        "range",         # {start: {line, character}, end: {line, character}}
    }
)

# Schema for the top-level pyright --outputjson summary.
PYRIGHT_SUMMARY_KEYS: frozenset[str] = frozenset(
    {
        "version",             # pyright version string
        "time",                # analysis duration (seconds)
        "generalDiagnostics",  # list of per-file diagnostic objects
        "summary",             # {filesAnalyzed, errorCount, warningCount, …}
    }
)

PyrightSeverity = Literal["error", "warning", "information", "none"]


@dataclass
class PyrightDiagnostic:
    """Typed representation of a single pyright diagnostic message."""

    file: str
    """Source file path."""

    severity: PyrightSeverity
    """Diagnostic severity level."""

    message: str
    """Human-readable diagnostic message."""

    rule: str = ""
    """Rule identifier (e.g. ``"reportUnknownMemberType"``)."""

    line: int = 0
    """1-based line number."""

    character: int = 0
    """1-based character offset."""

    end_line: int = 0
    """1-based end line number."""

    end_character: int = 0
    """1-based end character offset."""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PyrightDiagnostic:
        """Create a ``PyrightDiagnostic`` from a pyright JSON diagnostic dict."""
        rng = d.get("range", {}) or {}
        start = rng.get("start", {}) or {}
        end = rng.get("end", {}) or {}
        return cls(
            file=str(d.get("file", "")),
            severity=d.get("severity", "information"),  # type: ignore[arg-type]
            message=str(d.get("message", "")),
            rule=str(d.get("rule", "")),
            line=start.get("line", 0),
            character=start.get("character", 0),
            end_line=end.get("line", 0),
            end_character=end.get("character", 0),
        )

    def location_string(self) -> str:
        """Format as ``file:line:char`` (matching editor/compiler conventions)."""
        return f"{self.file}:{self.line}:{self.character}"

    def one_line(self) -> str:
        """Single-line summary: severity, location, rule, message."""
        loc = self.location_string()
        rule = f" [{self.rule}]" if self.rule else ""
        return f"{self.severity}: {loc}{rule} {self.message}"


# ---------------------------------------------------------------------------
# Output identification
# ---------------------------------------------------------------------------


def is_pyright_json_output(text: str) -> bool:
    """Heuristic: return True when *text* looks like pyright ``--outputjson`` output."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return "version" in data and "generalDiagnostics" in data


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_pyright_summary(json_text: str) -> dict[str, Any]:
    """Parse pyright ``--outputjson`` output and return the summary block.

    Returns an empty dict when parsing fails (graceful degradation).
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise PyrightParseError(
            f"failed to parse pyright JSON output: {exc}",
            code="JSON_PARSE",
        ) from exc
    if not isinstance(data, dict):
        raise PyrightParseError(
            "pyright output is not a JSON object",
            code="NOT_OBJECT",
        )
    return data.get("summary", {}) or {}


def parse_pyright_output(json_text: str) -> dict[str, Any]:
    """Parse pyright ``--outputjson`` output and return the full data dict.

    Raises
    ------
    PyrightParseError
        When the JSON cannot be parsed or is not a dict.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise PyrightParseError(
            f"failed to parse pyright JSON output: {exc}",
            code="JSON_PARSE",
        ) from exc
    if not isinstance(data, dict):
        raise PyrightParseError(
            "pyright output is not a JSON object",
            code="NOT_OBJECT",
        )
    return data


def extract_diagnostics(
    json_text: str,
    *,
    as_typed: bool = False,
) -> list[dict[str, Any]] | list[PyrightDiagnostic]:
    """Extract the list of per-file diagnostic objects from pyright JSON output.

    Parameters
    ----------
    json_text:
        Raw stdout from ``pyright --outputjson``.
    as_typed:
        When True, return ``PyrightDiagnostic`` instances instead of raw dicts.

    Returns
    -------
    list[dict] | list[PyrightDiagnostic]
        Diagnostics list (empty when none found or parse error).
    """
    try:
        data = parse_pyright_output(json_text)
    except PyrightParseError:
        return []
    raw = data.get("generalDiagnostics", []) or []
    if as_typed:
        return [PyrightDiagnostic.from_dict(d) for d in raw]
    return raw


def pyright_error_count(json_text: str) -> int:
    """Return the error count from pyright JSON output."""
    summary = parse_pyright_summary(json_text)
    return summary.get("errorCount", -1)


def pyright_warning_count(json_text: str) -> int:
    """Return the warning count from pyright JSON output."""
    summary = parse_pyright_summary(json_text)
    return summary.get("warningCount", -1)


def pyright_files_analyzed(json_text: str) -> int:
    """Return the number of files analyzed from pyright JSON output."""
    summary = parse_pyright_summary(json_text)
    return summary.get("filesAnalyzed", -1)


def pyright_version_string(json_text: str) -> str:
    """Return the pyright version string from ``--outputjson`` output."""
    try:
        data = parse_pyright_output(json_text)
    except PyrightParseError:
        return ""
    return str(data.get("version", ""))


def pyright_duration_seconds(json_text: str) -> float:
    """Return the analysis duration in seconds from ``--outputjson`` output."""
    try:
        data = parse_pyright_output(json_text)
    except PyrightParseError:
        return -1.0
    return float(data.get("time", -1.0))


# ---------------------------------------------------------------------------
# Diagnostic filtering
# ---------------------------------------------------------------------------


def filter_diagnostics_by_severity(
    diagnostics: list[dict[str, Any]],
    severity: str,
) -> list[dict[str, Any]]:
    """Return only diagnostics matching a given severity level.

    *severity* is compared case-insensitively.
    """
    sev = severity.lower()
    return [d for d in diagnostics if str(d.get("severity", "")).lower() == sev]


def filter_diagnostics_by_file(
    diagnostics: list[dict[str, Any]],
    file_path: str,
) -> list[dict[str, Any]]:
    """Return only diagnostics for a given file path.

    Performs suffix matching so that both absolute and relative paths work.
    """
    return [
        d
        for d in diagnostics
        if str(d.get("file", "")).endswith(file_path)
        or d.get("file") == file_path
    ]


def filter_diagnostics_by_rule(
    diagnostics: list[dict[str, Any]],
    rule: str,
) -> list[dict[str, Any]]:
    """Return only diagnostics matching a given rule identifier."""
    return [d for d in diagnostics if str(d.get("rule", "")) == rule]


def filter_diagnostics(
    diagnostics: list[dict[str, Any]],
    *,
    severity: str | None = None,
    file_path: str | None = None,
    rule: str | None = None,
) -> list[dict[str, Any]]:
    """Apply multiple filter criteria to a list of diagnostic dicts.

    All supplied criteria must match (AND logic).  Unspecified criteria
    pass everything.
    """
    result = diagnostics
    if severity is not None:
        result = filter_diagnostics_by_severity(result, severity)
    if file_path is not None:
        result = filter_diagnostics_by_file(result, file_path)
    if rule is not None:
        result = filter_diagnostics_by_rule(result, rule)
    return result


def count_diagnostics_by_severity(
    diagnostics: list[dict[str, Any]],
) -> dict[str, int]:
    """Return a tally of diagnostic counts keyed by severity level."""
    counts: dict[str, int] = {}
    for d in diagnostics:
        sev = str(d.get("severity", "unknown")).lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def diagnostic_to_string(diag: dict[str, Any]) -> str:
    """Format a single diagnostic dict as a human-readable one-liner.

    Example output::

        error: src/foo.py:42:15 [reportUnknownMemberType] Type "int" is not assignable...
    """
    file_ = diag.get("file", "<unknown>")
    severity = diag.get("severity", "??")
    message = diag.get("message", "")
    rule = diag.get("rule", "")
    rng = diag.get("range") or {}
    start = rng.get("start", {}) or {}
    line = start.get("line", 0)
    char = start.get("character", 0)

    rule_part = f" [{rule}]" if rule else ""
    return f"{severity}: {file_}:{line}:{char}{rule_part} {message}"


def format_diagnostics_summary(json_text: str) -> str:
    """Produce a compact human-readable summary of pyright JSON output.

    Covers error/warning counts, files analyzed, and a per-severity tally
    of diagnostics.  Returns a descriptive error string when JSON is invalid.
    """
    try:
        data = parse_pyright_output(json_text)
    except PyrightParseError as exc:
        return f"(pyright output parse error: {exc})"

    summary = data.get("summary", {}) or {}
    errors = summary.get("errorCount", 0)
    warnings = summary.get("warningCount", 0)
    infos = summary.get("informationCount", 0)
    files = summary.get("filesAnalyzed", 0)
    duration = data.get("time", -1)
    version = data.get("version", "?")

    lines: list[str] = [
        f"pyright {version} — {files} file(s) in {duration:.1f}s",
        f"  errors: {errors}  warnings: {warnings}  infos: {infos}",
    ]

    # Per-severity breakdown from raw diagnostics
    diags = data.get("generalDiagnostics", []) or []
    if diags:
        tally = count_diagnostics_by_severity(diags)
        lines.append(
            "  by severity: "
            + ", ".join(f"{sev}={n}" for sev, n in sorted(tally.items()))
        )

    return "\n".join(lines)


def format_diagnostics_table(
    diagnostics: list[dict[str, Any]],
    *,
    max_lines: int = 50,
) -> str:
    """Format a list of diagnostic dicts as a readable table.

    Truncates to *max_lines* entries and appends a truncation note.
    """
    if not diagnostics:
        return "(no diagnostics)"

    header = f"{'Severity':<12} {'File':<30} {'Line':>6} {'Rule':<30} Message"
    separator = "-" * len(header)
    rows: list[str] = [header, separator]

    for d in diagnostics[:max_lines]:
        sev = str(d.get("severity", "??"))[:11]
        file_ = str(d.get("file", ""))
        if len(file_) > 29:
            file_ = "…" + file_[-28:]
        rng = d.get("range") or {}
        start = rng.get("start", {}) or {}
        line = str(start.get("line", "?"))
        rule = str(d.get("rule", ""))[:29]
        msg = str(d.get("message", ""))
        rows.append(f"{sev:<12} {file_:<30} {line:>6} {rule:<30} {msg}")

    if len(diagnostics) > max_lines:
        rows.append(
            f"... and {len(diagnostics) - max_lines} more diagnostics"
        )

    return "\n".join(rows)


def format_diagnostics_by_file(
    diagnostics: list[dict[str, Any]],
) -> str:
    """Group diagnostics by file path and format as a readable report."""
    if not diagnostics:
        return "(no diagnostics)"

    by_file: dict[str, list[dict[str, Any]]] = {}
    for d in diagnostics:
        f = str(d.get("file", "<unknown>"))
        by_file.setdefault(f, []).append(d)

    blocks: list[str] = []
    for file_path, diags in sorted(by_file.items()):
        tally = count_diagnostics_by_severity(diags)
        tag = ", ".join(f"{s}={n}" for s, n in sorted(tally.items()))
        blocks.append(f"── {file_path}  ({len(diags)}: {tag})")
        for d in sorted(
            diags, key=lambda x: (x.get("range", {}).get("start", {}).get("line", 0) or 0)
        ):
            rng = d.get("range") or {}
            start = rng.get("start", {}) or {}
            line = start.get("line", "?")
            sev = d.get("severity", "??")
            msg = d.get("message", "")
            rule = d.get("rule", "")
            rule_s = f"  [{rule}]" if rule else ""
            blocks.append(f"  {sev}:{line}{rule_s} {msg}")
        blocks.append("")

    return "\n".join(blocks)


# ============================================================================
# 11. Safe flag argument validators
# ============================================================================

# Mapping from safe flag names to their validation functions.
# Each validator receives the argument value and returns (is_valid, error_message).
_FLAG_VALIDATORS: dict[str, Any] = {}  # populated below


def _build_flag_validators() -> dict[str, Any]:
    """Build the flag-validator registry lazily (breaks circular refs)."""
    return {
        "--pythonversion": _validate_python_version,
        "--pythonplatform": _validate_python_platform,
        "--level": _validate_diagnostic_level,
        "--project": _validate_path_arg,
        "-p": _validate_path_arg,
        "--typeshedpath": _validate_path_arg,
        "-t": _validate_path_arg,
        "--venvpath": _validate_path_arg,
        "-v": _validate_path_arg,
    }


def _validate_flag_arg(flag: str, value: str) -> tuple[bool, str]:
    """Validate a safe flag's string argument against its domain.

    Returns ``(True, "")`` when valid, ``(False, reason)`` otherwise.
    Flags without a registered validator always pass.
    """
    if not _FLAG_VALIDATORS:
        _FLAG_VALIDATORS.update(_build_flag_validators())
    validator = _FLAG_VALIDATORS.get(flag)
    if validator is None:
        return True, ""
    return validator(value)


def _validate_python_version(value: str) -> tuple[bool, str]:
    """Validate ``--pythonversion`` argument."""
    if value in PYRIGHT_PYTHON_VERSIONS:
        return True, ""
    versions = ", ".join(sorted(PYRIGHT_PYTHON_VERSIONS))
    return False, f"'{value}' is not a known Python version; expected one of: {versions}"


def _validate_python_platform(value: str) -> tuple[bool, str]:
    """Validate ``--pythonplatform`` argument."""
    if value in PYRIGHT_PYTHON_PLATFORMS:
        return True, ""
    platforms = ", ".join(sorted(PYRIGHT_PYTHON_PLATFORMS))
    return False, f"'{value}' is not a known platform; expected one of: {platforms}"


def _validate_diagnostic_level(value: str) -> tuple[bool, str]:
    """Validate ``--level`` argument."""
    if value in PYRIGHT_DIAGNOSTIC_LEVELS:
        return True, ""
    levels = ", ".join(sorted(PYRIGHT_DIAGNOSTIC_LEVELS))
    return False, f"'{value}' is not a valid diagnostic level; expected one of: {levels}"


def _validate_path_arg(value: str) -> tuple[bool, str]:
    """Validate a path-type argument (``--project``, ``--typeshedpath``, etc.).

    Rejects empty paths and paths containing suspicious traversal patterns.
    """
    if not value or not value.strip():
        return False, "path argument must not be empty"
    if _is_path_suspicious(value):
        return False, f"path '{value}' contains suspicious traversal patterns"
    return True, ""


def validate_pyright_flag_arg(flag: str, value: str) -> tuple[bool, str]:
    """Public entry point: validate a safe flag's argument value.

    Returns
    -------
    (bool, str)
        ``(True, "")`` when valid; ``(False, reason_string)`` otherwise.
    """
    return _validate_flag_arg(flag, value)


# ============================================================================
# 12. Path safety
# ============================================================================

# Patterns that indicate potential path-traversal abuse.
_PATH_TRAVERSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.\./"),                      # relative traversal
    re.compile(r"\.\.\\"),                     # Windows-style traversal
    re.compile(r"^~"),                         # home-directory expansion
    re.compile(r"\$[{]?[A-Za-z_][A-Za-z0-9_]*"),  # env-var expansion
    re.compile(r"`[^`]*`"),                   # backtick command substitution
    re.compile(r"\$\([^)]*\)"),                # $(command) substitution
    re.compile(r"&&|;|\|\||\|"),               # command chaining separators
    re.compile(r"/etc/passwd"),                # sensitive file (canary)
    re.compile(r"/etc/shadow"),                # sensitive file (canary)
    re.compile(r"^/dev/"),                     # device files
    re.compile(r"^/proc/"),                    # proc filesystem
    re.compile(r"^/sys/"),                     # sys filesystem
]


def _is_path_suspicious(path: str) -> bool:
    """Return True when *path* matches known path-traversal / injection patterns."""
    if not path:
        return False
    for pat in _PATH_TRAVERSAL_PATTERNS:
        if pat.search(path):
            return True
    return False


def _resolve_path(path: str) -> str | None:
    """Resolve *path* to a canonical absolute form.

    Tries ``os.path.realpath`` first (resolves symlinks), falling back to
    ``os.path.abspath`` when the path does not exist on disk.  Returns
    ``None`` when resolution fails entirely.
    """
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        try:
            return os.path.abspath(path)
        except (OSError, ValueError):
            return None


def _is_path_in_allowed_dirs(path: str, allowed_dirs: list[str]) -> bool:
    """Return True when *path* resolves inside one of the allowed directories.

    Resolves both the candidate path and each allowed directory to canonical
    forms, then checks prefix containment (with separator).
    """
    resolved = _resolve_path(path)
    if resolved is None:
        return False
    for ad in allowed_dirs:
        resolved_ad = _resolve_path(ad)
        if resolved_ad is None:
            continue
        if resolved == resolved_ad or resolved.startswith(
            resolved_ad + os.sep
        ):
            return True
    return False


def is_path_safe(path: str) -> bool:
    """Return True when *path* does not match known traversal/injection patterns."""
    return not _is_path_suspicious(path)


def validate_safe_path(path: str) -> tuple[bool, str]:
    """Validate a single path argument.

    Returns
    -------
    (bool, str)
        ``(True, "")`` when the path passes all safety checks;
        ``(False, reason)`` otherwise.
    """
    if not path or not path.strip():
        return False, "path argument must not be empty"
    if _is_path_suspicious(path):
        return False, f"path '{path}' matches suspicious traversal/injection patterns"
    return True, ""


def validate_safe_paths(
    paths: list[str],
    *,
    allowed_dirs: list[str] | None = None,
) -> tuple[bool, str]:
    """Validate a list of path arguments for safety.

    Checks every path against traversal heuristics and, when *allowed_dirs*
    is provided, verifies each path resolves inside one of the allowed
    directories.

    Returns
    -------
    (bool, str)
        ``(True, "")`` when all paths pass; ``(False, reason)`` when any fail.
    """
    for p in paths:
        ok, err = validate_safe_path(p)
        if not ok:
            return False, f"unsafe path '{p}': {err}"
    if allowed_dirs is not None:
        for p in paths:
            if not _is_path_in_allowed_dirs(p, allowed_dirs):
                return False, (
                    f"'{p}' is not within allowed directories: "
                    f"[{', '.join(allowed_dirs)}]"
                )
    return True, ""


# ============================================================================
# 13. Help text generation
# ============================================================================


def pyright_help_text() -> str:
    """Generate a human-readable help summary for pyright, derived from the
    typed ``PYRIGHT_COMMAND_SPEC``."""
    lines: list[str] = [
        f"Usage: pyright [options] [files...]",
        "",
        f"{PYRIGHT_COMMAND_SPEC.description}",
        "",
        "Options:",
    ]

    for opt in PYRIGHT_COMMAND_SPEC.options:
        names = ", ".join(opt.all_names)
        desc = opt.description
        if opt.args is not None:
            if isinstance(opt.args, list):
                arg_names = ", ".join(
                    a.name for a in opt.args if not a.isOptional
                )
            else:
                arg_names = opt.args.name
            names += f" <{arg_names}>"
        lines.append(f"  {names:<36} {desc}")

    if PYRIGHT_COMMAND_SPEC.args is not None:
        lines.append("")
        arg = PYRIGHT_COMMAND_SPEC.args
        opt_tag = " (optional)" if arg.isOptional else ""
        var_tag = " [files...]" if arg.isVariadic else ""
        lines.append(
            f"  {arg.name}{var_tag}{opt_tag}  {arg.description}"
        )

    lines.append("")
    lines.append("Exit codes:")
    for code, desc in PYRIGHT_EXIT_CODES.items():
        lines.append(f"  {code}  {desc}")

    return "\n".join(lines)


# ============================================================================
# 14. Dict-to-typed spec conversion
# ============================================================================


def _parse_option_arg(arg_dict: dict[str, Any]) -> OptionArg:
    """Convert a dict-style option arg to a typed ``OptionArg``."""
    return OptionArg(
        name=arg_dict.get("name", ""),
        description=arg_dict.get("description", ""),
        isOptional=arg_dict.get("isOptional", False),
        isVariadic=arg_dict.get("isVariadic", False),
        isCommand=arg_dict.get("isCommand", False),
        isDangerous=arg_dict.get("isDangerous", False),
        isModule=arg_dict.get("isModule", False),
        isScript=arg_dict.get("isScript", False),
    )


def _parse_option(opt_dict: dict[str, Any]) -> Option:
    """Convert a dict-style option to a typed ``Option``."""
    name: str | list[str] = opt_dict.get("name", "")
    # Normalize single string to list for consistency (Option handles both)
    args_raw = opt_dict.get("args")
    args: OptionArg | list[OptionArg] | None = None
    if isinstance(args_raw, list):
        args = [_parse_option_arg(a) for a in args_raw]
    elif isinstance(args_raw, dict):
        args = _parse_option_arg(args_raw)
    return Option(
        name=name,
        description=opt_dict.get("description", ""),
        args=args,
        isRequired=opt_dict.get("isRequired", False),
    )


def dict_to_command_spec(spec_dict: dict[str, Any]) -> CommandSpec:
    """Convert a legacy dict-style spec (such as ``PYRIGHT_SPEC``) into
    a typed ``CommandSpec`` instance.

    This enables interop between the dict representation consumed by
    ``index.py`` and the typed dataclass APIs.
    """
    options = [_parse_option(o) for o in spec_dict.get("options", [])]
    subcommands = [
        dict_to_command_spec(s) for s in spec_dict.get("subcommands", [])
    ]
    args_raw = spec_dict.get("args")
    args: OptionArg | None = None
    if isinstance(args_raw, dict):
        args = _parse_option_arg(args_raw)
    return CommandSpec(
        name=spec_dict.get("name", ""),
        description=spec_dict.get("description", ""),
        options=options,
        args=args,
        subcommands=subcommands,
    )


# ============================================================================
# 15. Quick-lookup helpers for external consumers
# ============================================================================


def get_all_pyright_safe_flags() -> dict[str, PyrightArgKind]:
    """Return a copy of the safe-flags mapping (flag name -> argument kind)."""
    return dict(PYRIGHT_SAFE_FLAGS)


def get_all_pyright_dangerous_flags() -> frozenset[str]:
    """Return the set of always-dangerous flag names."""
    return PYRIGHT_DANGEROUS_FLAGS


def pyright_flag_is_safe(flag: str) -> bool:
    """Return True when *flag* is in the read-only safe set."""
    return flag in PYRIGHT_SAFE_FLAGS


def pyright_flag_is_dangerous(flag: str) -> bool:
    """Return True when *flag* is always dangerous (never auto-approvable)."""
    return flag in PYRIGHT_DANGEROUS_FLAGS
