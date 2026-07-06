"""
SSRF guard for HTTP hooks (blocked private / link-local ranges).

Port of: src/utils/hooks/ssrfGuard.ts
"""

from __future__ import annotations

import ipaddress
from typing import Any


def is_blocked_address(address: str) -> bool:
    """
    Return True if *address* is a non-routable range HTTP hooks must not reach.

    Loopback is allowed (127.0.0.0/8, ::1).
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 4:
        return _is_blocked_v4(ip)
    return _is_blocked_v6(ip)


def _is_blocked_v4(ip: ipaddress.IPv4Address) -> bool:
    a = int(ip.packed[0])
    b = int(ip.packed[1])
    if a == 0:
        return True
    if a == 10:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    if a == 169 and b == 254:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    return False


def _is_blocked_v6(ip: ipaddress.IPv6Address) -> bool:
    if ip.is_unspecified:
        return True
    if ip.is_link_local:
        return True
    if ip.is_private:  # unique local fc00::/7 etc.
        return True
    if ip.ipv4_mapped and _is_blocked_v4(ip.ipv4_mapped):
        return True
    return False


async def resolve_and_check_host(
    _hostname: str,
    _lookup: Any | None = None,
) -> tuple[bool, list[str]]:
    """
    Stub: perform DNS lookup and validate each A/AAAA result.

    Wire to ``asyncio.get_event_loop().getaddrinfo`` or a DNS library in production.
    """
    return True, []
