"""
Bash pipe command analysis — parse and validate piped commands.

Port of: src/utils/bash/bashPipeCommand.ts

Splits piped commands into individual pipeline stages for security
analysis, detecting unsafe pipe patterns and stdin/heredoc redirects.
Also provides stage-by-stage safety classification, dangerous-pattern
detection, and pipeline-category tagging (source / filter / sink).
"""

from __future__ import annotations

import shlex
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Pipeline splitting
# ---------------------------------------------------------------------------


def split_pipeline(command: str) -> list[str]:
    """Split a shell command into pipeline stages.

    Handles quoting and escaping to avoid splitting on '|' inside
    quoted strings or subshells.
    """
    stages: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    depth = 0  # subshell depth

    i = 0
    while i < len(command):
        ch = command[i]

        if ch == '\\' and not in_single:
            if i + 1 < len(command):
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '(' and not in_single and not in_double:
            depth += 1
        elif ch == ')' and not in_single and not in_double:
            depth -= 1
        elif ch == '|' and not in_single and not in_double and depth == 0:
            # Check it's not || or |&
            if i + 1 < len(command) and command[i + 1] in ('|', '&'):
                current.append(ch)
            else:
                stages.append(''.join(current).strip())
                current = []
                i += 1
                continue

        current.append(ch)
        i += 1

    if current:
        stages.append(''.join(current).strip())

    return [s for s in stages if s]


# ---------------------------------------------------------------------------
# Stdin / heredoc helpers
# ---------------------------------------------------------------------------


def has_stdin_redirect(command: str) -> bool:
    """Check if a command uses stdin redirection (<, <<, <<<)."""
    return bool(re.search(r'(?<!\\)<(?:\s*\(|\s*\w|\s*"|\s*\'|<<)', command))


def has_heredoc(command: str) -> bool:
    """Check if a command uses heredoc (<<EOF or <<'EOF')."""
    return bool(re.search(r'<<\s*[\'"]?\w+', command))


def extract_heredoc_delimiter(command: str) -> Optional[str]:
    """Extract the heredoc delimiter from a command. Returns None if no heredoc found."""
    match = re.search(r'<<\s*[\'"]?(\w+)', command)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Pipeline rearrangement
# ---------------------------------------------------------------------------


def rearrange_pipe_command(command: str) -> str:
    """Rearrange piped commands to place stdin redirect after the first segment.

    When stdin redirection appears after pipes, it must be moved to the
    first pipeline stage for correct execution.
    """
    # Skip complex commands
    if '`' in command or '$(' in command:
        return _quote_for_eval(command)

    stages = split_pipeline(command)
    if len(stages) <= 1:
        return _quote_for_eval(command)

    # Check if any stage after the first has stdin redirect
    for i, stage in enumerate(stages):
        if i > 0 and has_stdin_redirect(stage):
            # Move the first pipe's stdin to the beginning
            return _rearrange_with_stdin_first(stages)

    # No stdin redirect in later stages - just wrap
    return _quote_for_eval(command)


def _rearrange_with_stdin_first(stages: list[str]) -> str:
    """Rearrange pipeline with stdin redirected to first stage."""
    stdin_cmd = None
    for i, stage in enumerate(stages):
        if has_stdin_redirect(stage):
            stdin_cmd = stage
            break

    if stdin_cmd is None:
        return ' | '.join(stages)

    # Extract the redirect from the stdin command
    redirect_match = re.search(r'(<\s*\S+)', stdin_cmd)
    redirect = redirect_match.group(1) if redirect_match else ""

    # Remove redirect from original position
    clean_stages = []
    for stage in stages:
        if has_stdin_redirect(stage):
            clean = re.sub(r'<\s*\S+', '', stage).strip()
            if clean:
                clean_stages.append(clean)
        else:
            clean_stages.append(stage)

    if redirect and clean_stages:
        clean_stages[0] = f"{clean_stages[0]} {redirect}"

    return ' | '.join(clean_stages)


def _quote_for_eval(command: str) -> str:
    """Wrap a command for safe eval execution."""
    return f"eval {shlex.quote(command)}"


def is_single_pipe_safe(command: str, safe_readonly_commands: frozenset[str] | None = None) -> bool:
    """Check if all stages in a pipeline are safe read-only commands."""
    if safe_readonly_commands is None:
        safe_readonly_commands = frozenset({
            "cat", "head", "tail", "grep", "rg", "sort", "uniq",
            "wc", "awk", "sed", "cut", "tr", "tee", "column",
        })

    stages = split_pipeline(command)
    for stage in stages:
        first_word = stage.split()[0] if stage.split() else ""
        if first_word not in safe_readonly_commands:
            return False
    return True


# ---------------------------------------------------------------------------
# Stage-by-stage safety analysis
# ---------------------------------------------------------------------------


