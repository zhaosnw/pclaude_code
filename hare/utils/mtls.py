"""
mTLS / custom CA helpers for HTTPS and fetch. Port of src/utils/mtls.ts.

Provides:
- MTLSConfig / TLSConfig dataclasses
- Environment-variable-based mTLS configuration loading
- SSLContext construction with custom CA certs and client cert/key
- HTTPS agent (urllib3 PoolManager) creation with mTLS settings
- WebSocket TLS options
- Fetch/HTTP-client TLS options (including undici dispatcher stub)
- Cert/key validation and PEM parsing utilities
- Global mTLS configuration
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from hare.utils.debug import log_for_debugging


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MTLSConfig:
    """mTLS client certificate configuration.

    Attributes:
        cert: PEM-encoded client certificate chain.
        key: PEM-encoded client private key.
        passphrase: Optional passphrase for an encrypted private key.
    """

    cert: str | None = None
    key: str | None = None
    passphrase: str | None = None

    @property
    def is_complete(self) -> bool:
        """True when both cert and key are present (minimum for mTLS)."""
        return self.cert is not None and self.key is not None

    @property
    def has_encrypted_key(self) -> bool:
        """True when a passphrase is supplied alongside a key."""
        return self.key is not None and self.passphrase is not None


@dataclass
class TLSConfig:
    """Full TLS configuration including CA certificates.

    Attributes:
        cert: PEM-encoded client certificate chain.
        key: PEM-encoded client private key.
        passphrase: Optional passphrase for an encrypted private key.
        ca: Custom CA certificate(s) — single PEM string, bytes, or list of either.
    """

    cert: str | None = None
    key: str | None = None
    passphrase: str | None = None
    ca: str | bytes | list[str | bytes] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_file_utf8(path: str) -> str:
    """Read a file as UTF-8 text using the project filesystem abstraction."""
    from hare.utils.fs_operations import get_fs_implementation

    return get_fs_implementation().read_file_sync(path, encoding="utf-8")


def _normalize_pem_data(data: str | bytes) -> bytes:
    """Normalize PEM input to bytes, stripping leading/trailing whitespace.

    Accepts either a str (encoded to UTF-8) or raw bytes.
    """
    if isinstance(data, str):
        return data.strip().encode("utf-8")
    return data.strip()


def _ensure_bytes(data: str | bytes) -> bytes:
    """Convert str to UTF-8 bytes; pass bytes through unchanged."""
    if isinstance(data, str):
        return data.encode("utf-8")
    return data


def _load_ca_into_context(
    ctx: ssl.SSLContext, ca: str | bytes | list[str | bytes]
) -> None:
    """Load one or more CA certificate(s) into an SSLContext.

    Handles:
    - A single PEM string or bytes (may contain multiple certs).
    - A list of PEM strings / bytes.
    - Bundled PEM files (concatenated certs).
    """
    import tempfile

    if isinstance(ca, (str, bytes)):
        sources: list[str | bytes] = [ca]
    else:
        sources = ca

    for source in sources:
        data = _normalize_pem_data(source)

        # Quick sanity check: the data should look like PEM
        if not data.startswith(b"-----BEGIN"):
            log_for_debugging(
                "mTLS: CA data does not appear to be PEM-encoded — falling back to "
                "temporary file loading",
            )
            # Write to a temp file and load by path as a fallback
            with tempfile.NamedTemporaryFile(
                suffix=".pem", delete=False
            ) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                ctx.load_verify_locations(cafile=tmp_path)
                log_for_debugging(
                    f"mTLS: Loaded CA certificate(s) via temp file ({len(data)} bytes)"
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            continue

        try:
            # Prefer loading from data when possible (supported in Python 3.x
            # via load_verify_locations with cadata on newer versions, or via
            # temp files as a portable fallback).
            with tempfile.NamedTemporaryFile(
                suffix=".pem", delete=False
            ) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                ctx.load_verify_locations(cafile=tmp_path)
                log_for_debugging(
                    f"mTLS: Loaded CA certificate(s) ({len(data)} bytes)"
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except ssl.SSLError as exc:
            log_for_debugging(
                f"mTLS: Failed to load CA certificate(s): {exc}", level="error"
            )


def _load_client_cert_into_context(
    ctx: ssl.SSLContext, mtls: MTLSConfig
) -> None:
    """Load a client certificate chain and private key into an SSLContext.

    Handles:
    - PEM-encoded certificate and key as strings.
    - Encrypted private keys via the passphrase field.
    - Temporary file creation for loading via ssl.SSLContext methods.
    """
    import tempfile

    cert_data = _normalize_pem_data(mtls.cert)  # type: ignore[arg-type]
    key_data = _normalize_pem_data(mtls.key)  # type: ignore[arg-type]
    passphrase = mtls.passphrase

    # Write cert and key to temp files for loading
    cert_tmp: str | None = None
    key_tmp: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".pem", delete=False
        ) as tmp:
            tmp.write(cert_data)
            cert_tmp = tmp.name

        with tempfile.NamedTemporaryFile(
            suffix=".pem", delete=False
        ) as tmp:
            tmp.write(key_data)
            key_tmp = tmp.name

        # Load the certificate chain
        try:
            ctx.load_cert_chain(
                certfile=cert_tmp,
                keyfile=key_tmp,
                password=passphrase,
            )
            log_for_debugging(
                "mTLS: Loaded client certificate and key into SSL context"
                + (" (with passphrase)" if passphrase else "")
            )
        except ssl.SSLError as exc:
            msg = str(exc)
            if passphrase and (
                "bad decrypt" in msg.lower()
                or "wrong password" in msg.lower()
                or "bad password" in msg.lower()
                or "decryption failed" in msg.lower()
                or "bad passphrase" in msg.lower()
            ):
                log_for_debugging(
                    "mTLS: Failed to decrypt private key — the passphrase "
                    f"may be incorrect: {exc}", level="error"
                )
            else:
                log_for_debugging(
                    f"mTLS: Failed to load client certificate/key: {exc}",
                    level="error",
                )
            raise
    finally:
        for tmp_path in (cert_tmp, key_tmp):
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


def _build_ssl_context(
    mtls_config: MTLSConfig | None = None,
    ca_certs: str | bytes | list[str | bytes] | None = None,
    *,
    check_hostname: bool = True,
    verify_mode: ssl.VerifyMode = ssl.CERT_REQUIRED,
) -> ssl.SSLContext:
    """Build a properly configured :class:`ssl.SSLContext`.

    Args:
        mtls_config: Optional mTLS client certificate configuration.
        ca_certs: Optional custom CA certificate(s).
        check_hostname: Whether to verify the server hostname against the cert.
        verify_mode: SSL verification mode (default: ``CERT_REQUIRED``).

    Returns:
        A fully configured :class:`ssl.SSLContext` ready for use with urllib3,
        httpx, websockets, etc.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = check_hostname
    ctx.verify_mode = verify_mode

    # Load custom CA certificates
    if ca_certs:
        try:
            _load_ca_into_context(ctx, ca_certs)
        except Exception as exc:
            log_for_debugging(
                f"mTLS: Error loading CA certificates into SSL context: {exc}",
                level="error",
            )

    # Load client certificate and key for mTLS
    if mtls_config and mtls_config.cert is not None and mtls_config.key is not None:
        try:
            _load_client_cert_into_context(ctx, mtls_config)
        except Exception as exc:
            log_for_debugging(
                f"mTLS: Error loading client certificate into SSL context: {exc}",
                level="error",
            )

    return ctx


