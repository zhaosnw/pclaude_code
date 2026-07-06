"""
Download Hare native binaries from Artifactory (npm) or GCS.

Port of: src/utils/nativeInstaller/download.ts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Literal

from hare.constants.product import NATIVE_PACKAGE_URL
from hare.utils.debug import log_error, log_for_debugging
from hare.utils.platform import get_platform

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GCS_BUCKET_URL = (
    "https://storage.googleapis.com/hare-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
    "hare-code-releases"
)
ARTIFACTORY_REGISTRY_URL = "https://artifactory.infra.ant.dev/artifactory/api/npm/npm-all/"
CI_SENTINEL_BUCKET_URL = "https://storage.googleapis.com/claude-code-ci-sentinel"

DEFAULT_STALL_TIMEOUT_MS = 60_000
MAX_DOWNLOAD_RETRIES = 3
NPM_VIEW_TIMEOUT_MS = 30_000
NPM_CI_TIMEOUT_MS = 60_000
MANIFEST_FETCH_TIMEOUT_MS = 10_000
VERSION_FETCH_TIMEOUT_MS = 30_000
TOTAL_DOWNLOAD_TIMEOUT_MS = 5 * 60_000

_SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+(-\S+)?$")
_TEST_VERSION_RE = re.compile(r"^99\.99\.")

ReleaseChannel = Literal["stable", "latest"]
DownloadSource = Literal["npm", "binary"]

STALL_TIMEOUT_MS = DEFAULT_STALL_TIMEOUT_MS  # exported constant (mirrors TS)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StallTimeoutError(Exception):
    """Download stalled: no data received within the stall timeout window."""
    def __init__(self, timeout_ms: int | None = None) -> None:
        ms = timeout_ms or DEFAULT_STALL_TIMEOUT_MS
        super().__init__(f"Download stalled: no data received for {ms // 1000} seconds")


class VersionResolutionError(Exception):
    """Failed to resolve version from a remote source."""


class ChecksumMismatchError(Exception):
    """Downloaded binary checksum does not match expected value."""


class PlatformNotInManifestError(Exception):
    """Requested platform was not found in the release manifest."""


class NpmViewError(Exception):
    """npm view command failed."""


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _get_arch() -> str:
    machine = os.uname().machine if hasattr(os, "uname") else "x86_64"
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def _get_platform_segment() -> str:
    """GCS path segment: 'macos-arm64', 'linux-x64', 'windows-x64'."""
    plat = get_platform()
    arch = _get_arch()
    return f"{plat}-{arch}" if plat != "windows" else "windows-x64"


def _get_binary_name() -> str:
    return "hare.exe" if sys.platform == "win32" else "hare"


def _is_ant_user() -> bool:
    return os.environ.get("USER_TYPE") == "ant"


def _check_test_version_allowed(version: str) -> bool:
    return _TEST_VERSION_RE.match(version) and os.environ.get("ALLOW_TEST_VERSIONS") == "1"


def _get_stall_timeout_ms() -> int:
    try:
        val = os.environ.get("CLAUDE_CODE_STALL_TIMEOUT_MS_FOR_TESTING")
        return int(val) if val is not None else DEFAULT_STALL_TIMEOUT_MS
    except (ValueError, TypeError):
        return DEFAULT_STALL_TIMEOUT_MS


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


async def _exec_npm_view(args: list[str], timeout_ms: int = NPM_VIEW_TIMEOUT_MS) -> str:
    """Run ``npm view <args>``, return trimmed stdout, or raise NpmViewError."""
    proc = await asyncio.create_subprocess_exec(
        "npm", "view", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        raise NpmViewError(f"npm view timed out after {timeout_ms}ms")
    except Exception as exc:
        proc.kill(); await proc.wait()
        raise NpmViewError(f"npm view error: {exc}") from exc

    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace")
        raise NpmViewError(f"npm view failed with code {proc.returncode}: {err}")
    return (stdout or b"").decode(errors="replace").strip()


# ---------------------------------------------------------------------------
# Version resolution
# ---------------------------------------------------------------------------


async def get_latest_version_from_artifactory(tag: str = "latest") -> str:
    """Resolve npm dist-tag to semver via the Artifactory registry."""
    start = time.monotonic()
    try:
        version = await _exec_npm_view([
            f"{NATIVE_PACKAGE_URL}@{tag}", "version", "--prefer-online",
            "--registry", ARTIFACTORY_REGISTRY_URL,
        ])
        log_for_debugging(
            f"tengu_version_check_success latency_ms={int((time.monotonic()-start)*1000)} source_npm=true"
        )
        return version
    except NpmViewError as exc:
        log_for_debugging(
            f"tengu_version_check_failure latency_ms={int((time.monotonic()-start)*1000)} source_npm=true"
        )
        log_error(exc)
        raise VersionResolutionError(str(exc)) from exc


async def get_latest_version_from_binary_repo(
    channel: ReleaseChannel = "latest",
    base_url: str = GCS_BUCKET_URL,
    auth_config: dict[str, Any] | None = None,
) -> str:
    """GET {base_url}/{channel} to read the latest version string."""
    import aiohttp

    start = time.monotonic()
    url = f"{base_url.rstrip('/')}/{channel}"
    headers: dict[str, str] = {}
    session_auth: aiohttp.BasicAuth | None = None

    if auth_config:
        headers.update(auth_config.get("headers", {}))
        auth = auth_config.get("auth", {})
        if isinstance(auth, dict) and "username" in auth:
            session_auth = aiohttp.BasicAuth(auth["username"], auth.get("password", ""))

    try:
        timeout = aiohttp.ClientTimeout(total=VERSION_FETCH_TIMEOUT_MS / 1000.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, auth=session_auth) as resp:
                if resp.status != 200:
                    raise VersionResolutionError(f"HTTP {resp.status} from {url}")
                text = (await resp.text()).strip()
                log_for_debugging(
                    f"tengu_version_check_success latency_ms={int((time.monotonic()-start)*1000)}"
                )
                return text
    except aiohttp.ClientError as exc:
        log_for_debugging(
            f"tengu_version_check_failure latency_ms={int((time.monotonic()-start)*1000)} "
            f"is_timeout={'timeout' in str(exc).lower()}"
        )
        raise VersionResolutionError(f"Failed to fetch version from {url}: {exc}") from exc


async def get_latest_version(channel_or_version: str) -> str:
    """Resolve version or channel ('stable'/'latest') to concrete semver.

    Direct versions (e.g. '1.0.30', 'v2.1.0-beta.1') are normalised.
    99.99.x is blocked unless ALLOW_TEST_VERSIONS=1.
    Channels route to Artifactory (ant users) or GCS.
    """
    if _SEMVER_RE.match(channel_or_version):
        v = channel_or_version[1:] if channel_or_version.startswith("v") else channel_or_version
        if _TEST_VERSION_RE.match(v) and not _check_test_version_allowed(v):
            raise VersionResolutionError(
                f"Version {v} is not available for installation. Use 'stable' or 'latest'."
            )
        return v

    if channel_or_version not in ("stable", "latest"):
        raise VersionResolutionError(f"Invalid channel: {channel_or_version}")

    if _is_ant_user():
        return await get_latest_version_from_artifactory(
            "stable" if channel_or_version == "stable" else "latest"
        )
    return await get_latest_version_from_binary_repo(
        channel_or_version,  # type: ignore[arg-type]
        GCS_BUCKET_URL,
    )


# ---------------------------------------------------------------------------
# Artifactory (npm-based) download
# ---------------------------------------------------------------------------


async def download_version_from_artifactory(version: str, staging_path: str) -> None:
    """Download platform-specific binary via npm from Artifactory.

    Creates isolated npm project with pre-computed integrity hash in
    package-lock.json, then ``npm ci`` to install and verify.
    """
    platform = _get_platform_segment()
    platform_pkg = f"{NATIVE_PACKAGE_URL}-{platform}"
    staging = Path(staging_path)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    integrity = await _exec_npm_view([
        f"{platform_pkg}@{version}", "dist.integrity",
        "--registry", ARTIFACTORY_REGISTRY_URL,
    ])
    if not integrity:
        raise NpmViewError(f"Empty integrity hash for {platform_pkg}@{version}")
    log_for_debugging(f"Got integrity hash for {platform}: {integrity}")

    staging.mkdir(parents=True, exist_ok=True)
    (staging / "package.json").write_text(json.dumps({
        "name": "hare-native-installer", "version": "0.0.1",
        "dependencies": {NATIVE_PACKAGE_URL: version},
    }, indent=2), encoding="utf-8")
    (staging / "package-lock.json").write_text(json.dumps({
        "name": "hare-native-installer", "version": "0.0.1",
        "lockfileVersion": 3, "requires": True,
        "packages": {
            "": {
                "name": "hare-native-installer", "version": "0.0.1",
                "dependencies": {NATIVE_PACKAGE_URL: version},
            },
            f"node_modules/{NATIVE_PACKAGE_URL}": {
                "version": version,
                "optionalDependencies": {platform_pkg: version},
            },
            f"node_modules/{platform_pkg}": {
                "version": version, "integrity": integrity,
            },
        },
    }, indent=2), encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        "npm", "ci", "--prefer-online", "--registry", ARTIFACTORY_REGISTRY_URL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(staging),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), NPM_CI_TIMEOUT_MS / 1000.0)
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        raise NpmViewError(f"npm ci timed out after {NPM_CI_TIMEOUT_MS}ms")
    except Exception as exc:
        proc.kill(); await proc.wait()
        raise NpmViewError(f"npm ci error: {exc}") from exc

    if proc.returncode != 0:
        raise NpmViewError(
            f"npm ci failed with code {proc.returncode}: {(stderr or b'').decode(errors='replace')}"
        )
    log_for_debugging(f"Artifactory download complete: {NATIVE_PACKAGE_URL}@{version}")


# ---------------------------------------------------------------------------
# Binary repo (GCS) download with stall detection and retries
# ---------------------------------------------------------------------------


async def _download_and_verify_binary(
    binary_url: str,
    expected_checksum: str,
    binary_path: str,
    request_config: dict[str, Any] | None = None,
) -> None:
    """Download binary, verify SHA-256 checksum, chmod 755. Retries on stalls."""
    import aiohttp

    cfg = request_config or {}
    stall_ms = _get_stall_timeout_ms()
    headers: dict[str, str] = dict(cfg.get("headers", {}))
    auth = cfg.get("auth")
    auth_obj = (
        aiohttp.BasicAuth(auth["username"], auth.get("password", ""))
        if isinstance(auth, dict) and "username" in auth else None
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        try:
            timeout = aiohttp.ClientTimeout(
                total=TOTAL_DOWNLOAD_TIMEOUT_MS / 1000.0,
                sock_read=stall_ms / 1000.0,
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(binary_url, headers=headers, auth=auth_obj) as resp:
                    if resp.status != 200:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history,
                            status=resp.status, message=f"HTTP {resp.status}",
                            headers=resp.headers,
                        )
                    data = await resp.read()
        except asyncio.TimeoutError:
            last_error = StallTimeoutError(stall_ms)
            if attempt < MAX_DOWNLOAD_RETRIES:
                log_for_debugging(f"Download stalled attempt {attempt}/{MAX_DOWNLOAD_RETRIES}, retrying...")
                await asyncio.sleep(1)
                continue
            raise last_error
        except aiohttp.ClientError as exc:
            msg = str(exc)
            if "timeout" in msg.lower():
                last_error = StallTimeoutError(stall_ms)
                if attempt < MAX_DOWNLOAD_RETRIES:
                    log_for_debugging(f"Download stalled attempt {attempt}/{MAX_DOWNLOAD_RETRIES}, retrying...")
                    await asyncio.sleep(1)
                    continue
                raise last_error from exc
            raise

        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_checksum:
            raise ChecksumMismatchError(f"Expected {expected_checksum}, got {actual}")

        out = Path(binary_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        out.chmod(0o755)
        return

    raise last_error if last_error else RuntimeError(f"Download failed after {MAX_DOWNLOAD_RETRIES} retries")


async def download_version_from_binary_repo(
    version: str,
    staging_path: str,
    base_url: str = GCS_BUCKET_URL,
    auth_config: dict[str, Any] | None = None,
) -> None:
    """Download binary from GCS-style bucket via manifest + checksum verification."""
    import aiohttp

    staging = Path(staging_path)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    platform = _get_platform_segment()
    start = time.monotonic()

    manifest_url = f"{base_url.rstrip('/')}/{version}/manifest.json"
    headers: dict[str, str] = {}
    session_auth: aiohttp.BasicAuth | None = None
    if auth_config:
        headers.update(auth_config.get("headers", {}))
        auth = auth_config.get("auth", {})
        if isinstance(auth, dict) and "username" in auth:
            session_auth = aiohttp.BasicAuth(auth["username"], auth.get("password", ""))

    # Fetch manifest
    try:
        timeout = aiohttp.ClientTimeout(total=MANIFEST_FETCH_TIMEOUT_MS / 1000.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(manifest_url, headers=headers, auth=session_auth) as resp:
                if resp.status != 200:
                    log_for_debugging(
                        f"tengu_binary_manifest_fetch_failure "
                        f"latency_ms={int((time.monotonic()-start)*1000)} http_status={resp.status}"
                    )
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history,
                        status=resp.status, message=f"HTTP {resp.status}",
                        headers=resp.headers,
                    )
                manifest = await resp.json()
    except aiohttp.ClientError as exc:
        log_for_debugging(
            f"tengu_binary_manifest_fetch_failure "
            f"latency_ms={int((time.monotonic()-start)*1000)} "
            f"is_timeout={'timeout' in str(exc).lower()}"
        )
        raise VersionResolutionError(f"Failed to fetch manifest from {manifest_url}: {exc}") from exc

    platform_info = manifest.get("platforms", {}).get(platform)
    if not platform_info:
        raise PlatformNotInManifestError(f"Platform {platform} not in manifest v{version}")

    binary_name = _get_binary_name()
    binary_url = f"{base_url.rstrip('/')}/{version}/{platform}/{binary_name}"
    staging.mkdir(parents=True, exist_ok=True)
    binary_path = str(staging / binary_name)

    try:
        await _download_and_verify_binary(binary_url, platform_info["checksum"], binary_path, auth_config or {})
        log_for_debugging(f"tengu_binary_download_success latency_ms={int((time.monotonic()-start)*1000)}")
    except Exception as exc:
        msg = str(exc)
        log_for_debugging(
            f"tengu_binary_download_failure latency_ms={int((time.monotonic()-start)*1000)} "
            f"is_timeout={'timeout' in msg.lower() or 'stall' in msg.lower()} "
            f"is_checksum_mismatch={'Checksum mismatch' in msg}"
        )
        log_error(RuntimeError(f"Binary download failed from {binary_url}: {msg}"))
        raise


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


async def download_version(version: str, staging_path: str) -> DownloadSource:
    """Download native binary. Returns 'npm' (Artifactory) or 'binary' (GCS).

    Routes: CI sentinel (99.99.x + ALLOW_TEST_VERSIONS), Artifactory (ant
    users), or GCS (external users).
    """
    if _check_test_version_allowed(version):
        proc = await asyncio.create_subprocess_exec(
            "gcloud", "auth", "print-access-token",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        token = (stdout or b"").decode(errors="replace").strip()
        await download_version_from_binary_repo(
            version, staging_path, CI_SENTINEL_BUCKET_URL,
            {"headers": {"Authorization": f"Bearer {token}"}},
        )
        return "binary"

    if _is_ant_user():
        await download_version_from_artifactory(version, staging_path)
        return "npm"

    await download_version_from_binary_repo(version, staging_path, GCS_BUCKET_URL)
    return "binary"


# ---------------------------------------------------------------------------
# Legacy compatibility alias
# ---------------------------------------------------------------------------


async def download_native_binary(_version: str, _dest_dir: Path, **_opts: Any) -> Path:
    """Legacy entrypoint returning Path to downloaded binary."""
    dest_str = str(_dest_dir)
    source = await download_version(_version, dest_str)
    binary_name = _get_binary_name()
    binary_path = Path(dest_str) / binary_name

    # npm ci places binaries in node_modules/<pkg>-<platform>/ — locate them
    if not binary_path.exists() and source == "npm":
        platform = _get_platform_segment()
        platform_pkg = f"{NATIVE_PACKAGE_URL}-{platform}"
        for c in sorted(Path(dest_str).rglob(binary_name)):
            if platform_pkg.replace("/", "") in str(c):
                binary_path = c
                break
        else:
            candidates = sorted(Path(dest_str).rglob(binary_name))
            if candidates:
                binary_path = candidates[0]

    if not binary_path.exists():
        raise FileNotFoundError(f"Binary not found at {binary_path} after download")
    return binary_path


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

# Re-export for test access (mirrors TS export surface)
_download_and_verify_binary = _download_and_verify_binary

__all__ = [
    "GCS_BUCKET_URL",
    "ARTIFACTORY_REGISTRY_URL",
    "CI_SENTINEL_BUCKET_URL",
    "DEFAULT_STALL_TIMEOUT_MS",
    "MAX_DOWNLOAD_RETRIES",
    "STALL_TIMEOUT_MS",
    "StallTimeoutError",
    "VersionResolutionError",
    "ChecksumMismatchError",
    "PlatformNotInManifestError",
    "NpmViewError",
    "get_latest_version_from_artifactory",
    "get_latest_version_from_binary_repo",
    "get_latest_version",
    "download_version_from_artifactory",
    "download_version_from_binary_repo",
    "download_version",
    "download_native_binary",
    "_download_and_verify_binary",
]