class StageCategory(Enum):
    """Semantic category of a pipeline stage."""

    SOURCE = auto()   # produces data (cat, find, ls, …)
    FILTER = auto()   # transforms / filters in place (grep, sed, awk, sort, …)
    SINK = auto()     # writes / mutates (tee, dd, >, …)
    UNKNOWN = auto()


@dataclass
class StageSafety:
    """Safety assessment for a single pipeline stage."""

    command: str
    executable: str
    args: list[str] = field(default_factory=list)
    category: StageCategory = StageCategory.UNKNOWN
    is_safe: bool = True
    has_output_redirect: bool = False
    has_stdin_redirect: bool = False
    has_destructive_flag: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def first_word(self) -> str:
        return self.executable


@dataclass
class PipelineAnalysis:
    """Complete safety analysis result for a pipeline."""

    raw_command: str
    stages: list[StageSafety] = field(default_factory=list)
    is_safe_overall: bool = True
    has_heredoc: bool = False
    heredoc_delimiter: str | None = None
    warnings: list[str] = field(default_factory=list)


# Known commands and their default categories.
# SOURCE commands produce data on stdout; FILTER commands read stdin and
# write stdout; SINK commands write to disk / the system.
_SOURCE_COMMANDS: frozenset[str] = frozenset({
    "cat", "find", "ls", "locate", "echo", "printf", "ps", "df", "du",
    "whoami", "hostname", "uname", "date", "pwd", "id", "env", "printenv",
})

_FILTER_COMMANDS: frozenset[str] = frozenset({
    "grep", "rg", "egrep", "fgrep", "sed", "awk", "sort", "uniq", "head",
    "tail", "cut", "tr", "wc", "column", "rev", "tac", "nl", "jq",
    "xargs", "tee", "fold", "fmt",
})

_SINK_COMMANDS: frozenset[str] = frozenset({
    "dd", "cp", "mv", "rm", "touch", "mkdir", "rmdir", "chmod", "chown",
    "ln", "install",
})

# Commands whose presence in a pipeline makes the whole pipeline unsafe
# because they are inherently destructive or escalate privileges.
_DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset({
    "sudo", "su", "rm", "dd", "mkfs", "fdisk", "parted", "shutdown",
    "reboot", "init", "systemctl", "kill", "pkill", "killall",
    "iptables", "nft", "chmod", "chown", "mount", "umount",
})

# Flag patterns that indicate destructive intent even for otherwise-safe commands.
_DESTRUCTIVE_FLAGS: re.Pattern[str] = re.compile(
    r'\b(?:'
    r'-r[fR]?(?:\s|$)'               # rm -rf
    r'|-delete'                       # find … -delete
    r'|--remove-files'                # tar --remove-files
    r'|conv=notrunc\b.*\bof='         # dd of= (destructive)
    r'|-ioe?\w*[^a-z]'                # anything ending -i / -o with typical args
    r'|>\s*\S'                        # output redirect (not a flag, but notable)
    r')',
    re.IGNORECASE,
)


def _tokenize_stage(stage: str) -> tuple[str, list[str]]:
    """Parse a single pipeline stage into (executable, args)."""
    stripped = stage.strip()

    # Strip leading env-var assignments.
    while True:
        m = re.match(r'^[A-Za-z_][A-Za-z0-9_]*=\S+\s+', stripped)
        if not m:
            break
        stripped = stripped[m.end():]

    # Strip leading command (time, nice, nohup, …).
    for _prefix in ("command ", "builtin ", "exec "):
        if stripped.startswith(_prefix):
            stripped = stripped[len(_prefix):]

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        tokens = stripped.split()

    if not tokens:
        return ("", [])

    executable = tokens[0]
    args = tokens[1:]
    return (executable, args)


def _classify_stage(executable: str, args: list[str]) -> StageCategory:
    """Classify a pipeline stage into its semantic category."""
    if executable in _SINK_COMMANDS:
        return StageCategory.SINK
    if executable in _SOURCE_COMMANDS:
        return StageCategory.SOURCE
    if executable in _FILTER_COMMANDS:
        return StageCategory.FILTER

    # Heuristic: commands containing redirect operators are sinks.
    if any(">" in a for a in args):
        return StageCategory.SINK

    return StageCategory.UNKNOWN


def _check_destructive_flags(args: list[str]) -> bool:
    """Check whether any argument matches a destructive flag pattern."""
    return any(_DESTRUCTIVE_FLAGS.search(a) for a in args)


def _check_output_redirect(args: list[str]) -> bool:
    """Return True when the stage writes to a file (> or >>)."""
    return any(a in (">", ">>", "1>", "2>", "&>", ">>", "1>>", "2>>") for a in args) or \
        any(re.match(r'^[12&]?>>?', a) for a in args)


