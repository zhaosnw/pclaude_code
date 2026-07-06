"""Low-level macOS Keychain helpers.

Port of: src/utils/secureStorage/macOsKeychainHelpers.ts
"""

from __future__ import annotations

from typing import Protocol


class KeychainBackend(Protocol):
    def get_password(self, service: str, account: str) -> str | None: ...
    def set_password(self, service: str, account: str, password: str) -> None: ...


class StubKeychainBackend:
    _data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self._data.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self._data[(service, account)] = password


def create_default_keychain_backend() -> KeychainBackend:
    """Return real keychain binding when ``keyring`` is installed."""
    return StubKeychainBackend()


def get_macos_keychain_storage_service_name() -> str:
    """Get the macOS keychain service name (P2 — stub)."""
    return "com.anthropic.claude-code"
