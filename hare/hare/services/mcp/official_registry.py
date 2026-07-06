"""
Official Anthropic MCP registry — remote listing, local cache, validation.

Port of: src/services/mcp/officialRegistry.ts

Provides:
- Fetching the official MCP server registry from api.anthropic.com
- Disk-cached registry data with TTL (avoids repeated API calls)
- URL-based membership checks against the official set
- Structured server lookup by name or URL
- Validation of user-provided server configs against official entries
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

from hare.utils.cache_paths import CACHE_PATHS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGISTRY_API_URL = (
    "https://api.anthropic.com/mcp-registry/v0/"
    "servers?version=latest&visibility=commercial"
)
REGISTRY_CACHE_FILENAME = "mcp_official_registry.json"
REGISTRY_CACHE_TTL_SECONDS = 3_600  # 1 hour
REGISTRY_PREFETCH_TIMEOUT = 5  # seconds
REGISTRY_CACHE_MAX_AGE_SECONDS = 86_400  # 24 hours — fallback read if stale

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OfficialRegistryRemote:
    """A single remote endpoint within an official server entry."""

    url: str
    transport: str = "http"  # "stdio", "sse", "http", "ws"


@dataclass
class OfficialRegistryServer:
    """A single server entry from the official MCP registry."""

    name: str
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    remotes: list[OfficialRegistryRemote] = field(default_factory=list)
    website: str = ""
    docs_url: str = ""
    logo_url: str = ""
    visibility: str = "commercial"
    is_official: bool = True

    @property
    def urls(self) -> list[str]:
        """Normalized URLs for all remotes."""
        result: list[str] = []
        for r in self.remotes:
            n = _normalize_url(r.url)
            if n:
                result.append(n)
        return result


@dataclass
class OfficialRegistry:
    """The full parsed official MCP registry."""

    servers: list[OfficialRegistryServer] = field(default_factory=list)
    version: str = ""
    fetched_at: float = 0.0

    @property
    def url_set(self) -> set[str]:
        """Flat set of all normalized official server URLs."""
        s: set[str] = set()
        for server in self.servers:
            for url in server.urls:
                s.add(url)
        return s

    @property
    def age_seconds(self) -> float:
        """Seconds since this registry was fetched."""
        if not self.fetched_at:
            return float("inf")
        return time.time() - self.fetched_at

    @property
    def is_stale(self) -> bool:
        """True if the cached registry has exceeded its TTL."""
        return self.age_seconds > REGISTRY_CACHE_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        """True if the cache is so old it shouldn't be used even as fallback."""
        return self.age_seconds > REGISTRY_CACHE_MAX_AGE_SECONDS

    @property
    def server_count(self) -> int:
        return len(self.servers)


# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------

_official_urls: Optional[set[str]] = None
_registry: Optional[OfficialRegistry] = None
_cache_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _normalize_url(url: str) -> str | None:
    """Normalize a server URL: strip query/fragment, trailing slash."""
    try:
        u = urlparse(url)
        stripped = urlunparse((u.scheme, u.netloc, u.path, "", "", ""))
        return stripped.rstrip("/")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Disk cache read / write
# ---------------------------------------------------------------------------


def _cache_file_path() -> Path:
    """Path to the on-disk registry cache file."""
    base = CACHE_PATHS.base_logs()
    return Path(base) / REGISTRY_CACHE_FILENAME


