"""
PowerShell AST parsing via pwsh — spawns pwsh with an inlined AST-analysis
script, parses JSON output, returns structured command elements.

Port of: src/utils/powershell/parser.ts
"""

from __future__ import annotations

import asyncio, base64, json, re, shutil
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Alias table (keys and values are lowercase for case-insensitive matching)
# ---------------------------------------------------------------------------

COMMON_ALIASES: dict[str, str] = {
    "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem",
    "cd": "set-location", "sl": "set-location", "chdir": "set-location",
    "pwd": "get-location", "gl": "get-location",
    "cat": "get-content", "type": "get-content", "gc": "get-content",
    "ri": "remove-item", "del": "remove-item", "rd": "remove-item",
    "rmdir": "remove-item", "rm": "remove-item", "erase": "remove-item",
    "mi": "move-item", "mv": "move-item", "move": "move-item",
    "ci": "copy-item", "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item",
    "si": "set-item", "rni": "rename-item", "ren": "rename-item",
    "sp": "set-itemproperty", "rp": "remove-itemproperty",
    "ni": "new-item", "mkdir": "new-item", "md": "new-item",
    "ac": "add-content", "clc": "clear-content",
    "ps": "get-process", "gps": "get-process",
    "kill": "stop-process", "spps": "stop-process",
    "start": "start-process", "saps": "start-process",
    "iex": "invoke-expression", "iwr": "invoke-webrequest",
    "irm": "invoke-restmethod", "icm": "invoke-command", "ii": "invoke-item",
    "ipmo": "import-module", "nsn": "new-pssession", "etsn": "enter-pssession",
    "echo": "write-output", "write": "write-output",
    "tee": "tee-object", "epcsv": "export-csv",
    "select": "select-object", "where": "where-object",
    "%": "foreach-object", "?": "where-object",
    "ft": "format-table", "fl": "format-list", "fw": "format-wide",
    "sls": "select-string", "gv": "get-variable", "sv": "set-variable",
    "help": "get-help", "man": "get-help", "gcm": "get-command",
    "h": "get-history", "history": "get-history", "gsv": "get-service",
    "sleep": "start-sleep", "cls": "clear-host", "clear": "clear-host",
}

