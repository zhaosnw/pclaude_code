"""Request-side alignment: hare's exposed tool registry vs Claude Code 2.1.88.

Output-only differential testing can't see this — the tools hare advertises to
the model are part of the *request*, not the response. The reference set below
is the unconditional core of `getAllBaseTools()` in the 2.1.88 reference source
(recovered-from-cli-js-map/src/tools.ts), using each tool's TOOL_NAME constant.

Default flags (non-ant, embedded-search off so Glob/Grep present, todoV2/worktree
conditionals excluded). Agent is correctly named 'Agent' in 2.1.88 (renamed to
'Task' in later versions), so hare matches there.

This pins the KNOWN reproduction gap: when hare implements a missing tool, this
test fails and reminds you to shrink KNOWN_MISSING; a newly-missing tool is a
regression.
"""

import asyncio

from hare.tool import get_empty_tool_permission_context
from hare.tools import get_tools

# Unconditional core of getAllBaseTools() in the 2.1.88 reference source.
REFERENCE_2188_CORE_TOOLS = {
    "Agent", "TaskOutput", "Bash", "Glob", "Grep", "ExitPlanMode", "Read",
    "Edit", "Write", "NotebookEdit", "WebFetch", "TodoWrite", "WebSearch",
    "TaskStop", "AskUserQuestion", "Skill", "EnterPlanMode", "SendMessage",
}

# Tools in the 2.1.88 core that hare does not expose. CLOSED 2026-06-14: all 5
# (EnterPlanMode, AskUserQuestion, TaskStop, TaskOutput, SendMessage) were
# implemented under hare/tools_impl/ but unregistered (SendMessage was also
# mis-registered as a function-module instead of its class singleton). Now all
# registered in get_all_base_tools — hare exposes the full 2.1.88 core.
KNOWN_MISSING: set[str] = set()


def _hare_tool_names() -> set[str]:
    res = get_tools(get_empty_tool_permission_context())
    if asyncio.iscoroutine(res):
        res = asyncio.run(res)
    return {getattr(t, "name", None) for t in res if getattr(t, "name", None)}


def test_tool_registry_gap_is_exactly_known():
    hare = _hare_tool_names()
    missing = REFERENCE_2188_CORE_TOOLS - hare
    assert missing == KNOWN_MISSING, (
        f"hare tool-registry gap vs 2.1.88 changed.\n"
        f"  now missing: {sorted(missing)}\n"
        f"  expected   : {sorted(KNOWN_MISSING)}\n"
        f"If you implemented a tool, remove it from KNOWN_MISSING. "
        f"If a tool went missing, that's a regression."
    )


def _schema(name: str) -> dict:
    res = get_tools(get_empty_tool_permission_context())
    if asyncio.iscoroutine(res):
        res = asyncio.run(res)
    for t in res:
        if getattr(t, "name", None) == name:
            return t.input_schema()
    raise AssertionError(f"tool {name} not registered")


def test_required_params_match_2188_for_aligned_tools():
    """Lock the schema 'required' sets that were aligned to 2.1.88 (verified
    against recovered-from-cli-js-map/src/tools/*). Regression guard for the
    WebFetch/Agent fixes."""
    # 2.1.88: WebFetch requires url AND prompt
    assert set(_schema("WebFetch").get("required", [])) == {"url", "prompt"}
    # 2.1.88: Agent (Task) requires description AND prompt
    assert set(_schema("Agent").get("required", [])) == {"description", "prompt"}


def test_param_sets_match_2188_for_aligned_tools():
    """Lock the full param sets aligned to 2.1.88 (Grep context, NotebookEdit
    model rewrite, Read pages). Verified against the recovered 2.1.88 source."""
    def params(name):
        return set(_schema(name).get("properties", {}))

    assert "context" in params("Grep")  # canonical alias for -C (2.1.88)
    assert params("NotebookEdit") == {
        "notebook_path", "cell_id", "new_source", "cell_type", "edit_mode"}
    assert set(_schema("NotebookEdit").get("required", [])) == {
        "notebook_path", "new_source"}
    assert params("Read") == {"file_path", "offset", "limit", "pages"}
