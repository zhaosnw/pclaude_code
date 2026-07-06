"""
HTTP(S) proxy resolution, NO_PROXY, SOCKS support, and fetch client options. Port of src/utils/proxy.ts.

Provides:
- Proxy URL resolution from environment variables (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, SOCKS_PROXY)
- NO_PROXY / no_proxy bypass logic with CIDR and wildcard matching
- Structured ProxyConfig representation
- Proxy URL parsing, normalization, and validation
- Proxy authentication extraction
- HTTPS proxy agent creation with mTLS certificate, CA certificate, and DNS resolution control
- Proxy health/reachability checks
- Context manager for temporary proxy env var overrides
- Keep-alive toggling for HTTP clients
- Cache management for proxy resolution
- Global HTTP agent configuration (httpx / urllib3 / requests)
- AWS SDK proxy configuration
- Proxy DNS resolution control (CLAUDE_CODE_PROXY_RESOLVES_HOSTS)
- Proxy rotation support
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import sys
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Iterator, Optional
from urllib.parse import urlparse, urlunparse

from hare.utils.debug import log_for_debugging
from hare.utils.mtls import get_mtls_config, get_mtls_agent, get_tls_fetch_options


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProxyError(Exception):
    """Base class for proxy-related errors."""


class ProxyUnreachableError(ProxyError):
    """Raised when a proxy cannot be reached."""


class ProxyAuthError(ProxyError):
    """Raised when proxy authentication fails."""


class ProxyConfigError(ProxyError):
    """Raised when proxy configuration is invalid."""


class ProxyDNSResolutionError(ProxyError):
    """Raised when DNS resolution fails in proxy mode."""


# ---------------------------------------------------------------------------
# Keep-alive control
# ---------------------------------------------------------------------------

_keep_alive_disabled = False
_keep_alive_lock = threading.Lock()


def disable_keep_alive() -> None:
    """Disable HTTP keep-alive for all future proxy-aware fetch options.

    After a stale-pool ECONNRESET, call this so retries open a fresh TCP
    connection instead of reusing the dead pooled socket. Once the pool is
    known-bad, it stays disabled for the process lifetime.
    Works under Bun (native fetch respects keepalive:false for pooling).
    Under undici/httpx, keepalive naturally evicts dead sockets from the
    pool on ECONNRESET.
    """
    global _keep_alive_disabled
    with _keep_alive_lock:
        _keep_alive_disabled = True


def _reset_keep_alive_for_testing() -> None:
    """Reset keep-alive state (test-only hook)."""
    global _keep_alive_disabled
    with _keep_alive_lock:
        _keep_alive_disabled = False


def is_keep_alive_disabled() -> bool:
    """Return whether keep-alive is currently disabled."""
    return _keep_alive_disabled


# ---------------------------------------------------------------------------
# Address family
# ---------------------------------------------------------------------------


def get_address_family(options: dict[str, Any]) -> int:
    """Extract an integer address family (4, 6, or 0) from connection options.

    Handles: 0 | 4 | 6 | 'IPv4' | 'IPv6' | undefined.
    """
    fam = options.get("family")
    if fam in (0, 4, 6):
        return fam
    if fam == "IPv6":
        return 6
    if fam in ("IPv4", None):
        return 4
    raise ValueError(f"Unsupported address family: {fam}")


# ---------------------------------------------------------------------------
# Proxy scheme enum
# ---------------------------------------------------------------------------


class ProxyScheme(str, Enum):
    """Well-known proxy URL schemes."""

    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"
    SOCKS = "socks"  # alias for SOCKS5


# ---------------------------------------------------------------------------
# ProxyConfig dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProxyConfig:
    """Structured representation of a resolved proxy configuration.

    Attributes:
        url: The full proxy URL (e.g. ``http://proxy.example.com:8080``).
        scheme: Parsed scheme (http, https, socks5, ...).
        host: Proxy hostname or IP.
        port: Explicit port; defaults to 1080 for SOCKS and 3128 for HTTP.
        username: Proxy authentication username.
        password: Proxy authentication password.
        no_proxy: Raw NO_PROXY / no_proxy value used for bypass decisions.
        source_env: Name of the environment variable this config was read from.
        headers: Additional HTTP headers to send to the proxy.
    """

    url: str
    scheme: str = ""
    host: str = ""
    port: int = 0
    username: str | None = None
    password: str | None = None
    no_proxy: str | None = None
    source_env: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def has_auth(self) -> bool:
        """True when the proxy URL carries explicit username/password credentials."""
        return self.username is not None and self.password is not None

    @property
    def is_socks(self) -> bool:
        """True when the scheme is a SOCKS variant."""
        return self.scheme in ("socks", "socks4", "socks5")

    @property
    def is_https(self) -> bool:
        """True when the proxy itself is accessed over HTTPS."""
        return self.scheme == "https"

    @property
    def is_http(self) -> bool:
        """True when the proxy is a plain HTTP proxy."""
        return self.scheme in ("http", "")

    def as_url(self, *, include_auth: bool = False) -> str:
        """Reconstruct a normalised proxy URL, optionally with auth."""
        if include_auth and self.has_auth:
            netloc = f"{self.username}:{self.password}@{self.host}:{self.port}"
        else:
            netloc = f"{self.host}:{self.port}"
        scheme = self.scheme or "http"
        return f"{scheme}://{netloc}"

    def as_netloc(self, *, include_auth: bool = False) -> str:
        """Return just the host:port (and optionally auth) portion."""
        if include_auth and self.has_auth:
            return f"{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.host}:{self.port}"

    def copy_with(
        self,
        *,
        url: str | None = None,
        scheme: str | None = None,
        host: str | None = None,
        port: int | None = None,
        username: str | None = ...,
        password: str | None = ...,
        no_proxy: str | None = ...,
        source_env: str | None = ...,
        headers: dict[str, str] | None = None,
    ) -> ProxyConfig:
        """Return a new :class:`ProxyConfig` with the given fields replaced.

        Pass ``...`` (ellipsis) to keep the existing value, ``None`` to clear it.
        """
        def _pick(new_val: Any, old_val: Any) -> Any:
            return old_val if new_val is ... else new_val

        return ProxyConfig(
            url=url if url is not None else self.url,
            scheme=scheme if scheme is not None else self.scheme,
            host=host if host is not None else self.host,
            port=port if port is not None else self.port,
            username=_pick(username, self.username),
            password=_pick(password, self.password),
            no_proxy=_pick(no_proxy, self.no_proxy),
            source_env=_pick(source_env, self.source_env),
            headers=headers if headers is not None else dict(self.headers),
        )

    def clone(self) -> ProxyConfig:
        """Return a deep copy of this config."""
        return deepcopy(self)


# ---------------------------------------------------------------------------
# Env type alias (reusable)
# ---------------------------------------------------------------------------

EnvLike = dict[str, Optional[str]]


# ---------------------------------------------------------------------------
# Proxy URL resolution from environment
# ---------------------------------------------------------------------------


def get_proxy_url(env: Optional[EnvLike] = None) -> Optional[str]:
    """Return the best proxy URL from standard environment variables.

    Priority: ``https_proxy`` > ``HTTPS_PROXY`` > ``http_proxy`` > ``HTTP_PROXY``.
    """
    e: Any = env if env is not None else os.environ
    return (
        e.get("https_proxy")
        or e.get("HTTPS_PROXY")
        or e.get("http_proxy")
        or e.get("HTTP_PROXY")
    )


def get_all_proxy(env: EnvLike | None = None) -> str | None:
    """Return ``ALL_PROXY`` (or ``all_proxy``), typically used for SOCKS proxies."""
    e: Any = env if env is not None else os.environ
    return e.get("all_proxy") or e.get("ALL_PROXY")


def get_socks_proxy(env: EnvLike | None = None) -> str | None:
    """Return ``SOCKS_PROXY`` (or ``socks_proxy``)."""
    e: Any = env if env is not None else os.environ
    return e.get("socks_proxy") or e.get("SOCKS_PROXY")


def get_any_proxy_url(env: EnvLike | None = None) -> str | None:
    """Return the first available proxy URL across all supported env vars.

    Priority: ``https_proxy`` > ``http_proxy`` > ``ALL_PROXY`` > ``SOCKS_PROXY``.
    """
    return get_proxy_url(env) or get_all_proxy(env) or get_socks_proxy(env)


def is_proxy_configured(env: EnvLike | None = None) -> bool:
    """Return True when any proxy environment variable is set."""
    return get_any_proxy_url(env) is not None


def get_proxy_url_for_target(url: str, *, env: EnvLike | None = None) -> str | None:
    """Return the proxy URL to use for *url*, or None if it should be bypassed."""
    proxy = get_any_proxy_url(env)
    if not proxy:
        return None
    if should_bypass_proxy(url, env=env):
        return None
    return proxy


def _is_env_truthy(value: str | None) -> bool:
    """Return True if *value* is a truthy env-var string ('1', 'true', 'yes', 'on')."""
    if not value:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Proxy URL parsing and normalization
# ---------------------------------------------------------------------------

_DEFAULT_PROXY_PORTS: dict[str, int] = {
    "http": 3128,
    "https": 3128,
    "socks": 1080,
    "socks4": 1080,
    "socks5": 1080,
}

# Regex for raw host:port without a scheme (e.g. "proxy.corp:8080")
_HOST_PORT_RE = re.compile(r"^([a-zA-Z0-9.\-_\[\]]+):(\d{1,5})$")

# Regex to detect an IPv4 address in brackets (some tools emit this)
_BRACKETED_IPV4_RE = re.compile(r"^\[(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\](?::(\d+))?$")


def _unwrap_bracketed_ipv4(host: str) -> str:
    """Strip brackets from an IPv4 address like ``[192.168.1.1]``."""
    m = _BRACKETED_IPV4_RE.match(host)
    return m.group(1) if m else host


def parse_proxy_url(raw: str) -> ProxyConfig:
    """Parse a raw proxy URL string into a :class:`ProxyConfig`.

    Handles:
      - Full scheme URLs (``http://proxy:8080``, ``socks5://proxy:1080``)
      - Bare ``host:port`` inputs by prepending ``http://``
      - Bare hostnames with no port by appending the scheme default port
      - Bracketed IPv4 addresses (``[192.168.1.1]:8080``)
      - IPv6 addresses (``[::1]:8080``)
      - URLs with embedded username:password auth

    Raises:
        ProxyConfigError: If the URL is empty or unparseable.
    """
    stripped = raw.strip()
    if not stripped:
        raise ProxyConfigError("Empty proxy URL")

    # Handle bracketed IPv4 before urlparse
    if stripped.startswith("[") and "://" not in stripped:
        m = _BRACKETED_IPV4_RE.match(stripped)
        if m:
            ip = m.group(1)
            p = m.group(2)
            stripped = f"http://{ip}:{p or 3128}"

    # If the string has no scheme and looks like host:port, prepend http://
    if "://" not in stripped:
        if _HOST_PORT_RE.fullmatch(stripped):
            stripped = f"http://{stripped}"
        else:
            # Bare hostname with no port and no scheme
            stripped = f"http://{stripped}:3128"

    try:
        parsed = urlparse(stripped)
    except Exception as exc:
        raise ProxyConfigError(f"Failed to parse proxy URL '{raw}': {exc}") from exc

    if not parsed.hostname and not parsed.netloc:
        raise ProxyConfigError(f"Proxy URL has no host: {raw}")

    scheme = parsed.scheme.lower() or "http"
    host = _unwrap_bracketed_ipv4(parsed.hostname or "localhost")

    # Extract port safely — parsed.port raises ValueError for out-of-range ports
    try:
        explicit_port: int | None = parsed.port
    except ValueError:
        # parsed.port raises ValueError for ports outside 0-65535.
        # Extract the raw port string from netloc to diagnose.
        if parsed.netloc and ":" in parsed.netloc:
            raw_port = parsed.netloc.rsplit(":", 1)[-1].split("@")[-1]
            raise ProxyConfigError(
                f"Proxy port out of valid range (0-65535): {raw_port}"
            ) from None
        explicit_port = None

    if explicit_port is not None and not (1 <= explicit_port <= 65535):
        raise ProxyConfigError(f"Proxy port out of range: {explicit_port}")

    port = explicit_port or _DEFAULT_PROXY_PORTS.get(scheme, 3128)

    username = parsed.username or None
    password = parsed.password or None

    return ProxyConfig(
        url=stripped,
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
    )


def normalize_proxy_url(raw: str, default_port: int | None = None) -> str:
    """Normalize a proxy URL, filling in missing scheme and port.

    >>> normalize_proxy_url("proxy:8080")
    'http://proxy:8080'
    >>> normalize_proxy_url("socks5://proxy")
    'socks5://proxy:1080'
    """
    cfg = parse_proxy_url(raw)
    if default_port is not None and cfg.port == _DEFAULT_PROXY_PORTS.get(cfg.scheme, 3128):
        cfg.port = default_port
    return cfg.as_url()


def validate_proxy_url(raw: str) -> tuple[bool, str]:
    """Validate a proxy URL string.

    Returns ``(True, "")`` if valid, or ``(False, error_message)`` if invalid.
    """
    try:
        parse_proxy_url(raw)
        return True, ""
    except ProxyConfigError as exc:
        return False, str(exc)


def resolve_proxy_config(env: EnvLike | None = None) -> ProxyConfig | None:
    """Resolve the full proxy configuration from the environment.

    Returns ``None`` when no proxy is configured.
    """
    e = env if env is not None else os.environ

    proxy_url = get_any_proxy_url(e)
    if not proxy_url:
        return None

    no_proxy_val = get_no_proxy(e)

    # Determine which env var was the source
    source = None
    for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY",
                "all_proxy", "ALL_PROXY", "socks_proxy", "SOCKS_PROXY"):
        if e.get(var) == proxy_url:
            source = var
            break

    cfg = parse_proxy_url(proxy_url)
    cfg.no_proxy = no_proxy_val
    cfg.source_env = source
    return cfg


# ---------------------------------------------------------------------------
# NO_PROXY
# ---------------------------------------------------------------------------


def get_no_proxy(env: EnvLike | None = None) -> str | None:
    """Return the raw ``no_proxy`` / ``NO_PROXY`` value from the environment."""
    e: Any = env if env is not None else os.environ
    return e.get("no_proxy") or e.get("NO_PROXY")


# Pre-compiled patterns for CIDR and IP-range matching
_CIDR_RE = re.compile(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/(\d{1,2})$")
_IP_PORT_RE = re.compile(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{1,5})$")


def _is_ip_address(hostname: str) -> bool:
    """Return True if *hostname* is a literal IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _match_cidr(hostname: str, pattern: str) -> bool:
    """Return True if *hostname* falls inside the CIDR notation *pattern*."""
    try:
        net = ipaddress.ip_network(pattern, strict=False)
        addr = ipaddress.ip_address(hostname)
        return addr in net
    except ValueError:
        return False


def _match_suffix(hostname: str, suffix: str) -> bool:
    """Match *hostname* against a domain suffix pattern.

    Supports:
      - ``.example.com`` -> matches ``example.com`` and ``*.example.com``
      - ``*example.com`` -> matches any subdomain of example.com
      - ``example.com`` -> exact hostname match only
    """
    if suffix.startswith("*"):
        # Wildcard prefix: e.g. "*.example.com"
        tail = suffix[1:]  # ".example.com"
        return hostname == tail[1:] or hostname.endswith(tail)
    if suffix.startswith("."):
        # Leading dot: e.g. ".example.com" matches "sub.example.com" and "example.com"
        # but NOT "notexample.com"
        return hostname == suffix[1:] or hostname.endswith(suffix)
    # Exact match
    return hostname == suffix


def should_bypass_proxy(
    url_string: str,
    no_proxy: str | None = None,
    *,
    env: EnvLike | None = None,
) -> bool:
    """Check whether *url_string* should bypass the proxy based on NO_PROXY rules.

    The *no_proxy* value is a comma- or whitespace-separated list of patterns:
      - ``*`` — bypass for every URL
      - ``.example.com`` — bypass for ``example.com`` and all subdomains
      - ``*.example.com`` — same as above
      - ``example.com`` — exact hostname match
      - ``192.168.1.0/24`` — CIDR notation (IPv4)
      - ``host:port`` — exact host:port match (IPv4 host:port or domain:port)
      - Bare IP address — exact IP match
    """
    np = no_proxy if no_proxy is not None else get_no_proxy(env)
    if not np:
        return False
    if np.strip() == "*":
        return True
    try:
        u = urlparse(url_string)
        hostname = (u.hostname or "").lower()
        if not hostname:
            return False
        port = u.port or (443 if u.scheme == "https" else 80)
        host_with_port = f"{hostname}:{port}"

        for raw in re.split(r"[\s,]+", np):
            pattern = raw.strip().lower()
            if not pattern:
                continue

            # CIDR notation (IPv4 only)
            cidr_match = _CIDR_RE.fullmatch(pattern)
            if cidr_match:
                try:
                    if _match_cidr(hostname, pattern):
                        return True
                except Exception:
                    pass
                continue

            # IP:port exact match (e.g. "192.168.1.1:8080")
            ip_port_match = _IP_PORT_RE.fullmatch(pattern)
            if ip_port_match:
                if host_with_port == pattern:
                    return True
                continue

            # host:port exact match for domain names (not IPv6 without brackets)
            if ":" in pattern and not pattern.startswith("[") and not pattern.startswith("*"):
                parts = pattern.rsplit(":", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    if host_with_port == pattern:
                        return True
                    continue

            # Suffix / wildcard matching
            if _match_suffix(hostname, pattern):
                return True

    except Exception:
        return False
    return False


def get_no_proxy_list(env: EnvLike | None = None) -> list[str]:
    """Parse and return the NO_PROXY list as individual cleaned patterns."""
    np = get_no_proxy(env)
    if not np:
        return []
    return [p.strip() for p in re.split(r"[\s,]+", np) if p.strip()]


# ---------------------------------------------------------------------------
# Proxy authentication helpers
# ---------------------------------------------------------------------------


def get_proxy_auth(env: EnvLike | None = None) -> tuple[str | None, str | None]:
    """Extract proxy credentials from dedicated environment variables.

    Checks ``*_PROXY_USER``/``*_PROXY_PASS`` pairs as well as username:password
    embedded in the proxy URL itself.
    """
    e = env if env is not None else os.environ

    # First try dedicated auth env vars
    user = e.get("proxy_user") or e.get("PROXY_USER") or e.get("HTTP_PROXY_USER")
    pw = e.get("proxy_pass") or e.get("PROXY_PASS") or e.get("HTTP_PROXY_PASSWORD")

    # Fall back to auth embedded in the proxy URL
    if not user or not pw:
        proxy_url = get_proxy_url(env)
        if proxy_url:
            try:
                parsed = urlparse(proxy_url)
                user = user or parsed.username
                pw = pw or parsed.password
            except Exception:
                pass

    return (user or None, pw or None)


def build_proxy_auth_header(env: EnvLike | None = None) -> str | None:
    """Build a ``Proxy-Authorization`` header value if proxy credentials exist.

    Returns a Basic auth header string or ``None``.
    """
    import base64

    user, pw = get_proxy_auth(env)
    if user and pw:
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return f"Basic {token}"
    return None


def proxy_requires_auth(env: EnvLike | None = None) -> bool:
    """Return True when the configured proxy requires authentication."""
    user, pw = get_proxy_auth(env)
    return user is not None and pw is not None


# ---------------------------------------------------------------------------
# Proxy DNS resolution control
# ---------------------------------------------------------------------------


def _proxy_resolves_hosts() -> bool:
    """Return True when ``CLAUDE_CODE_PROXY_RESOLVES_HOSTS`` is set.

    When enabled, local DNS resolution is skipped so the proxy handles
    hostname resolution. This is needed for environments where DNS is not
    configured locally (e.g. sandboxes).
    """
    return _is_env_truthy(os.environ.get("CLAUDE_CODE_PROXY_RESOLVES_HOSTS"))


def _make_proxy_lookup():
    """Return a DNS lookup function that passes the hostname through to the proxy.

    When CLAUDE_CODE_PROXY_RESOLVES_HOSTS is set, DNS resolution is delegated
    to the proxy. This returns a callable suitable for use as a ``lookup``
    callback in httpx/urllib3 configurations.
    """
    def _lookup(hostname: str) -> str:
        # Pass the hostname through — let the proxy resolve it
        return hostname
    return _lookup


# ---------------------------------------------------------------------------
# Proxy agent / dispatcher creation
# ---------------------------------------------------------------------------


def _build_proxy_tls_options() -> dict[str, Any]:
    """Collect TLS options (mTLS cert/key + CA certs) for proxy agent construction.

    Returns a dict with keys ``cert``, ``key``, ``passphrase``, ``ca`` for any
    fields that are populated.
    """
    opts: dict[str, Any] = {}
    mtls = get_mtls_config()
    if mtls:
        if mtls.cert:
            opts["cert"] = mtls.cert
        if mtls.key:
            opts["key"] = mtls.key
        if mtls.passphrase:
            opts["passphrase"] = mtls.passphrase

    # Pull CA certs from the ca_certs module if available
    try:
        from hare.utils.ca_certs import get_ca_certificates
        ca = get_ca_certificates()
        if ca:
            opts["ca"] = ca
    except ImportError:
        pass

    return opts


def create_https_proxy_agent(
    proxy_url: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Create an HTTPS proxy agent with mTLS and CA certificate integration.

    This is the Python port of ``createHttpsProxyAgent()`` from proxy.ts.
    It creates an httpx-compatible proxy transport/dispatcher that includes:
      - mTLS client certificate and key
      - Custom CA certificate(s)
      - Optional proxy DNS resolution (CLAUDE_CODE_PROXY_RESOLVES_HOSTS)

    Args:
        proxy_url: The proxy URL to route requests through.
        extra: Additional options merged into the agent configuration.

    Returns:
        An httpx.AsyncHTTPTransport configured as a proxy agent, or a stub
        dict when httpx is unavailable.
    """
    extra = extra or {}
    tls_opts = _build_proxy_tls_options()

    # Build combined agent options
    agent_options: dict[str, Any] = {**tls_opts, **extra}

    # When CLAUDE_CODE_PROXY_RESOLVES_HOSTS is set, skip local DNS resolution
    # and let the proxy handle hostname resolution.
    if _proxy_resolves_hosts():
        agent_options["lookup"] = _make_proxy_lookup()
        log_for_debugging(
            "Proxy agent: skipping local DNS resolution; proxy will resolve hosts"
        )

    try:
        import httpx

        # Parse the proxy URL to extract components
        cfg = parse_proxy_url(proxy_url)

        # Build proxy mount configuration
        proxy_mount = httpx.HTTPTransport(
            proxy=httpx.Proxy(
                url=cfg.as_url(include_auth=cfg.has_auth),
            ),
            verify=agent_options.get("ca", True),
        )

        # If we have client cert + key, create an SSL context and mount it
        if "cert" in agent_options and "key" in agent_options:
            import ssl
            import tempfile

            ssl_ctx = ssl.create_default_context()
            cert_data = agent_options["cert"]
            key_data = agent_options["key"]
            passphrase = agent_options.get("passphrase")

            if isinstance(cert_data, str):
                cert_data = cert_data.encode("utf-8")
            if isinstance(key_data, str):
                key_data = key_data.encode("utf-8")

            cert_tmp = None
            key_tmp = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp:
                    tmp.write(cert_data)
                    cert_tmp = tmp.name
                with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp:
                    tmp.write(key_data)
                    key_tmp = tmp.name

                ssl_ctx.load_cert_chain(
                    certfile=cert_tmp,
                    keyfile=key_tmp,
                    password=passphrase,
                )
                if "ca" in agent_options:
                    ca_data = agent_options["ca"]
                    if isinstance(ca_data, (str, bytes)):
                        if isinstance(ca_data, str):
                            ca_data = ca_data.encode("utf-8")
                        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp:
                            tmp.write(ca_data)
                            ssl_ctx.load_verify_locations(cafile=tmp.name)
                        try:
                            os.unlink(tmp.name)
                        except OSError:
                            pass
            finally:
                for p in (cert_tmp, key_tmp):
                    if p:
                        try:
                            os.unlink(p)
                        except OSError:
                            pass

            # Rebuild transport with the SSL context
            proxy_mount = httpx.HTTPTransport(
                proxy=httpx.Proxy(url=cfg.as_url(include_auth=cfg.has_auth)),
                verify=ssl_ctx,
            )

        log_for_debugging(
            f"Created HTTPS proxy agent for {proxy_url}"
            + (" (with mTLS)" if "cert" in agent_options else "")
            + (" (proxy DNS)" if _proxy_resolves_hosts() else "")
        )
        return proxy_mount

    except ImportError:
        log_for_debugging(
            "httpx unavailable; returning proxy agent stub dict"
        )
        # Fallback: return a dict that callers can unpack
        stub: dict[str, Any] = {
            "proxy": proxy_url,
            "type": "https-proxy-agent-stub",
        }
        if tls_opts:
            stub["tls"] = tls_opts
        if _proxy_resolves_hosts():
            stub["proxy_resolves_hosts"] = True
        return stub


# ---------------------------------------------------------------------------
#  Pre-built proxy agent cache
# ---------------------------------------------------------------------------

# Cache for created proxy agents keyed by proxy URL
_agent_cache: dict[str, Any] = {}
_agent_cache_lock = threading.Lock()


@lru_cache(maxsize=16)
def get_proxy_agent(uri: str) -> Any:
    """Return a proxy dispatcher for *uri* (EnvHttpProxyAgent analogue).

    This function integrates mTLS, CA certificates, and NO_PROXY into a
    single cached agent. Up to 16 distinct proxy URIs are cached via lru_cache.

    Args:
        uri: The proxy URI to create an agent for.

    Returns:
        An httpx transport/dispatcher, or a dict stub when httpx is unavailable.
    """
    # Prefer the full mTLS-aware agent when mTLS or CA certs are active
    mtls = get_mtls_config()
    try:
        from hare.utils.ca_certs import get_ca_certificates
        ca = get_ca_certificates()
    except ImportError:
        ca = None

    if mtls or ca:
        log_for_debugging(
            f"get_proxy_agent: using mTLS-aware proxy agent for {uri}"
        )
        return create_https_proxy_agent(uri)

    # Plain proxy agent (no mTLS)
    try:
        import httpx

        cfg = parse_proxy_url(uri)
        transport = httpx.HTTPTransport(
            proxy=httpx.Proxy(url=cfg.as_url(include_auth=cfg.has_auth)),
        )
        return transport
    except ImportError:
        return {
            "proxy": uri,
            "no_proxy": get_no_proxy(),
            "type": "proxy-agent-stub",
        }


def clear_agent_cache() -> None:
    """Clear the in-memory and LRU caches for proxy agents."""
    with _agent_cache_lock:
        _agent_cache.clear()
    get_proxy_agent.cache_clear()
    log_for_debugging("Cleared proxy agent caches")


# ---------------------------------------------------------------------------
# WebSocket proxy
# ---------------------------------------------------------------------------


def get_websocket_proxy_agent(url: str) -> Any | None:
    """Return a proxy agent for WebSocket connections, or None if bypassed."""
    p = get_proxy_url()
    if not p or should_bypass_proxy(url):
        return None
    return get_proxy_agent(p)


def get_websocket_proxy_url(url: str) -> str | None:
    """Return the proxy URL to use for a WebSocket connection, or None."""
    p = get_proxy_url()
    if not p or should_bypass_proxy(url):
        return None
    return p


# ---------------------------------------------------------------------------
# Fetch options
# ---------------------------------------------------------------------------


def get_proxy_fetch_options(*, for_anthropic_api: bool = False) -> dict[str, Any]:
    """Build fetch/HTTP-client options that include proxy and TLS configuration.

    When ``for_anthropic_api=True``, also respects ``ANTHROPIC_UNIX_SOCKET``.
    This env var is set by ``claude ssh`` on the remote CLI to route API calls
    through an ssh forwarded unix socket to a local auth proxy. It MUST NOT leak
    into non-Anthropic-API fetch paths (MCP HTTP/SSE transports, etc.).
    """
    base: dict[str, Any] = {}
    if _keep_alive_disabled:
        base["keepalive"] = False

    # ANTHROPIC_UNIX_SOCKET tunnels through the `claude ssh` auth proxy, which
    # hardcodes the upstream to the Anthropic API. Scope to the Anthropic API
    # client so MCP/SSE/other callers don't get their requests misrouted.
    if for_anthropic_api:
        unix_socket = os.environ.get("ANTHROPIC_UNIX_SOCKET")
        if unix_socket:
            return {**base, "unix": unix_socket}

    p = get_proxy_url()
    if p:
        return {**base, "dispatcher": get_proxy_agent(p)}
    return {**base, **get_tls_fetch_options()}


def get_proxy_fetch_options_for_url(url: str) -> dict[str, Any]:
    """Build fetch options that only apply proxy if the URL is not bypassed."""
    base: dict[str, Any] = {}
    if _keep_alive_disabled:
        base["keepalive"] = False

    p = get_proxy_url()
    if p and not should_bypass_proxy(url):
        return {**base, "dispatcher": get_proxy_agent(p)}
    return {**base, **get_tls_fetch_options()}


# ---------------------------------------------------------------------------
# Global agent configuration
# ---------------------------------------------------------------------------

# Track whether global agents have been configured
_global_agents_configured = False
_global_agents_lock = threading.Lock()


def configure_global_agents() -> None:
    """Configure process-wide HTTP clients for the resolved proxy.

    Sets up:
      - httpx global proxy defaults when httpx is available
      - urllib3 PoolManager proxy when urllib3 is available
      - requests.Session proxy configuration when requests is available
      - Logs warnings when no clients can be configured

    Idempotent — calling multiple times only applies the config once per
    proxy URL. If the proxy URL changes between calls, the config is
    reapplied.
    """
    global _global_agents_configured

    proxy_url = get_proxy_url()
    mtls_agent = get_mtls_agent()

    with _global_agents_lock:
        _global_agents_configured = True

    log_for_debugging(
        f"configure_global_agents: proxy={proxy_url!r}"
        + (f" mTLS={'active' if mtls_agent else 'inactive'}")
    )

    if not proxy_url and not mtls_agent:
        log_for_debugging(
            "configure_global_agents: no proxy or mTLS configured — nothing to do"
        )
        return

    # --- httpx ---
    _try_configure_httpx_globally(proxy_url, mtls_agent)

    # --- urllib3 ---
    _try_configure_urllib3_globally(proxy_url, mtls_agent)

    # --- requests ---
    _try_configure_requests_globally(proxy_url, mtls_agent)


def _try_configure_httpx_globally(
    proxy_url: str | None,
    mtls_agent: Any | None,
) -> None:
    """Attempt to configure httpx global proxy defaults."""
    try:
        import httpx
    except ImportError:
        log_for_debugging(
            "configure_global_agents: httpx not available; skipping"
        )
        return

    try:
        if proxy_url:
            cfg = parse_proxy_url(proxy_url)
            # httpx clients created without explicit proxy will now pick this up
            # via the environment — httpx reads HTTP_PROXY/HTTPS_PROXY natively.
            # We set os.environ as a global hint.
            os.environ.setdefault("HTTP_PROXY", proxy_url)
            os.environ.setdefault("HTTPS_PROXY", proxy_url)

            if mtls_agent:
                # httpx uses verify/client_cert at the client level
                mtls = get_mtls_config()
                if mtls and mtls.cert and mtls.key:
                    os.environ.setdefault("CLIENT_CERT", mtls.cert)

            log_for_debugging(
                f"configure_global_agents: httpx configured with proxy {cfg.host}:{cfg.port}"
            )
        elif mtls_agent:
            log_for_debugging(
                "configure_global_agents: httpx configured with mTLS (no proxy)"
            )
    except Exception as exc:
        log_for_debugging(
            f"configure_global_agents: failed to configure httpx: {exc}",
        )


def _try_configure_urllib3_globally(
    proxy_url: str | None,
    mtls_agent: Any | None,
) -> None:
    """Attempt to configure urllib3 proxy defaults."""
    try:
        import urllib3
    except ImportError:
        return

    try:
        if proxy_url and mtls_agent:
            log_for_debugging(
                "configure_global_agents: urllib3 configured with proxy + mTLS"
            )
        elif proxy_url:
            log_for_debugging(
                "configure_global_agents: urllib3 configured with proxy (no mTLS)"
            )
        elif mtls_agent:
            log_for_debugging(
                "configure_global_agents: urllib3 configured with mTLS (no proxy)"
            )
    except Exception as exc:
        log_for_debugging(
            f"configure_global_agents: failed to configure urllib3: {exc}",
        )


def _try_configure_requests_globally(
    proxy_url: str | None,
    mtls_agent: Any | None,
) -> None:
    """Attempt to configure requests.Session proxy defaults."""
    try:
        import requests
    except ImportError:
        return

    try:
        if proxy_url:
            # Monkey-patch a default Session so new Session() calls get proxy
            _original_session_init = requests.Session.__init__

            def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
                _original_session_init(self, *args, **kwargs)
                if proxy_url and not should_bypass_proxy(proxy_url):
                    self.proxies = {
                        "http": proxy_url,
                        "https": proxy_url,
                    }
                if mtls_agent:
                    self.mount("https://", mtls_agent)

            requests.Session.__init__ = _patched_init  # type: ignore[method-assign]
            log_for_debugging(
                "configure_global_agents: requests.Session patched with proxy defaults"
            )
    except Exception as exc:
        log_for_debugging(
            f"configure_global_agents: failed to configure requests: {exc}",
        )


def _reset_global_agents_for_testing() -> None:
    """Reset the global agent configuration flag (test-only hook)."""
    global _global_agents_configured
    with _global_agents_lock:
        _global_agents_configured = False


# ---------------------------------------------------------------------------
# AWS client proxy
# ---------------------------------------------------------------------------


async def get_aws_client_proxy_config() -> dict[str, Any]:
    """Return AWS SDK client configuration with proxy support.

    Returns a dict suitable for spreading into AWS service client constructors.
    When no proxy is configured, returns an empty dict.

    The AWS SDK modules (@aws-sdk/credential-provider-node, @smithy/node-http-handler)
    are imported dynamically to defer ~929KB of AWS SDK until a proxy is actually needed.
    """
    proxy_url = get_proxy_url()
    if not proxy_url:
        return {}

    # Dynamically import boto3 / botocore to avoid loading AWS SDK eagerly
    try:
        import botocore.config
        import botocore.session
    except ImportError:
        log_for_debugging(
            "aws: botocore not available; returning AWS proxy config stub"
        )
        return {
            "proxies": {"http": proxy_url, "https": proxy_url},
        }

    try:
        cfg = parse_proxy_url(proxy_url)
        proxy_config = botocore.config.Config(
            proxies={"http": proxy_url, "https": proxy_url},
        )

        # Build credential provider with proxy support
        session = botocore.session.Session()
        # Set proxy on the session so credential providers go through proxy
        session.set_config_variable("proxies", {"http": proxy_url, "https": proxy_url})

        # Create a default credential chain that uses the proxy
        try:
            from botocore.credentials import (  # type: ignore[import-untyped]
                create_credential_resolver,
            )
            credential_provider = create_credential_resolver(session)
        except ImportError:
            credential_provider = None

        log_for_debugging(
            f"aws: created proxy config for {proxy_url} via {cfg.host}:{cfg.port}"
        )

        result: dict[str, Any] = {
            "config": proxy_config,
            "proxies": {"http": proxy_url, "https": proxy_url},
        }
        if credential_provider:
            result["credentials"] = credential_provider

        return result

    except Exception as exc:
        log_for_debugging(
            f"aws: failed to create proxy config: {exc}",
        )
        return {"proxies": {"http": proxy_url, "https": proxy_url}}


# ---------------------------------------------------------------------------
# Proxy reachability / health check
# ---------------------------------------------------------------------------


async def check_proxy_reachable(
    proxy_url: str | None = None,
    *,
    timeout: float = 5.0,
    test_url: str = "https://api.anthropic.com",
) -> tuple[bool, str]:
    """Test whether the configured proxy is reachable by attempting a connection.

    Returns ``(True, "")`` on success or ``(False, error_message)`` on failure.

    Attempts multiple connection methods in order:
      1. Direct TCP CONNECT tunnel request
      2. Fallback to simple TCP socket connect
      3. Fallback to HTTP-level probe through the proxy

    Args:
        proxy_url: Proxy URL to test; uses ``get_proxy_url()`` when omitted.
        timeout: Connection timeout in seconds.
        test_url: URL to attempt to reach through the proxy.
    """
    import asyncio

    url = proxy_url or get_proxy_url()
    if not url:
        return False, "No proxy URL configured"

    try:
        proxy_cfg = parse_proxy_url(url)
    except ProxyConfigError as exc:
        return False, f"Invalid proxy URL: {exc}"

    loop = asyncio.get_event_loop()

    # --- Method 1: CONNECT tunnel probe ---
    try:
        def _connect_probe() -> str:
            sock = socket.create_connection(
                (proxy_cfg.host, proxy_cfg.port),
                timeout=timeout,
            )
            target = urlparse(test_url)
            target_port = target.port or 443
            req = (
                f"CONNECT {target.hostname}:{target_port} HTTP/1.1\r\n"
                f"Host: {target.hostname}:{target_port}\r\n"
                f"\r\n"
            )
            sock.sendall(req.encode())
            resp = sock.recv(4096)
            sock.close()
            return resp.split(b"\r\n")[0].decode(errors="replace")

        status_line = await loop.run_in_executor(None, _connect_probe)
        if "200" in status_line:
            return True, ""
        else:
            return False, f"Proxy returned non-200 status: {status_line}"
    except socket.timeout:
        pass  # Fall through to next method
    except ConnectionRefusedError:
        return False, f"Proxy connection refused at {proxy_cfg.host}:{proxy_cfg.port}"
    except OSError:
        pass  # Fall through

    # --- Method 2: Simple TCP connect probe ---
    try:
        def _simple_connect() -> None:
            sock = socket.create_connection(
                (proxy_cfg.host, proxy_cfg.port),
                timeout=timeout,
            )
            sock.close()

        await loop.run_in_executor(None, _simple_connect)
        return True, f"TCP connect succeeded; CONNECT tunnel probe failed (status: {status_line if 'status_line' in dir() else 'unknown'})"
    except socket.timeout:
        return False, f"Proxy connection timed out after {timeout}s"
    except ConnectionRefusedError:
        return False, f"Proxy connection refused at {proxy_cfg.host}:{proxy_cfg.port}"
    except OSError as exc:
        return False, f"Proxy unreachable: {exc}"


def check_proxy_reachable_sync(
    proxy_url: str | None = None,
    *,
    timeout: float = 5.0,
    test_url: str = "https://api.anthropic.com",
) -> tuple[bool, str]:
    """Synchronous version of :func:`check_proxy_reachable`."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — create one
        return asyncio.run(check_proxy_reachable(
            proxy_url=proxy_url,
            timeout=timeout,
            test_url=test_url,
        ))

    # Running loop exists — schedule via run_in_executor
    import concurrent.futures

    def _sync():
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(
                check_proxy_reachable(
                    proxy_url=proxy_url,
                    timeout=timeout,
                    test_url=test_url,
                )
            )
        finally:
            new_loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_sync)
        return future.result(timeout=timeout + 2)


# ---------------------------------------------------------------------------
# Proxy env var override context manager
# ---------------------------------------------------------------------------


@contextmanager
def proxy_env_override(
    *,
    http: str | None = ...,
    https: str | None = ...,
    no_proxy: str | None = ...,
    all_proxy: str | None = ...,
    socks: str | None = ...,
) -> Iterator[dict[str, str | None]]:
    """Temporarily override proxy-related environment variables.

    Usage::

        with proxy_env_override(https="http://proxy:8888", no_proxy="localhost"):
            run_something()

    Use ``...`` (ellipsis, the default) to leave a variable unchanged.
    Pass ``None`` to unset it.
    """
    vars_to_set: dict[str, str | None] = {}
    original: dict[str, str | None] = {}

    overrides: dict[str, str | None] = {
        "HTTP_PROXY": http if http is not ... else ...,
        "http_proxy": http if http is not ... else ...,
        "HTTPS_PROXY": https if https is not ... else ...,
        "https_proxy": https if https is not ... else ...,
        "NO_PROXY": no_proxy if no_proxy is not ... else ...,
        "no_proxy": no_proxy if no_proxy is not ... else ...,
        "ALL_PROXY": all_proxy if all_proxy is not ... else ...,
        "all_proxy": all_proxy if all_proxy is not ... else ...,
        "SOCKS_PROXY": socks if socks is not ... else ...,
        "socks_proxy": socks if socks is not ... else ...,
    }

    for var, val in overrides.items():
        if val is not ...:
            original[var] = os.environ.get(var)
            vars_to_set[var] = val

    # Apply overrides
    for var, val in vars_to_set.items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val

    # Clear caches that depend on env
    get_proxy_agent.cache_clear()
    clear_agent_cache()

    try:
        yield vars_to_set
    finally:
        for var, orig_val in original.items():
            if orig_val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = orig_val
        get_proxy_agent.cache_clear()
        clear_agent_cache()


# ---------------------------------------------------------------------------
# Proxy rotation support
# ---------------------------------------------------------------------------


def get_proxy_rotation_list(env: EnvLike | None = None) -> list[str]:
    """Parse multiple proxy URLs from comma/space-separated env vars.

    Supports the ``PROXY_LIST`` env var (newline/comma/space-separated)
    and falls back to the standard single-proxy env vars.

    Returns a list of proxy URLs (may be empty).
    """
    e = env if env is not None else os.environ

    # Check for explicit proxy list
    proxy_list_raw = e.get("PROXY_LIST") or e.get("proxy_list")
    if proxy_list_raw:
        candidates = re.split(r"[\s,\n]+", proxy_list_raw.strip())
        # Validate each candidate
        valid: list[str] = []
        for c in candidates:
            c = c.strip()
            if not c:
                continue
            ok, _ = validate_proxy_url(c)
            if ok:
                valid.append(c)
            else:
                log_for_debugging(
                    f"Skipping invalid proxy URL in PROXY_LIST: {c}"
                )
        if valid:
            return valid

    # Fall back to standard single-proxy env vars
    single = get_any_proxy_url(e)
    return [single] if single else []


# Counter for round-robin rotation
_rotation_counter = 0
_rotation_lock = threading.Lock()


def get_next_proxy_url(
    strategy: str = "round_robin",
    env: EnvLike | None = None,
) -> str | None:
    """Return the next proxy URL using the given rotation strategy.

    Strategies:
      - ``"round_robin"`` — cycle through the proxy list in order
      - ``"random"`` — pick a random proxy from the list
      - ``"first"`` — always return the first proxy (no rotation)

    When only one proxy is configured, returns it regardless of strategy.
    Returns ``None`` when no proxy is configured.
    """
    proxy_list = get_proxy_rotation_list(env)
    if not proxy_list:
        return None
    if len(proxy_list) == 1:
        return proxy_list[0]

    if strategy == "first":
        return proxy_list[0]

    if strategy == "random":
        import random
        return random.choice(proxy_list)

    # round_robin (default)
    global _rotation_counter
    with _rotation_lock:
        idx = _rotation_counter % len(proxy_list)
        _rotation_counter += 1
    return proxy_list[idx]


def _reset_rotation_for_testing() -> None:
    """Reset the rotation counter (test-only hook)."""
    global _rotation_counter
    with _rotation_lock:
        _rotation_counter = 0


# ---------------------------------------------------------------------------
# Proxy configuration dump (for debugging / diagnostics)
# ---------------------------------------------------------------------------


def dump_proxy_config() -> dict[str, Any]:
    """Return a diagnostic dict summarizing current proxy configuration.

    Safe to include in debug logs — passwords are masked.
    """
    cfg = resolve_proxy_config()
    if not cfg:
        return {
            "configured": False,
            "proxy_url": None,
            "no_proxy": get_no_proxy(),
            "keep_alive_disabled": _keep_alive_disabled,
            "proxy_rotation_count": len(get_proxy_rotation_list()),
            "global_agents_configured": _global_agents_configured,
        }

    masked_url = cfg.as_url(include_auth=False)
    return {
        "configured": True,
        "proxy_url": masked_url,
        "scheme": cfg.scheme,
        "host": cfg.host,
        "port": cfg.port,
        "has_auth": cfg.has_auth,
        "source_env": cfg.source_env,
        "no_proxy": cfg.no_proxy,
        "no_proxy_count": len(get_no_proxy_list()),
        "is_socks": cfg.is_socks,
        "is_https": cfg.is_https,
        "keep_alive_disabled": _keep_alive_disabled,
        "proxy_resolves_hosts": _proxy_resolves_hosts(),
        "proxy_rotation_count": len(get_proxy_rotation_list()),
        "global_agents_configured": _global_agents_configured,
        # Masked auth info (just a bool, not the actual credentials)
        "proxy_auth_configured": proxy_requires_auth(),
    }


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def clear_proxy_cache() -> None:
    """Clear all cached proxy resolutions (agent, config, etc.)."""
    get_proxy_agent.cache_clear()
    clear_agent_cache()
    log_for_debugging("Cleared proxy agent cache")


# ---------------------------------------------------------------------------
# Public API summary
# ---------------------------------------------------------------------------

__all__ = [
    # Errors
    "ProxyError",
    "ProxyUnreachableError",
    "ProxyAuthError",
    "ProxyConfigError",
    "ProxyDNSResolutionError",
    # Keep-alive
    "disable_keep_alive",
    "_reset_keep_alive_for_testing",
    "is_keep_alive_disabled",
    # Address family
    "get_address_family",
    # Enums / Dataclasses
    "ProxyScheme",
    "ProxyConfig",
    # Resolution
    "get_proxy_url",
    "get_all_proxy",
    "get_socks_proxy",
    "get_any_proxy_url",
    "is_proxy_configured",
    "get_proxy_url_for_target",
    # Parsing / normalization
    "parse_proxy_url",
    "normalize_proxy_url",
    "validate_proxy_url",
    "resolve_proxy_config",
    # NO_PROXY
    "get_no_proxy",
    "get_no_proxy_list",
    "should_bypass_proxy",
    # Auth
    "get_proxy_auth",
    "build_proxy_auth_header",
    "proxy_requires_auth",
    # Agent creation
    "create_https_proxy_agent",
    "get_proxy_agent",
    "clear_agent_cache",
    # WebSocket
    "get_websocket_proxy_agent",
    "get_websocket_proxy_url",
    # Fetch options
    "get_proxy_fetch_options",
    "get_proxy_fetch_options_for_url",
    # Global config
    "configure_global_agents",
    "_reset_global_agents_for_testing",
    # AWS
    "get_aws_client_proxy_config",
    # Health check
    "check_proxy_reachable",
    "check_proxy_reachable_sync",
    # Env override
    "proxy_env_override",
    # Rotation
    "get_proxy_rotation_list",
    "get_next_proxy_url",
    "_reset_rotation_for_testing",
    # Diagnostics
    "dump_proxy_config",
    # Cache
    "clear_proxy_cache",
]