_CMD_RE = re.compile(r"^[A-Za-z]+-[A-Za-z][A-Za-z0-9_]*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_NONASCII_RE = re.compile(r"[-￿]")
_PS_DASH = frozenset({"-", "–", "—", "―"})

# ---------------------------------------------------------------------------
# Inlined PowerShell AST-analysis script.
# User command injected via $EncodedCommand (base64-utf8), output is
# ConvertTo-Json -Depth 10 -Compress.
# ---------------------------------------------------------------------------

_PARSE_SCRIPT = r"""$Command=[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($EncodedCommand))
$tokens=$null;$parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput($Command,[ref]$tokens,[ref]$parseErrors)
$allVars=@();foreach($v in $ast.FindAll({param($n)$n -is [System.Management.Automation.Language.VariableExpressionAst]},$true)){$allVars+=@{path=$v.VariablePath.ToString();isSplatted=[bool]$v.Splatted}}
$hasStop=$false;foreach($tk in $tokens){if($tk.Kind -eq [System.Management.Automation.Language.TokenKind]::MinusMinus){$hasStop=$true;break}}
$stmts=@()
function gce($c){$e=@();foreach($x in $c.CommandElements){$d=@{type=$x.GetType().Name;text=$x.Extent.Text};if($x.PSObject.Properties['Value']-and $null -ne $x.Value -and $x.Value -is [string]){$d.value=$x.Value};if($x -is [System.Management.Automation.Language.CommandExpressionAst]){$d.expressionType=$x.Expression.GetType().Name};$a=$x.Argument;if($a){$d.children=@(@{type=$a.GetType().Name;text=$a.Extent.Text})};$e+=$d};return $e}
function gr($r){$o=@();foreach($x in $r){$d=@{type=$x.GetType().Name};if($x -is [System.Management.Automation.Language.FileRedirectionAst]){$d.append=[bool]$x.Append;$d.fromStream=$x.FromStream.ToString();$d.locationText=$x.Location.Extent.Text};$o+=$d};return $o}
function pb($b){if(-not $b){return};foreach($s in $b.Statements){$st=@{type=$s.GetType().Name;text=$s.Extent.Text};if($s -is [System.Management.Automation.Language.PipelineAst]){$el=@();foreach($e in $s.PipelineElements){$ed=@{type=$e.GetType().Name;text=$e.Extent.Text};if($e -is [System.Management.Automation.Language.CommandAst]){$ed.commandElements=@(gce $e);$ed.redirections=@(gr $e.Redirections)}elseif($e -is [System.Management.Automation.Language.CommandExpressionAst]){$ed.expressionType=$e.Expression.GetType().Name;$ed.redirections=@(gr $e.Redirections)};$el+=$ed};$st.elements=@($el);$nc=$s.FindAll({param($n)$n -is [System.Management.Automation.Language.CommandAst]},$true);$ncl=@();foreach($c in $nc){if($c.Parent -eq $s){continue};$ncl+=@{type=$c.GetType().Name;text=$c.Extent.Text;commandElements=@(gce $c);redirections=@(gr $c.Redirections)}};if($ncl.Count -gt 0){$st.nestedCommands=@($ncl)};$r=$s.FindAll({param($n)$n -is [System.Management.Automation.Language.FileRedirectionAst]},$true);if($r.Count -gt 0){$st.redirections=@(gr $r)}}else{$nc=$s.FindAll({param($n)$n -is [System.Management.Automation.Language.CommandAst]},$true);$ncl=@();foreach($c in $nc){$ncl+=@{type='CommandAst';text=$c.Extent.Text;commandElements=@(gce $c);redirections=@(gr $c.Redirections)}};if($ncl.Count -gt 0){$st.nestedCommands=@($ncl)};$r=$s.FindAll({param($n)$n -is [System.Management.Automation.Language.FileRedirectionAst]},$true);if($r.Count -gt 0){$st.redirections=@(gr $r)}};$stmts+=$st};if($b.Traps){foreach($t in $b.Traps){$st=@{type='TrapStatementAst';text=$t.Extent.Text};$nc=$t.FindAll({param($n)$n -is [System.Management.Automation.Language.CommandAst]},$true);$ncl=@();foreach($c in $nc){$ncl+=@{type=$c.GetType().Name;text=$c.Extent.Text;commandElements=@(gce $c);redirections=@(gr $c.Redirections)}};if($ncl.Count -gt 0){$st.nestedCommands=@($ncl)};$r=$t.FindAll({param($n)$n -is [System.Management.Automation.Language.FileRedirectionAst]},$true);if($r.Count -gt 0){$st.redirections=@(gr $r)};$stmts+=$st}}}
pb $ast.BeginBlock;pb $ast.ProcessBlock;pb $ast.EndBlock;pb $ast.CleanBlock;pb $ast.DynamicParamBlock
if($ast.ParamBlock){$pb=$ast.ParamBlock;$pn=@();foreach($c in $pb.FindAll({param($n)$n -is [System.Management.Automation.Language.CommandAst]},$true)){$pn+=@{type='CommandAst';text=$c.Extent.Text;commandElements=@(gce $c);redirections=@(gr $c.Redirections)}};$pr=$pb.FindAll({param($n)$n -is [System.Management.Automation.Language.FileRedirectionAst]},$true);if($pn.Count -gt 0 -or $pr.Count -gt 0){$st=@{type='ParamBlockAst';text=$pb.Extent.Text};if($pn.Count -gt 0){$st.nestedCommands=@($pn)};if($pr.Count -gt 0){$st.redirections=@(gr $pr)};$stmts+=$st}}
$hasUsing=$ast.UsingStatements -and $ast.UsingStatements.Count -gt 0;$hasReq=$ast.ScriptRequirements -ne $null
@{valid=($parseErrors.Count -eq 0);errors=@($parseErrors|%{@{message=$_.Message;errorId=$_.ErrorId}});statements=@($stmts);variables=@($allVars);hasStopParsing=$hasStop;originalCommand=$Command;typeLiterals=@();hasUsingStatements=[bool]$hasUsing;hasScriptRequirements=[bool]$hasReq}|ConvertTo-Json -Depth 10 -Compress
"""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParsedCommandElement:
    """A single command invocation in a pipeline segment."""
    name: str
    args: list[str] = field(default_factory=list)
    name_type: Literal["cmdlet", "application", "unknown"] = "unknown"
    element_types: list[str] | None = None
    text: str = ""
    children: list[list[dict[str, str]]] | None = None
    # Pipeline position: 0-indexed segment index, None if not in a pipeline
    pipeline_position: int | None = None
    redirections: list[dict[str, str]] | None = None


@dataclass
class PipelineSegment:
    """A single stage in a PowerShell pipeline (connected by |)."""
    command: ParsedCommandElement | None = None
    text: str = ""
    position: int = 0
    is_expression: bool = False
    expression_type: str | None = None
    redirections: list[dict[str, str]] = field(default_factory=list)


@dataclass
class PipelineParseResult:
    """Full parse result including pipeline structure, variables, and metadata."""
    segments: list[PipelineSegment] = field(default_factory=list)
    # Flat list for backward compatibility
    all_commands: list[ParsedCommandElement] = field(default_factory=list)
    variables: list[dict[str, Any]] = field(default_factory=list)
    parse_errors: list[dict[str, str]] = field(default_factory=list)
    valid: bool = True
    has_stop_parsing: bool = False
    has_using_statements: bool = False
    has_script_requirements: bool = False
    original_command: str = ""


@dataclass
class DangerClassification:
    """Result of checking a parsed PowerShell command against known-dangerous sets."""
    is_dangerous: bool = False
    matched_categories: list[str] = field(default_factory=list)
    canonical_name: str = ""


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def classify_command_name(name: str) -> Literal["cmdlet", "application", "unknown"]:
    """Verb-Noun → cmdlet, path-contains → application, else unknown."""
    if not name or _NONASCII_RE.search(name):
        return "unknown" if not name else "application"
    if _CMD_RE.match(name):
        return "cmdlet"
    return "application" if ("." in name or "\\" in name or "/" in name) else "unknown"


def strip_module_prefix(name: str) -> str:
    """Strip Module\\Cmdlet → Cmdlet. Preserves drive/UNC/relative paths."""
    i = name.rfind("\\")
    if i < 0:
        return name
    if _DRIVE_RE.match(name) or name.startswith("\\\\") or name.startswith(".\\") or name.startswith("..\\"):
        return name
    return name[i + 1:]


def resolve_to_canonical(name: str) -> str:
    """Module-prefix stripping + alias lookup. Returns lowercase canonical name."""
    s = strip_module_prefix(name).lower()
    return COMMON_ALIASES.get(s, s)


# ---------------------------------------------------------------------------
# Dangerous-cmdlet classification
# ---------------------------------------------------------------------------

# Import lazily to avoid circular imports at module level.
_dangerous_sets: dict[str, frozenset[str]] | None = None


def _load_dangerous_sets() -> dict[str, frozenset[str]]:
    """Lazy-load the dangerous-cmdlet sets from the sibling module."""
    global _dangerous_sets
    if _dangerous_sets is None:
        from hare.utils.powershell.dangerous_cmdlets import (
            DANGEROUS_SCRIPT_BLOCK_CMDLETS,
            FILEPATH_EXECUTION_CMDLETS,
            MODULE_LOADING_CMDLETS,
            SHELLS_AND_SPAWNERS,
        )
        _dangerous_sets = {
            "script_block": DANGEROUS_SCRIPT_BLOCK_CMDLETS,
            "filepath_exec": FILEPATH_EXECUTION_CMDLETS,
            "module_loading": MODULE_LOADING_CMDLETS,
            "shells_and_spawners": SHELLS_AND_SPAWNERS,
        }
    return _dangerous_sets


def classify_danger(element: ParsedCommandElement) -> DangerClassification:
    """Check if a parsed command element matches any known dangerous-cmdlet set.

    Returns a DangerClassification indicating whether the command is dangerous
    and which categories matched (e.g. 'script_block', 'shells_and_spawners').
    """
    canonical = resolve_to_canonical(element.name)
    if not canonical:
        return DangerClassification(canonical_name=canonical)
    sets = _load_dangerous_sets()
    matched: list[str] = []
    for category, cmdlets in sets.items():
        if canonical in cmdlets:
            matched.append(category)
    return DangerClassification(
        is_dangerous=len(matched) > 0,
        matched_categories=matched,
        canonical_name=canonical,
    )


def any_dangerous(elements: list[ParsedCommandElement]) -> bool:
    """True if any element in the list matches a dangerous-cmdlet set."""
    for e in elements:
        if classify_danger(e).is_dangerous:
            return True
    return False


def dangerous_report(elements: list[ParsedCommandElement]) -> dict[str, list[str]]:
    """Group dangerous commands by category.  Returns ``{category: [names...]}``."""
    report: dict[str, list[str]] = {}
    for e in elements:
        dc = classify_danger(e)
        if dc.is_dangerous:
            for cat in dc.matched_categories:
                report.setdefault(cat, []).append(e.name)
    return report


# ---------------------------------------------------------------------------
# AST element-type mapping + raw-JSON transformers
# ---------------------------------------------------------------------------

_ELEMENT_MAP = {
    "ScriptBlockExpressionAst": "ScriptBlock",
    "SubExpressionAst": "SubExpression", "ArrayExpressionAst": "SubExpression",
    "ParenExpressionAst": "SubExpression",
    "ExpandableStringExpressionAst": "ExpandableString",
    "InvokeMemberExpressionAst": "MemberInvocation",
    "MemberExpressionAst": "MemberInvocation",
    "VariableExpressionAst": "Variable",
    "StringConstantExpressionAst": "StringConstant",
    "ConstantExpressionAst": "StringConstant",
    "CommandParameterAst": "Parameter",
}

def _me(rt: str, et: str | None = None) -> str:
    if rt == "CommandExpressionAst" and et:
        return _ELEMENT_MAP.get(et, "Other")
    return _ELEMENT_MAP.get(rt, "Other")

def _arr(val: Any) -> list[Any]:
    return [] if val is None else (val if isinstance(val, list) else [val])


def _parse_redirections(raw_redirs: list[dict]) -> list[dict[str, str]]:
    """Convert raw PowerShell redirection dicts into a normalized list.

    Each output dict has keys: type, from_stream (optional), append (optional),
    location_text (target file or path).
    """
    result: list[dict[str, str]] = []
    for r in raw_redirs:
        if not isinstance(r, dict):
            continue
        entry: dict[str, str] = {"type": r.get("type", "FileRedirectionAst")}
        for key in ("fromStream", "append", "locationText"):
            if key in r:
                entry[key] = str(r[key])
        result.append(entry)
    return result


def _xform(raw: dict) -> ParsedCommandElement:
    """Transform raw PowerShell JSON command element → ParsedCommandElement."""
    cmds = _arr(raw.get("commandElements"))
    redirs = _parse_redirections(_arr(raw.get("redirections")))
    if not cmds:
        return ParsedCommandElement(name="", text=raw.get("text", ""),
                                     redirections=redirs if redirs else None)
    f = cmds[0]
    rn = str(f["value"]) if isinstance(f.get("value"), str) else str(f.get("text", ""))
    rn = rn.strip("'\"")
    nt: Literal["cmdlet", "application", "unknown"] = (
        "application" if _NONASCII_RE.search(rn) else classify_command_name(rn)
    )
    name = strip_module_prefix(rn).lower()
    ets = [_me(f.get("type", ""), f.get("expressionType"))]
    args: list[str] = []
    childs: list[list[dict[str, str]]] = []
    for ce in cmds[1:]:
        lit = ce.get("type") in ("StringConstantExpressionAst", "ExpandableStringExpressionAst")
        args.append(str(ce.get("value")) if lit and ce.get("value") is not None else str(ce.get("text", "")))
        ets.append(_me(ce.get("type", ""), ce.get("expressionType")))
        if ce.get("children"):
            childs.append([{"type": _me(c.get("type", "")), "text": str(c.get("text", ""))}
                           for c in _arr(ce["children"]) if isinstance(c, dict)])
    return ParsedCommandElement(name=name, args=args, name_type=nt,
                                element_types=ets, text=raw.get("text", ""),
                                children=childs if childs else None,
                                redirections=redirs if redirs else None)


# ---------------------------------------------------------------------------
# Pipeline-aware parsing (pwsh AST)
# ---------------------------------------------------------------------------

async def parse_powershell_pipeline(script: str) -> PipelineParseResult:
    """Parse PowerShell via pwsh's native AST and return full pipeline structure.

    Unlike ``parse_powershell_command`` which returns a flat list, this
    returns a ``PipelineParseResult`` with proper pipeline segmentation,
    variable usage, parse errors, and script-level metadata.

    Falls back to inline pipeline segmentation when pwsh is not available.
    """
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        return _fallback_pipeline(script)

    cmd_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    full = f"$EncodedCommand = '{cmd_b64}'\n{_PARSE_SCRIPT}"
    script_b64 = base64.b64encode(full.encode("utf-16-le")).decode("ascii")

    try:
        proc = await asyncio.create_subprocess_exec(
            pwsh, "-NoProfile", "-NonInteractive", "-NoLogo",
            "-EncodedCommand", script_b64,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (asyncio.TimeoutError, Exception):
        return _fallback_pipeline(script)

    try:
        raw = json.loads(stdout.decode("utf-8", errors="replace").strip())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _fallback_pipeline(script)

    return _build_pipeline_result(raw)


def _build_pipeline_result(raw: dict) -> PipelineParseResult:
    """Assemble a PipelineParseResult from the raw pwsh AST JSON."""
    variables: list[dict[str, Any]] = []
    for v in _arr(raw.get("variables")):
        if isinstance(v, dict):
            variables.append(v)

    errors: list[dict[str, str]] = []
    for e in _arr(raw.get("errors")):
        if isinstance(e, dict):
            errors.append({"message": str(e.get("message", "")),
                           "error_id": str(e.get("errorId", ""))})

    segments: list[PipelineSegment] = []
    all_cmds: list[ParsedCommandElement] = []
    pos = 0

    for stmt in _arr(raw.get("statements")):
        stmt_type = stmt.get("type", "") if isinstance(stmt, dict) else ""
        elements = _arr(stmt.get("elements")) if isinstance(stmt, dict) else []

        # Determine if this statement is a pipeline (has multiple elements)
        is_pipeline = stmt_type == "PipelineAst" and len(elements) > 1

        for e in elements:
            if not isinstance(e, dict):
                continue
            etype = e.get("type", "")
            if etype in ("CommandAst", "CommandExpressionAst"):
                parsed = _xform(e)
                parsed.pipeline_position = pos
                seg_redirs = _parse_redirections(_arr(e.get("redirections")))
                segment = PipelineSegment(
                    command=parsed,
                    text=e.get("text", ""),
                    position=pos,
                    is_expression=(etype == "CommandExpressionAst"),
                    expression_type=e.get("expressionType"),
                    redirections=seg_redirs,
                )
                segments.append(segment)
                all_cmds.append(parsed)
                pos += 1

        # Capture nested commands (e.g. in if-blocks, foreach, etc.)
        for n in _arr(stmt.get("nestedCommands")):
            if isinstance(n, dict):
                parsed = _xform(n)
                parsed.pipeline_position = pos
                segments.append(PipelineSegment(
                    command=parsed,
                    text=n.get("text", ""),
                    position=pos,
                ))
                all_cmds.append(parsed)
                pos += 1

    return PipelineParseResult(
        segments=segments,
        all_commands=all_cmds,
        variables=variables,
        parse_errors=errors,
        valid=bool(raw.get("valid", True)),
        has_stop_parsing=bool(raw.get("hasStopParsing", False)),
        has_using_statements=bool(raw.get("hasUsingStatements", False)),
        has_script_requirements=bool(raw.get("hasScriptRequirements", False)),
        original_command=str(raw.get("originalCommand", script or "")),
    )


# ---------------------------------------------------------------------------
# Fallback pipeline parsing (no pwsh)
# ---------------------------------------------------------------------------

_PIPE_SPLIT_RE = re.compile(
    r"""(?x)
    \|
    (?!
        \s*\|\s*\|           # prevent matching || or |||
      | \s*\?\s*\|           # ternary-like pattern ?|
      | \s*[|?]               # adjacent pipe or question
    )
    """,
)


def _smart_split_pipeline(command: str) -> list[str]:
    """Split a PowerShell command on pipeline operators (|), respecting string
    literals and not splitting on || or ?| patterns."""
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(command)

    while i < n:
        ch = command[i]

        # Backtick escape (PowerShell line continuation / escape)
        if ch == "`" and i + 1 < n:
            current.append(command[i + 1])
            i += 2
            continue

        # String quote tracking
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double

        # Pipeline operator
        if ch == "|" and not in_single and not in_double:
            next_ch = command[i + 1] if i + 1 < n else ""
            if next_ch == "|":
                # || is not a PowerShell pipeline — keep the pair intact
                current.append("||")
                i += 2
                continue
            segments.append("".join(current).strip())
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    if current:
        segments.append("".join(current).strip())

    return [s for s in segments if s]


def _fallback_pipeline(command: str) -> PipelineParseResult:
    """Build a PipelineParseResult without pwsh by splitting on pipe operators."""
    command = command.strip()
    if not command:
        return PipelineParseResult(original_command=command)

    parts = _smart_split_pipeline(command)
    segments: list[PipelineSegment] = []
    all_cmds: list[ParsedCommandElement] = []

    for pos, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        tokens = part.split(None, 1)
        name_raw = tokens[0] if tokens else ""
        lo = name_raw.lower()
        resolved = COMMON_ALIASES.get(lo, lo)
        element = ParsedCommandElement(
            name=resolved if resolved != lo else name_raw.lower(),
            args=tokens[1:] if len(tokens) > 1 else [],
            name_type=classify_command_name(name_raw.lower()),
            text=part,
            pipeline_position=pos,
        )
        segments.append(PipelineSegment(command=element, text=part, position=pos))
        all_cmds.append(element)

    return PipelineParseResult(
        segments=segments,
        all_commands=all_cmds,
        original_command=command,
    )


# ---------------------------------------------------------------------------
# Main async parse function (flat list — backward compatible)
# ---------------------------------------------------------------------------

async def parse_powershell_command(script: str) -> list[ParsedCommandElement]:
    """Parse PowerShell via pwsh's native AST parser.

    Spawns pwsh with an inlined AST-analysis script via -EncodedCommand
    (UTF-16LE base64).  Returns a flat list of command elements from all
    pipeline segments and nested commands.  Falls back to inline
    classification when pwsh is unavailable or parsing fails.

    For full pipeline structure, use ``parse_powershell_pipeline`` instead.
    """
    result = await parse_powershell_pipeline(script)
    return result.all_commands


def _fallback(command: str) -> list[ParsedCommandElement]:
    """Inline parser (no pwsh): tokenization + alias resolution."""
    result = _fallback_pipeline(command)
    return result.all_commands


# ---------------------------------------------------------------------------
# Synchronous convenience wrapper
# ---------------------------------------------------------------------------

def parse_powershell_command_sync(script: str) -> list[ParsedCommandElement]:
    """Synchronous wrapper around ``parse_powershell_command``.

    Runs the async function in the current event loop if one is running;
    otherwise creates a new loop.  Prefer the async function in async
    contexts to avoid blocking the event loop.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(parse_powershell_command(script))
    # Running loop exists — use run_coroutine_threadsafe is not appropriate
    # here since caller expects the result.  Create a task and drive it.
    import concurrent.futures
    future = concurrent.futures.Future()

    def _runner() -> None:
        try:
            result = asyncio.run(parse_powershell_command(script))
            future.set_result(result)
        except Exception as exc:
            future.set_exception(exc)

    import threading
    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=6.0)
    if not future.done():
        future.set_exception(TimeoutError("parse_powershell_command_sync timed out"))
    return future.result()


# ---------------------------------------------------------------------------
# Error extraction
# ---------------------------------------------------------------------------

def get_parse_errors(script: str) -> list[dict[str, str]]:
    """Run a quick parse and return only the error list.

    Useful for validation before execution — raises no exception on
    parse failures, just returns the error diagnostics from pwsh.
    """
    import subprocess, sys

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        return []

    cmd_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    full = f"$EncodedCommand = '{cmd_b64}'\n{_PARSE_SCRIPT}"
    script_b64 = base64.b64encode(full.encode("utf-16-le")).decode("ascii")

    try:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-NoLogo",
             "-EncodedCommand", script_b64],
            capture_output=True, timeout=5, text=False,
        )
        raw = json.loads(result.stdout.decode("utf-8", errors="replace").strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, UnicodeDecodeError, Exception):
        return []

    errors: list[dict[str, str]] = []
    for e in _arr(raw.get("errors", [])):
        if isinstance(e, dict):
            errors.append({
                "message": str(e.get("message", "")),
                "error_id": str(e.get("errorId", "")),
            })
    return errors


# ---------------------------------------------------------------------------
# Variable extraction
# ---------------------------------------------------------------------------

def extract_variable_paths(script: str) -> list[str]:
    """Extract variable paths (e.g. ``$env:Path``, ``$_``) without spawning
    pwsh, using a regex-based heuristic.  Returns deduplicated, sorted paths.

    For AST-authoritative variable info, use ``parse_powershell_pipeline``
    and inspect ``result.variables``.
    """
    var_re = re.compile(r"""
        \$ \{?                          # $ or ${
        (?:
            (?:[A-Za-z_][A-Za-z0-9_]*)  # simple variable name
            (?:::[A-Za-z_][A-Za-z0-9_]*)?  # optional ::member access
        )
        \}?                             # optional closing brace
    """, re.VERBOSE)
    seen: set[str] = set()
    for m in var_re.finditer(script):
        raw = m.group(0)
        # Normalize: strip leading $, unwrap braces, lowercase
        name = raw.lstrip("$").strip("{}").lower()
        if name:
            seen.add(name)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def get_all_command_names(elements: list[ParsedCommandElement]) -> list[str]:
    """Lowercase command names, skipping blanks."""
    return [e.name for e in elements if e.name]


def has_command_named(elements: list[ParsedCommandElement], name: str) -> bool:
    """True if any element matches *name* after alias resolution."""
    c = COMMON_ALIASES.get(name.lower(), name.lower())
    return any(resolve_to_canonical(e.name) == c for e in elements)


def command_has_arg(cmd: ParsedCommandElement, arg: str) -> bool:
    """True if *cmd* has *arg* in its args (case-insensitive)."""
    lo = arg.lower()
    return any(a.lower() == lo for a in cmd.args)


def is_power_shell_parameter(arg: str, element_type: str | None = None) -> bool:
    """True if *arg* is a PS parameter flag. AST element_type is authoritative
    when available; otherwise checks leading dash character."""
    if element_type is not None:
        return element_type == "Parameter"
    return len(arg) > 0 and arg[0] in _PS_DASH


def get_pipeline_commands(elements: list[ParsedCommandElement]) -> list[ParsedCommandElement]:
    """Return only the elements that are part of a pipeline (position != None)."""
    return [e for e in elements if e.pipeline_position is not None]


def format_command_summary(element: ParsedCommandElement) -> str:
    """One-line summary of a parsed command element for logging / display."""
    danger = classify_danger(element)
    flags: list[str] = []
    if danger.is_dangerous:
        flags.append(f"!{'+'.join(danger.matched_categories)}")
    parts = [element.name]
    if element.args:
        parts.extend(element.args[:3])
        if len(element.args) > 3:
            parts.append("...")
    line = " ".join(parts)
    if flags:
        line = f"[{' '.join(flags)}] {line}"
    return line
