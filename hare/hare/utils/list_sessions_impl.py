"""List sessions for Agent SDK — port of `listSessionsImpl.ts`."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from hare.utils.get_worktree_paths_portable import get_worktree_paths_portable
from hare.utils.session_storage_portable import (
    MAX_SANITIZED_LENGTH,
    LiteSessionFile,
    canonicalize_path,
    extract_first_prompt_from_head,
    extract_json_string_field,
    extract_last_json_string_field,
    find_project_dir,
    get_projects_dir,
    read_session_lite,
    sanitize_path,
    validate_uuid,
)

READ_BATCH_SIZE = 32


@dataclass
class SessionInfo:
    session_id: str
    summary: str
    last_modified: float
    file_size: int | None = None
    custom_title: str | None = None
    first_prompt: str | None = None
    git_branch: str | None = None
    cwd: str | None = None
    tag: str | None = None
    created_at: float | None = None


@dataclass
class ListSessionsOptions:
    dir: str | None = None
    limit: int | None = None
    offset: int = 0
    include_worktrees: bool = True


def parse_session_info_from_lite(
    session_id: str,
    lite: LiteSessionFile,
    project_path: str | None = None,
) -> SessionInfo | None:
    head, tail = lite.head, lite.tail
    first_nl = head.find("\n")
    first_line = head[:first_nl] if first_nl >= 0 else head
    if '"isSidechain":true' in first_line or '"isSidechain": true' in first_line:
        return None

    custom_title = (
        extract_last_json_string_field(tail, "customTitle")
        or extract_last_json_string_field(head, "customTitle")
        or extract_last_json_string_field(tail, "aiTitle")
        or extract_last_json_string_field(head, "aiTitle")
    )
    first_prompt = extract_first_prompt_from_head(head) or None
    first_ts = extract_json_string_field(head, "timestamp")
    created_at: float | None = None
    if first_ts:
        # ISO string → epoch ms
        try:
            from datetime import datetime

            created_at = (
                datetime.fromisoformat(first_ts.replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except ValueError:
            pass

    summary = (
        custom_title
        or extract_last_json_string_field(tail, "lastPrompt")
        or extract_last_json_string_field(tail, "summary")
        or first_prompt
    )
    if not summary:
        return None

    git_branch = extract_last_json_string_field(
        tail, "gitBranch"
    ) or extract_json_string_field(head, "gitBranch")
    session_cwd = extract_json_string_field(head, "cwd") or project_path

    tag_line = None
    for line in reversed(tail.split("\n")):
        if line.startswith('{"type":"tag"'):
            tag_line = line
            break
    tag = extract_last_json_string_field(tag_line, "tag") if tag_line else None

    return SessionInfo(
        session_id=session_id,
        summary=summary,
        last_modified=lite.mtime,
        file_size=lite.size,
        custom_title=custom_title,
        first_prompt=first_prompt,
        git_branch=git_branch,
        cwd=session_cwd,
        tag=tag,
        created_at=created_at,
    )


@dataclass
class _Candidate:
    session_id: str
    file_path: str
    mtime: float
    project_path: str | None = None


async def list_candidates(
    project_dir: str, do_stat: bool, project_path: str | None = None
) -> list[_Candidate]:
    try:
        names = os.listdir(project_dir)
    except OSError:
        return []
    out: list[_Candidate] = []

    async def one(name: str) -> _Candidate | None:
        if not name.endswith(".jsonl"):
            return None
        sid = validate_uuid(name[:-6])
        if not sid:
            return None
        fp = str(Path(project_dir) / name)
        if not do_stat:
            return _Candidate(
                session_id=sid, file_path=fp, mtime=0, project_path=project_path
            )
        try:
            st = Path(fp).stat()
            return _Candidate(
                session_id=sid,
                file_path=fp,
                mtime=st.st_mtime * 1000,
                project_path=project_path,
            )
        except OSError:
            return None

    results = await asyncio.gather(*[one(n) for n in names])
    for r in results:
        if r is not None:
            out.append(r)
    return out


async def _read_candidate(c: _Candidate) -> SessionInfo | None:
    lite = await asyncio.to_thread(read_session_lite, c.file_path)
    if not lite:
        return None
    info = parse_session_info_from_lite(c.session_id, lite, c.project_path)
    if not info:
        return None
    if c.mtime:
        info.last_modified = c.mtime
    return info


async def _apply_sort_and_limit(
    candidates: list[_Candidate],
    limit: int | None,
    offset: int,
) -> list[SessionInfo]:
    candidates.sort(key=lambda x: (-x.mtime, x.session_id))

    sessions: list[SessionInfo] = []
    want = limit if limit and limit > 0 else 2**63
    skipped = 0
    seen: set[str] = set()

    i = 0
    while i < len(candidates) and len(sessions) < want:
        batch_end = min(i + READ_BATCH_SIZE, len(candidates))
        batch = candidates[i:batch_end]
        results = await asyncio.gather(*[_read_candidate(c) for c in batch])
        for r in results:
            i += 1
            if not r:
                continue
            if r.session_id in seen:
                continue
            seen.add(r.session_id)
            if skipped < offset:
                skipped += 1
                continue
            sessions.append(r)
            if len(sessions) >= want:
                break
    return sessions


async def _read_all_and_sort(candidates: list[_Candidate]) -> list[SessionInfo]:
    all_results = await asyncio.gather(*[_read_candidate(c) for c in candidates])
    by_id: dict[str, SessionInfo] = {}
    for s in all_results:
        if not s:
            continue
        existing = by_id.get(s.session_id)
        if not existing or s.last_modified > existing.last_modified:
            by_id[s.session_id] = s
    sessions = list(by_id.values())
    sessions.sort(key=lambda x: (-x.last_modified, x.session_id))
    return sessions


async def _gather_project_candidates(
    dir_path: str,
    include_worktrees: bool,
    do_stat: bool,
) -> list[_Candidate]:
    canonical_dir = await canonicalize_path(dir_path)

    if include_worktrees:
        try:
            worktree_paths = await get_worktree_paths_portable(canonical_dir)
        except Exception:
            worktree_paths = []
    else:
        worktree_paths = []

    if len(worktree_paths) <= 1:
        pd = await find_project_dir(canonical_dir)
        if not pd:
            return []
        return await list_candidates(pd, do_stat, canonical_dir)

    projects_dir = Path(get_projects_dir())
    case_insensitive = os.name == "nt"

    indexed = sorted(
        [
            (wt, sanitize_path(wt).lower() if case_insensitive else sanitize_path(wt))
            for wt in worktree_paths
        ],
        key=lambda x: -len(x[1]),
    )

    all_c: list[_Candidate] = []
    seen_dirs: set[str] = set()

    canonical_pd = await find_project_dir(canonical_dir)
    if canonical_pd:
        base = (
            Path(canonical_pd).name.lower()
            if case_insensitive
            else Path(canonical_pd).name
        )
        seen_dirs.add(base)
        all_c.extend(await list_candidates(canonical_pd, do_stat, canonical_dir))

    try:
        all_dirents = list(projects_dir.iterdir())
    except OSError:
        pd = await find_project_dir(canonical_dir)
        if not pd:
            return []
        return await list_candidates(pd, do_stat, canonical_dir)

    for dirent in all_dirents:
        if not dirent.is_dir():
            continue
        dir_name = dirent.name.lower() if case_insensitive else dirent.name
        if dir_name in seen_dirs:
            continue
        for wt_path, prefix in indexed:
            is_match = dir_name == prefix or (
                len(prefix) >= MAX_SANITIZED_LENGTH
                and dir_name.startswith(prefix + "-")
            )
            if is_match:
                seen_dirs.add(dir_name)
                all_c.extend(await list_candidates(str(dirent), do_stat, wt_path))
                break

    return all_c


async def _gather_all_candidates(do_stat: bool) -> list[_Candidate]:
    projects_dir = get_projects_dir()
    try:
        dirents = [d for d in Path(projects_dir).iterdir() if d.is_dir()]
    except OSError:
        return []
    nested = await asyncio.gather(
        *[list_candidates(str(d), do_stat, None) for d in dirents]
    )
    out: list[_Candidate] = []
    for n in nested:
        out.extend(n)
    return out


async def list_sessions_impl(
    options: ListSessionsOptions | None = None,
) -> list[SessionInfo]:
    opt = options or ListSessionsOptions()
    off = opt.offset
    do_stat = (opt.limit is not None and opt.limit > 0) or off > 0

    if opt.dir:
        candidates = await _gather_project_candidates(
            opt.dir, opt.include_worktrees, do_stat
        )
    else:
        candidates = await _gather_all_candidates(do_stat)

    if not do_stat:
        return await _read_all_and_sort(candidates)
    return await _apply_sort_and_limit(candidates, opt.limit, off)
