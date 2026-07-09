"""ExitPlanMode plan-mode disk storage (2.1.88 ExitPlanModeV2Tool).

2.1.88 reads/writes the plan from the on-disk plan file (utils/plans.ts:
getPlan / writeFile / getPlanFilePath) and returns the saved file path in the
result. hare had the disk layer (utils/plans.py) but the ExitPlanMode tool only
echoed the passed `plan` — never persisting or reading it. These tests pin the
wiring: the tool writes a provided plan, falls back to disk when none is given,
returns the file path, and honours subagent (agent_id) paths.
"""

import asyncio
import types

import pytest

from hare.utils import plans
from hare.tools_impl.ExitPlanModeTool import exit_plan_mode_tool as T


@pytest.fixture
def tmp_plans(monkeypatch, tmp_path):
    """Redirect the plans directory to a tmp path and reset slug cache."""
    monkeypatch.setattr(plans, "_plans_dir", str(tmp_path))
    plans.clear_all_plan_slugs()
    # Stable session id so the slug is deterministic within a test.
    monkeypatch.setattr(plans, "get_session_id", lambda: "test-session", raising=False)
    yield tmp_path
    plans.clear_all_plan_slugs()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_plan_is_not_required(tmp_plans):
    """2.1.88 plan is optional (read from disk when omitted)."""
    schema = T.input_schema()
    assert "plan" not in schema.get("required", [])
    assert "allowed_prompts" in schema["properties"]


# ---------------------------------------------------------------------------
# write-through: a provided plan is persisted to disk
# ---------------------------------------------------------------------------

def test_call_persists_plan_to_disk(tmp_plans):
    res = _run(T.call(plan="# My Plan\nstep 1"))
    data = res.data if hasattr(res, "data") else res
    # file path surfaced and ends in .md
    fp = data["file_path"]
    assert fp.endswith(".md")
    # the plan round-trips through disk
    assert plans.get_plan() == "# My Plan\nstep 1"
    assert data["plan"] == "# My Plan\nstep 1"


# ---------------------------------------------------------------------------
# disk fallback: no plan passed → read the on-disk plan
# ---------------------------------------------------------------------------

def test_call_reads_plan_from_disk_when_omitted(tmp_plans):
    plans.save_plan("# Disk Plan\nfrom file")
    res = _run(T.call())
    data = res.data if hasattr(res, "data") else res
    assert data["plan"] == "# Disk Plan\nfrom file"
    assert data["file_path"].endswith(".md")


def test_call_empty_plan_with_no_disk_file(tmp_plans):
    """No plan arg and no disk file → plan is None/empty, no crash."""
    res = _run(T.call())
    data = res.data if hasattr(res, "data") else res
    assert not data.get("plan")


# ---------------------------------------------------------------------------
# subagent path uses {slug}-agent-{id}.md
# ---------------------------------------------------------------------------

def test_call_subagent_path(tmp_plans):
    ctx = types.SimpleNamespace(agent_id="abc123")
    res = _run(T.call(plan="agent plan", context=ctx))
    data = res.data if hasattr(res, "data") else res
    assert "-agent-abc123.md" in data["file_path"]
    assert plans.get_plan(agent_id="abc123") == "agent plan"


# ---------------------------------------------------------------------------
# result still carries the human-facing text + the saved path
# ---------------------------------------------------------------------------

def test_result_mentions_saved_path(tmp_plans):
    res = _run(T.call(plan="P"))
    data = res.data if hasattr(res, "data") else res
    text = data.get("data", "")
    assert data["file_path"] in text


# ---------------------------------------------------------------------------
# model-visible content: must be clean text, NOT a stringified dict
# ---------------------------------------------------------------------------

def test_model_sees_clean_text_not_dict_repr(tmp_plans):
    """The registered tool must deliver the rendered text as tool_result content
    (via map_tool_result_to_tool_result_block_param), never str(dict)."""
    from hare.tools import get_all_base_tools

    tool = next(t for t in get_all_base_tools() if t.name == "ExitPlanMode")
    res = _run(tool.call({"plan": "# Real Plan\nstep 1"}, None))
    block = tool.map_tool_result_to_tool_result_block_param(res.data, "tu_1")
    content = block["content"]
    assert isinstance(content, str)
    assert content.startswith("User has approved your plan")
    assert "## Approved Plan" in content
    assert "# Real Plan" in content
    # the structured keys must NOT leak into the model-visible content
    assert "'mode'" not in content and "allowed_prompts" not in content


def test_subagent_gets_ok_ack(tmp_plans):
    """A subagent (agent_id present) gets the TS 'respond with ok' message."""
    import types

    ctx = types.SimpleNamespace(agent_id="ag1")
    res = _run(T.call(plan="agent plan", context=ctx))
    data = res.data if hasattr(res, "data") else res
    assert data["is_agent"] is True
    assert 'respond with "ok"' in T._render_result_text(data)


def test_normal_plan_label_not_edited(tmp_plans):
    """hare has no user-edit channel (CCR/Ctrl+G), so a model-supplied plan is
    labelled 'Approved Plan', never '(edited by user)' — matching TS's normal
    (non-edited) flow."""
    res = _run(T.call(plan="a plan"))
    data = res.data if hasattr(res, "data") else res
    text = T._render_result_text(data)
    assert "## Approved Plan:" in text
    assert "(edited by user)" not in text
