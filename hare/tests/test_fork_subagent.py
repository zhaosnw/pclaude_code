"""Tests for fork subagent cache sharing — aligned with forkSubagent.ts."""

from __future__ import annotations

import pytest

from hare.tools_impl.AgentTool.fork_subagent import (
    FORK_AGENT,
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_PLACEHOLDER_RESULT,
    FORK_SUBAGENT_TYPE,
    build_child_message,
    build_forked_messages,
    build_worktree_notice,
    create_fork_context,
    freeze_rendered_system_prompt,
    get_rendered_system_prompt,
    is_fork_subagent_enabled,
    is_in_fork_child,
    should_fork,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_fork_agent_definition() -> None:
    assert FORK_AGENT["agentType"] == FORK_SUBAGENT_TYPE
    assert FORK_AGENT["tools"] == ["*"]
    assert FORK_AGENT["maxTurns"] == 200
    assert FORK_AGENT["model"] == "inherit"
    assert FORK_AGENT["permissionMode"] == "bubble"


def test_fork_directive_prefix() -> None:
    assert isinstance(FORK_DIRECTIVE_PREFIX, str)
    assert len(FORK_DIRECTIVE_PREFIX) > 0


# ---------------------------------------------------------------------------
# is_fork_subagent_enabled
# ---------------------------------------------------------------------------


def test_fork_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_FORK_SUBAGENT", raising=False)
    assert not is_fork_subagent_enabled()


# ---------------------------------------------------------------------------
# should_fork
# ---------------------------------------------------------------------------


def test_should_fork_no_type_fork_enabled(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_FORK_SUBAGENT", "1")
    assert should_fork(subagent_type=None, fork_enabled=True)


def test_should_fork_empty_type(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_FORK_SUBAGENT", "1")
    assert should_fork(subagent_type="", fork_enabled=True)


def test_should_fork_with_type_declines() -> None:
    assert not should_fork(subagent_type="general-purpose", fork_enabled=True)


def test_should_fork_disabled() -> None:
    assert not should_fork(subagent_type=None, fork_enabled=False)


# ---------------------------------------------------------------------------
# is_in_fork_child — TS lines 78-89
# ---------------------------------------------------------------------------


def test_not_fork_child_empty() -> None:
    assert not is_in_fork_child([])


def test_not_fork_child_normal_user_message() -> None:
    assert not is_in_fork_child(
        [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    )


def test_not_fork_child_assistant_message() -> None:
    """Assistant messages should not trigger fork detection (only user messages)."""
    msg = build_child_message("test")
    assert not is_in_fork_child(
        [{"role": "assistant", "content": [{"type": "text", "text": msg}]}]
    )


def test_is_fork_child_detected() -> None:
    msg = build_child_message("test task")
    assert is_in_fork_child(
        [{"role": "user", "content": [{"type": "text", "text": msg}]}]
    )


def test_is_fork_child_mixed_content() -> None:
    """Only the text block with boilerplate triggers detection."""
    msg = build_child_message("task")
    assert is_in_fork_child(
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": "ok"},
                    {"type": "text", "text": msg},
                ],
            }
        ]
    )


# ---------------------------------------------------------------------------
# build_child_message — TS lines 171-198
# ---------------------------------------------------------------------------


def test_build_child_message_contains_boilerplate() -> None:
    msg = build_child_message("do something")
    assert f"<{FORK_BOILERPLATE_TAG}>" in msg
    assert f"</{FORK_BOILERPLATE_TAG}>" in msg


def test_build_child_message_contains_10_rules() -> None:
    msg = build_child_message("task")
    assert "RULES (non-negotiable)" in msg
    assert "Scope:" in msg
    assert "Key files:" in msg
    assert "Files changed:" in msg
    assert "Issues:" in msg


def test_build_child_message_includes_directive() -> None:
    msg = build_child_message("analyze the auth module")
    assert FORK_DIRECTIVE_PREFIX in msg
    assert "analyze the auth module" in msg


def test_build_child_message_format() -> None:
    msg = build_child_message("test")
    assert "Scope:" in msg
    assert "Result:" in msg
    assert "STOP. READ THIS FIRST." in msg


# ---------------------------------------------------------------------------
# build_worktree_notice — TS lines 205-210
# ---------------------------------------------------------------------------


def test_build_worktree_notice() -> None:
    notice = build_worktree_notice("/home/user/project", "/tmp/worktree-1")
    assert "/home/user/project" in notice
    assert "/tmp/worktree-1" in notice
    assert "isolated git worktree" in notice


# ---------------------------------------------------------------------------
# build_forked_messages — TS lines 107-169
# ---------------------------------------------------------------------------


def test_build_forked_messages_no_tool_uses() -> None:
    """No tool_use blocks → returns user message with just the directive."""
    assistant = {
        "role": "assistant",
        "content": [{"type": "text", "text": "I'll help"}],
    }

    result = build_forked_messages("fork me", assistant)

    assert len(result) == 1  # only the user message (no clone when no tool_uses)
    assert result[0]["role"] == "user"


def test_build_forked_messages_with_tool_uses() -> None:
    """Tool_uses in assistant → returns [cloned_assistant, user_with_placeholders]."""
    assistant = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me read those."},
            {"type": "tool_use", "id": "tu_1", "name": "read", "input": {}},
            {"type": "tool_use", "id": "tu_2", "name": "glob", "input": {}},
        ],
    }

    result = build_forked_messages("fork task", assistant)

    assert len(result) == 2  # cloned assistant + user with tool_results
    # First message is the cloned assistant
    assert result[0]["role"] == "assistant"
    # Second is the user message with tool_results
    user_msg = result[1]
    assert user_msg["role"] == "user"
    content = user_msg["content"]
    tool_results = [b for b in content if b.get("type") == "tool_result"]
    text_blocks = [b for b in content if b.get("type") == "text"]
    assert len(tool_results) == 2
    assert len(text_blocks) >= 1
    # All placeholders are identical
    assert tool_results[0]["tool_use_id"] == "tu_1"
    assert tool_results[1]["tool_use_id"] == "tu_2"
    for tr in tool_results:
        assert tr["content"][0]["text"] == FORK_PLACEHOLDER_RESULT


