"""macOS Keychain-backed secure storage.

Port of: src/utils/secureStorage/macOsKeychainStorage.ts
"""

from __future__ import annotations

from hare.utils.secure_storage.mac_os_keychain_helpers import (
    KeychainBackend,
    create_default_keychain_backend,
)


class MacOSKeychainStorage:
    def __init__(
        self, service_name: str, backend: KeychainBackend | None = None
    ) -> None:
        self._service = service_name
        self._backend = backend or create_default_keychain_backend()

    def read(self, account: str) -> str | None:
        return self._backend.get_password(self._service, account)

    def write(self, account: str, secret: str) -> None:
        self._backend.set_password(self._service, account, secret)

    def delete(self, account: str) -> None:
        # Stub backend has no delete; real impl would remove item
        self._backend.set_password(self._service, account, "")
