"""
Example slash-command suggestions from git history and project config.

Port of: src/utils/exampleCommands.ts
"""

from __future__ import annotations

import os
import random
import re
from functools import lru_cache
from typing import Any

from hare.utils.cwd import get_cwd
from hare.utils.env import env
from hare.utils.exec_file_no_throw import exec_file_no_throw_with_cwd
from hare.utils.log import log_error

NON_CORE_PATTERNS = [
    re.compile(
        r"(?:^|/)(?:package-lock\.json|yarn\.lock|bun\.lock|bun\.lockb|pnpm-lock\.yaml|Pipfile\.lock|poetry\.lock|Cargo\.lock|Gemfile\.lock|go\.sum|composer\.lock|uv\.lock)$"
    ),
    re.compile(r"\.generated\."),
    re.compile(r"(?:^|/)(?:dist|build|out|target|node_modules|\.next|__pycache__)/"),
    re.compile(r"\.(?:min\.js|min\.css|map|pyc|pyo)$"),
    re.compile(
        r"\.(?:json|ya?ml|toml|xml|ini|cfg|conf|env|lock|txt|md|mdx|rst|csv|log|svg)$",
        re.I,
    ),
    re.compile(
        r"(?:^|/)\.?(?:eslintrc|prettierrc|babelrc|editorconfig|gitignore|gitattributes|dockerignore|npmrc)"
    ),
    re.compile(
        r"(?:^|/)(?:tsconfig|jsconfig|biome|vitest\.config|jest\.config|webpack\.config|vite\.config|rollup\.config)\.[a-z]+$"
    ),
    re.compile(r"(?:^|/)\.(?:github|vscode|idea|hare)/"),
    re.compile(
        r"(?:^|/)(?:CHANGELOG|LICENSE|CONTRIBUTING|CODEOWNERS|README)(?:\.[a-z]+)?$",
        re.I,
    ),
]


def _is_core_file(path: str) -> bool:
    return not any(p.search(path) for p in NON_CORE_PATTERNS)


def count_and_sort_items(items: list[str], top_n: int = 20) -> str:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: -x[1])[:top_n]
    return "\n".join(f"{str(c).rjust(6)} {it}" for it, c in ranked)


def pick_diverse_core_files(sorted_paths: list[str], want: int) -> list[str]:
    picked: list[str] = []
    seen_bases: set[str] = set()
    dir_tally: dict[str, int] = {}
    for cap in range(1, want + 1):
        if len(picked) >= want:
            break
        for p in sorted_paths:
            if len(picked) >= want:
                break
            if not _is_core_file(p):
                continue
            last_sep = max(p.rfind("/"), p.rfind("\\"))
            base = p[last_sep + 1 :] if last_sep >= 0 else p
            if not base or base in seen_bases:
                continue
            dir_part = p[:last_sep] if last_sep >= 0 else "."
            if dir_tally.get(dir_part, 0) >= cap:
                continue
            picked.append(base)
            seen_bases.add(base)
            dir_tally[dir_part] = dir_tally.get(dir_part, 0) + 1
    return picked if len(picked) >= want else []


def _get_current_project_config() -> dict[str, Any]:
    try:
        from hare.utils.config import get_current_project_config

        return get_current_project_config()
    except ImportError:
        return {}


def _save_current_project_config(updater: Any) -> None:
    try:
        from hare.utils.config import save_current_project_config

        save_current_project_config(updater)
    except ImportError:
        pass


async def _get_git_email() -> str:
    try:
        from hare.utils.user import get_git_email

        return await get_git_email()
    except ImportError:
        r = await exec_file_no_throw_with_cwd(
            "git",
            ["config", "user.email"],
            cwd=get_cwd(),
            preserve_output_on_error=False,
        )
        return (r["stdout"] or "").strip() if r["code"] == 0 else ""


async def _get_is_git() -> bool:
    try:
        from hare.utils.git import is_git_repo

        return await is_git_repo()
    except ImportError:
        r = await exec_file_no_throw_with_cwd(
            "git",
            ["rev-parse", "--is-inside-work-tree"],
            cwd=get_cwd(),
            preserve_output_on_error=False,
        )
        return r["code"] == 0 and "true" in (r["stdout"] or "").lower()


async def get_frequently_modified_files() -> list[str]:
    if os.environ.get("NODE_ENV") == "test":
        return []
    if env.platform == "win32":
        return []
    if not await _get_is_git():
        return []
    try:
        user_email = await _get_git_email()
        log_args = [
            "log",
            "-n",
            "1000",
            "--pretty=format:",
            "--name-only",
            "--diff-filter=M",
        ]
        counts: dict[str, int] = {}

        def tally_into(stdout: str) -> None:
            for line in stdout.split("\n"):
                f = line.strip()
                if f:
                    counts[f] = counts.get(f, 0) + 1

        if user_email:
            r = await exec_file_no_throw_with_cwd(
                "git",
                [*log_args, f"--author={user_email}"],
                cwd=get_cwd(),
                preserve_output_on_error=False,
            )
            if r["code"] == 0:
                tally_into(r["stdout"] or "")
        if len(counts) < 10:
            r = await exec_file_no_throw_with_cwd(
                "git", log_args, cwd=get_cwd(), preserve_output_on_error=False
            )
            if r["code"] == 0:
                tally_into(r["stdout"] or "")
        sorted_paths = [p for p, _ in sorted(counts.items(), key=lambda x: -x[1])]
        return pick_diverse_core_files(sorted_paths, 5)
    except Exception as e:  # noqa: BLE001
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return []


ONE_WEEK_MS = 7 * 24 * 60 * 60 * 1000


@lru_cache(maxsize=1)
def get_example_command_from_cache() -> str:
    project_config = _get_current_project_config()
    ex = project_config.get("example_files") or []
    frequent_file = random.choice(ex) if ex else "<filepath>"
    commands = [
        "fix lint errors",
        "fix typecheck errors",
        f"how does {frequent_file} work?",
        f"refactor {frequent_file}",
        "how do I log an error?",
        f"edit {frequent_file} to...",
        f"write a test for {frequent_file}",
        "create a util logging.py that...",
    ]
    return f'Try "{random.choice(commands)}"'


async def refresh_example_commands() -> None:
    project_config = _get_current_project_config()
    now = __import__("time").time() * 1000
    last = project_config.get("example_files_generated_at") or 0
    if now - last > ONE_WEEK_MS:
        project_config["example_files"] = []
    ex = project_config.get("example_files") or []
    if not ex:

        async def _bg() -> None:
            files = await get_frequently_modified_files()
            if files:
                _save_current_project_config(
                    lambda c: {
                        **c,
                        "example_files": files,
                        "example_files_generated_at": __import__("time").time() * 1000,
                    }
                )

        import asyncio

        asyncio.create_task(_bg())