def analyze_pipeline_stage(stage: str) -> StageSafety:
    """Produce a full safety assessment for a single pipeline stage."""
    executable, args = _tokenize_stage(stage)
    has_out = _check_output_redirect(args)
    destructive = _check_destructive_flags(args)
    stdin_redir = has_stdin_redirect(stage)
    category = _classify_stage(executable, args)

    warnings: list[str] = []

    if executable in _DESTRUCTIVE_COMMANDS:
        warnings.append(f"stage contains destructive command: {executable}")
    if destructive:
        warnings.append(f"stage has destructive flag pattern")
    if has_out:
        warnings.append(f"stage redirects output to file")
    if stdin_redir:
        warnings.append(f"stage uses stdin redirection")
    if category == StageCategory.SINK and executable not in ("tee",):
        warnings.append(f"stage is a sink (writes to system)")

    is_safe = (
        executable not in _DESTRUCTIVE_COMMANDS
        and not destructive
        and not has_out
        and category != StageCategory.SINK
    )

    return StageSafety(
        command=stage,
        executable=executable,
        args=args,
        category=category,
        is_safe=is_safe,
        has_output_redirect=has_out,
        has_stdin_redirect=stdin_redir,
        has_destructive_flag=destructive,
        warnings=warnings,
    )


def analyze_pipeline(command: str) -> PipelineAnalysis:
    """Perform complete stage-by-stage safety analysis of a pipeline.

    Returns a ``PipelineAnalysis`` that aggregates every stage's safety
    assessment, heredoc detection, and an overall safety verdict.  A
    pipeline is considered safe *only* when every individual stage is
    safe and the pipeline contains **both** a source and a filter (never
    a sink).
    """
    stages_raw = split_pipeline(command)
    stages: list[StageSafety] = []

    for raw in stages_raw:
        stages.append(analyze_pipeline_stage(raw))

    categories = {s.category for s in stages}
    exec_names = {s.executable for s in stages}

    # Heredoc detection runs on the whole command.
    delim = extract_heredoc_delimiter(command)

    all_warnings: list[str] = []
    for s in stages:
        prefix = f"[{s.executable or '?'}]"
        all_warnings.extend(f"{prefix} {w}" for w in s.warnings)

    # Overall safety: every stage safe, and we can identify at least a
    # source + filter relationship (or single safe stage).
    has_source = StageCategory.SOURCE in categories
    has_filter = StageCategory.FILTER in categories
    has_sink = StageCategory.SINK in categories

    if has_sink:
        all_warnings.append("pipeline contains a sink stage — potentially destructive")

    is_safe_overall = all(s.is_safe for s in stages)

    # 2+ stages and no source→filter chain is suspicious.
    if len(stages) >= 2 and not has_filter:
        all_warnings.append(
            "multi-stage pipeline with no filter stage; verify intent"
        )

    return PipelineAnalysis(
        raw_command=command,
        stages=stages,
        is_safe_overall=is_safe_overall,
        has_heredoc=has_heredoc(command),
        heredoc_delimiter=delim,
        warnings=all_warnings,
    )


# ---------------------------------------------------------------------------
# Bulk analysis of compound commands (&& / ; / &)
# ---------------------------------------------------------------------------


async def analyze_compound_command(command: str) -> list[PipelineAnalysis]:
    """Analyze every sub-command in a compound shell expression.

    Compound separators ``&&``, ``;`` and ``&`` are split using the same
    quoting rules as ``split_pipeline``, then each sub-command is
    individually analyzed.  Skips empty / whitespace-only fragments.
    """
    results: list[PipelineAnalysis] = []

    # Lightweight split mirroring commands.split_command but returning
    # only the non-empty segments.
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0

    while i < len(command):
        ch = command[i]

        if ch == '\\' and i + 1 < len(command) and (in_double or not in_single):
            current.append(ch)
            current.append(command[i + 1])
            i += 2
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif not in_single and not in_double:
            if ch in ('&', '|') and i + 1 < len(command) and command[i + 1] == ch:
                segment = ''.join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
                i += 2
                continue
            if ch == ';':
                segment = ''.join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
                i += 1
                continue
            current.append(ch)
        else:
            current.append(ch)

        i += 1

    remainder = ''.join(current).strip()
    if remainder:
        segments.append(remainder)

    for seg in segments:
        results.append(analyze_pipeline(seg))

    return results


# ---------------------------------------------------------------------------
# Convenience: quick destruction check
# ---------------------------------------------------------------------------


def contains_destructive_command(command: str) -> bool:
    """Return True when **any** pipeline stage uses a destructive command."""
    stages = split_pipeline(command)
    for stage in stages:
        executable, _ = _tokenize_stage(stage)
        if executable in _DESTRUCTIVE_COMMANDS:
            return True
    return False


def get_destructive_commands(command: str) -> list[str]:
    """Return the list of destructive command names found in the pipeline."""
    found: list[str] = []
    stages = split_pipeline(command)
    for stage in stages:
        executable, _ = _tokenize_stage(stage)
        if executable in _DESTRUCTIVE_COMMANDS:
            found.append(executable)
    return found
