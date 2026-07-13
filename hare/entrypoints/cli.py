"""
Bootstrap entrypoint – checks for special flags before loading the full CLI.

Port of: src/entrypoints/cli.tsx

All imports are dynamic to minimize module evaluation for fast paths.
Fast-path for --version has zero imports beyond this file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VERSION = "2.1.88"
BUILD_TIME = "recovered-from-sourcemap"


def _project_root() -> Path:
    # ``cli.py`` lives at ``<repo>/hare/entrypoints/cli.py``.
    return Path(__file__).resolve().parents[2]


def _frontend_root() -> Path:
    return _project_root() / "hare" / "frontend"


def _frontend_config_root() -> Path:
    return _project_root() / ".hare-frontend-home"


def _is_non_interactive(args: list[str]) -> bool:
    return (
        "-p" in args
        or "--print" in args
        or "--resume" in args
        or "--continue" in args
        or not sys.stdout.isatty()
    )


def _initialize_entrypoint(is_non_interactive: bool) -> None:
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli" if is_non_interactive else "cli"


def _run_ts_frontend(args: list[str]) -> int:
    frontend_root = _frontend_root()
    cli_entry = frontend_root / "src" / "entrypoints" / "cli.tsx"
    if not cli_entry.exists():
        raise FileNotFoundError(f"TS frontend entrypoint not found: {cli_entry}")

    bun_bin = os.path.expanduser("~/.bun/bin/bun")
    bun = bun_bin if os.path.exists(bun_bin) else "bun"

    env = dict(os.environ)
    # Preserve the user's real Hare config location by default so frontend
    # and Python backend both read ~/.hare/settings.json unless the caller
    # explicitly overrides it. The old temporary .hare-frontend-home shim
    # caused model/settings drift between the JS and Python paths.
    explicit_config_root = env.get("HARE_CONFIG_DIR") or env.get("CLAUDE_CONFIG_DIR")
    if explicit_config_root:
        env.setdefault("HARE_CONFIG_DIR", explicit_config_root)
        env.setdefault("CLAUDE_CONFIG_DIR", explicit_config_root)
    env.setdefault("HARE_PYTHON_BACKEND", "1")
    # Strip auth vars inherited from the shell so that ~/.hare/settings.json
    # is the authoritative source of credentials. Without this, a stale
    # ANTHROPIC_AUTH_TOKEN or ANTHROPIC_BASE_URL in the parent shell silently
    # overrides settings.json and causes 401 errors.
    for _auth_var in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(_auth_var, None)
    # Build the backend command so it works regardless of the caller's cwd.
    # Using `python -m hare.backend` can fail if cwd contains an outer `hare/`
    # directory that shadows the installed package as a namespace package.
    # Inserting the package root into sys.path explicitly avoids the collision.
    pkg_root = str(_project_root() / "hare")
    backend_cmd = (
        f"{sys.executable} -c "
        f'"import sys; sys.path.insert(0, {repr(pkg_root)}); '
        f'from hare.backend import main; main()"'
    )
    env.setdefault("HARE_PYTHON_BACKEND_CMD", backend_cmd)

    # Run the TS frontend from the caller's working directory so the REPL sees
    # the user's project root instead of the bundled frontend directory.
    proc = subprocess.run(
        [bun, "run", str(cli_entry), *args],
        cwd=os.getcwd(),
        env=env,
    )
    return int(proc.returncode)


def main() -> None:
    """
    Bootstrap entrypoint. Checks for special flags before loading the full CLI.

    Mirrors the async function main() in src/entrypoints/cli.tsx.
    """
    args = sys.argv[1:]

    # Fast-path for --version/-v: zero module loading needed
    if len(args) == 1 and args[0] in ("--version", "-v", "-V"):
        print(f"{VERSION} (Hare)")
        return

    # Set COREPACK_ENABLE_AUTO_PIN=0 (bugfix for corepack auto-pinning)
    os.environ["COREPACK_ENABLE_AUTO_PIN"] = "0"

    # Set max heap size for child processes in CCR environments
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true":
        existing = os.environ.get("NODE_OPTIONS", "")
        os.environ["NODE_OPTIONS"] = (
            f"{existing} --max-old-space-size=8192"
            if existing
            else "--max-old-space-size=8192"
        )

    # --bare: set SIMPLE early so gates fire during module eval
    if "--bare" in args:
        os.environ["CLAUDE_CODE_SIMPLE"] = "1"

    # Redirect common update flag mistakes to the update subcommand
    if len(args) == 1 and args[0] in ("--update", "--upgrade"):
        args = ["update"]

    is_non_interactive = _is_non_interactive(args)
    _initialize_entrypoint(is_non_interactive)

    # Interactive default path delegates to the TS/Ink frontend.
    if not is_non_interactive and "--python-repl" not in args:
        try:
            exit_code = _run_ts_frontend(args)
            raise SystemExit(exit_code)
        except (FileNotFoundError, Exception):
            # Fallback to Python REPL if TS frontend is unavailable
            pass

    # No special flags detected, load and run the Python CLI
    import asyncio
    from hare.main import cli_main

    filtered_args = [arg for arg in args if arg != "--python-repl"]
    asyncio.run(cli_main(filtered_args))


if __name__ == "__main__":
    main()
