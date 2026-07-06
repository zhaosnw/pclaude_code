"""
HTTP hook execution — sends HTTP requests for hook callbacks.

Port of: src/utils/hooks/execHttpHook.ts

Executes HTTP-based hooks by sending requests to configured URLs
and returning structured results. Supports GET/POST/PUT/DELETE methods.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class HttpHookResult:
    status_code: int
    body: str
    headers: dict[str, str]


async def exec_http_hook(
    url: str,
    *,
    method: str = "POST",
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes | str] = None,
    timeout_sec: float = 30.0,
    follow_redirects: bool = True,
) -> HttpHookResult:
    """Execute an HTTP hook request.

    Args:
        url: The hook endpoint URL
        method: HTTP method (GET, POST, PUT, DELETE)
        headers: Request headers
        body: Request body (string or bytes)
        timeout_sec: Request timeout in seconds
        follow_redirects: Whether to follow HTTP redirects

    Returns:
        HttpHookResult with status_code, body, and response headers.
    """
    import asyncio

    if isinstance(body, str):
        body = body.encode("utf-8")

    req_headers: dict[str, str] = dict(headers or {})
    if body and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"

    def _sync_request() -> HttpHookResult:
        req = urllib.request.Request(
            url,
            data=body,
            headers=req_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
                response_headers = dict(resp.headers.items())
                return HttpHookResult(
                    status_code=resp.getcode() or 200,
                    body=response_body,
                    headers=response_headers,
                )
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            response_headers = dict(e.headers.items()) if hasattr(e, 'headers') else {}
            return HttpHookResult(
                status_code=e.code,
                body=error_body,
                headers=response_headers,
            )
        except urllib.error.URLError as e:
            return HttpHookResult(
                status_code=0,
                body=f"Connection error: {e.reason}",
                headers={},
            )

    return await asyncio.get_event_loop().run_in_executor(None, _sync_request)


def parse_hook_response(result: HttpHookResult) -> dict[str, Any]:
    """Parse an HTTP hook result into a structured hook response.

    Exit code semantics: HTTP 2xx → exit 0 (pass), 4xx/5xx → exit 2 (block).
    """
    is_success = 200 <= result.status_code < 300

    response_data: dict[str, Any] = {
        "hookEventName": "",
        "exitCode": 0 if is_success else 2,
        "stdout": result.body,
    }

    # Try to parse JSON body for structured output
    if result.body.strip():
        try:
            parsed = json.loads(result.body)
            if isinstance(parsed, dict):
                response_data.update(parsed)
        except json.JSONDecodeError:
            pass

    if not is_success and not response_data.get("systemMessage"):
        response_data["systemMessage"] = (
            f"HTTP hook returned {result.status_code}"
        )

    return response_data