def _read_registry_from_disk() -> Optional[OfficialRegistry]:
    """Read the cached registry from disk, returning None on any failure."""
    cache_path = _cache_file_path()
    if not cache_path.exists():
        return None
    try:
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to read MCP registry cache: %s", exc)
        return None

    servers_raw = data.get("servers", [])
    servers: list[OfficialRegistryServer] = []
    for entry in servers_raw:
        remotes_raw = entry.get("remotes") or []
        remotes = [
            OfficialRegistryRemote(
                url=r.get("url", ""),
                transport=r.get("transport", "http"),
            )
            for r in remotes_raw
            if isinstance(r, dict) and r.get("url")
        ]
        servers.append(
            OfficialRegistryServer(
                name=entry.get("name", ""),
                description=entry.get("description", ""),
                category=entry.get("category", ""),
                tags=entry.get("tags", []),
                remotes=remotes,
                website=entry.get("website", ""),
                docs_url=entry.get("docs_url", ""),
                logo_url=entry.get("logo_url", ""),
                visibility=entry.get("visibility", "commercial"),
            )
        )

    return OfficialRegistry(
        servers=servers,
        version=data.get("version", ""),
        fetched_at=data.get("fetched_at", 0.0),
    )


def _write_registry_to_disk(registry: OfficialRegistry) -> None:
    """Persist the registry to the local disk cache."""
    cache_path = _cache_file_path()
    data: dict = {
        "version": registry.version,
        "fetched_at": registry.fetched_at,
        "servers": [],
    }
    for server in registry.servers:
        entry: dict = {
            "name": server.name,
            "description": server.description,
            "category": server.category,
            "tags": server.tags,
            "visibility": server.visibility,
            "website": server.website,
            "docs_url": server.docs_url,
            "logo_url": server.logo_url,
            "remotes": [
                {"url": r.url, "transport": r.transport} for r in server.remotes
            ],
        }
        data["servers"].append(entry)

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("MCP registry cached to %s (%d servers)", cache_path, len(registry.servers))
    except OSError as exc:
        logger.warning("Failed to write MCP registry cache: %s", exc)


# ---------------------------------------------------------------------------
# Remote fetch
# ---------------------------------------------------------------------------


