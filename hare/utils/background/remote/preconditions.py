"""Preconditions for starting remote background work.

Port of: src/utils/background/remote/preconditions.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RemotePreconditions:
    git_clean: bool = True
    network_ok: bool = True


def check_remote_preconditions() -> RemotePreconditions:
    return RemotePreconditions()
