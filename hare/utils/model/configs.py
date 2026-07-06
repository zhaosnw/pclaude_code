"""
Per-provider canonical model IDs.

Port of: src/utils/model/configs.ts
"""

from __future__ import annotations

from typing import Literal

APIProvider = Literal["firstParty", "bedrock", "vertex", "foundry"]
ModelConfig = dict[APIProvider, str]

CLAUDE_3_7_SONNET_CONFIG: ModelConfig = {
    "firstParty": "hare-3-7-sonnet-20250219",
    "bedrock": "us.anthropic.hare-3-7-sonnet-20250219-v1:0",
    "vertex": "hare-3-7-sonnet@20250219",
    "foundry": "hare-3-7-sonnet",
}

CLAUDE_3_5_V2_SONNET_CONFIG: ModelConfig = {
    "firstParty": "hare-3-5-sonnet-20241022",
    "bedrock": "anthropic.hare-3-5-sonnet-20241022-v2:0",
    "vertex": "hare-3-5-sonnet-v2@20241022",
    "foundry": "hare-3-5-sonnet",
}

CLAUDE_3_5_HAIKU_CONFIG: ModelConfig = {
    "firstParty": "hare-3-5-haiku-20241022",
    "bedrock": "us.anthropic.hare-3-5-haiku-20241022-v1:0",
    "vertex": "hare-3-5-haiku@20241022",
    "foundry": "hare-3-5-haiku",
}

CLAUDE_HAIKU_4_5_CONFIG: ModelConfig = {
    "firstParty": "claude-haiku-4-5-20251001",
    "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "vertex": "claude-haiku-4-5@20251001",
    "foundry": "claude-haiku-4-5",
}

CLAUDE_SONNET_4_CONFIG: ModelConfig = {
    "firstParty": "claude-sonnet-4-20250514",
    "bedrock": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "vertex": "claude-sonnet-4@20250514",
    "foundry": "claude-sonnet-4",
}

CLAUDE_SONNET_4_5_CONFIG: ModelConfig = {
    "firstParty": "claude-sonnet-4-5-20250929",
    "bedrock": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "vertex": "claude-sonnet-4-5@20250929",
    "foundry": "claude-sonnet-4-5",
}

CLAUDE_OPUS_4_CONFIG: ModelConfig = {
    "firstParty": "claude-opus-4-20250514",
    "bedrock": "us.anthropic.claude-opus-4-20250514-v1:0",
    "vertex": "claude-opus-4@20250514",
    "foundry": "claude-opus-4",
}

CLAUDE_OPUS_4_1_CONFIG: ModelConfig = {
    "firstParty": "claude-opus-4-1-20250805",
    "bedrock": "us.anthropic.claude-opus-4-1-20250805-v1:0",
    "vertex": "claude-opus-4-1@20250805",
    "foundry": "claude-opus-4-1",
}

CLAUDE_OPUS_4_5_CONFIG: ModelConfig = {
    "firstParty": "claude-opus-4-5-20251101",
    "bedrock": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "vertex": "claude-opus-4-5@20251101",
    "foundry": "claude-opus-4-5",
}

CLAUDE_OPUS_4_6_CONFIG: ModelConfig = {
    "firstParty": "claude-opus-4-6",
    "bedrock": "us.anthropic.claude-opus-4-6-v1",
    "vertex": "claude-opus-4-6",
    "foundry": "claude-opus-4-6",
}

CLAUDE_SONNET_4_6_CONFIG: ModelConfig = {
    "firstParty": "claude-sonnet-4-6",
    "bedrock": "us.anthropic.claude-sonnet-4-6",
    "vertex": "claude-sonnet-4-6",
    "foundry": "claude-sonnet-4-6",
}

ALL_MODEL_CONFIGS: dict[str, ModelConfig] = {
    "haiku35": CLAUDE_3_5_HAIKU_CONFIG,
    "haiku45": CLAUDE_HAIKU_4_5_CONFIG,
    "sonnet35": CLAUDE_3_5_V2_SONNET_CONFIG,
    "sonnet37": CLAUDE_3_7_SONNET_CONFIG,
    "sonnet40": CLAUDE_SONNET_4_CONFIG,
    "sonnet45": CLAUDE_SONNET_4_5_CONFIG,
    "sonnet46": CLAUDE_SONNET_4_6_CONFIG,
    "opus40": CLAUDE_OPUS_4_CONFIG,
    "opus41": CLAUDE_OPUS_4_1_CONFIG,
    "opus45": CLAUDE_OPUS_4_5_CONFIG,
    "opus46": CLAUDE_OPUS_4_6_CONFIG,
}

CANONICAL_MODEL_IDS: tuple[str, ...] = tuple(
    c["firstParty"] for c in ALL_MODEL_CONFIGS.values()
)

CANONICAL_ID_TO_KEY: dict[str, str] = {
    cfg["firstParty"]: key for key, cfg in ALL_MODEL_CONFIGS.items()
}
