"""Slack channel autocomplete for slash commands.

Port of: src/utils/suggestions/slackChannelSuggestions.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlackChannel:
    id: str
    name: str


async def suggest_slack_channels(_query: str) -> list[SlackChannel]:
    return []