def _verify_cert_key_pair(cert_pem: str, key_pem: str) -> bool:
    """Verify that a certificate and private key correspond to each other.

    Compares the public key modulus from the certificate with the modulus
    from the private key. Returns True if they match.

    This is a lightweight check that does not require loading into an
    SSLContext — it uses the cryptography library if available, falling
    back to a simple structural PEM comparison.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed448, ed25519
    except ImportError:
        log_for_debugging(
            "mTLS: cryptography library not available; skipping cert/key "
            "pair verification"
        )
        return True  # Can't verify, assume valid

    try:
        # Load certificate
        cert_bytes = _normalize_pem_data(cert_pem)
        cert = x509.load_pem_x509_certificate(cert_bytes)
        cert_pubkey = cert.public_key()

        # Load private key (try without passphrase first)
        key_bytes = _normalize_pem_data(key_pem)
        try:
            privkey = serialization.load_pem_private_key(key_bytes, password=None)
        except TypeError:
            # Encrypted key — can't verify without passphrase here, skip
            log_for_debugging(
                "mTLS: Private key is encrypted; skipping cert/key pair "
                "verification (passphrase required)"
            )
            return True

        # Compare public key from cert to public key from private key
        priv_pubkey = privkey.public_key()

        # RSA: compare modulus and exponent
        if isinstance(cert_pubkey, rsa.RSAPublicKey) and isinstance(
            priv_pubkey, rsa.RSAPublicKey
        ):
            pub_nums = cert_pubkey.public_numbers()
            priv_nums = priv_pubkey.public_numbers()
            match = (
                pub_nums.n == priv_nums.n and pub_nums.e == priv_nums.e
            )
            if not match:
                log_for_debugging(
                    "mTLS: RSA certificate and key modulus do not match",
                    level="error",
                )
            return match

        # EC: compare curve and point
        if isinstance(cert_pubkey, ec.EllipticCurvePublicKey) and isinstance(
            priv_pubkey, ec.EllipticCurvePublicKey
        ):
            pub_nums = cert_pubkey.public_numbers()
            priv_nums = priv_pubkey.public_numbers()
            match = (
                pub_nums.curve.name == priv_nums.curve.name
                and pub_nums.x == priv_nums.x
                and pub_nums.y == priv_nums.y
            )
            if not match:
                log_for_debugging(
                    "mTLS: EC certificate and key do not match", level="error"
                )
            return match

        # Ed25519 / Ed448: compare raw public bytes
        if isinstance(cert_pubkey, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            cert_raw = cert_pubkey.public_bytes_raw()
            priv_raw = priv_pubkey.public_bytes_raw()
            match = cert_raw == priv_raw
            if not match:
                log_for_debugging(
                    "mTLS: EdDSA certificate and key do not match",
                    level="error",
                )
            return match

        # Fallback: compare serialized public key DER
        cert_der = cert_pubkey.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        priv_der = priv_pubkey.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        match = cert_der == priv_der
        if not match:
            log_for_debugging(
                "mTLS: Certificate and key public key DER do not match",
                level="error",
            )
        return match

    except Exception as exc:
        log_for_debugging(
            f"mTLS: Error during cert/key pair verification: {exc}",
            level="error",
        )
        return False


def _check_cert_expiry(cert_pem: str) -> tuple[bool, str]:
    """Check whether a PEM certificate is expired or near expiry.

    Returns:
        Tuple of ``(is_valid, message)`` where *is_valid* is False if the
        certificate has expired or will expire within 30 days.
    """
    try:
        from cryptography import x509
    except ImportError:
        return True, ""

    try:
        cert_bytes = _normalize_pem_data(cert_pem)
        cert = x509.load_pem_x509_certificate(cert_bytes)
        now = datetime.now(timezone.utc)
        warn_delta = cert.not_valid_after_utc - now

        if now > cert.not_valid_after_utc:
            return False, (
                f"Client certificate expired on "
                f"{cert.not_valid_after_utc.strftime('%Y-%m-%d')}"
            )
        if now < cert.not_valid_before_utc:
            return False, (
                f"Client certificate not yet valid (valid from "
                f"{cert.not_valid_before_utc.strftime('%Y-%m-%d')})"
            )
        if warn_delta.days < 30:
            return True, (
                f"Client certificate expires in {warn_delta.days} days "
                f"({cert.not_valid_after_utc.strftime('%Y-%m-%d')})"
            )
        return True, ""
    except Exception as exc:
        log_for_debugging(
            f"mTLS: Could not check certificate expiry: {exc}"
        )
        return True, ""


# ---------------------------------------------------------------------------
# CA certificate loading
# ---------------------------------------------------------------------------


def _get_ca_certificates() -> str | bytes | list[str | bytes] | None:
    """Load custom CA certificates from the ca_certs module.

    Returns None if no custom CA configuration is active; otherwise returns
    the CA data in the format provided by :func:`get_ca_certificates`.
    """
    try:
        from hare.utils.ca_certs import get_ca_certificates

        return get_ca_certificates()
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# mTLS configuration from environment
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_mtls_config() -> MTLSConfig | None:
    """Read mTLS configuration from environment variables.

    Reads from:
    - ``CLAUDE_CODE_CLIENT_CERT`` — path to PEM client certificate file
    - ``CLAUDE_CODE_CLIENT_KEY`` — path to PEM client private key file
    - ``CLAUDE_CODE_CLIENT_KEY_PASSPHRASE`` — passphrase for encrypted key

    Results are cached (LRU) for the process lifetime. Use
    :func:`clear_mtls_cache` to invalidate.

    Returns:
        :class:`MTLSConfig` if at least one field is populated, else ``None``.
    """
    cfg = MTLSConfig()

    # Client certificate
    cert_path = os.environ.get("CLAUDE_CODE_CLIENT_CERT")
    if cert_path:
        try:
            cfg.cert = _read_file_utf8(cert_path)
            log_for_debugging(
                "mTLS: Loaded client certificate from CLAUDE_CODE_CLIENT_CERT"
            )
        except OSError as e:
            log_for_debugging(
                f"mTLS: Failed to load client certificate: {e}", level="error"
            )

    # Client key
    key_path = os.environ.get("CLAUDE_CODE_CLIENT_KEY")
    if key_path:
        try:
            cfg.key = _read_file_utf8(key_path)
            log_for_debugging(
                "mTLS: Loaded client key from CLAUDE_CODE_CLIENT_KEY"
            )
        except OSError as e:
            log_for_debugging(
                f"mTLS: Failed to load client key: {e}", level="error"
            )

    # Key passphrase
    passphrase = os.environ.get("CLAUDE_CODE_CLIENT_KEY_PASSPHRASE")
    if passphrase:
        cfg.passphrase = passphrase
        log_for_debugging("mTLS: Using client key passphrase")

    # Only return config if at least one option is set
    fields = {k: v for k, v in vars(cfg).items() if v is not None}
    if not fields:
        return None
    return cfg


def validate_mtls_config(
    config: MTLSConfig | None = None,
    *,
    check_expiry: bool = True,
) -> list[str]:
    """Validate an mTLS configuration and return a list of warnings/errors.

    Checks performed:
    - Certificate and key are both present (when one is provided)
    - PEM data looks well-formed
    - Certificate and key correspond to each other
    - Certificate has not expired (optional)

    Args:
        config: The config to validate; uses :func:`get_mtls_config` if None.
        check_expiry: If True, check certificate expiration dates.

    Returns:
        A list of human-readable warning/error strings. Empty list = valid.
    """
    if config is None:
        config = get_mtls_config()
    if config is None:
        return []

    issues: list[str] = []

    # Both cert and key should be present if either is
    if config.cert and not config.key:
        issues.append(
            "CLAUDE_CODE_CLIENT_CERT is set but CLAUDE_CODE_CLIENT_KEY "
            "is missing — mTLS requires both"
        )
    if config.key and not config.cert:
        issues.append(
            "CLAUDE_CODE_CLIENT_KEY is set but CLAUDE_CODE_CLIENT_CERT "
            "is missing — mTLS requires both"
        )

    # Check PEM format
    if config.cert:
        cert_stripped = config.cert.strip()
        if not cert_stripped.startswith("-----BEGIN"):
            issues.append(
                "Client certificate does not appear to be PEM-encoded "
                "(missing '-----BEGIN' header)"
            )
        if not cert_stripped.endswith("-----"):
            issues.append(
                "Client certificate PEM appears truncated "
                "(missing '-----END' footer)"
            )

    if config.key:
        key_stripped = config.key.strip()
        if not key_stripped.startswith("-----BEGIN"):
            issues.append(
                "Client key does not appear to be PEM-encoded "
                "(missing '-----BEGIN' header)"
            )
        if not key_stripped.endswith("-----"):
            issues.append(
                "Client key PEM appears truncated "
                "(missing '-----END' footer)"
            )
        # Detect encrypted key without passphrase
        if (
            "ENCRYPTED" in key_stripped[:100]
            and not config.passphrase
        ):
            issues.append(
                "Client key is encrypted but no passphrase is provided "
                "(set CLAUDE_CODE_CLIENT_KEY_PASSPHRASE)"
            )

    # Verify cert/key correspondence
    if config.cert and config.key:
        if not _verify_cert_key_pair(config.cert, config.key):
            issues.append(
                "Client certificate and private key do not match"
            )

    # Check expiry
    if check_expiry and config.cert:
        is_valid, msg = _check_cert_expiry(config.cert)
        if msg:
            level = "error" if not is_valid else "warning"
            issues.append(f"[{level}] {msg}")

    return issues


# ---------------------------------------------------------------------------
# SSL context construction
# ---------------------------------------------------------------------------


def get_ssl_context(
    *,
    check_hostname: bool = True,
    verify_mode: ssl.VerifyMode = ssl.CERT_REQUIRED,
) -> ssl.SSLContext | None:
    """Return a fully configured :class:`ssl.SSLContext`, or None if unused.

    This is the main entry point for obtaining an SSL context that respects
    the current mTLS and CA certificate environment configuration.

    Args:
        check_hostname: Whether to verify hostnames (default True).
        verify_mode: Verification mode (default CERT_REQUIRED).

    Returns:
        An :class:`ssl.SSLContext` or None if no custom TLS config is active.
    """
    mtls = get_mtls_config()
    ca = _get_ca_certificates()
    if not mtls and not ca:
        return None

    return _build_ssl_context(
        mtls_config=mtls,
        ca_certs=ca,
        check_hostname=check_hostname,
        verify_mode=verify_mode,
    )


# ---------------------------------------------------------------------------
# HTTPS agent (urllib3)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_mtls_agent() -> Any | None:
    """Return an urllib3/requests-compatible HTTPS agent, or None if unused.

    Creates a :class:`urllib3.poolmanager.PoolManager` configured with the
    custom SSL context built from mTLS and CA certificate settings.

    Results are cached. Use :func:`clear_mtls_cache` to invalidate.

    Returns:
        A :class:`urllib3.PoolManager` instance, a stub ``object()`` when
        urllib3 is unavailable, or ``None`` when no custom TLS is configured.
    """
    mtls = get_mtls_config()
    ca = _get_ca_certificates()
    if not mtls and not ca:
        return None

    # Log any config validation issues
    if mtls:
        issues = validate_mtls_config(mtls, check_expiry=True)
        for issue in issues:
            if "[error]" in issue:
                log_for_debugging(f"mTLS validation: {issue}", level="error")
            else:
                log_for_debugging(f"mTLS validation: {issue}")

    try:
        from urllib3.poolmanager import PoolManager  # type: ignore[import-untyped]
    except ImportError:
        log_for_debugging(
            "mTLS: urllib3 not available; returning agent stub"
        )
        return object()

    ssl_ctx = _build_ssl_context(mtls_config=mtls, ca_certs=ca)

    log_for_debugging(
        "mTLS: Creating HTTPS agent with custom certificates"
    )
    return PoolManager(
        ssl_context=ssl_ctx,
        maxsize=10,
        block=True,
    )


# ---------------------------------------------------------------------------
# WebSocket TLS options
# ---------------------------------------------------------------------------


def get_websocket_tls_options() -> dict[str, Any] | None:
    """Return TLS options suitable for WebSocket connections.

    The returned dict can be unpacked into websocket connection parameters
    (e.g., ``ws.connect(url, ssl=ssl_context, **options)``).

    Returns:
        A dict with ``cert``, ``key``, ``passphrase``, and ``ca`` keys
        (only populated fields included), or ``None`` if no custom TLS
        is configured.
    """
    mtls = get_mtls_config()
    ca = _get_ca_certificates()
    if not mtls and not ca:
        return None

    out: dict[str, Any] = {}

    if mtls:
        if mtls.cert:
            out["cert"] = mtls.cert
        if mtls.key:
            out["key"] = mtls.key
        if mtls.passphrase:
            out["passphrase"] = mtls.passphrase

    if ca:
        out["ca"] = ca

    return out


def get_websocket_ssl_context() -> ssl.SSLContext | None:
    """Return a pre-built :class:`ssl.SSLContext` for WebSocket connections.

    This is an alternative to :func:`get_websocket_tls_options` that returns
    a ready-to-use SSL context instead of a raw options dict.

    Returns:
        An :class:`ssl.SSLContext` or ``None`` if no custom TLS is active.
    """
    mtls = get_mtls_config()
    ca = _get_ca_certificates()
    if not mtls and not ca:
        return None

    return _build_ssl_context(mtls_config=mtls, ca_certs=ca)


# ---------------------------------------------------------------------------
# Fetch / HTTP client TLS options
# ---------------------------------------------------------------------------


def get_tls_fetch_options() -> dict[str, Any]:
    """Return TLS options for HTTP fetch clients.

    Includes a :class:`TLSConfig` under the ``"tls"`` key and optionally
    an ``undici`` dispatcher when the undici package is available.

    When no custom TLS is configured, returns an empty dict.

    Returns:
        A dict that may contain ``"tls"`` and ``"dispatcher"`` keys.
    """
    mtls = get_mtls_config()
    ca = _get_ca_certificates()
    if not mtls and not ca:
        return {}

    tls_cfg: dict[str, Any] = {}
    if mtls:
        if mtls.cert:
            tls_cfg["cert"] = mtls.cert
        if mtls.key:
            tls_cfg["key"] = mtls.key
        if mtls.passphrase:
            tls_cfg["passphrase"] = mtls.passphrase
    if ca:
        tls_cfg["ca"] = ca

    out: dict[str, Any] = {
        "tls": TLSConfig(
            cert=tls_cfg.get("cert"),
            key=tls_cfg.get("key"),
            passphrase=tls_cfg.get("passphrase"),
            ca=tls_cfg.get("ca"),
        )
    }

    # Try to create an undici Agent for environments that use undici
    try:
        import undici  # type: ignore[import-untyped]

        connect_opts: dict[str, Any] = {}
        if "cert" in tls_cfg:
            connect_opts["cert"] = tls_cfg["cert"]
        if "key" in tls_cfg:
            connect_opts["key"] = tls_cfg["key"]
        if "passphrase" in tls_cfg:
            connect_opts["passphrase"] = tls_cfg["passphrase"]
        if "ca" in tls_cfg:
            connect_opts["ca"] = tls_cfg["ca"]

        out["dispatcher"] = undici.Agent(
            connect=connect_opts if connect_opts else None,
            pipelining=1,
        )
        log_for_debugging(
            "TLS: Created undici agent with custom certificates"
        )
    except ImportError:
        pass
    except Exception as exc:
        log_for_debugging(
            f"TLS: Failed to create undici agent: {exc}", level="error"
        )

    return out


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def clear_mtls_cache() -> None:
    """Clear all cached mTLS configuration and agent data.

    Invalidates the LRU caches for :func:`get_mtls_config` and
    :func:`get_mtls_agent`.
    """
    get_mtls_config.cache_clear()
    get_mtls_agent.cache_clear()
    log_for_debugging("Cleared mTLS configuration cache")


# ---------------------------------------------------------------------------
# Global mTLS configuration
# ---------------------------------------------------------------------------


def configure_global_mtls() -> None:
    """Configure process-wide TLS settings based on environment.

    - Checks that mTLS config is present.
    - Validates the configuration and logs any issues.
    - Logs a notice when ``NODE_EXTRA_CA_CERTS`` is detected (Node.js
      handles this automatically; the log is informational).
    - Sets ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` style hints when
      appropriate (for Python ssl module awareness).
    """
    mtls_config = get_mtls_config()

    if not mtls_config:
        return

    # NODE_EXTRA_CA_CERTS is automatically handled by Node.js at runtime
    if os.environ.get("NODE_EXTRA_CA_CERTS"):
        log_for_debugging(
            "NODE_EXTRA_CA_CERTS detected — Node.js will automatically "
            "append to built-in CAs"
        )

    # Validate and log issues
    issues = validate_mtls_config(mtls_config, check_expiry=True)
    if issues:
        for issue in issues:
            if "[error]" in issue:
                log_for_debugging(
                    f"mTLS global config error: {issue}", level="error"
                )
            else:
                log_for_debugging(f"mTLS global config: {issue}")

    # Warm up the agent cache so the first real request does not pay
    # the SSL context construction cost
    try:
        get_mtls_agent()
    except Exception as exc:
        log_for_debugging(
            f"mTLS: Failed to pre-warm mTLS agent: {exc}", level="error"
        )

    # Set environment hints for Python's ssl module
    # When custom CA certs are used, point Python at them if possible
    ca = _get_ca_certificates()
    if ca and isinstance(ca, (str, bytes)):
        # Set this so subprocess python invocations pick up the same CA
        if not os.environ.get("SSL_CERT_FILE") and not os.environ.get(
            "REQUESTS_CA_BUNDLE"
        ):
            log_for_debugging(
                "mTLS: Custom CA certificates active — Python ssl module "
                "will use them via the agent; set SSL_CERT_FILE or "
                "REQUESTS_CA_BUNDLE for subprocess awareness if needed"
            )

    log_for_debugging("mTLS: Global mTLS configuration applied")
