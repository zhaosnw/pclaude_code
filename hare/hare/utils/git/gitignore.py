"""Git ignore helpers via `git check-ignore`. Port of: src/utils/git/gitignore.ts"""

from __future__ import annotations

from pathlib import Path

from hare.utils.cwd import get_cwd
from hare.utils.exec_file_no_throw import exec_file_no_throw_with_cwd
from hare.utils.git_utils import dir_is_in_git_repo
from hare.utils.log import log_error


async def is_path_gitignored(file_path: str, cwd: str) -> bool:
    r = await exec_file_no_throw_with_cwd(
        "git",
        ["check-ignore", file_path],
        cwd=cwd,
        preserve_output_on_error=False,
    )
    return r.get("code") == 0


def get_global_gitignore_path() -> str:
    return str(Path.home() / ".config" / "git" / "ignore")


async def add_file_glob_rule_to_gitignore(
    filename: str, cwd: str | None = None
) -> None:
    cwd = cwd or get_cwd()
    try:
        if not await dir_is_in_git_repo(cwd):
            return
        gitignore_entry = f"**/{filename}"
        test_path = f"{filename}sample-file.txt" if filename.endswith("/") else filename
        if await is_path_gitignored(test_path, cwd):
            return
        global_path = Path(get_global_gitignore_path())
        global_path.parent.mkdir(parents=True, exist_ok=True)
        if global_path.is_file():
            content = global_path.read_text(encoding="utf-8")
            if gitignore_entry in content:
                return
            with global_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{gitignore_entry}\n")
        else:
            global_path.write_text(f"{gitignore_entry}\n", encoding="utf-8")
    except Exception as e:
        log_error(e)
