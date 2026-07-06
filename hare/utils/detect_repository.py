"""Parse Git remote into host/owner/name (`detectRepository.ts`)."""

from __future__ import annotations

from hare.utils.cwd import get_cwd
from hare.utils.debug import log_for_debugging
from hare.utils.git_utils import ParsedGitRemote, get_remote_url, parse_git_remote

ParsedRepository = ParsedGitRemote

_repository_with_host_cache: dict[str, ParsedGitRemote | None] = {}


def clear_repository_caches() -> None:
    _repository_with_host_cache.clear()


async def detect_current_repository() -> str | None:
    result = await detect_current_repository_with_host()
    if result is None:
        return None
    if result.host != "github.com":
        return None
    return f"{result.owner}/{result.name}"


async def detect_current_repository_with_host() -> ParsedGitRemote | None:
    cwd = get_cwd()
    if cwd in _repository_with_host_cache:
        return _repository_with_host_cache[cwd]

    try:
        remote_url = await get_remote_url()
        log_for_debugging(f"Git remote URL: {remote_url}")
        if not remote_url:
            log_for_debugging("No git remote URL found")
            _repository_with_host_cache[cwd] = None
            return None
        parsed = parse_git_remote(remote_url)
        log_for_debugging(
            f"Parsed repository: {parsed.host}/{parsed.owner}/{parsed.name} from URL: {remote_url}"
            if parsed
            else f"Parsed repository: null from URL: {remote_url}"
        )
        _repository_with_host_cache[cwd] = parsed
        return parsed
    except Exception as e:  # noqa: BLE001
        log_for_debugging(f"Error detecting repository: {e}")
        _repository_with_host_cache[cwd] = None
        return None


def get_cached_repository() -> str | None:
    parsed = _repository_with_host_cache.get(get_cwd())
    if not parsed or parsed.host != "github.com":
        return None
    return f"{parsed.owner}/{parsed.name}"


def parse_github_repository(input_str: str) -> str | None:
    trimmed = input_str.strip()
    parsed = parse_git_remote(trimmed)
    if parsed:
        if parsed.host != "github.com":
            return None
        return f"{parsed.owner}/{parsed.name}"
    if "://" not in trimmed and "@" not in trimmed and "/" in trimmed:
        parts = trimmed.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            repo = parts[1].removesuffix(".git")
            return f"{parts[0]}/{repo}"
    log_for_debugging(f"Could not parse repository from: {trimmed}")
    return None
