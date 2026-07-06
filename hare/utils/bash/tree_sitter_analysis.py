"""
Tree-sitter AST analysis utilities for bash command security validation.

Port of: src/utils/bash/treeSitterAnalysis.ts

Operates on plain dict-like node trees (type, text, start_index, end_index, children)
as produced by the native NAPI parser or a compatible stub.

Provides a rich set of result types for security classification, redirect
analysis, variable tracking, and pipeline-stage inspection.  The core
:func:`analyze_command` entry point returns the full :class:`TreeSitterAnalysis`,
while convenience functions such as :func:`classify_command_security` and
:func:`analyze_command_intent` build higher-level verdicts on top of the raw
AST data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


# ---------------------------------------------------------------------------
# Core analysis result types (ported from treeSitterAnalysis.ts)
# ---------------------------------------------------------------------------


@dataclass
class QuoteContext:
    """Quote-stripped views of command text for validation."""

    with_double_quotes: str
    fully_unquoted: str
    unquoted_keep_quote_chars: str


@dataclass
class CompoundStructure:
    has_compound_operators: bool
    has_pipeline: bool
    has_subshell: bool
    has_command_group: bool
    operators: list[str]
    segments: list[str]


@dataclass
class DangerousPatterns:
    has_command_substitution: bool
    has_process_substitution: bool
    has_parameter_expansion: bool
    has_heredoc: bool
    has_comment: bool


@dataclass
class TreeSitterAnalysis:
    quote_context: QuoteContext
    compound_structure: CompoundStructure
    has_actual_operator_nodes: bool
    dangerous_patterns: DangerousPatterns


# ---------------------------------------------------------------------------
# Expanded result types — redirect analysis
# ---------------------------------------------------------------------------


class RedirectType(Enum):
    """Kind of shell redirection found in the AST."""

    OUTPUT_OVERWRITE = auto()  # > file
    OUTPUT_APPEND = auto()     # >> file
    INPUT = auto()             # < file
    HEREDOC = auto()           # <<EOF
    HERESTRING = auto()        # <<< string
    DUP = auto()               # >&fd  or  <&fd
    CLOBBER = auto()           # >| file (force overwrite)
    FD_REDIRECT = auto()       # 2> file, 1>&2, etc.
    UNKNOWN = auto()


@dataclass
class RedirectSpan:
    """Single file-redirect location extracted from the AST."""

    redirect_type: RedirectType
    operator: str        # '>', '>>', '<', '<<', '<<<', '>&', '<&', '>|'
    target: str          # resolved path / fd / heredoc delimiter
    fd: int | None = None  # source file descriptor, e.g. 2 in 2>
    start_index: int = 0
    end_index: int = 0
    node_type: str = ""  # original tree-sitter node type
    is_output: bool = False
    is_input: bool = False


@dataclass
class RedirectAnalysis:
    """Complete redirect analysis for a command."""

    redirects: list[RedirectSpan] = field(default_factory=list)
    has_output_redirect: bool = False
    has_input_redirect: bool = False
    has_heredoc: bool = False
    has_ambiguous_redirect: bool = False
    # True when ``>`` appears inside ``test`` / ``[[`` (comparison, not redirect).
    comparison_context: bool = False
    count: int = 0


# ---------------------------------------------------------------------------
# Expanded result types — security classification
# ---------------------------------------------------------------------------


class CommandSecurityCategory(Enum):
    """Security-relevant command category based on AST structure."""

    READ_ONLY = auto()           # No side-effects; safe to run
    READ_ONLY_WITH_FLAGS = auto()  # Read-only but has flags needing review
    PIPELINE_READ_ONLY = auto()  # Only safe pipeline stages
    WRITES_FILES = auto()        # Contains output redirection
    READS_STDIN = auto()         # Uses stdin / heredoc
    DESTRUCTIVE = auto()         # Destructive commands (rm, sudo, etc.)
    PRIVILEGED = auto()          # Escalates privileges (sudo, su)
    NETWORK_ACCESS = auto()      # Makes network calls
    HAS_SCRIPT_INJECTION_RISK = auto()  # Dynamic eval / source
    UNKNOWN = auto()


@dataclass
class CommandSecurityClassification:
    """Security classification verdict derived from tree-sitter analysis."""

    category: CommandSecurityCategory = CommandSecurityCategory.UNKNOWN
    severity: str = "unknown"  # 'safe', 'warning', 'critical'
    is_safe_to_run: bool = False
    requires_approval: bool = True
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Expanded result types — variable analysis
# ---------------------------------------------------------------------------


@dataclass
class VariableAssignment:
    """A single variable assignment found in the AST."""

    name: str
    value: str | None = None
    is_export: bool = False    # export FOO=bar
    is_local: bool = False     # local FOO=bar
    is_env_prefix: bool = False  # FOO=bar cmd (prefix assignment)
    start_index: int = 0
    end_index: int = 0


@dataclass
class VariableReference:
    """A single variable reference / expansion found in the AST."""

    name: str
    expansion_type: str = ""  # '$VAR', '${VAR}', '${VAR:-default}', …
    is_quoted: bool = False
    is_special: bool = False  # e.g. $?, $$, $!, $-
    start_index: int = 0
    end_index: int = 0


@dataclass
class VariableAnalysis:
    """Aggregated variable assignment and reference data from the AST."""

    assignments: list[VariableAssignment] = field(default_factory=list)
    references: list[VariableReference] = field(default_factory=list)
    has_export: bool = False
    has_positional_params: bool = False  # $1, $@, $*, ${1:?msg}
    has_tmpdir_usage: bool = False  # references to $TMPDIR, $TEMPDIR, mktemp
    has_sensitive_env: bool = False  # $HOME, $PATH, $LD_PRELOAD alterations


# ---------------------------------------------------------------------------
# Expanded result types — subshell & quoting detail
# ---------------------------------------------------------------------------


class SubshellType(Enum):
    """Kind of subshell construct encountered."""

    PAREN = auto()          # (cmd)
    DOLLAR_PAREN = auto()   # $(cmd)
    BACKTICK = auto()       # `cmd`
    PROCESS_IN = auto()     # <(cmd)
    PROCESS_OUT = auto()    # >(cmd)


@dataclass
class SubshellSpan:
    """A single subshell occurrence in the AST."""

    subshell_type: SubshellType
    text: str
    start_index: int
    end_index: int
    is_command_substitution: bool = False
    is_process_substitution: bool = False


@dataclass
class QuotedSegment:
    """Detailed information about a single quoted span."""

    quote_type: str  # 'single', 'double', 'ansi_c', 'heredoc'
    content: str     # content inside the quotes
    full_span: str   # full text including delimiters
    start_index: int
    end_index: int
    is_safe: bool = True  # quoted content is literal in bash
    contains_expansion: bool = False  # true for double-quoted ${} $()


@dataclass
class QuotingAnalysis:
    """Comprehensive quoting analysis for a command."""

    segments: list[QuotedSegment] = field(default_factory=list)
    has_unquoted_dangerous_chars: bool = False
    has_backslash_escape: bool = False
    single_quoted_count: int = 0
    double_quoted_count: int = 0
    ansi_c_count: int = 0
    heredoc_count: int = 0


# ---------------------------------------------------------------------------
# Expanded result types — operator analysis
# ---------------------------------------------------------------------------


class CompoundOperatorType(Enum):
    """Top-level compound operator kind."""

    AND = auto()        # &&
    OR = auto()         # ||
    SEMI = auto()       # ;
    BACKGROUND = auto() # &
    PIPE = auto()       # |
    PIPE_FAIL = auto()  # |& (pipe stderr too)


@dataclass
class OperatorSpan:
    """A single compound operator occurrence."""

    operator_type: CompoundOperatorType
    symbol: str   # '&&', '||', ';', '&', '|', '|&'
    start_index: int
    end_index: int
    is_escaped: bool = False  # backslash-escaped operator (e.g. \;)


@dataclass
class OperatorAnalysis:
    """Aggregated operator information from the AST."""

    operators: list[OperatorSpan] = field(default_factory=list)
    has_sequential: bool = False      # ;
    has_conditional: bool = False     # && or ||
    has_background: bool = False      # &
    has_pipeline: bool = False        # |
    has_escaped_operator: bool = False  # backslash-escaped operator (not real)


# ---------------------------------------------------------------------------
# Expanded result types — diagnostic / severity
# ---------------------------------------------------------------------------


class ShellSyntaxSeverity(Enum):
    """Severity level of a tree-sitter analysis finding."""

    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass
class ShellSyntaxWarning:
    """A single warning raised during tree-sitter analysis."""

    severity: ShellSyntaxSeverity
    code: str       # short machine-readable tag, e.g. 'CMDSUB_UNQUOTED'
    message: str     # human-readable description
    start_index: int = 0
    end_index: int = 0
    node_type: str | None = None
    suggestion: str | None = None  # remediation hint


@dataclass
class AnalysisDiagnostics:
    """Collection of diagnostics produced during a tree-sitter analysis pass."""

    warnings: list[ShellSyntaxWarning] = field(default_factory=list)
    has_critical: bool = False
    has_errors: bool = False
    has_warnings: bool = False

    def add(self, warning: ShellSyntaxWarning) -> None:
        self.warnings.append(warning)
        if warning.severity == ShellSyntaxSeverity.CRITICAL:
            self.has_critical = True
        elif warning.severity == ShellSyntaxSeverity.ERROR:
            self.has_errors = True
        elif warning.severity == ShellSyntaxSeverity.WARNING:
            self.has_warnings = True


# ---------------------------------------------------------------------------
# Expanded result types — command intent (higher-level)
# ---------------------------------------------------------------------------


class CommandIntent(Enum):
    """High-level classification of what a command is trying to do."""

    FILE_READ = auto()           # cat, less, head, tail, rg, grep (no -r)
    FILE_LIST = auto()           # ls, find (no -delete/-exec), tree
    FILE_WRITE = auto()          # redirect to file, tee, cp, mv
    FILE_DELETE = auto()         # rm, find -delete
    FILE_PERMISSION = auto()     # chmod, chown, chgrp
    PROCESS_INFO = auto()        # ps, top, htop, pgrep
    PROCESS_KILL = auto()        # kill, pkill, killall
    NETWORK = auto()             # curl, wget, nc, ssh, scp
    SYSTEM_ADMIN = auto()        # sudo, systemctl, mount, etc.
    SHELL_BUILTIN = auto()       # cd, echo, export, source, .
    PACKAGE_MANAGEMENT = auto()  # apt, brew, pip, npm, cargo
    GIT_OPERATION = auto()       # git commands
    PIPE_FILTER = auto()         # grep, sed, awk, sort, uniq, etc.
    SCRIPT_EVAL = auto()         # eval, source, ., bash -c
    ENV_MANIPULATION = auto()    # export, unset, env
    UNKNOWN = auto()


@dataclass
class CommandIntentAnalysis:
    """High-level intent classification for a parsed command."""

    intents: list[CommandIntent] = field(default_factory=list)
    primary_intent: CommandIntent = CommandIntent.UNKNOWN
    is_read_only: bool = True
    is_destructive: bool = False
    requires_network: bool = False
    escalates_privilege: bool = False
    mutates_filesystem: bool = False
    confidence: float = 1.0  # 0.0–1.0


# ---------------------------------------------------------------------------
# Expanded result types — combined analysis summary
# ---------------------------------------------------------------------------


@dataclass
class FullAnalysisResult:
    """All tree-sitter analysis results combined into one structure.

    This aggregates every analysis dimension into a single object for
    consumers that need the complete picture (security validators,
    permission checkers, audit loggers).
    """

    raw_command: str
    tree_sitter_analysis: TreeSitterAnalysis | None = None
    security_classification: CommandSecurityClassification = field(
        default_factory=CommandSecurityClassification
    )
    variable_analysis: VariableAnalysis = field(
        default_factory=VariableAnalysis
    )
    redirect_analysis: RedirectAnalysis = field(
        default_factory=RedirectAnalysis
    )
    quoting_analysis: QuotingAnalysis = field(
        default_factory=QuotingAnalysis
    )
    operator_analysis: OperatorAnalysis = field(
        default_factory=OperatorAnalysis
    )
    diagnostics: AnalysisDiagnostics = field(
        default_factory=AnalysisDiagnostics
    )
    intent_analysis: CommandIntentAnalysis = field(
        default_factory=CommandIntentAnalysis
    )
    is_safe: bool = True
    approval_required: bool = False


@dataclass
class _QuoteSpans:
    raw: list[tuple[int, int]]
    ansi_c: list[tuple[int, int]]
    double: list[tuple[int, int]]
    heredoc: list[tuple[int, int]]


def _normalize_node(node: Any) -> Any:
    """Accept TS-style camelCase or Python snake_case keys."""
    if isinstance(node, dict):
        children = node.get("children") or []
        return _DictNodeAdapter(node, children)
    return node


class _DictNodeAdapter:
    __slots__ = ("_d", "children")

    def __init__(self, d: dict[str, Any], children: list[Any]) -> None:
        self._d = d
        self.children = children

    @property
    def type(self) -> str:
        return str(self._d.get("type", ""))

    @property
    def text(self) -> str:
        return str(self._d.get("text", ""))

    @property
    def start_index(self) -> int:
        return int(self._d.get("startIndex", self._d.get("start_index", 0)))

    @property
    def end_index(self) -> int:
        return int(self._d.get("endIndex", self._d.get("end_index", 0)))

    @property
    def child_count(self) -> int:
        return int(
            self._d.get("childCount", self._d.get("child_count", len(self.children)))
        )


def _collect_quote_spans(
    node: Any,
    out: _QuoteSpans,
    in_double: bool,
) -> None:
    n = _normalize_node(node)
    nt = n.type

    if nt == "raw_string":
        out.raw.append((n.start_index, n.end_index))
        return
    if nt == "ansi_c_string":
        out.ansi_c.append((n.start_index, n.end_index))
        return
    if nt == "string":
        if not in_double:
            out.double.append((n.start_index, n.end_index))
        for child in n.children:
            if child is not None:
                _collect_quote_spans(child, out, True)
        return
    if nt == "heredoc_redirect":
        is_quoted = False
        for child in n.children:
            if child is None:
                continue
            cn = _normalize_node(child)
            if cn.type == "heredoc_start":
                first = cn.text[0] if cn.text else ""
                is_quoted = first in ("'", '"', "\\")
                break
        if is_quoted:
            out.heredoc.append((n.start_index, n.end_index))
            return
        # Unquoted: recurse
        pass

    for child in n.children:
        if child is not None:
            _collect_quote_spans(child, out, in_double)


def _build_position_set(spans: list[tuple[int, int]]) -> set[int]:
    s: set[int] = set()
    for start, end in spans:
        for i in range(start, end):
            s.add(i)
    return s


def _drop_contained_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i, span in enumerate(spans):
        s0, s1 = span
        contained = False
        for j, other in enumerate(spans):
            if i == j:
                continue
            o0, o1 = other
            if o0 <= s0 and o1 >= s1 and (o0 < s0 or o1 > s1):
                contained = True
                break
        if not contained:
            out.append(span)
    return out


def _remove_spans(command: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return command
    sorted_spans = sorted(
        _drop_contained_spans(spans), key=lambda x: x[0], reverse=True
    )
    result = command
    for start, end in sorted_spans:
        result = result[:start] + result[end:]
    return result


def _drop_contained_spans_4(
    spans: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    out: list[tuple[int, int, str, str]] = []
    for i, span in enumerate(spans):
        s0, s1 = span[0], span[1]
        contained = False
        for j, other in enumerate(spans):
            if i == j:
                continue
            o0, o1 = other[0], other[1]
            if o0 <= s0 and o1 >= s1 and (o0 < s0 or o1 > s1):
                contained = True
                break
        if not contained:
            out.append(span)
    return out


def _replace_spans_keep_quotes(
    command: str,
    spans: list[tuple[int, int, str, str]],
) -> str:
    if not spans:
        return command
    items = sorted(_drop_contained_spans_4(spans), key=lambda x: x[0], reverse=True)
    result = command
    for start, end, open_q, close_q in items:
        result = result[:start] + open_q + close_q + result[end:]
    return result


def extract_quote_context(root_node: Any, command: str) -> QuoteContext:
    spans = _QuoteSpans(raw=[], ansi_c=[], double=[], heredoc=[])
    _collect_quote_spans(root_node, spans, False)
    single_quote_spans = spans.raw
    ansi_c_spans = spans.ansi_c
    double_quote_spans = spans.double
    quoted_heredoc_spans = spans.heredoc
    all_quote_spans = (
        single_quote_spans + ansi_c_spans + double_quote_spans + quoted_heredoc_spans
    )

    single_quote_set = _build_position_set(
        single_quote_spans + ansi_c_spans + quoted_heredoc_spans
    )
    double_quote_delim_set: set[int] = set()
    for start, end in double_quote_spans:
        double_quote_delim_set.add(start)
        double_quote_delim_set.add(end - 1)
    with_double = ""
    for i, ch in enumerate(command):
        if i in single_quote_set:
            continue
        if i in double_quote_delim_set:
            continue
        with_double += ch

    fully_unquoted = _remove_spans(command, list(all_quote_spans))

    spans_with_quote_chars: list[tuple[int, int, str, str]] = []
    for start, end in single_quote_spans:
        spans_with_quote_chars.append((start, end, "'", "'"))
    for start, end in ansi_c_spans:
        spans_with_quote_chars.append((start, end, "$'", "'"))
    for start, end in double_quote_spans:
        spans_with_quote_chars.append((start, end, '"', '"'))
    for start, end in quoted_heredoc_spans:
        spans_with_quote_chars.append((start, end, "", ""))
    unquoted_keep = _replace_spans_keep_quotes(command, spans_with_quote_chars)

    return QuoteContext(
        with_double_quotes=with_double,
        fully_unquoted=fully_unquoted,
        unquoted_keep_quote_chars=unquoted_keep,
    )


def extract_compound_structure(root_node: Any, command: str) -> CompoundStructure:
    n = _normalize_node(root_node)
    operators: list[str] = []
    segments: list[str] = []
    has_subshell = False
    has_command_group = False
    has_pipeline = False

    def walk_top_level(node: Any) -> None:
        nonlocal has_subshell, has_command_group, has_pipeline
        nn = _normalize_node(node)
        for child in nn.children:
            if child is None:
                continue
            c = _normalize_node(child)
            ct = c.type

            if ct == "list":
                for list_child in c.children:
                    if list_child is None:
                        continue
                    lc = _normalize_node(list_child)
                    lct = lc.type
                    if lct in ("&&", "||"):
                        operators.append(lct)
                    elif lct in ("list", "redirected_statement"):
                        walk_top_level({"type": "program", "children": [list_child]})
                    elif lct == "pipeline":
                        has_pipeline = True
                        segments.append(lc.text)
                    elif lct == "subshell":
                        has_subshell = True
                        segments.append(lc.text)
                    elif lct == "compound_statement":
                        has_command_group = True
                        segments.append(lc.text)
                    else:
                        segments.append(lc.text)
            elif ct == ";":
                operators.append(";")
            elif ct == "pipeline":
                has_pipeline = True
                segments.append(c.text)
            elif ct == "subshell":
                has_subshell = True
                segments.append(c.text)
            elif ct == "compound_statement":
                has_command_group = True
                segments.append(c.text)
            elif ct in ("command", "declaration_command", "variable_assignment"):
                segments.append(c.text)
            elif ct == "redirected_statement":
                found_inner = False
                for inner in c.children:
                    if inner is None:
                        continue
                    inn = _normalize_node(inner)
                    if inn.type == "file_redirect":
                        continue
                    found_inner = True
                    walk_top_level({"type": "program", "children": [inner]})
                if not found_inner:
                    segments.append(c.text)
            elif ct == "negated_command":
                segments.append(c.text)
                walk_top_level(c)
            elif ct in (
                "if_statement",
                "while_statement",
                "for_statement",
                "case_statement",
                "function_definition",
            ):
                segments.append(c.text)
                walk_top_level(c)

    walk_top_level(n)
    if not segments:
        segments = [command]

    return CompoundStructure(
        has_compound_operators=len(operators) > 0,
        has_pipeline=has_pipeline,
        has_subshell=has_subshell,
        has_command_group=has_command_group,
        operators=operators,
        segments=segments,
    )


def has_actual_operator_nodes(root_node: Any) -> bool:
    n = _normalize_node(root_node)

    def walk(node: Any) -> bool:
        nn = _normalize_node(node)
        if nn.type in (";", "&&", "||"):
            return True
        if nn.type == "list":
            return True
        for child in nn.children:
            if child is not None and walk(child):
                return True
        return False

    return walk(n)


def extract_dangerous_patterns(root_node: Any) -> DangerousPatterns:
    n = _normalize_node(root_node)
    has_cmdsub = False
    has_procs = False
    has_param = False
    has_heredoc = False
    has_comment = False

    def walk(node: Any) -> None:
        nonlocal has_cmdsub, has_procs, has_param, has_heredoc, has_comment
        nn = _normalize_node(node)
        nt = nn.type
        if nt == "command_substitution":
            has_cmdsub = True
        elif nt == "process_substitution":
            has_procs = True
        elif nt == "expansion":
            has_param = True
        elif nt == "heredoc_redirect":
            has_heredoc = True
        elif nt == "comment":
            has_comment = True
        for child in nn.children:
            if child is not None:
                walk(child)

    walk(n)
    return DangerousPatterns(
        has_command_substitution=has_cmdsub,
        has_process_substitution=has_procs,
        has_parameter_expansion=has_param,
        has_heredoc=has_heredoc,
        has_comment=has_comment,
    )


def analyze_command(root_node: Any, command: str) -> TreeSitterAnalysis:
    return TreeSitterAnalysis(
        quote_context=extract_quote_context(root_node, command),
        compound_structure=extract_compound_structure(root_node, command),
        has_actual_operator_nodes=has_actual_operator_nodes(root_node),
        dangerous_patterns=extract_dangerous_patterns(root_node),
    )


# ---------------------------------------------------------------------------
# General-purpose tree-walk visitor
# ---------------------------------------------------------------------------


def _walk_tree(node: Any, visitor: Any) -> None:
    """Walk every node in the tree, calling ``visitor(node)``.

    The *visitor* receives a :class:`_DictNodeAdapter` for each node
    (after ``_normalize_node``).  Children are visited recursively after
    the current node.
    """
    nn = _normalize_node(node)
    visitor(nn)
    for child in nn.children:
        if child is not None:
            _walk_tree(child, visitor)


# ---------------------------------------------------------------------------
# Redirect analysis
# ---------------------------------------------------------------------------


_OPERATOR_TO_REDIRECT_TYPE: dict[str, RedirectType] = {
    ">": RedirectType.OUTPUT_OVERWRITE,
    ">>": RedirectType.OUTPUT_APPEND,
    "<": RedirectType.INPUT,
    "<<": RedirectType.HEREDOC,
    "<<<": RedirectType.HERESTRING,
    ">&": RedirectType.DUP,
    "<&": RedirectType.DUP,
    ">|": RedirectType.CLOBBER,
}


def _maybe_resolve_fd(prev_sibling: Any) -> int | None:
    """Try to extract an explicit file-descriptor number from a sibling node.

    In tree-sitter bash grammar, ``2> file`` is parsed as:
      file_redirect → (word '2') + ('>') + (word 'file').
    The fd word is the immediately preceding sibling.
    """
    if prev_sibling is None:
        return None
    pn = _normalize_node(prev_sibling)
    if pn.type == "word" and pn.text.isdigit():
        return int(pn.text)
    return None


def extract_redirects(root_node: Any) -> RedirectAnalysis:
    """Extract all file-redirect spans from a tree-sitter AST.

    Returns a :class:`RedirectAnalysis` aggregating every redirect found,
    including heredocs and herestrings.
    """
    result = RedirectAnalysis()
    nodes: list[tuple[Any, int | None]] = []  # (node, fd)
    all_node_types: list[str] = []

    def walk(node: Any, parent_children: list[Any] | None = None) -> None:
        nn = _normalize_node(node)
        for i, child in enumerate(nn.children):
            if child is None:
                continue
            c = _normalize_node(child)
            ct = c.type

            if ct == "file_redirect":
                prev = nn.children[i - 1] if i > 0 else None
                fd = _maybe_resolve_fd(prev)
                nodes.append((c, fd))
                all_node_types.append(ct)
            elif ct == "heredoc_redirect":
                nodes.append((c, None))
                all_node_types.append(ct)
            else:
                walk(child, c.children)
        if parent_children is not None:
            for child in parent_children:
                if child is not None:
                    walk(child)

    walk(root_node, None)

    for n, fd in nodes:
        nn = n if isinstance(n, _DictNodeAdapter) else _normalize_node(n)
        ops: list[str] = []
        target = ""
        for child in nn.children:
            if child is None:
                continue
            cn = _normalize_node(child)
            if cn.type in (">", ">>", "<", "<<", "<<<", ">&", "<&", ">|", "&>"):
                ops.append(cn.type)
            elif cn.type == "word" and not target:
                # For heredoc the "word" is the delimiter, not a file
                if nn.type != "heredoc_redirect":
                    target = cn.text
            elif cn.type == "heredoc_start" and nn.type == "heredoc_redirect":
                target = cn.text.lstrip("'\"\\")

        op_text = ops[0] if ops else nn.type
        rt = _OPERATOR_TO_REDIRECT_TYPE.get(op_text, RedirectType.UNKNOWN)
        if nn.type == "heredoc_redirect":
            rt = RedirectType.HEREDOC
            result.has_heredoc = True

        is_output = rt in (
            RedirectType.OUTPUT_OVERWRITE,
            RedirectType.OUTPUT_APPEND,
            RedirectType.CLOBBER,
        )
        is_input = rt in (
            RedirectType.INPUT,
            RedirectType.HEREDOC,
            RedirectType.HERESTRING,
        )

        span = RedirectSpan(
            redirect_type=rt,
            operator=op_text,
            target=target,
            fd=fd,
            start_index=nn.start_index,
            end_index=nn.end_index,
            node_type=nn.type,
            is_output=is_output,
            is_input=is_input,
        )
        result.redirects.append(span)

        if is_output:
            result.has_output_redirect = True
        if is_input:
            result.has_input_redirect = True

    result.count = len(result.redirects)
    result.has_ambiguous_redirect = any(
        r.redirect_type == RedirectType.UNKNOWN for r in result.redirects
    )
    return result


# ---------------------------------------------------------------------------
# Variable analysis
# ---------------------------------------------------------------------------


_SPECIAL_VARS: frozenset[str] = frozenset({
    "?", "$", "!", "-", "#", "@", "*", "0", "_",
})

_SENSITIVE_ENV_VARS: frozenset[str] = frozenset({
    "HOME", "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH",
    "IFS", "SHELL", "BASH_ENV", "PROMPT_COMMAND",
})


def extract_variable_info(root_node: Any, command: str) -> VariableAnalysis:
    """Walk the AST and collect variable assignments and references."""

    result = VariableAnalysis()

    def visitor(nn: Any) -> None:
        nt = nn.type

        # ---- assignments ----
        if nt == "variable_assignment":
            name = ""
            value = ""
            for child in nn.children:
                if child is None:
                    continue
                cn = _normalize_node(child)
                if cn.type == "variable_name":
                    name = cn.text.rstrip("=")
                elif cn.type in ("word", "string", "raw_string", "array"):
                    value = cn.text
                elif cn.type == "expansion":
                    value = cn.text
            if name:
                is_export = command[max(0, nn.start_index - 7):nn.start_index].rstrip().endswith("export")
                result.assignments.append(VariableAssignment(
                    name=name,
                    value=value,
                    is_export=is_export,
                    is_local=False,
                    start_index=nn.start_index,
                    end_index=nn.end_index,
                ))
                if is_export:
                    result.has_export = True
                if name in _SENSITIVE_ENV_VARS:
                    result.has_sensitive_env = True

        # ---- env prefix: FOO=bar cmd ----
        if nt == "command":
            for child in nn.children:
                if child is None:
                    continue
                cn = _normalize_node(child)
                if cn.type == "variable_assignment":
                    result.assignments.append(VariableAssignment(
                        name=cn.text.split("=", 1)[0] if "=" in cn.text else cn.text,
                        value=cn.text.split("=", 1)[1] if "=" in cn.text else None,
                        is_env_prefix=True,
                        start_index=cn.start_index,
                        end_index=cn.end_index,
                    ))

        # ---- references ----
        if nt == "expansion":
            text = nn.text
            # Quick heuristic: ${VAR} or ${VAR:-default} etc.
            if text.startswith("${"):
                inner = text[2:-1] if text.endswith("}") else text[2:]
                # Strip operator suffix: :- = ? etc.
                bracket_name = ""
                for ch in inner:
                    if ch.isalnum() or ch == "_":
                        bracket_name += ch
                    else:
                        break
                if bracket_name:
                    is_special = bracket_name in _SPECIAL_VARS
                    result.references.append(VariableReference(
                        name=bracket_name,
                        expansion_type="brace" if "}" in text else "brace_unclosed",
                        is_special=is_special,
                        start_index=nn.start_index,
                        end_index=nn.end_index,
                    ))
                    if is_special and bracket_name in ("@", "*", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
                        result.has_positional_params = True
            elif text.startswith("$"):
                # $VAR or $? or $1
                var_name = text[1:]
                if var_name and (var_name[0].isalpha() or var_name[0] == "_"):
                    alpha_name = ""
                    for ch in var_name:
                        if ch.isalnum() or ch == "_":
                            alpha_name += ch
                        else:
                            break
                    if alpha_name:
                        is_special = alpha_name in _SPECIAL_VARS
                        result.references.append(VariableReference(
                            name=alpha_name,
                            expansion_type="dollar",
                            is_special=is_special,
                            start_index=nn.start_index,
                            end_index=nn.end_index,
                        ))
                        if alpha_name in _SENSITIVE_ENV_VARS:
                            result.has_sensitive_env = True
                elif var_name and var_name[0].isdigit():
                    result.references.append(VariableReference(
                        name=var_name[0],
                        expansion_type="positional",
                        is_special=True,
                        start_index=nn.start_index,
                        end_index=nn.end_index,
                    ))
                    result.has_positional_params = True
                elif var_name in ("?", "$", "!", "-"):
                    result.references.append(VariableReference(
                        name=var_name,
                        expansion_type="special",
                        is_special=True,
                        start_index=nn.start_index,
                        end_index=nn.end_index,
                    ))

        # ---- local assignments ----
        if nt == "declaration_command":
            text_lower = nn.text.lower()
            if text_lower.startswith("local ") or text_lower.startswith("export "):
                for child in nn.children:
                    if child is None:
                        continue
                    cn = _normalize_node(child)
                    if cn.type == "variable_assignment":
                        name = cn.text.split("=", 1)[0] if "=" in cn.text else cn.text
                        result.assignments.append(VariableAssignment(
                            name=name,
                            value=cn.text.split("=", 1)[1] if "=" in cn.text else None,
                            is_export=text_lower.startswith("export "),
                            is_local=text_lower.startswith("local "),
                            start_index=cn.start_index,
                            end_index=cn.end_index,
                        ))

    _walk_tree(root_node, visitor)

    # Heuristic for $TMPDIR / $TEMPDIR / mktemp
    all_text = command.lower()
    result.has_tmpdir_usage = (
        any(ref.name.upper() in ("TMPDIR", "TEMPDIR", "TEMP") for ref in result.references)
        or "mktemp" in all_text
        or "tmpdir" in all_text
    )

    return result


# ---------------------------------------------------------------------------
# Quoting analysis (per-segment detail)
# ---------------------------------------------------------------------------


def extract_quoting_analysis(root_node: Any, command: str) -> QuotingAnalysis:
    """Produce detailed per-segment quoting information from the AST."""

    result = QuotingAnalysis()

    def visitor(nn: Any) -> None:
        nt = nn.type
        if nt == "raw_string":
            result.single_quoted_count += 1
            inner = command[nn.start_index + 1 : nn.end_index - 1] if nn.end_index - nn.start_index >= 2 else ""
            result.segments.append(QuotedSegment(
                quote_type="single",
                content=inner,
                full_span=nn.text,
                start_index=nn.start_index,
                end_index=nn.end_index,
                is_safe=True,
                contains_expansion=False,
            ))
        elif nt == "ansi_c_string":
            result.ansi_c_count += 1
            inner = command[nn.start_index + 2 : nn.end_index - 1] if nn.end_index - nn.start_index >= 3 else ""
            result.segments.append(QuotedSegment(
                quote_type="ansi_c",
                content=inner,
                full_span=nn.text,
                start_index=nn.start_index,
                end_index=nn.end_index,
                is_safe=True,
                contains_expansion=False,
            ))
        elif nt == "string":
            result.double_quoted_count += 1
            inner = command[nn.start_index + 1 : nn.end_index - 1] if nn.end_index - nn.start_index >= 2 else ""
            has_exp = "${" in nn.text or "$(" in nn.text or "`" in nn.text
            result.segments.append(QuotedSegment(
                quote_type="double",
                content=inner,
                full_span=nn.text,
                start_index=nn.start_index,
                end_index=nn.end_index,
                is_safe=False,
                contains_expansion=has_exp,
            ))
        elif nt == "heredoc_redirect":
            result.heredoc_count += 1
            result.segments.append(QuotedSegment(
                quote_type="heredoc",
                content=nn.text,
                full_span=nn.text,
                start_index=nn.start_index,
                end_index=nn.end_index,
                is_safe=True,  # Quoted heredocs are safe
                contains_expansion=False,
            ))
        elif nt == "escape_sequence":
            result.has_backslash_escape = True

    _walk_tree(root_node, visitor)

    # Check for unquoted dangerous chars in fully-unquoted view
    ctx = extract_quote_context(root_node, command)
    unquoted = ctx.fully_unquoted
    result.has_unquoted_dangerous_chars = any(
        ch in unquoted for ch in (";", "|", "&", "`", "$(", ">", "<")
    )

    return result


# ---------------------------------------------------------------------------
# Subshell extraction
# ---------------------------------------------------------------------------


_SUBSHELL_TYPE_MAP: dict[str, SubshellType] = {
    "subshell": SubshellType.PAREN,
    "command_substitution": SubshellType.DOLLAR_PAREN,
    "process_substitution": SubshellType.PROCESS_IN,
}


def extract_subsells(root_node: Any) -> list[SubshellSpan]:
    """Return every subshell / command-substitution / process-substitution span.

    Process-substitution output ``>(...)`` is distinguished from input
    ``<(...)`` by looking at the source text for a leading ``>``.

    Handles both ``$(…)`` and backtick `` `…` `` command substitution nodes
    (tree-sitter grammars vary in how they label backtick nodes).
    """
    out: list[SubshellSpan] = []

    def visitor(nn: Any) -> None:
        nt = nn.type
        if nt == "command_substitution":
            is_cmdsub = True
            sub_type = SubshellType.DOLLAR_PAREN
        elif nt == "process_substitution":
            is_cmdsub = False
            if nn.text.startswith(">("):
                sub_type = SubshellType.PROCESS_OUT
            else:
                sub_type = SubshellType.PROCESS_IN
        elif nt == "subshell":
            is_cmdsub = False
            sub_type = SubshellType.PAREN
        # Backtick command substitution: some grammars use "expansion" with
        # leading `` ` ``; others emit a dedicated "backtick_command" node.
        elif nt in ("backtick_command", "backtick"):
            is_cmdsub = True
            sub_type = SubshellType.BACKTICK
        elif nt == "expansion" and nn.text and nn.text.startswith("`"):
            is_cmdsub = True
            sub_type = SubshellType.BACKTICK
        else:
            return  # Don't add non-subshell nodes; but still recurse

        out.append(SubshellSpan(
            subshell_type=sub_type,
            text=nn.text,
            start_index=nn.start_index,
            end_index=nn.end_index,
            is_command_substitution=is_cmdsub,
            is_process_substitution=nt == "process_substitution",
        ))

    _walk_tree(root_node, visitor)

    # Deduplicate: a backtick may appear both as a dedicated node AND inside an
    # expansion wrapper depending on the grammar version.
    seen: set[tuple[int, int]] = set()
    deduped: list[SubshellSpan] = []
    for span in out:
        key = (span.start_index, span.end_index)
        if key not in seen:
            seen.add(key)
            deduped.append(span)
    return deduped


# Properly-spelled alias
extract_subshells = extract_subsells


# ---------------------------------------------------------------------------
# Operator analysis
# ---------------------------------------------------------------------------


_OP_SYMBOL_TO_TYPE: dict[str, CompoundOperatorType] = {
    "&&": CompoundOperatorType.AND,
    "||": CompoundOperatorType.OR,
    ";": CompoundOperatorType.SEMI,
    "&": CompoundOperatorType.BACKGROUND,
    "|": CompoundOperatorType.PIPE,
    "|&": CompoundOperatorType.PIPE_FAIL,
}


def extract_operator_analysis(root_node: Any) -> OperatorAnalysis:
    """Extract every compound operator and its position from the AST."""

    result = OperatorAnalysis()

    def visitor(nn: Any) -> None:
        nt = nn.type
        op_type = _OP_SYMBOL_TO_TYPE.get(nt)
        if op_type is not None:
            span = OperatorSpan(
                operator_type=op_type,
                symbol=nt,
                start_index=nn.start_index,
                end_index=nn.end_index,
                is_escaped=False,
            )
            result.operators.append(span)

            if op_type == CompoundOperatorType.SEMI:
                result.has_sequential = True
            elif op_type in (CompoundOperatorType.AND, CompoundOperatorType.OR):
                result.has_conditional = True
            elif op_type == CompoundOperatorType.BACKGROUND:
                result.has_background = True
            elif op_type in (CompoundOperatorType.PIPE, CompoundOperatorType.PIPE_FAIL):
                result.has_pipeline = True

    _walk_tree(root_node, visitor)

    # Backslash-escaped operators: detect when an operator node is preceded by
    # a backslash in the raw text.  True tree-sitter grammars emit escape
    # nodes separately, but this heuristic catches edge cases in lightweight
    # or stub parsers that inline the backslash before the operator text.
    for op in result.operators:
        if op.start_index > 0:
            # We need the command text to check the preceding character, but
            # extract_operator_analysis only receives the root node.  Mark
            # operators that appear inside an escape_sequence context.
            pass

    # If a 'word' node contains a literal operator preceded by backslash,
    # tree-sitter may not emit a separate operator node at all.  Walk the
    # tree looking for word nodes containing escaped operator patterns.
    def _detect_escaped_in_words(node: Any) -> None:
        nn = _normalize_node(node)
        # Check raw text of word/string nodes for backslash-operator pairs
        if nn.type in ("word", "raw_string", "string", "ansi_c_string"):
            t = nn.text
            for op_str in ("&&", "||", ";", "&", "|", "|&"):
                escaped_op = "\\" + op_str
                if escaped_op in t:
                    # Find position of the escaped operator within the word
                    pos = t.find(escaped_op)
                    if pos >= 0:
                        result.has_escaped_operator = True
        for child in nn.children:
            if child is not None:
                _detect_escaped_in_words(child)

    _detect_escaped_in_words(root_node)

    return result


# ---------------------------------------------------------------------------
# Security classification
# ---------------------------------------------------------------------------


# Commands flagged as destructive regardless of flags.
_DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset({
    "rm", "dd", "mkfs", "fdisk", "parted", "shutdown", "reboot",
    "init", "systemctl", "kill", "pkill", "killall", "iptables", "nft",
    "mount", "umount",
})

# Commands that escalate privileges.
_PRIVILEGED_COMMANDS: frozenset[str] = frozenset({
    "sudo", "su", "pkexec", "doas",
})

# Commands that access the network.
_NETWORK_COMMANDS: frozenset[str] = frozenset({
    "curl", "wget", "nc", "ncat", "ssh", "scp", "sftp", "ftp",
    "telnet", "rsync", "dig", "nslookup", "host", "ping",
    "aws", "gcloud", "az",
})

# Commands that dynamically evaluate code (injection risk).
_SCRIPT_EVAL_COMMANDS: frozenset[str] = frozenset({
    "eval", "source", ".", "bash", "sh", "zsh", "exec",
})


def classify_command_security(
    root_node: Any,
    command: str,
    *,
    known_safe_commands: frozenset[str] | None = None,
) -> CommandSecurityClassification:
    """Produce a security classification for *command* based on AST analysis.

    Uses the tree-sitter AST for accurate detection of quotes, subshells,
    and compound operators.  Falls back to string heuristics when the AST
    is incomplete (e.g. no tree-sitter available).
    """
    analysis = analyze_command(root_node, command)
    cs = analysis.compound_structure
    dp = analysis.dangerous_patterns

    findings: list[str] = []
    recommendations: list[str] = []

    # Extract the first executable name from the first segment.
    first_seg = cs.segments[0] if cs.segments else command
    first_word = first_seg.split()[0] if first_seg.split() else ""
    # Strip path: /usr/bin/rm -> rm
    executable = first_word.rsplit("/", 1)[-1] if "/" in first_word else first_word

    # ---- classify ----
    category = CommandSecurityCategory.UNKNOWN
    is_safe = True
    requires_approval = False

    # 1. Privilege escalation
    if executable in _PRIVILEGED_COMMANDS:
        category = CommandSecurityCategory.PRIVILEGED
        is_safe = False
        requires_approval = True
        findings.append(f"Command escalates privileges: {executable}")

    # 2. Destructive
    if executable in _DESTRUCTIVE_COMMANDS:
        if category == CommandSecurityCategory.UNKNOWN:
            category = CommandSecurityCategory.DESTRUCTIVE
        is_safe = False
        requires_approval = True
        findings.append(f"Destructive command: {executable}")

    # 3. Network
    if executable in _NETWORK_COMMANDS:
        if category == CommandSecurityCategory.UNKNOWN:
            category = CommandSecurityCategory.NETWORK_ACCESS
        requires_approval = True
        findings.append(f"Network-access command: {executable}")

    # 4. Script eval
    if executable in _SCRIPT_EVAL_COMMANDS or dp.has_command_substitution:
        if category == CommandSecurityCategory.UNKNOWN:
            category = CommandSecurityCategory.HAS_SCRIPT_INJECTION_RISK
        is_safe = False
        requires_approval = True
        findings.append("Command uses dynamic evaluation or injection vector")

    # 5. Output redirect (writes files)
    if cs.segments:
        for seg in cs.segments:
            if ">" in seg and not seg.strip().startswith(("echo", "printf")):
                if category == CommandSecurityCategory.UNKNOWN:
                    category = CommandSecurityCategory.WRITES_FILES
                    recommendations.append("Verify output destination is safe")

    # 6. Stdin / heredoc
    if dp.has_heredoc:
        if category == CommandSecurityCategory.UNKNOWN:
            category = CommandSecurityCategory.READS_STDIN

    # 7. Pipeline
    if cs.has_pipeline and category == CommandSecurityCategory.UNKNOWN:
        category = CommandSecurityCategory.PIPELINE_READ_ONLY

    # 8. Known safe
    if known_safe_commands and executable in known_safe_commands:
        if category in (CommandSecurityCategory.UNKNOWN, CommandSecurityCategory.PIPELINE_READ_ONLY):
            category = CommandSecurityCategory.READ_ONLY
            is_safe = True
            requires_approval = False

    # 9. Compound operators always need scrutiny
    if cs.has_compound_operators and not analysis.has_actual_operator_nodes:
        findings.append("Compound operators detected but may be escaped/non-functional")

    severity = "critical" if not is_safe else ("warning" if requires_approval else "safe")

    return CommandSecurityClassification(
        category=category,
        severity=severity,
        is_safe_to_run=is_safe,
        requires_approval=requires_approval,
        findings=findings,
        recommendations=recommendations,
    )


# ---------------------------------------------------------------------------
# Command intent classification
# ---------------------------------------------------------------------------


# Mapping from common executable names to primary intent.
_INTENT_MAP: dict[str, CommandIntent] = {
    # File read
    "cat": CommandIntent.FILE_READ, "less": CommandIntent.FILE_READ,
    "more": CommandIntent.FILE_READ, "head": CommandIntent.FILE_READ,
    "tail": CommandIntent.FILE_READ, "bat": CommandIntent.FILE_READ,
    # File list
    "ls": CommandIntent.FILE_LIST, "dir": CommandIntent.FILE_LIST,
    "tree": CommandIntent.FILE_LIST, "find": CommandIntent.FILE_LIST,
    "fd": CommandIntent.FILE_LIST, "fdfind": CommandIntent.FILE_LIST,
    "locate": CommandIntent.FILE_LIST,
    # File write
    "cp": CommandIntent.FILE_WRITE, "mv": CommandIntent.FILE_WRITE,
    "tee": CommandIntent.FILE_WRITE, "touch": CommandIntent.FILE_WRITE,
    "mkdir": CommandIntent.FILE_WRITE, "install": CommandIntent.FILE_WRITE,
    "ln": CommandIntent.FILE_WRITE, "tar": CommandIntent.FILE_WRITE,
    # File delete
    "rm": CommandIntent.FILE_DELETE, "rmdir": CommandIntent.FILE_DELETE,
    "unlink": CommandIntent.FILE_DELETE,
    # File permission
    "chmod": CommandIntent.FILE_PERMISSION, "chown": CommandIntent.FILE_PERMISSION,
    "chgrp": CommandIntent.FILE_PERMISSION,
    # Process
    "ps": CommandIntent.PROCESS_INFO, "top": CommandIntent.PROCESS_INFO,
    "htop": CommandIntent.PROCESS_INFO, "pgrep": CommandIntent.PROCESS_INFO,
    "kill": CommandIntent.PROCESS_KILL, "pkill": CommandIntent.PROCESS_KILL,
    "killall": CommandIntent.PROCESS_KILL,
    # Network
    "curl": CommandIntent.NETWORK, "wget": CommandIntent.NETWORK,
    "ssh": CommandIntent.NETWORK, "scp": CommandIntent.NETWORK,
    "nc": CommandIntent.NETWORK, "ncat": CommandIntent.NETWORK,
    "rsync": CommandIntent.NETWORK, "ping": CommandIntent.NETWORK,
    # System
    "sudo": CommandIntent.SYSTEM_ADMIN, "su": CommandIntent.SYSTEM_ADMIN,
    "systemctl": CommandIntent.SYSTEM_ADMIN, "mount": CommandIntent.SYSTEM_ADMIN,
    "umount": CommandIntent.SYSTEM_ADMIN, "service": CommandIntent.SYSTEM_ADMIN,
    # Shell builtins
    "cd": CommandIntent.SHELL_BUILTIN, "echo": CommandIntent.SHELL_BUILTIN,
    "export": CommandIntent.SHELL_BUILTIN, "source": CommandIntent.SHELL_BUILTIN,
    ".": CommandIntent.SCRIPT_EVAL, "eval": CommandIntent.SCRIPT_EVAL,
    "exec": CommandIntent.SCRIPT_EVAL,
    # Package management
    "apt": CommandIntent.PACKAGE_MANAGEMENT, "apt-get": CommandIntent.PACKAGE_MANAGEMENT,
    "brew": CommandIntent.PACKAGE_MANAGEMENT, "pip": CommandIntent.PACKAGE_MANAGEMENT,
    "pip3": CommandIntent.PACKAGE_MANAGEMENT, "npm": CommandIntent.PACKAGE_MANAGEMENT,
    "yarn": CommandIntent.PACKAGE_MANAGEMENT, "cargo": CommandIntent.PACKAGE_MANAGEMENT,
    "gem": CommandIntent.PACKAGE_MANAGEMENT, "dnf": CommandIntent.PACKAGE_MANAGEMENT,
    "yum": CommandIntent.PACKAGE_MANAGEMENT, "pacman": CommandIntent.PACKAGE_MANAGEMENT,
    # Git
    "git": CommandIntent.GIT_OPERATION,
    # Pipe filters
    "grep": CommandIntent.PIPE_FILTER, "egrep": CommandIntent.PIPE_FILTER,
    "fgrep": CommandIntent.PIPE_FILTER, "rg": CommandIntent.PIPE_FILTER,
    "sed": CommandIntent.PIPE_FILTER, "awk": CommandIntent.PIPE_FILTER,
    "sort": CommandIntent.PIPE_FILTER, "uniq": CommandIntent.PIPE_FILTER,
    "wc": CommandIntent.PIPE_FILTER, "cut": CommandIntent.PIPE_FILTER,
    "tr": CommandIntent.PIPE_FILTER, "column": CommandIntent.PIPE_FILTER,
    "jq": CommandIntent.PIPE_FILTER, "xargs": CommandIntent.PIPE_FILTER,
    # Env manipulation
    "env": CommandIntent.ENV_MANIPULATION, "unset": CommandIntent.ENV_MANIPULATION,
    "printenv": CommandIntent.ENV_MANIPULATION, "set": CommandIntent.ENV_MANIPULATION,
}


def classify_command_intent(
    root_node: Any,
    command: str,
) -> CommandIntentAnalysis:
    """Classify the high-level intent(s) of a shell command.

    Uses the AST for accurate pipeline / subshell detection, then maps
    executable names and patterns to :class:`CommandIntent` values.
    """
    analysis = analyze_command(root_node, command)
    cs = analysis.compound_structure

    # Collect intents from every pipeline segment.
    intents: set[CommandIntent] = set()
    for seg in cs.segments:
        executable = seg.split()[0] if seg.split() else ""
        executable = executable.rsplit("/", 1)[-1] if "/" in executable else executable

        intent = _INTENT_MAP.get(executable)
        if intent is not None:
            intents.add(intent)
        else:
            intents.add(CommandIntent.UNKNOWN)

    # Derive compound intents from structure.
    if cs.has_pipeline:
        intents.add(CommandIntent.PIPE_FILTER)

    if not intents:
        intents = {CommandIntent.UNKNOWN}

    intent_list = list(intents)

    # Determine primary intent (most "dangerous" wins).
    priority_order = [
        CommandIntent.PROCESS_KILL,
        CommandIntent.FILE_DELETE,
        CommandIntent.SCRIPT_EVAL,
        CommandIntent.SYSTEM_ADMIN,
        CommandIntent.FILE_PERMISSION,
        CommandIntent.PACKAGE_MANAGEMENT,
        CommandIntent.NETWORK,
        CommandIntent.FILE_WRITE,
        CommandIntent.ENV_MANIPULATION,
        CommandIntent.GIT_OPERATION,
        CommandIntent.PIPE_FILTER,
        CommandIntent.FILE_LIST,
        CommandIntent.FILE_READ,
        CommandIntent.PROCESS_INFO,
        CommandIntent.SHELL_BUILTIN,
        CommandIntent.UNKNOWN,
    ]
    primary = CommandIntent.UNKNOWN
    for intent in priority_order:
        if intent in intents:
            primary = intent
            break

    destructive_intents = {
        CommandIntent.FILE_DELETE,
        CommandIntent.FILE_PERMISSION,
        CommandIntent.PROCESS_KILL,
        CommandIntent.SYSTEM_ADMIN,
        CommandIntent.SCRIPT_EVAL,
    }
    network_intents = {CommandIntent.NETWORK}
    privileged_intents = {CommandIntent.SYSTEM_ADMIN}
    fs_intents = {
        CommandIntent.FILE_WRITE,
        CommandIntent.FILE_DELETE,
        CommandIntent.FILE_PERMISSION,
    }

    is_destructive = bool(intents & destructive_intents)
    is_read_only = not is_destructive

    return CommandIntentAnalysis(
        intents=intent_list,
        primary_intent=primary,
        is_read_only=is_read_only,
        is_destructive=is_destructive,
        requires_network=bool(intents & network_intents),
        escalates_privilege=bool(intents & privileged_intents),
        mutates_filesystem=bool(intents & fs_intents),
        confidence=0.9 if analysis is not None else 0.5,
    )


# ---------------------------------------------------------------------------
# Full combined analysis
# ---------------------------------------------------------------------------


def run_full_analysis(root_node: Any, command: str) -> FullAnalysisResult:
    """Run all tree-sitter analyses and return a combined result.

    This is the one-stop entry point for consumers that need every analysis
    dimension (security validators, permission checkers, audit loggers).
    """
    ts = analyze_command(root_node, command)
    security = classify_command_security(root_node, command)

    # Variable analysis may fail gracefully on incomplete ASTs
    try:
        va = extract_variable_info(root_node, command)
    except Exception:
        va = VariableAnalysis()

    ra = extract_redirects(root_node)
    qa = extract_quoting_analysis(root_node, command)
    oa = extract_operator_analysis(root_node)
    intent = classify_command_intent(root_node, command)

    diag = AnalysisDiagnostics()

    # Synthesize diagnostics from security findings.
    for finding in security.findings:
        severity = (
            ShellSyntaxSeverity.CRITICAL
            if security.severity == "critical"
            else ShellSyntaxSeverity.WARNING
        )
        diag.add(ShellSyntaxWarning(
            severity=severity,
            code="SEC_" + security.category.name[:12].upper(),
            message=finding,
        ))

    is_safe = security.is_safe_to_run and not intent.is_destructive
    approval = security.requires_approval or not is_safe

    return FullAnalysisResult(
        raw_command=command,
        tree_sitter_analysis=ts,
        security_classification=security,
        variable_analysis=va,
        redirect_analysis=ra,
        quoting_analysis=qa,
        operator_analysis=oa,
        diagnostics=diag,
        intent_analysis=intent,
        is_safe=is_safe,
        approval_required=approval,
    )


# ---------------------------------------------------------------------------
# Syntax validation
# ---------------------------------------------------------------------------


class SyntaxValidationResult:
    """Outcome of checking whether a tree-sitter AST represents valid syntax."""

    __slots__ = ("is_valid", "error_node_count", "missing_node_count",
                 "unexpected_token_count", "message")

    def __init__(
        self,
        is_valid: bool = True,
        error_node_count: int = 0,
        missing_node_count: int = 0,
        unexpected_token_count: int = 0,
        message: str = "",
    ) -> None:
        self.is_valid = is_valid
        self.error_node_count = error_node_count
        self.missing_node_count = missing_node_count
        self.unexpected_token_count = unexpected_token_count
        self.message = message


def validate_syntax(root_node: Any, *, command: str = "") -> SyntaxValidationResult:
    """Check whether the tree-sitter AST represents a syntactically valid script.

    Counts ERROR / MISSING nodes (common in tree-sitter grammars when the parse
    is incomplete).  An empty *command* that still parses as a ``program`` node
    is considered valid.
    """
    error_count = 0
    missing_count = 0
    unexpected_count = 0
    messages: list[str] = []

    def visitor(nn: Any) -> None:
        nonlocal error_count, missing_count, unexpected_count
        nt = nn.type
        if nt == "ERROR" or "ERROR" in nt:
            error_count += 1
            snippet = nn.text[:80] if nn.text else "(empty)"
            messages.append(f"ERROR node at pos {nn.start_index}: {snippet!r}")
        elif nt == "MISSING" or "MISSING" in nt:
            missing_count += 1
            messages.append(f"MISSING node at pos {nn.start_index}")
        # Some grammars use "UNEXPECTED" as a token type
        if "UNEXPECTED" in nt.upper():
            unexpected_count += 1

    _walk_tree(root_node, visitor)

    # If the root node itself is ERROR, the parse failed entirely.
    nn = _normalize_node(root_node)
    root_is_error = nn.type == "ERROR"

    is_valid = (
        error_count == 0
        and missing_count == 0
        and not root_is_error
        and (bool(command.strip()) or nn.type == "program")
    )

    message = "; ".join(messages) if messages else "Syntax OK"

    return SyntaxValidationResult(
        is_valid=is_valid,
        error_node_count=error_count,
        missing_node_count=missing_count,
        unexpected_token_count=unexpected_count,
        message=message,
    )


# ---------------------------------------------------------------------------
# Command-name extraction
# ---------------------------------------------------------------------------


def get_command_name(root_node: Any, command: str = "") -> str:
    """Return the primary executable name from a tree-sitter AST.

    Walks the first command / variable_assignment / subshell to find the
    first ``word`` node that is not a redirect, operator, or keyword.
    Falls back to splitting *command* on whitespace when the AST is sparse.
    """
    n = _normalize_node(root_node)

    def _first_command_word(node: Any) -> str | None:
        nn = _normalize_node(node)
        nt = nn.type

        if nt == "command":
            for child in nn.children:
                if child is None:
                    continue
                cn = _normalize_node(child)
                ct = cn.type
                if ct == "command_name":
                    return cn.text
                if ct == "word":
                    w = cn.text
                    # Skip words that are really redirect targets or assignments
                    if w.startswith("-"):
                        continue
                    if "=" in w and ct == "word":
                        # Could be env prefix; skip to next word
                        continue
                    return w
            # Recurse into the first non-redirect child
            for child in nn.children:
                if child is None:
                    continue
                cn = _normalize_node(child)
                if cn.type == "file_redirect":
                    continue
                inner = _first_command_word(child)
                if inner:
                    return inner
            return None

        if nt == "pipeline":
            for child in nn.children:
                if child is None:
                    continue
                cn = _normalize_node(child)
                if cn.type == "command":
                    inner = _first_command_word(child)
                    if inner:
                        return inner
            return None

        if nt == "subshell":
            for child in nn.children:
                if child is None:
                    continue
                inner = _first_command_word(child)
                if inner:
                    return inner
            return None

        if nt == "variable_assignment":
            text = nn.text
            name = text.split("=", 1)[0] if text and "=" in text else text
            return name or None

        if nt == "declaration_command":
            for child in nn.children:
                if child is None:
                    continue
                inner = _first_command_word(child)
                if inner:
                    return inner
            return None

        # Fall through to generic child recursion for all other node types
        for child in nn.children:
            if child is None:
                continue
            inner = _first_command_word(child)
            if inner:
                return inner

        return None

    result = _first_command_word(n)

    if not result and command:
        # Fallback: split command on whitespace and take first non-assignment token
        tokens = command.split()
        for token in tokens:
            if "=" not in token and not token.startswith(("{", "(")):
                result = token.rsplit("/", 1)[-1]
                break

    return result or ""


def extract_all_command_names(root_node: Any) -> list[str]:
    """Return every executable name found in the AST (pipeline-aware).

    Traverses each pipeline stage, command, and subshell to collect all
    ``command_name`` or leading ``word`` nodes.  Useful for understanding
    every tool that will be invoked.
    """
    names: list[str] = []
    seen_names: set[str] = set()

    def visitor(nn: Any) -> None:
        nt = nn.type
        if nt == "command_name":
            name = nn.text
            if name and name not in seen_names:
                seen_names.add(name)
                names.append(name)
        elif nt == "command":
            # Fallback: if no command_name child exists, grab first non-assignment word.
            has_cmd_name = any(
                child is not None and _normalize_node(child).type == "command_name"
                for child in nn.children
            )
            if has_cmd_name:
                return  # command_name children are already visited independently
            for child in nn.children:
                if child is None:
                    continue
                cn = _normalize_node(child)
                ct = cn.type
                if ct == "word":
                    w = cn.text
                    if "=" not in w:
                        name = w.rsplit("/", 1)[-1] if "/" in w else w
                        if name and name not in seen_names:
                            seen_names.add(name)
                            names.append(name)
                        break

    _walk_tree(root_node, visitor)
    return names


# ---------------------------------------------------------------------------
# Argument-injection detection
# ---------------------------------------------------------------------------


# Shell flags that indicate dangerous operations when injected.
_SUSPICIOUS_FLAG_PATTERNS: frozenset[str] = frozenset({
    "-c", "-e",  # bash -c, sh -c, eval-like
    "-o",        # set -o, could enable risky options
    "--eval", "--command",
    "-p",        # bash -p (privileged mode)
    "-rf", "-f", "--force",  # rm -rf
})

# Common short flags that can be chained after a single dash (e.g. -rf, -rfv)
_DANGEROUS_SHORT_FLAGS: frozenset[str] = frozenset("rfR")


def detect_argument_injection(root_node: Any, command: str) -> list[ShellSyntaxWarning]:
    """Detect patterns that suggest argument / flag injection risk.

    Returns a list of :class:`ShellSyntaxWarning` for patterns like:
    - Unquoted variables used as flags (``$FLAGS``)
    - User-controlled strings passed to ``-c`` / ``-e``
    - Dangerous flag combos like ``-rf`` appearing outside quotes
    """
    warnings: list[ShellSyntaxWarning] = []
    ctx = extract_quote_context(root_node, command)
    unquoted = ctx.fully_unquoted
    unquoted_tokens = unquoted.split()

    # 1. Unquoted variable used as a flag (e.g. cmd $OPT file)
    for i, token in enumerate(unquoted_tokens):
        if token.startswith("$") and not token.startswith("${"):
            # Variable used as argument — could inject flags
            if i > 0:  # not the command itself
                warnings.append(ShellSyntaxWarning(
                    severity=ShellSyntaxSeverity.WARNING,
                    code="ARG_INJ_UNQUOTED_VAR",
                    message=f"Unquoted variable used as argument: {token}",
                    suggestion=f"Quote or validate the value of {token}",
                ))

    # 2. Dangerous flag used outside quotes
    for token in unquoted_tokens:
        token_lower = token.lower()
        if token_lower == "-c" or token_lower == "-e":
            warnings.append(ShellSyntaxWarning(
                severity=ShellSyntaxSeverity.CRITICAL,
                code="ARG_INJ_EVAL_FLAG",
                message=f"Dangerous flag {token} may enable code execution",
                suggestion="Avoid passing user input to -c/-e flags",
            ))
        # Check for chained short flags like -rf, -rfv
        if token.startswith("-") and not token.startswith("--") and len(token) <= 5:
            for ch in token[1:]:
                if ch in _DANGEROUS_SHORT_FLAGS:
                    warnings.append(ShellSyntaxWarning(
                        severity=ShellSyntaxSeverity.CRITICAL,
                        code="ARG_INJ_DANGEROUS_FLAG",
                        message=f"Dangerous flag combo: {token}",
                        suggestion="Verify that destructive flags are intentional",
                    ))
                    break

    # 3. Unquoted expansion in argument position to eval-like commands
    command_name = get_command_name(root_node, command)
    if command_name in ("eval", "source", "bash", "sh", "zsh", "exec"):
        for ref in extract_variable_info(root_node, command).references:
            if not ref.is_special:
                warnings.append(ShellSyntaxWarning(
                    severity=ShellSyntaxSeverity.CRITICAL,
                    code="ARG_INJ_EVAL_WITH_VAR",
                    message=f"eval-like command '{command_name}' uses variable ${ref.name}",
                    suggestion="Avoid dynamic code evaluation with user-controlled variables",
                ))

    return warnings


# ---------------------------------------------------------------------------
# Path-traversal detection
# ---------------------------------------------------------------------------


_PATH_TRAVERSAL_PATTERNS: tuple[str, ...] = (
    "../", "..\\", "/etc/passwd", "/etc/shadow",
    "/root/", "~root", "/proc/", "/sys/",
)


def is_path_traversal(command: str) -> bool:
    """Check *command* text for common path-traversal or sensitive-path patterns.

    This is a string-level heuristic; the AST-based analysis can narrow
    the check to unquoted segments via :func:`extract_quote_context`.
    """
    command_lower = command.lower()
    for pattern in _PATH_TRAVERSAL_PATTERNS:
        if pattern.lower() in command_lower:
            return True
    # Detect repeated ../ beyond a threshold
    if command.count("../") >= 3:
        return True
    return False


def check_path_traversal_ast(root_node: Any, command: str) -> list[ShellSyntaxWarning]:
    """AST-aware path-traversal check: only flags unquoted ``../`` and
    sensitive paths, ignoring quoted literal strings.
    """
    warnings: list[ShellSyntaxWarning] = []
    ctx = extract_quote_context(root_node, command)
    # Use fully_unquoted — ../ inside single quotes is a literal string
    unquoted = ctx.fully_unquoted.lower()

    for pattern in _PATH_TRAVERSAL_PATTERNS:
        if pattern.lower() in unquoted:
            warnings.append(ShellSyntaxWarning(
                severity=ShellSyntaxSeverity.WARNING,
                code="PATH_TRAVERSAL",
                message=f"Unquoted sensitive path pattern: {pattern}",
                suggestion="Validate or restrict file access",
            ))

    if unquoted.count("../") >= 3:
        warnings.append(ShellSyntaxWarning(
            severity=ShellSyntaxSeverity.CRITICAL,
            code="PATH_TRAVERSAL_DEEP",
            message="Multiple ../ sequences in unquoted text",
            suggestion="Restrict to specific directories",
        ))

    return warnings


# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------


def _find_comment_spans(root_node: Any) -> list[tuple[int, int]]:
    """Collect all (start, end) spans for ``comment`` nodes."""
    spans: list[tuple[int, int]] = []

    def visitor(nn: Any) -> None:
        if nn.type == "comment":
            spans.append((nn.start_index, nn.end_index))

    _walk_tree(root_node, visitor)
    return spans


def strip_comments(root_node: Any, command: str) -> str:
    """Remove comment text from *command* using AST-level comment-node positions.

    Falls back to line-based ``#`` stripping when the AST has no comment nodes,
    which can happen with incomplete or stub parsers.
    """
    spans = _find_comment_spans(root_node)
    if spans:
        # Remove from right to left to preserve indices
        result = command
        for start, end in sorted(spans, key=lambda x: x[0], reverse=True):
            # Also strip leading whitespace before the comment on the same line
            ws_start = start
            while ws_start > 0 and result[ws_start - 1] in (" ", "\t"):
                ws_start -= 1
            result = result[:ws_start] + result[end:]
        return result

    # Fallback: strip #-style comments line by line
    lines = command.split("\n")
    stripped: list[str] = []
    for line in lines:
        # Find first # that is not inside quotes (simple heuristic)
        in_single = False
        in_double = False
        hash_pos = -1
        for i, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                hash_pos = i
                break
        if hash_pos >= 0:
            stripped.append(line[:hash_pos])
        else:
            stripped.append(line)
    return "\n".join(stripped)


# ---------------------------------------------------------------------------
# Convenience: analyze a raw command string
# ---------------------------------------------------------------------------


def analyze_command_string(
    command: str,
    parse_fn: Any,
    *,
    known_safe_commands: frozenset[str] | None = None,
) -> FullAnalysisResult:
    """Parse *command* with *parse_fn* and run the full analysis suite.

    ``parse_fn(command: str) -> tree`` must return a tree-sitter root node
    (or compatible dict).  If parsing fails, the result carries diagnostics
    and safe defaults.

    This is the primary convenience entry point for one-shot analysis.
    """
    try:
        root_node = parse_fn(command)
    except Exception as exc:
        diagnostics = AnalysisDiagnostics()
        diagnostics.add(ShellSyntaxWarning(
            severity=ShellSyntaxSeverity.ERROR,
            code="PARSE_FAILED",
            message=f"Parse error: {exc}",
        ))
        return FullAnalysisResult(
            raw_command=command,
            diagnostics=diagnostics,
            is_safe=False,
            approval_required=True,
        )

    if root_node is None:
        diagnostics = AnalysisDiagnostics()
        diagnostics.add(ShellSyntaxWarning(
            severity=ShellSyntaxSeverity.ERROR,
            code="PARSE_NULL",
            message="Parser returned None",
        ))
        return FullAnalysisResult(
            raw_command=command,
            diagnostics=diagnostics,
            is_safe=False,
            approval_required=True,
        )

    try:
        result = run_full_analysis(root_node, command)

        # Augment with argument-injection and path-traversal checks.
        arg_warnings = detect_argument_injection(root_node, command)
        for w in arg_warnings:
            result.diagnostics.add(w)
            if w.severity in (ShellSyntaxSeverity.CRITICAL, ShellSyntaxSeverity.ERROR):
                result.is_safe = False
                result.approval_required = True

        path_warnings = check_path_traversal_ast(root_node, command)
        for w in path_warnings:
            result.diagnostics.add(w)
            if w.severity == ShellSyntaxSeverity.CRITICAL:
                result.is_safe = False
                result.approval_required = True

        # Validate syntax
        syntax = validate_syntax(root_node, command=command)
        if not syntax.is_valid:
            result.diagnostics.add(ShellSyntaxWarning(
                severity=ShellSyntaxSeverity.ERROR,
                code="SYNTAX_INVALID",
                message=syntax.message,
            ))
            result.is_safe = False
            result.approval_required = True

        # If a known_safe_commands override is provided, re-run classification
        # to incorporate it.
        if known_safe_commands:
            sec = classify_command_security(
                root_node, command, known_safe_commands=known_safe_commands
            )
            result.security_classification = sec
            result.is_safe = sec.is_safe_to_run and not result.intent_analysis.is_destructive
            result.approval_required = sec.requires_approval or not result.is_safe

        return result

    except Exception as exc:
        diagnostics = AnalysisDiagnostics()
        diagnostics.add(ShellSyntaxWarning(
            severity=ShellSyntaxSeverity.ERROR,
            code="ANALYSIS_FAILED",
            message=f"Analysis error: {exc}",
        ))
        return FullAnalysisResult(
            raw_command=command,
            diagnostics=diagnostics,
            is_safe=False,
            approval_required=True,
        )


# ---------------------------------------------------------------------------
# Safe-alternative suggestion
# ---------------------------------------------------------------------------


_SAFE_ALTERNATIVES: dict[str, str] = {
    "rm": "Consider moving to trash (trash, gio trash) instead of permanent delete",
    "rm -rf": "Use trash command or review paths before deleting",
    "sudo rm": "Avoid sudo rm; delete as regular user when possible",
    "kill -9": "Prefer kill without -9 (SIGTERM) to allow cleanup",
    "shutdown": "Use systemctl poweroff or confirm with a dry-run",
    "dd": "Use ddrescue for recovery or verify of= target carefully",
    "mkfs": "Verify device path before formatting",
    ":(){ :|:& };:": "Do not run fork bombs",
    "chmod 777": "Prefer more restrictive permissions (755, 644, 700)",
    "chmod -R 777": "Avoid world-writable recursive permissions",
    "curl | bash": "Download and review the script before piping to bash",
    "wget -O - | sh": "Download and review the script before piping to sh",
}


def suggest_safe_alternative(command: str) -> str | None:
    """Return a human-readable safe-alternative suggestion for *command*, or None.

    Matches against common dangerous command patterns.
    """
    stripped = command.strip()
    stripped_lower = stripped.lower()

    # Exact and prefix matches
    for pattern, suggestion in _SAFE_ALTERNATIVES.items():
        if stripped_lower.startswith(pattern.lower()):
            return suggestion

    # Pattern-based heuristics
    if "curl" in stripped_lower and ("| bash" in stripped_lower or "| sh" in stripped_lower):
        return "Download and review the script before piping to bash"
    if "wget" in stripped_lower and ("| bash" in stripped_lower or "| sh" in stripped_lower):
        return "Download and review the script before piping to sh"
    if stripped_lower.startswith("sudo "):
        inner = stripped[5:].strip()
        return f"Run '{inner}' without sudo if possible, or review why privilege escalation is needed"
    if "> /dev/" in stripped or "> /dev/" in stripped_lower:
        return "Verify the output device path is correct to avoid overwriting disk devices"
    if stripped_lower.startswith("git push --force") or "git push -f" in stripped_lower:
        return "Use git push --force-with-lease to avoid overwriting remote changes"

    return None


# ---------------------------------------------------------------------------
# Heredoc body extraction
# ---------------------------------------------------------------------------


@dataclass
class HeredocBody:
    """Extracted heredoc body with metadata."""

    delimiter: str
    content: str
    start_index: int   # of the << operator
    end_index: int     # end of the heredoc body
    is_quoted: bool    # delimiter was quoted → no expansion
    allows_expansion: bool


def extract_heredoc_bodies(root_node: Any, command: str) -> list[HeredocBody]:
    """Extract heredoc bodies and their delimiters from the AST.

    Returns one :class:`HeredocBody` per heredoc found, distinguishing
    quoted (no expansion) from unquoted delimiters.
    """
    bodies: list[HeredocBody] = []

    def visitor(nn: Any) -> None:
        if nn.type != "heredoc_redirect":
            return

        delimiter = ""
        is_quoted = False
        body_text = ""
        body_start = nn.end_index
        body_end = nn.end_index

        for child in nn.children:
            if child is None:
                continue
            cn = _normalize_node(child)
            ct = cn.type

            if ct == "heredoc_start":
                raw = cn.text
                # Strip leading quote/backslash markers from the delimiter text
                first = raw[0] if raw else ""
                if first in ("'", '"', "\\"):
                    is_quoted = True
                # The actual delimiter is the word after <<
                delimiter = raw.lstrip("'\"\\").strip()
                body_start = cn.end_index

            elif ct in ("heredoc_body", "heredoc_content"):
                body_text = cn.text
                body_end = cn.end_index

            elif ct == "word" and not delimiter:
                # Some grammars put the delimiter in a word node
                raw = cn.text
                first = raw[0] if raw else ""
                if first in ("'", '"', "\\"):
                    is_quoted = True
                delimiter = raw.lstrip("'\"\\").strip()

        # If the grammar didn't split body into a separate node, find it via
        # delimiter positions in the raw command string.
        if not body_text and delimiter:
            # Search for the delimiter on a line by itself after the heredoc start
            pos = body_start
            if pos < len(command):
                remaining = command[pos:]
                lines = remaining.split("\n")
                # Skip the first partial line (rest of the heredoc start line)
                body_lines: list[str] = []
                in_body = False
                for line in lines[1:]:
                    if line.strip() == delimiter:
                        break
                    body_lines.append(line)
                    in_body = True
                if in_body:
                    body_text = "\n".join(body_lines)
                    # Approximate end
                    body_end = pos + len("\n".join(lines[:len(body_lines) + 2]))

        if delimiter:
            bodies.append(HeredocBody(
                delimiter=delimiter,
                content=body_text,
                start_index=nn.start_index,
                end_index=body_end,
                is_quoted=is_quoted,
                allows_expansion=not is_quoted,
            ))

    _walk_tree(root_node, visitor)
    return bodies


# ---------------------------------------------------------------------------
# Pipeline chain analysis
# ---------------------------------------------------------------------------


@dataclass
class PipelineChainAnalysis:
    """Information about a pipeline chain extracted from the AST."""

    stage_count: int = 0
    stage_commands: list[str] = field(default_factory=list)
    stage_executables: list[str] = field(default_factory=list)
    has_destructive_stage: bool = False
    has_network_stage: bool = False
    has_filter_stage: bool = False
    total_operator_count: int = 0


def detect_pipe_chain(root_node: Any, command: str) -> PipelineChainAnalysis:
    """Analyze the pipeline structure from the AST.

    Returns the number of stages, each stage's command text and executable,
    and flags for destructive / network / filter stages.
    """
    result = PipelineChainAnalysis()
    cs = extract_compound_structure(root_node, command)
    oa = extract_operator_analysis(root_node)

    # Count pipe operators
    pipe_count = sum(
        1 for op in oa.operators
        if op.operator_type in (CompoundOperatorType.PIPE, CompoundOperatorType.PIPE_FAIL)
    )

    result.total_operator_count = pipe_count
    result.stage_count = len(cs.segments)

    for seg in cs.segments:
        result.stage_commands.append(seg)
        seg_exec = seg.split()[0] if seg.split() else ""
        seg_exec = seg_exec.rsplit("/", 1)[-1] if "/" in seg_exec else seg_exec
        result.stage_executables.append(seg_exec)

        # Classify each stage
        if seg_exec in _DESTRUCTIVE_COMMANDS:
            result.has_destructive_stage = True
        if seg_exec in _NETWORK_COMMANDS:
            result.has_network_stage = True
        if seg_exec in {
            "grep", "egrep", "fgrep", "rg", "sed", "awk", "sort",
            "uniq", "wc", "cut", "tr", "head", "tail", "column",
            "jq", "xargs",
        }:
            result.has_filter_stage = True

    return result


# ---------------------------------------------------------------------------
# Per-stage pipeline analysis
# ---------------------------------------------------------------------------


def analyze_pipeline_stages(
    root_node: Any,
    command: str,
) -> list[CommandSecurityClassification]:
    """Run security classification independently on each pipeline stage.

    Returns one :class:`CommandSecurityClassification` per segment in the
    pipeline, preserving stage order.
    """
    cs = extract_compound_structure(root_node, command)
    results: list[CommandSecurityClassification] = []

    # For each segment, re-parse it as a standalone command
    for seg in cs.segments:
        try:
            # Build a minimal analysis for this segment
            seg_analysis = analyze_command(root_node, command)
            # Override the segment text
            seg_analysis.compound_structure.segments = [seg]
            seg_analysis.compound_structure.operators = []
            seg_analysis.compound_structure.has_compound_operators = False
            seg_analysis.compound_structure.has_pipeline = False

            classification = classify_command_security(root_node, seg)
            results.append(classification)
        except Exception:
            results.append(CommandSecurityClassification(
                category=CommandSecurityCategory.UNKNOWN,
                severity="warning",
                is_safe_to_run=False,
                requires_approval=True,
                findings=[f"Could not analyze stage: {seg}"],
            ))

    return results


# ---------------------------------------------------------------------------
# Shell-builtin-only detection
# ---------------------------------------------------------------------------


_SHELL_BUILTINS: frozenset[str] = frozenset({
    "cd", "echo", "printf", "pwd", "true", "false", "type", "command",
    "export", "readonly", "unset", "set", "alias", "unalias",
    "bg", "fg", "jobs", "disown", "wait", "times", "umask",
    "pushd", "popd", "dirs", "hash", "help", "history",
    "caller", "compgen", "complete", "mapfile", "readarray",
    "enable", "eval", "exec", "exit", "logout", "return",
    "shift", "source", "test", "[", "[[", "let", "declare",
    "local", "typeset", "builtin", "bind", "getopts",
    "shopt", "suspend", "trap",
})


def is_shell_builtin_only(root_node: Any, command: str = "") -> tuple[bool, list[str]]:
    """Check whether every executable in the AST is a shell builtin.

    Returns ``(all_builtins, non_builtin_list)``.  An empty command returns
    ``(True, [])``.
    """
    names = extract_all_command_names(root_node)
    if not names:
        return True, []

    non_builtins: list[str] = []
    for name in names:
        if name not in _SHELL_BUILTINS and name:
            non_builtins.append(name)

    if not non_builtins and command.strip():
        # Double-check: try splitting command directly
        first_word = command.strip().split()[0] if command.strip().split() else ""
        cmd_name = first_word.rsplit("/", 1)[-1] if "/" in first_word else first_word
        if cmd_name and cmd_name not in _SHELL_BUILTINS:
            return False, [cmd_name]

    return len(non_builtins) == 0, non_builtins


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def summarize_analysis(result: FullAnalysisResult) -> str:
    """Produce a human-readable multi-line summary of a full analysis.

    Suitable for display in audit logs, approval prompts, or debug output.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("TREE-SITTER COMMAND ANALYSIS")
    lines.append("=" * 60)
    lines.append(f"Command: {result.raw_command!r}")
    lines.append(f"Safe: {result.is_safe}")
    lines.append(f"Approval required: {result.approval_required}")

    # Security
    sec = result.security_classification
    lines.append("")
    lines.append(f"Security category:  {sec.category.name}")
    lines.append(f"Severity:           {sec.severity}")
    if sec.findings:
        lines.append("Findings:")
        for f in sec.findings:
            lines.append(f"  - {f}")
    if sec.recommendations:
        lines.append("Recommendations:")
        for r in sec.recommendations:
            lines.append(f"  - {r}")

    # Intent
    intent = result.intent_analysis
    lines.append("")
    lines.append(f"Primary intent:     {intent.primary_intent.name}")
    lines.append(f"All intents:        {[i.name for i in intent.intents]}")
    lines.append(f"Read-only:          {intent.is_read_only}")
    lines.append(f"Destructive:        {intent.is_destructive}")
    lines.append(f"Network:            {intent.requires_network}")
    lines.append(f"Privilege:          {intent.escalates_privilege}")
    lines.append(f"Mutates filesystem: {intent.mutates_filesystem}")

    # Redirects
    ra = result.redirect_analysis
    lines.append("")
    lines.append(f"Redirects:          {ra.count}")
    lines.append(f"  Output:           {ra.has_output_redirect}")
    lines.append(f"  Input:            {ra.has_input_redirect}")
    lines.append(f"  Heredoc:          {ra.has_heredoc}")

    # Variables
    va = result.variable_analysis
    lines.append("")
    lines.append(f"Assignments:        {len(va.assignments)}")
    lines.append(f"References:         {len(va.references)}")
    lines.append(f"Positional params:  {va.has_positional_params}")
    lines.append(f"Sensitive env:      {va.has_sensitive_env}")

    # Quoting
    qa = result.quoting_analysis
    lines.append("")
    lines.append(f"Single-quoted segs: {qa.single_quoted_count}")
    lines.append(f"Double-quoted segs: {qa.double_quoted_count}")
    lines.append(f"ANSI-C quoted segs: {qa.ansi_c_count}")
    lines.append(f"Heredoc segs:       {qa.heredoc_count}")
    lines.append(f"Backslash escapes:  {qa.has_backslash_escape}")
    lines.append(f"Unquoted dangerous: {qa.has_unquoted_dangerous_chars}")

    # Operators
    oa = result.operator_analysis
    lines.append("")
    lines.append(f"Operators:          {len(oa.operators)}")
    lines.append(f"  Sequential (;):   {oa.has_sequential}")
    lines.append(f"  Conditional:      {oa.has_conditional}")
    lines.append(f"  Background (&):   {oa.has_background}")
    lines.append(f"  Pipeline (|):     {oa.has_pipeline}")
    lines.append(f"  Escaped:          {oa.has_escaped_operator}")

    # Diagnostics
    diag = result.diagnostics
    lines.append("")
    lines.append(f"Diagnostics:        {len(diag.warnings)} warnings")
    if diag.warnings:
        for w in diag.warnings:
            lines.append(f"  [{w.severity.name}] {w.code}: {w.message}")

    # Safe alternative
    alt = suggest_safe_alternative(result.raw_command)
    if alt:
        lines.append("")
        lines.append(f"Suggestion: {alt}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structured security report (JSON-friendly)
# ---------------------------------------------------------------------------


def get_security_report(
    command: str,
    parse_fn: Any,
    *,
    known_safe_commands: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Run the full analysis and return a JSON-friendly security report dict.

    The returned dict is suitable for serialization, API responses, or
    structured logging.  It includes all analysis dimensions, a pass/fail
    verdict, and human-readable reasons.
    """
    result = analyze_command_string(
        command, parse_fn, known_safe_commands=known_safe_commands
    )
    sec = result.security_classification
    intent = result.intent_analysis

    # Build a list of all reasons the command requires approval
    approval_reasons: list[str] = []
    if not result.is_safe:
        approval_reasons.append("Command classified as unsafe")
    if result.approval_required:
        approval_reasons.extend(sec.findings)
    if intent.is_destructive:
        approval_reasons.append(f"Destructive intent: {intent.primary_intent.name}")
    if intent.escalates_privilege:
        approval_reasons.append("Escalates privileges")
    if intent.requires_network:
        approval_reasons.append("Requires network access")
    for w in result.diagnostics.warnings:
        if w.severity in (ShellSyntaxSeverity.CRITICAL, ShellSyntaxSeverity.ERROR):
            approval_reasons.append(f"[{w.code}] {w.message}")

    safe_alternative = suggest_safe_alternative(command)

    return {
        "command": command,
        "verdict": "safe" if (result.is_safe and not result.approval_required) else "requires_approval",
        "is_safe": result.is_safe,
        "approval_required": result.approval_required,
        "approval_reasons": approval_reasons,
        "security": {
            "category": sec.category.name,
            "severity": sec.severity,
            "findings": sec.findings,
            "recommendations": sec.recommendations,
        },
        "intent": {
            "primary": intent.primary_intent.name,
            "all": [i.name for i in intent.intents],
            "is_read_only": intent.is_read_only,
            "is_destructive": intent.is_destructive,
            "requires_network": intent.requires_network,
            "escalates_privilege": intent.escalates_privilege,
            "mutates_filesystem": intent.mutates_filesystem,
        },
        "redirects": {
            "count": result.redirect_analysis.count,
            "has_output": result.redirect_analysis.has_output_redirect,
            "has_input": result.redirect_analysis.has_input_redirect,
            "details": [
                {
                    "type": r.redirect_type.name,
                    "target": r.target,
                    "fd": r.fd,
                }
                for r in result.redirect_analysis.redirects
            ],
        },
        "variables": {
            "assignment_count": len(result.variable_analysis.assignments),
            "reference_count": len(result.variable_analysis.references),
            "has_sensitive_env": result.variable_analysis.has_sensitive_env,
            "has_positional_params": result.variable_analysis.has_positional_params,
        },
        "quoting": {
            "single_quoted": result.quoting_analysis.single_quoted_count,
            "double_quoted": result.quoting_analysis.double_quoted_count,
            "ansi_c": result.quoting_analysis.ansi_c_count,
            "heredoc": result.quoting_analysis.heredoc_count,
            "has_unquoted_dangerous": result.quoting_analysis.has_unquoted_dangerous_chars,
        },
        "operators": {
            "count": len(result.operator_analysis.operators),
            "has_pipeline": result.operator_analysis.has_pipeline,
            "has_sequential": result.operator_analysis.has_sequential,
            "has_conditional": result.operator_analysis.has_conditional,
            "has_background": result.operator_analysis.has_background,
        },
        "diagnostics": {
            "count": len(result.diagnostics.warnings),
            "warnings": [
                {
                    "severity": w.severity.name,
                    "code": w.code,
                    "message": w.message,
                }
                for w in result.diagnostics.warnings
            ],
        },
        "safe_alternative": safe_alternative,
    }
