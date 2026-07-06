"""Early CLI flag parsing and `--` handling (mirrors `cliArgs.ts`)."""

from __future__ import annotations


def eager_parse_cli_flag(flag_name: str, argv: list[str] | None = None) -> str | None:
    """Parse `--flag value` or `--flag=value` before full argparse/commander."""
    if argv is None:
        import sys

        argv = list(sys.argv)
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith(f"{flag_name}="):
            return arg[len(flag_name) + 1 :]
        if arg == flag_name and i + 1 < len(argv):
            return argv[i + 1]
        i += 1
    return None


def extract_args_after_double_dash(
    command_or_value: str,
    args: list[str] | None = None,
) -> tuple[str, list[str]]:
    """If positional is `--`, treat `args[0]` as the real command."""
    if args is None:
        args = []
    if command_or_value == "--" and len(args) > 0:
        return args[0], args[1:]
    return command_or_value, args
