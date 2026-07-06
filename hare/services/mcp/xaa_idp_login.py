"""
Enterprise IdP login for MCP — browser-based OIDC authorization_code + PKCE flow.
Acquires an id_token from the configured IdP, caches it in secure storage keyed
by normalized issuer.  Supports client_secret_post, conformance-test injection
via save_idp_id_token_from_jwt(), and abort-signal cancellation.

Port of: src/services/mcp/xaaIdpLogin.ts
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from hare.utils.env_utils import is_env_truthy
from hare.utils.secure_storage.storage import get_secure_storage

logger = logging.getLogger("hare.mcp.xaa_idp_login")

_IDP_TIMEOUT = 300     # seconds for the full login flow
_REQ_TIMEOUT = 30      # seconds per HTTP request
_EXPIRY_BUFFER = 60    # seconds before token expiry to consider it stale


# ── Exceptions ──────────────────────────────────────────────────────────────


class IdpLoginError(Exception):
    def __init__(self, msg: str, code: int = 0): super().__init__(msg); self.status_code = code

class OidcDiscoveryError(IdpLoginError): ...
class CallbackTimeoutError(IdpLoginError): ...
class CallbackStateMismatchError(IdpLoginError): ...
class IdpTokenError(IdpLoginError): ...


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class XaaIdpSettings:
    issuer: str = ""
    client_id: str = ""
    callback_port: int | None = None


@dataclass
class IdpLoginOptions:
    idp_issuer: str
    idp_client_id: str
    idp_client_secret: str | None = None
    callback_port: int | None = None
    on_authorization_url: Callable[[str], None] | None = None
    skip_browser_open: bool = False


# ── Feature gate ────────────────────────────────────────────────────────────


def is_xaa_enabled() -> bool:
    return is_env_truthy(os.environ.get("CLAUDE_CODE_ENABLE_XAA"))


# ── Settings accessor ───────────────────────────────────────────────────────


def get_xaa_idp_settings() -> XaaIdpSettings | None:
    path = os.path.expanduser("~/.hare/settings.json")
    try:
        if not os.path.isfile(path):
            return None
        data = json.loads(open(path).read())
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Cannot read XAA IdP settings: %s", e)
        return None
    x = data.get("xaaIdp")
    if not isinstance(x, dict):
        return None
    issuer, client_id = str(x.get("issuer", "")), str(x.get("clientId", ""))
    if not issuer or not client_id:
        return None
    cp = x.get("callbackPort")
    return XaaIdpSettings(issuer=issuer, client_id=client_id,
                          callback_port=int(cp) if cp is not None else None)


# ── Issuer key normalization ────────────────────────────────────────────────


def issuer_key(issuer: str) -> str:
    """Lowercase host, strip trailing slash, preserve path/query."""
    try:
        p = urlparse(issuer)
        h = (p.hostname or p.netloc).lower()
        path = p.path.rstrip("/")
        base = f"{p.scheme}://{h}"
        if p.port and p.port != {"https": 443, "http": 80}.get(p.scheme, 0):
            base = f"{base}:{p.port}"
        base = f"{base}{path or '/'}"
        if p.query:
            base = f"{base}?{p.query}"
        return base
    except Exception:
        return issuer.rstrip("/")


# ── Secure storage helpers ──────────────────────────────────────────────────


def _store() -> get_secure_storage().__class__:
    return get_secure_storage()


def _load_xaa() -> dict:
    try:
        return json.loads(_store().get("mcpXaaIdp") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _save_xaa(d: dict) -> None:
    _store().set("mcpXaaIdp", json.dumps(d))


def get_cached_idp_id_token(idp_issuer: str = "") -> str | None:
    d = _load_xaa().get("mcpXaaIdp")
    if not isinstance(d, dict):
        return None

    def _ok(v):
        if not isinstance(v, dict):
            return None
        rem = v.get("expiresAt", 0) - int(time.time() * 1000)
        return v.get("idToken") if rem > _EXPIRY_BUFFER * 1000 else None

    if idp_issuer:
        return _ok(d.get(issuer_key(idp_issuer)))
    for v in d.values():
        tok = _ok(v)
        if tok:
            return tok
    return None


def clear_idp_id_token(idp_issuer: str = "") -> None:
    d = _load_xaa()
    m = d.get("mcpXaaIdp", {})
    if not isinstance(m, dict):
        return
    if idp_issuer:
        m.pop(issuer_key(idp_issuer), None)
        d["mcpXaaIdp"] = m
    else:
        d["mcpXaaIdp"] = {}
    _save_xaa(d)


def _save_token(idp_issuer: str, id_token: str, expires_at_ms: float) -> None:
    d = _load_xaa()
    m = d.get("mcpXaaIdp", {})
    if not isinstance(m, dict):
        m = {}
    m[issuer_key(idp_issuer)] = {"idToken": id_token, "expiresAt": int(expires_at_ms)}
    d["mcpXaaIdp"] = m
    _save_xaa(d)


def get_idp_client_secret(idp_issuer: str = "") -> str | None:
    d = _load_xaa().get("mcpXaaIdpConfig")
    if not isinstance(d, dict):
        return None
    if idp_issuer:
        e = d.get(issuer_key(idp_issuer))
        return e.get("clientSecret") if isinstance(e, dict) else None
    for v in d.values():
        if isinstance(v, dict) and v.get("clientSecret"):
            return v["clientSecret"]
    return None


def save_idp_client_secret(idp_issuer: str, client_secret: str) -> dict[str, Any]:
    try:
        d = _load_xaa()
        m = d.get("mcpXaaIdpConfig", {})
        if not isinstance(m, dict):
            m = {}
        m[issuer_key(idp_issuer)] = {"clientSecret": client_secret}
        d["mcpXaaIdpConfig"] = m
        _save_xaa(d)
        return {"success": True}
    except Exception as exc:
        logger.error("Failed to save IdP client secret: %s", exc)
        return {"success": False, "warning": str(exc)}


def clear_idp_client_secret(idp_issuer: str) -> None:
    d = _load_xaa()
    m = d.get("mcpXaaIdpConfig", {})
    if isinstance(m, dict) and issuer_key(idp_issuer) in m:
        del m[issuer_key(idp_issuer)]
        d["mcpXaaIdpConfig"] = m
        _save_xaa(d)


# ── JWT exp (unverified — cache TTL only) ──────────────────────────────────


def _jwt_exp(jwt: str) -> int | None:
    parts = jwt.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "==").decode("utf-8"))
        exp = payload.get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


# ── OIDC discovery ─────────────────────────────────────────────────────────


async def discover_oidc(idp_issuer: str) -> dict[str, str]:
    if not idp_issuer:
        raise OidcDiscoveryError("idp_issuer is required")
    base = idp_issuer + "/" if not idp_issuer.endswith("/") else idp_issuer
    url = urllib.parse.urljoin(base, ".well-known/openid-configuration")

    def _fetch() -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_REQ_TIMEOUT) as resp:
                if resp.status >= 400:
                    raise OidcDiscoveryError(f"HTTP {resp.status}", resp.status)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise OidcDiscoveryError(f"HTTP {e.code}", e.code) from e
        except urllib.error.URLError as e:
            raise OidcDiscoveryError(str(e.reason)) from e
        except json.JSONDecodeError:
            raise OidcDiscoveryError(f"Non-JSON response at {url} (captive portal?)") from None

    body = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    if not isinstance(body, dict):
        raise OidcDiscoveryError(f"Unexpected type: {type(body).__name__}")

    te = body.get("token_endpoint", "")
    if not te:
        raise OidcDiscoveryError("Missing token_endpoint")
    p = urlparse(te)
    if p.scheme != "https" and p.hostname not in ("localhost", "127.0.0.1", "[::1]"):
        raise OidcDiscoveryError(f"Refusing non-HTTPS token endpoint: {te}")

    return {
        "issuer": body.get("issuer", idp_issuer),
        "authorization_endpoint": body.get("authorization_endpoint", ""),
        "token_endpoint": te,
        "jwks_uri": body.get("jwks_uri", ""),
        "registration_endpoint": body.get("registration_endpoint", ""),
    }


# ── PKCE ───────────────────────────────────────────────────────────────────


def _b64url(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str]:
    v = _b64url(secrets.token_bytes(32))
    return v, _b64url(hashlib.sha256(v.encode()).digest())


def _state() -> str:
    return secrets.token_hex(16)


# ── Auth URL ───────────────────────────────────────────────────────────────


def _auth_url(ep: str, cid: str, uri: str, ch: str, st: str, aud: str | None = None) -> str:
    params = {"response_type": "code", "client_id": cid, "redirect_uri": uri,
              "code_challenge": ch, "code_challenge_method": "S256",
              "state": st, "scope": "openid"}
    if aud:
        params["audience"] = aud
    base, _ = urllib.parse.urldefrag(ep)
    return f"{base}{'&' if '?' in base else '?'}{urllib.parse.urlencode(params)}"


# ── Token exchange ────────────────────────────────────────────────────────


async def _exchange(token_ep: str, cid: str, uri: str, code: str,
                    verifier: str, secret: str | None = None) -> dict:
    data = {"grant_type": "authorization_code", "client_id": cid, "code": code,
            "redirect_uri": uri, "code_verifier": verifier}
    if secret:
        data["client_secret"] = secret

    def _post() -> dict:
        req = urllib.request.Request(
            token_ep, data=urllib.parse.urlencode(data).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_REQ_TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            err = {}
            try:
                err = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                pass
            raise IdpTokenError(
                f"Token exchange failed (HTTP {e.code}): "
                f"{err.get('error_description', err.get('error', body[:200]))}", e.code) from e
        except urllib.error.URLError as e:
            raise IdpTokenError(str(e.reason)) from e

    return await asyncio.get_event_loop().run_in_executor(None, _post)


# ── Port discovery ───────────────────────────────────────────────────────


def _port(pref: int | None = None) -> int:
    def free(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p)); return True
            except OSError:
                return False
    if pref is not None and free(pref):
        return pref
    for _ in range(100):
        p = 49152 + secrets.randbelow(16384)
        if free(p):
            return p
    if free(3119):
        return 3119
    raise IdpLoginError("No available ports for callback server")


# ── Loopback callback server ──────────────────────────────────────────────

_HTML_OK = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            b"Connection: close\r\n\r\n<html><body style=\"font-family:sans-serif;"
            b"text-align:center;padding-top:3rem\"><h2 style=\"color:#16a34a\">"
            b"Login Complete</h2><p>You can close this window.</p></body></html>")

_HTML_ERR = (b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\n"
             b"Connection: close\r\n\r\n<html><body style=\"font-family:sans-serif;"
             b"text-align:center;padding-top:3rem\"><h2 style=\"color:#dc2626\">"
             b"Login Failed</h2><p>%b</p></body></html>")


async def _wait_callback(port: int, expected_state: str,
                         on_listening: Callable[[], None]) -> str:
    fut: asyncio.Future[str] = asyncio.Future()

    async def _h(reader, writer):
        if fut.done():
            writer.close(); await writer.wait_closed(); return
        raw = bytearray()
        try:
            while b"\r\n\r\n" not in raw:
                c = await asyncio.wait_for(reader.read(4096), timeout=5)
                if not c:
                    break
                raw.extend(c)
        except asyncio.TimeoutError:
            writer.close(); await writer.wait_closed(); return
        try:
            line = raw.split(b"\r\n")[0].decode(errors="replace")
        except IndexError:
            writer.close(); await writer.wait_closed(); return
        parts = line.split()
        if len(parts) < 2:
            writer.close(); await writer.wait_closed(); return
        qs = parse_qs(urlparse(parts[1]).query)
        if urlparse(parts[1]).path != "/callback":
            writer.write(b"HTTP/1.1 404\r\nContent-Length:0\r\nConnection:close\r\n\r\n")
            writer.close(); await writer.wait_closed(); return

        err = qs.get("error", [None])[0]
        if err:
            desc = qs.get("error_description", [""])[0]
            writer.write(_HTML_ERR % f"{err}: {desc}".encode())
            writer.close(); await writer.wait_closed()
            fut.set_exception(IdpLoginError(f"IdP error: {err}{' - ' + desc if desc else ''}")); return

        if qs.get("state", [""])[0] != expected_state:
            writer.write(_HTML_ERR % b"State mismatch"); writer.close(); await writer.wait_closed()
            fut.set_exception(CallbackStateMismatchError("State mismatch - CSRF?")); return

        code = qs.get("code", [None])[0]
        if not code:
            writer.close(); await writer.wait_closed(); return
        writer.write(_HTML_OK); writer.close(); await writer.wait_closed()
        fut.set_result(code)

    try:
        srv = await asyncio.start_server(_h, host="127.0.0.1", port=port)
    except OSError as e:
        raise IdpLoginError(f"Port {port} in use: {e}") from e

    async with srv:
        try:
            on_listening()
        except Exception:
            logger.exception("on_listening callback failed")
        try:
            return await asyncio.wait_for(fut, timeout=_IDP_TIMEOUT)
        except asyncio.TimeoutError:
            raise CallbackTimeoutError(f"Login timed out after {_IDP_TIMEOUT}s")


# ── Browser opener ────────────────────────────────────────────────────────


def _browser(url: str) -> None:
    import shutil, subprocess
    for cmd in (["open"], ["xdg-open"], ["start"]):
        b = shutil.which(cmd[0])
        if b:
            try:
                subprocess.Popen([b, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                continue
    print(f"\n[XAA IdP] Open this URL in your browser:\n{url}\n", file=sys.stderr)


# ── Main: acquire id_token ───────────────────────────────────────────────


async def acquire_idp_id_token() -> str | None:
    """Run the full OIDC authorization_code + PKCE flow to get an id_token.

    Reads IdP settings from ~/.hare/settings.json; checks the token cache;
    discovers OIDC metadata; starts a loopback callback server; opens the
    browser; exchanges the code for tokens; caches and returns the id_token.
    Returns None if no IdP is configured.
    """
    s = get_xaa_idp_settings()
    if s is None:
        return None

    cached = get_cached_idp_id_token(s.issuer)
    if cached:
        logger.info("Using cached id_token for %s", s.issuer)
        return cached

    logger.info("Starting OIDC login for %s", s.issuer)
    meta = await discover_oidc(s.issuer)
    ae = meta["authorization_endpoint"]
    te = meta["token_endpoint"]
    if not ae or not te:
        raise IdpLoginError(f"Incomplete OIDC metadata for {s.issuer}")

    verifier, challenge = _pkce()
    state = _state()
    port = _port(pref=s.callback_port)
    redirect = f"http://127.0.0.1:{port}/callback"
    url = _auth_url(ae, s.client_id, redirect, challenge, state)

    def _ready() -> None:
        print(f"\n[XAA IdP] Opening browser for IdP login...\n"
              f"Or visit: {url}\n", file=sys.stderr)
        _browser(url)

    code = await _wait_callback(port, state, _ready)
    tokens = await _exchange(te, s.client_id, redirect,
                             code, verifier, get_idp_client_secret(s.issuer))

    id_tok = tokens.get("id_token", "")
    if not id_tok:
        raise IdpTokenError("Token response missing id_token (scope=openid required)")

    exp_jwt = _jwt_exp(id_tok)
    exp_in = tokens.get("expires_in")
    ms = (exp_jwt * 1000 if exp_jwt
          else int(time.time() * 1000) + int(exp_in) * 1000 if isinstance(exp_in, (int, float)) and exp_in > 0
          else int(time.time() * 1000) + 3600_000)
    _save_token(s.issuer, id_tok, ms)
    return id_tok


# ── Convenience: inject pre-signed id_token (testing) ────────────────────


def save_idp_id_token_from_jwt(idp_issuer: str, id_token: str) -> int:
    """Save an externally-obtained id_token; parse JWT exp for TTL. Returns expiresAt ms."""
    exp = _jwt_exp(id_token)
    ms = exp * 1000 if exp else int(time.time() * 1000) + 3600_000
    _save_token(idp_issuer, id_token, ms)
    return ms
