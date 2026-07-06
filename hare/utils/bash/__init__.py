"""
Bash/shell command parsing and analysis.

Port of: src/utils/bash/
"""

from hare.utils.bash.parser import parse_command, ParsedCommand
from hare.utils.bash.shell_quoting import shell_quote, shell_join
from hare.utils.bash.commands import split_command, get_command_name

# Re-export expanded shell quoting utilities from shell_quote.py
from hare.utils.bash.shell_quote import (
    # Core quoting
    shell_quote_auto,
    shell_quote_single,
    shell_quote_double,
    shell_quote_ansi_c,
    shell_quote_backslash,
    shell_escape,
    shell_quote_list,
    shell_quote,         # basic shlex.quote wrapper
    shell_quote_shlex,   # explicit shlex.quote alias
    shell_maybe_quote,   # quote only if needed
    # Command building
    build_command,
    join_commands,
    # Unquoting
    shell_unquote,
    shell_expand_escapes,
    # Detection / helpers
    needs_shell_quoting,
    is_safely_quoted,
    is_shell_safe,
    sanitize_shell_arg,
    count_quote_complexity,
    SHELL_METACHARS,
    # Heredoc
    heredoc,
    heredoc_quote_delimiter,
    # Path quoting
    shell_quote_path,
    shell_quote_args_for_subprocess,
    # Variable expansion
    shell_expand_vars,
    shell_word_split,
    shell_tokenize,
    # JSON quoting
    shell_quote_json,
    # Shell feature detection
    has_shell_operators,
    has_command_substitution,
    has_redirection,
    shell_feature_summary,
    # Parsing + quoting (original API)
    ParseEntry,
    try_parse_shell_command,
    try_quote_shell_args,
    has_malformed_tokens,
    has_shell_quote_single_quote_bug,
    quote,
    ShellParseSuccess,
    ShellParseFailure,
    ShellParseResult,
    ShellQuoteSuccess,
    ShellQuoteFailure,
    ShellQuoteResult,
)
