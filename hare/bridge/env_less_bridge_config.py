"""Port of: src/bridge/envLessBridgeConfig.ts"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class EnvLessBridgeConfig:
    base_url: str = "https://api.anthropic.com"
    version: str = "v2"
    timeout_ms: int = 300000


def get_env_less_bridge_config() -> EnvLessBridgeConfig:
    return EnvLessBridgeConfig()