def test_build_forked_messages_directive_present() -> None:
    """The per-child directive appears in a text block after placeholders."""
    assistant = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu_1", "name": "read", "input": {}}],
    }

    result = build_forked_messages("my custom task", assistant)
    user_content = result[1]["content"]
    text_blocks = [b for b in user_content if b.get("type") == "text"]
    assert len(text_blocks) == 1
    assert "my custom task" in text_blocks[0]["text"]
    assert FORK_BOILERPLATE_TAG in text_blocks[0]["text"]


# ---------------------------------------------------------------------------
# create_fork_context (legacy)
# ---------------------------------------------------------------------------


def test_create_fork_context() -> None:
    ctx = create_fork_context(
        [{"role": "user", "content": "hi"}], "fork prompt", name="test"
    )
    assert ctx["is_fork"] is True
    assert ctx["prompt"] == "fork prompt"
    assert ctx["name"] == "test"
    assert len(ctx["messages"]) == 1


# ---------------------------------------------------------------------------
# rendered_system_prompt freeze/get
# ---------------------------------------------------------------------------


class _FakeContext:
    pass


def test_get_rendered_system_prompt_none_by_default() -> None:
    ctx = _FakeContext()
    assert get_rendered_system_prompt(ctx) is None


def test_freeze_and_get_rendered_system_prompt() -> None:
    ctx = _FakeContext()
    sp = ["system line 1", "system line 2"]
    freeze_rendered_system_prompt(ctx, sp)
    assert get_rendered_system_prompt(ctx) is sp


def test_freeze_rendered_system_prompt_overwrite() -> None:
    ctx = _FakeContext()
    freeze_rendered_system_prompt(ctx, "old")
    freeze_rendered_system_prompt(ctx, "new")
    assert get_rendered_system_prompt(ctx) == "new"


def test_get_rendered_system_prompt_from_dict() -> None:
    ctx = {"rendered_system_prompt": "from_dict"}
    assert get_rendered_system_prompt(ctx) == "from_dict"


def test_get_rendered_system_prompt_camelcase_fallback() -> None:
    ctx = {"renderedSystemPrompt": "camel"}
    assert get_rendered_system_prompt(ctx) == "camel"


# ---------------------------------------------------------------------------
# Integration: full fork flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_fork_flow_no_recursive_fork() -> None:
    """Verify that the fork boilerplate prevents recursive fork detection."""
    msg = build_child_message("do work")
    assert is_in_fork_child(
        [{"role": "user", "content": [{"type": "text", "text": msg}]}]
    )