def _fetch_registry_sync() -> set[str]:
    """Synchronous HTTP fetch: parse raw API response → flat set of normalized URLs.

    Used in asyncio.to_thread so the event loop is never blocked.
    """
    urls: set[str] = set()
    req = urllib.request.Request(REGISTRY_API_URL)
    with urllib.request.urlopen(req, timeout=REGISTRY_PREFETCH_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    for entry in data.get("servers", []):
        server = entry.get("server") or {}
        for remote in server.get("remotes") or []:
            u = remote.get("url")
            if isinstance(u, str):
                n = _normalize_url(u)
                if n:
                    urls.add(n)
    return urls


def _fetch_full_registry_sync() -> OfficialRegistry:
    """Synchronous HTTP fetch returning the full parsed OfficialRegistry.

    Designed for asyncio.to_thread to avoid blocking the event loop.
    """
    servers: list[OfficialRegistryServer] = []
    version = ""

    req = urllib.request.Request(REGISTRY_API_URL)
    with urllib.request.urlopen(req, timeout=REGISTRY_PREFETCH_TIMEOUT) as resp:
        data = json.loads(resp.read().decode())

    version = str(data.get("version", ""))
    for entry in data.get("servers", []):
        remotes_raw = entry.get("remotes") or []
        remotes = [
            OfficialRegistryRemote(
                url=r.get("url", ""),
                transport=r.get("transport", "http"),
            )
            for r in remotes_raw
            if isinstance(r, dict) and r.get("url")
        ]
        servers.append(
            OfficialRegistryServer(
                name=entry.get("name", ""),
                description=entry.get("description", ""),
                category=entry.get("category", ""),
                tags=entry.get("tags", []),
                remotes=remotes,
                website=entry.get("website", ""),
                docs_url=entry.get("docs_url", ""),
                logo_url=entry.get("logo_url", ""),
                visibility=entry.get("visibility", "commercial"),
            )
        )

    return OfficialRegistry(servers=servers, version=version, fetched_at=time.time())


# ---------------------------------------------------------------------------
# Public: prefetch / load
# ---------------------------------------------------------------------------


async def prefetch_official_mcp_urls() -> None:
    """Prefetch the official MCP server URL set into module-level cache.

    Respects CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC.  Falls back to
    the on-disk cache when the remote fetch fails (network, parse, etc.).
    Called early in the CLI startup sequence.
    """
    global _official_urls
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        logger.debug("Non-essential traffic disabled — skipping MCP registry prefetch")
        return

    try:
        _official_urls = await asyncio.to_thread(_fetch_registry_sync)
        logger.debug("Fetched %d official MCP server URLs", len(_official_urls))
    except Exception as exc:
        logger.debug("Failed to prefetch MCP registry: %s", exc)
        # Attempt fallback: load the URL set from the on-disk full registry cache
        disk = _read_registry_from_disk()
        if disk is not None:
            _official_urls = disk.url_set
            logger.debug(
                "Fallback: loaded %d official MCP URLs from disk cache (age %.0fs)",
                len(_official_urls),
                disk.age_seconds,
            )


async def load_official_registry(
    *, force_refresh: bool = False
) -> Optional[OfficialRegistry]:
    """Load the full official MCP registry (structured, with metadata).

    Priority:
    1. In-memory cache if fresh (or unless force_refresh)
    2. On-disk cache if within MAX_AGE
    3. Remote fetch (blocks on network)

    Returns None when networking is disabled or all sources fail.
    """
    global _registry
    async with _cache_lock:
        # 1. In-memory hit
        if _registry is not None and not _registry.is_stale and not force_refresh:
            return _registry

        # 2. Disk cache (even if stale — better than nothing when offline)
        if not force_refresh:
            disk = _read_registry_from_disk()
            if disk is not None and not disk.is_expired:
                _registry = disk
                return _registry

        # 3. Remote fetch
        if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
            logger.debug("Non-essential traffic disabled — cannot fetch registry")
            return _registry  # return whatever we have, even if stale

        try:
            fresh = await asyncio.to_thread(_fetch_full_registry_sync)
            _registry = fresh
            _write_registry_to_disk(fresh)
            # Also update the flat URL set for is_official_mcp_url checks
            global _official_urls
            _official_urls = fresh.url_set
            logger.info("Loaded official MCP registry: %d servers (v%s)", fresh.server_count, fresh.version)
            return fresh
        except Exception as exc:
            logger.warning("Failed to fetch MCP registry from API: %s", exc)
            # Fallback: disk cache (even if expired)
            disk = _read_registry_from_disk()
            if disk is not None:
                _registry = disk
                return _registry
            return _registry


# ---------------------------------------------------------------------------
# Public: membership checks
# ---------------------------------------------------------------------------


def is_official_mcp_url(normalized_url: str) -> bool:
    """Check whether a normalized URL belongs to the official MCP registry.

    Returns False when the registry has not been prefetched.
    """
    if _official_urls is None:
        return False
    return normalized_url in _official_urls


def is_server_official(name_or_url: str) -> bool:
    """Check if a server name or URL matches an entry in the official registry.

    Returns True if either the name or any normalized URL variant matches.
    """
    reg = _registry
    if reg is None:
        # Lazy check against the URL set only
        if _official_urls is not None:
            norm = _normalize_url(name_or_url)
            if norm and norm in _official_urls:
                return True
        return False

    for server in reg.servers:
        if server.name.lower() == name_or_url.lower():
            return True
        for remote in server.remotes:
            norm = _normalize_url(remote.url)
            if norm and norm == _normalize_url(name_or_url):
                return True
    return False


# ---------------------------------------------------------------------------
# Public: structured lookup
# ---------------------------------------------------------------------------


def lookup_official_server_by_url(url: str) -> Optional[OfficialRegistryServer]:
    """Find the official server entry matching a given URL.

    Accepts both raw and normalized URLs.  Matches against all remotes.
    """
    reg = _registry
    if reg is None:
        return None

    target = _normalize_url(url)
    if not target:
        return None

    for server in reg.servers:
        for remote in server.remotes:
            if _normalize_url(remote.url) == target:
                return server
    return None


def lookup_official_server_by_name(name: str) -> Optional[OfficialRegistryServer]:
    """Find the official server entry by display name (case-insensitive)."""
    reg = _registry
    if reg is None:
        return None

    lower = name.lower()
    for server in reg.servers:
        if server.name.lower() == lower:
            return server
    return None


def list_official_servers(
    *, category: Optional[str] = None, tag: Optional[str] = None
) -> list[OfficialRegistryServer]:
    """List official servers, optionally filtered by category or tag."""
    reg = _registry
    if reg is None:
        return []

    result = list(reg.servers)
    if category:
        result = [s for s in result if s.category.lower() == category.lower()]
    if tag:
        result = [s for s in result if tag.lower() in (t.lower() for t in s.tags)]
    return result


def list_official_categories() -> list[str]:
    """Return all unique categories from the official registry (sorted)."""
    reg = _registry
    if reg is None:
        return []
    return sorted({s.category for s in reg.servers if s.category})


def list_official_tags() -> list[str]:
    """Return all unique tags from the official registry (sorted)."""
    reg = _registry
    if reg is None:
        return []
    seen: set[str] = set()
    for s in reg.servers:
        seen.update(t.lower() for t in s.tags)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Public: validation
# ---------------------------------------------------------------------------


def validate_against_official_registry(
    server_name: str,
    config_data: dict,
) -> tuple[bool, Optional[str]]:
    """Validate a user-supplied server config against the official registry.

    Checks:
    - Is the server name known to the official registry?
    - If known, does at least one URL in the config match an official remote?

    Returns (is_valid, reason).
    - (True, None)         — config matches an official entry
    - (False, reason_str)  — config does not match; reason explains why
    """
    official = lookup_official_server_by_name(server_name)
    if official is None:
        return False, f"Server '{server_name}' not found in official MCP registry"

    # If the config has a URL, verify it matches an official remote
    config_url = config_data.get("url", "")
    if config_url:
        cfg_norm = _normalize_url(config_url)
        if cfg_norm:
            for remote in official.remotes:
                if _normalize_url(remote.url) == cfg_norm:
                    return True, None
            return (
                False,
                f"URL '{config_url}' does not match any official remote for server '{server_name}'",
            )

    # Stdio servers or configs without a URL — considered valid if the name matches
    return True, None


def validate_multiple_against_official_registry(
    configs: dict[str, dict],
) -> dict[str, tuple[bool, Optional[str]]]:
    """Batch-validate multiple server configs against the official registry.

    Returns {server_name: (is_valid, reason)} for each config.
    """
    results: dict[str, tuple[bool, Optional[str]]] = {}
    for name, config_data in configs.items():
        results[name] = validate_against_official_registry(name, config_data)
    return results


# ---------------------------------------------------------------------------
# Public: registry metadata
# ---------------------------------------------------------------------------


def get_registry_meta() -> dict:
    """Return metadata about the currently loaded registry.

    Returns empty dict when no registry is loaded.
    """
    reg = _registry
    if reg is None:
        return {}
    return {
        "version": reg.version,
        "server_count": reg.server_count,
        "fetched_at": reg.fetched_at,
        "age_seconds": reg.age_seconds,
        "is_stale": reg.is_stale,
        "categories": list_official_categories(),
        "tags": list_official_tags(),
    }


def get_registry_fetched_at_iso() -> Optional[str]:
    """ISO-8601 timestamp of when the registry was last fetched, or None."""
    reg = _registry
    if reg is None or reg.fetched_at == 0.0:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(reg.fetched_at, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public: testing
# ---------------------------------------------------------------------------


def reset_official_mcp_urls_for_testing() -> None:
    """Reset module-level caches for test isolation."""
    global _official_urls, _registry
    _official_urls = None
    _registry = None
