"""
/insights command - generate a report analyzing your sessions.

Port of: src/commands/insights.ts

Analyzes session logs and generates insights about usage patterns,
productivity trends, common tasks, and areas for improvement.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "insights"
DESCRIPTION = "Generate a report analyzing your Claude Code sessions"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Generate session insights report."""
    get_session_logs = context.get("get_session_logs")
    get_session_analytics = context.get("get_session_analytics")

    if get_session_analytics:
        try:
            analytics = await get_session_analytics()
            return {
                "type": "text",
                "value": (
                    "## Session Insights\n\n"
                    f"**Total sessions:** {analytics.get('total_sessions', 0)}\n"
                    f"**Total messages:** {analytics.get('total_messages', 0):,}\n"
                    f"**Total tokens:** {analytics.get('total_tokens', 0):,}\n"
                    f"**Active days:** {analytics.get('active_days', 0)}\n\n"
                    f"**Most used commands:** {', '.join(analytics.get('top_commands', []))}\n"
                    f"**Most used tools:** {', '.join(analytics.get('top_tools', []))}\n\n"
                    "*Run with `/extra-usage` for detailed token breakdown.*"
                ),
            }
        except Exception:
            pass

    if get_session_logs:
        logs = await get_session_logs()
        if logs:
            return {
                "type": "text",
                "value": (
                    "## Session Insights\n\n"
                    f"**Total sessions:** {len(logs)}\n"
                    f"**Recent activity:** {logs[0].get('firstPrompt', 'N/A') if logs else 'N/A'}\n\n"
                    "For detailed analytics, ensure analytics collection is enabled."
                ),
            }

    return {
        "type": "text",
        "value": (
            "## Session Insights\n\n"
            "Session insights are generated from your conversation history.\n"
            "Run more sessions to see patterns and trends.\n\n"
            "For detailed analytics, use `/stats` and `/extra-usage`."
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
