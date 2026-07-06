"""Cloud auth refresh status singleton (`awsAuthStatusManager.ts`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from hare.utils.signal import Signal, create_signal


@dataclass
class AwsAuthStatus:
    is_authenticating: bool = False
    output: list[str] = field(default_factory=list)
    error: str | None = None


class AwsAuthStatusManager:
    _instance: AwsAuthStatusManager | None = None

    def __init__(self) -> None:
        self._status = AwsAuthStatus()
        self._changed: Signal = create_signal()

    @classmethod
    def get_instance(cls) -> AwsAuthStatusManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_status(self) -> AwsAuthStatus:
        return AwsAuthStatus(
            is_authenticating=self._status.is_authenticating,
            output=list(self._status.output),
            error=self._status.error,
        )

    def start_authentication(self) -> None:
        self._status = AwsAuthStatus(is_authenticating=True, output=[])
        self._notify()

    def add_output(self, line: str) -> None:
        self._status.output.append(line)
        self._notify()

    def set_error(self, error: str) -> None:
        self._status.error = error
        self._notify()

    def end_authentication(self, success: bool) -> None:
        if success:
            self._status = AwsAuthStatus()
        else:
            self._status.is_authenticating = False
        self._notify()

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._changed.subscribe(listener)

    @staticmethod
    def reset() -> None:
        AwsAuthStatusManager._instance = None

    def _notify(self) -> None:
        self._changed.emit()
