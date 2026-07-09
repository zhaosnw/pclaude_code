"""Agent tool swarm/isolation params + access guard (2.1.88 AgentTool).

2.1.88's Agent inputSchema ALWAYS advertises the multi-agent params (name,
team_name, mode) and isolation; cwd is gated on KAIROS/ant. The swarm feature
itself is gated at call time: passing team_name without agent-swarms enabled is
rejected with a clear message. hare advertised only the base 5 params and
silently ignored team_name — a request-side gap. These tests pin the alignment.
"""

import asyncio

import pytest

from hare.tools_impl.AgentTool.agent_tool import AgentTool
from hare.utils.bundle_feature import feature
from hare.app_types.permissions import EXTERNAL_PERMISSION_MODES


def _props():
    return AgentTool.input_schema()["properties"]


# ---------------------------------------------------------------------------
# schema: multi-agent params always present
# ---------------------------------------------------------------------------

def test_multi_agent_params_present():
    props = _props()
    for p in ("name", "team_name", "mode", "isolation"):
        assert p in props, f"missing Agent param: {p}"


def test_base_params_still_present():
    props = _props()
    for p in ("prompt", "description", "subagent_type", "model", "run_in_background"):
        assert p in props


def test_mode_enum_matches_permission_modes():
    mode = _props()["mode"]
    assert mode.get("type") == "string"
    # external permission modes must all be offered
    for m in EXTERNAL_PERMISSION_MODES:
        assert m in mode["enum"]
    # 'auto' only under TRANSCRIPT_CLASSIFIER (off in the recovered external build)
    assert ("auto" in mode["enum"]) == feature("TRANSCRIPT_CLASSIFIER")


def test_model_enum_constrained():
    model = _props()["model"]
    assert model["enum"] == ["sonnet", "opus", "haiku"]


def test_subagent_type_is_free_string():
    st = _props()["subagent_type"]
    assert st.get("type") == "string"
    assert "enum" not in st  # TS uses z.string(); resolved against active agents


def test_isolation_enum_is_worktree_external():
    iso = _props()["isolation"]
    assert iso["enum"] == ["worktree"]


def test_cwd_gated_on_kairos():
    """cwd is advertised only under KAIROS/ant (omitted for external builds)."""
    assert ("cwd" in _props()) == feature("KAIROS")


def test_required_unchanged():
    assert AgentTool.input_schema()["required"] == ["description", "prompt"]


# ---------------------------------------------------------------------------
# call-time access guard
# ---------------------------------------------------------------------------

def test_team_name_without_swarms_is_rejected(monkeypatch):
    import hare.tools_impl.AgentTool.agent_tool as mod

    # Ensure swarms are disabled (default), regardless of ambient env.
    monkeypatch.setattr(mod, "is_agent_swarms_enabled", lambda: False)

    async def go():
        # TS throws → the tool_execution pipeline surfaces it as an is_error
        # tool_result. Mirrored here as a raised exception.
        with pytest.raises(ValueError, match="Agent Teams is not yet available"):
            await AgentTool.call(
                {
                    "prompt": "do x",
                    "description": "x",
                    "team_name": "alpha",
                    "name": "bob",
                },
                None,
            )

    asyncio.run(go())


def test_no_team_name_does_not_trip_guard(monkeypatch):
    """Without team_name the guard must not fire (it returns the team error only
    for team spawns). We stub the heavy engine path to keep the test fast."""
    import hare.tools_impl.AgentTool.agent_tool as mod

    monkeypatch.setattr(mod, "is_agent_swarms_enabled", lambda: False)

    async def go():
        res = await AgentTool.call({"prompt": "", "description": "x"}, None)
        data = res.data if hasattr(res, "data") else str(res)
        # empty prompt → the pre-existing "prompt is required" path, NOT the
        # team-access error.
        assert "Agent Teams is not yet available" not in data

    asyncio.run(go())
