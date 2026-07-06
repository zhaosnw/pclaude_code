"""TLS CA certificate loading (`caCerts.ts`)."""

from __future__ import annotations

import os
import ssl
from functools import lru_cache
from pathlib import Path

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import has_node_option


def _default_pem_bundle() -> list[str]:
    ctx = ssl.create_default_context()
    return list(ctx.get_ca_certs(binary_form=False))


@lru_cache(maxsize=1)
def get_ca_certificates() -> list[str] | None:
    """Return PEM strings for custom TLS verify, or None for runtime defaults."""
    use_system_ca = has_node_option("--use-system-ca") or has_node_option(
        "--use-openssl-ca"
    )
    extra_path = os.environ.get("NODE_EXTRA_CA_CERTS")

    log_for_debugging(
        f"CA certs: useSystemCA={use_system_ca}, extraCertsPath={extra_path}"
    )

    if not use_system_ca and not extra_path:
        return None

    certs: list[str] = _default_pem_bundle()

    if use_system_ca:
        log_for_debugging(
            f"CA certs: Loaded {len(certs)} certificates from default verify store (--use-system-ca)",
        )
    else:
        log_for_debugging(
            f"CA certs: Loaded {len(certs)} bundled root certificates as base",
        )

    if extra_path:
        try:
            extra = Path(extra_path).read_text(encoding="utf-8")
            certs.append(extra)
            log_for_debugging(
                f"CA certs: Appended extra certificates from NODE_EXTRA_CA_CERTS ({extra_path})",
            )
        except OSError as e:
            log_for_debugging(
                f"CA certs: Failed to read NODE_EXTRA_CA_CERTS file ({extra_path}): {e}",
                level="error",
            )

    return certs if certs else None


def clear_ca_certs_cache() -> None:
    get_ca_certificates.cache_clear()
    log_for_debugging("Cleared CA certificates cache")
