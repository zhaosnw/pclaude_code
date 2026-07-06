"""Sandbox UI helper strings and formatting.

Port of: src/utils/sandbox/sandbox-ui-utils.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SandboxUiCopy:
    title: str = "Sandbox"
    description: str = "Commands run in an isolated environment."


def get_sandbox_banner_text(enabled: bool) -> str:
    return "Sandbox: on" if enabled else "Sandbox: off"
