"""
Query source labels for analytics. Port of src/utils/promptCategory.ts.
"""

from __future__ import annotations

from hare.constants.output_styles import OUTPUT_STYLE_CONFIGS
from hare.utils.settings.settings import get_settings

QuerySource = str

DEFAULT_OUTPUT_STYLE_NAME = "default"


def get_query_source_for_agent(
    agent_type: str | None, is_built_in_agent: bool
) -> QuerySource:
    if is_built_in_agent:
        return f"agent:builtin:{agent_type}" if agent_type else "agent:default"
    return "agent:custom"


def get_query_source_for_repl() -> QuerySource:
    settings = get_settings()
    style = (
        settings.get("outputStyle")
        or settings.get("output_style")
        or DEFAULT_OUTPUT_STYLE_NAME
    )
    if style == DEFAULT_OUTPUT_STYLE_NAME:
        return "repl_main_thread"
    if style in OUTPUT_STYLE_CONFIGS:
        return f"repl_main_thread:outputStyle:{style}"
    return "repl_main_thread:outputStyle:custom"
