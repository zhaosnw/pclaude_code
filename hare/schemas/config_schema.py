"""
Config JSON schema definition.

Port of: src/schemas/configSchema.ts
"""

from __future__ import annotations

from typing import Any

CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": {"type": "string"},
        "smallModel": {"type": "string"},
        "maxTurns": {"type": "number"},
        "systemPrompt": {"type": "string"},
        "appendSystemPrompt": {"type": "string"},
        "allowedTools": {"type": "array", "items": {"type": "string"}},
        "disallowedTools": {"type": "array", "items": {"type": "string"}},
        "permissions": {
            "type": "object",
            "properties": {
                "allow": {"type": "array", "items": {"type": "string"}},
                "deny": {"type": "array", "items": {"type": "string"}},
            },
        },
        "mcpServers": {"type": "object"},
        "customApiKeyResponses": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
}
