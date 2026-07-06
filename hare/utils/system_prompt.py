"""Build effective system prompt (port of systemPrompt.ts)."""

from __future__ import annotations

from typing import Any

from hare.utils.system_prompt_type import SystemPrompt, as_system_prompt


def build_effective_system_prompt(
    *,
    main_thread_agent_definition: Any | None,
    tool_use_context: Any,
    custom_system_prompt: str | None,
    default_system_prompt: list[str],
    append_system_prompt: str | None,
    override_system_prompt: str | None = None,
) -> SystemPrompt:
    if override_system_prompt:
        return as_system_prompt(override_system_prompt)

    # Coordinator / proactive branches require coordinator_mode + services — keep default path.
    agent_prompt = None
    if main_thread_agent_definition is not None:
        gsp = getattr(main_thread_agent_definition, "get_system_prompt", None)
        if callable(gsp):
            try:
                agent_prompt = gsp(
                    tool_use_context={
                        "options": getattr(tool_use_context, "options", {})
                    }
                )
            except TypeError:
                agent_prompt = gsp()

    base: list[str] = []
    if agent_prompt:
        base = [agent_prompt]
    elif custom_system_prompt:
        base = [custom_system_prompt]
    else:
        base = list(default_system_prompt)

    out = base + ([append_system_prompt] if append_system_prompt else [])
    return as_system_prompt("\n\n".join(out))
