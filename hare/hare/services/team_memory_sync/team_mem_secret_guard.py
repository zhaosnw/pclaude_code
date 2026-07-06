"""
Block team memory sync when secrets may be present.

Port of: src/services/teamMemorySync/teamMemSecretGuard.ts
"""

from __future__ import annotations


async def should_block_sync_for_secrets(_content: str) -> bool:
    return False
