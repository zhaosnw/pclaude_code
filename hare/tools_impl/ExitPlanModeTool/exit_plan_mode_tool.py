"""
ExitPlanModeTool — exit plan mode, present plan for user approval.

Port of: src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts

Presents the implementation plan and returns to normal execution mode
where all tools are available.
"""

from __future__ import annotations
from typing import Any

TOOL_NAME = "ExitPlanMode"
DESCRIPTION = (
    "Exit plan mode — present your implementation plan for user approval "
    "and return to normal execution mode with full tool access."
)


def input_schema() -> dict[str, Any]:
    # 2.1.88's internal schema is `allowedPrompts` only — the plan is read from
    # the on-disk plan file (utils/plans.ts getPlan), and `plan` is injected
    # into the SDK-facing schema by normalizeToolInput (so it is optional, not
    # required). We keep `plan` advertised (the model usually supplies it) but
    # optional, with disk fallback in call().
    return {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "The complete implementation plan to present to the user, "
                "including the files to modify, the approach, and any trade-offs. "
                "If omitted, the plan is read from the session plan file on disk.",
            },
            "allowed_prompts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "enum": ["Bash"]},
                        "prompt": {"type": "string", "description": "Description of allowed bash action"},
                    },
                },
                "description": "Optional: bash commands that can be pre-approved during plan review",
            },
        },
        "required": [],
    }


def _agent_id(context: Any) -> str | None:
    """Extract the subagent id from the tool-use context (None for the main
    conversation), matching ExitPlanModeV2Tool's `context.agentId`."""
    if context is None:
        return None
    return getattr(context, "agent_id", None)


async def call(
    plan: str = "",
    allowed_prompts: list[dict[str, str]] | None = None,
    context: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Exit plan mode and present the plan for approval.

    Plan-mode disk storage (ExitPlanModeV2Tool.call): when the model supplies a
    `plan`, persist it to the session plan file and echo it back; otherwise read
    the plan from disk. Either way, surface the saved file path so the model can
    refer back to it during implementation. Disk errors are non-fatal — plan
    exit still succeeds.
    """
    from hare.utils.plans import get_plan, get_plan_file_path, save_plan

    agent_id = _agent_id(context)
    is_agent = bool(agent_id)
    input_plan = plan if isinstance(plan, str) and plan.strip() else None

    file_path: str | None = None
    final_plan: str | None
    try:
        if input_plan is not None:
            # Write-through: persist the provided plan so VerifyPlanExecution /
            # Read / resume recovery see it (TS writeFile).
            file_path = save_plan(input_plan, agent_id)
            final_plan = input_plan
        else:
            final_plan = get_plan(agent_id)
            file_path = get_plan_file_path(agent_id)
    except OSError:
        final_plan = input_plan
        file_path = None

    # Structured output (TS Output). The model-visible text is built separately
    # in map_tool_result_to_tool_result_block_param so it is NOT delivered as a
    # str()-ified dict. `data` mirrors that text for in-process callers/tests.
    #
    # plan_was_edited: in TS this is true ONLY for a genuine CCR/Ctrl+G user edit
    # (inputPlan comes from permissionResult.updatedInput; the model never sets it
    # because TS's internal schema is allowedPrompts-only). hare has no such
    # user-edit channel — the model always supplies `plan` directly — so the
    # "(edited by user)" label never applies; the normal label is "Approved Plan",
    # matching TS's normal (non-edited) flow.
    result = {
        "mode": "default",
        "plan": final_plan,
        "is_agent": is_agent,
        "allowed_prompts": allowed_prompts or [],
        "file_path": file_path,
        "plan_was_edited": False,
    }
    result["data"] = _render_result_text(result)
    return result


def _render_result_text(result: dict[str, Any]) -> str:
    """Build the model-visible tool_result content from the structured output,
    mirroring ExitPlanModeV2Tool.mapToolResultToToolResultBlockParam (the
    non-teammate branches: isAgent / empty-plan / normal)."""
    plan = result.get("plan")
    file_path = result.get("file_path")

    if result.get("is_agent"):
        return (
            "User has approved the plan. There is nothing else needed from you "
            'now. Please respond with "ok"'
        )

    if not plan or not str(plan).strip():
        return "User has approved exiting plan mode. You can now proceed."

    plan_label = (
        "Approved Plan (edited by user)"
        if result.get("plan_was_edited")
        else "Approved Plan"
    )
    saved = (
        f"\n\nYour plan has been saved to: {file_path}\n"
        "You can refer back to it if needed during implementation."
        if file_path
        else ""
    )
    return (
        "User has approved your plan. You can now start coding. Start with "
        f"updating your todo list if applicable{saved}\n\n"
        f"## {plan_label}:\n{plan}"
    )


def map_tool_result_to_tool_result_block_param(
    content: Any, tool_use_id: str
) -> dict[str, Any]:
    """Model-visible tool_result content. `content` is the structured dict from
    call(); render it to the clean TS-style string instead of str(dict)."""
    if isinstance(content, dict):
        text = content.get("data") or _render_result_text(content)
    else:
        text = str(content) if content is not None else ""
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
