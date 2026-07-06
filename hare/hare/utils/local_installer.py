"""Local npm installation under ~/.hare/local — port of `localInstaller.ts`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.log import log_error

PACKAGE_URL = "https://www.npmjs.com/package/@anthropic-ai/claude-code"


def get_local_install_dir() -> str:
    return str(Path(get_hare_config_home_dir()) / "local")


def get_local_hare_path() -> str:
    return str(Path(get_local_install_dir()) / "hare")


def is_running_from_local_installation() -> bool:
    a1 = sys.argv[1] if len(sys.argv) > 1 else ""
    return "/.hare/local/node_modules/" in a1


def _write_if_missing(path: Path, content: str, mode: int | None = None) -> bool:
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode or 0o644)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        if mode is not None:
            os.chmod(path, mode)
        return True
    except FileExistsError:
        return False


async def ensure_local_package_environment() -> bool:
    try:
        lid = Path(get_local_install_dir())
        lid.mkdir(parents=True, exist_ok=True)
        pkg = lid / "package.json"
        if not pkg.exists():
            pkg.write_text(
                json.dumps(
                    {"name": "hare-local", "version": "0.0.1", "private": True},
                    indent=2,
                ),
                encoding="utf-8",
            )
        wrapper = lid / "hare"
        created = _write_if_missing(
            wrapper,
            f'#!/bin/sh\nexec "{lid}/node_modules/.bin/hare" "$@"\n',
            0o755,
        )
        if created:
            os.chmod(wrapper, 0o755)
        return True
    except Exception as e:
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return False


async def install_or_update_hare_package(
    channel: str, specific_version: str | None = None
) -> str:
    try:
        if not await ensure_local_package_environment():
            return "install_failed"
        ver = specific_version or ("stable" if channel == "stable" else "latest")
        r = subprocess.run(
            ["npm", "install", f"{PACKAGE_URL}@{ver}"],
            cwd=get_local_install_dir(),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            log_error(RuntimeError(r.stderr or "npm install failed"))
            return "install_failed" if r.returncode != 190 else "in_progress"
        return "success"
    except Exception as e:
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return "install_failed"


async def local_installation_exists() -> bool:
    p = Path(get_local_install_dir()) / "node_modules" / ".bin" / "hare"
    return p.is_file()


def get_shell_type() -> str:
    shell_path = os.environ.get("SHELL", "")
    if "zsh" in shell_path:
        return "zsh"
    if "bash" in shell_path:
        return "bash"
    if "fish" in shell_path:
        return "fish"
    return "unknown"
