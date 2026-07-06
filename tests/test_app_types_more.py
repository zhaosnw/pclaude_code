"""
Tests for remaining app_types modules: plugin, session, logs, ids,
connector_text, text_input_types.
"""

from __future__ import annotations

import pytest

from hare.app_types.plugin import (
    PluginError,
    PluginErrorKind,
    PluginManifest,
    PluginPermission,
    InstalledPlugin,
    LoadedPlugin,
    PluginLoadResult,
    BuiltinPluginDefinition,
    PluginConfig,
    PluginRepository,
    get_plugin_error_message,
)
from hare.app_types.session import SessionInfo, SessionHistory
from hare.app_types.logs import (
    LogEntry,
    SerializedMessage,
    LogOption,
    TranscriptMessage,
    SummaryMessage,
    TagMessage,
    TitleMessage,
    ModeMessage,
    AgentNameMessage,
    AgentColorMessage,
    PRLinkMessage,
    AttributionSnapshotMessage,
    WorktreeStateEntry,
    ContentReplacementEntry,
    is_transcript_message,
)
from hare.app_types.ids import AgentId, SessionId, as_agent_id, as_session_id


# ---------------------------------------------------------------------------
# Plugin types tests
# ---------------------------------------------------------------------------


class TestPluginErrorKind:
    def test_all_variants_exist(self) -> None:
        assert PluginErrorKind.NOT_FOUND.value == "not_found"
        assert PluginErrorKind.UNKNOWN.value == "unknown"
        assert PluginErrorKind.MARKETPLACE_ERROR.value == "marketplace_error"
        assert PluginErrorKind.COMMAND_CONFLICT.value == "command_conflict"

    def test_enum_length(self) -> None:
        # Verify all 19 error kinds
        assert len(PluginErrorKind.__members__) == 19


class TestPluginError:
    def test_create_with_defaults(self) -> None:
        err = PluginError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.kind == PluginErrorKind.UNKNOWN
        assert err.details == {}

    def test_create_with_kind_and_details(self) -> None:
        err = PluginError(
            "not found",
            kind=PluginErrorKind.NOT_FOUND,
            details={"plugin_name": "test-plugin"},
        )
        assert err.kind == PluginErrorKind.NOT_FOUND
        assert err.details == {"plugin_name": "test-plugin"}

    def test_is_exception_subclass(self) -> None:
        err = PluginError("test")
        assert isinstance(err, Exception)

    def test_get_plugin_error_message_known_kind(self) -> None:
        err = PluginError("msg", kind=PluginErrorKind.NOT_FOUND)
        assert get_plugin_error_message(err) == "Plugin not found."

    def test_get_plugin_error_message_unknown_kind(self) -> None:
        # PluginErrorKind.UNKNOWN is in the messages dict, so returns its value
        err = PluginError("fallback message", kind=PluginErrorKind.UNKNOWN)
        assert get_plugin_error_message(err) == "Unknown plugin error."

    def test_get_plugin_error_message_kind_not_in_dict(self) -> None:
        # For kinds explicitly NOT in the dict, falls back to str(error)
        err = PluginError("fallback message")
        err.kind = "nonexistent_kind"  # type: ignore[assignment]
        assert get_plugin_error_message(err) == "fallback message"

    def test_get_plugin_error_message_all_kinds(self) -> None:
        for kind in PluginErrorKind:
            err = PluginError("test", kind=kind)
            msg = get_plugin_error_message(err)
            assert isinstance(msg, str)
            assert len(msg) > 0


class TestPluginManifest:
    def test_default_values(self) -> None:
        m = PluginManifest(name="test")
        assert m.name == "test"
        assert m.version == "0.0.1"
        assert m.description == ""
        assert m.author == ""
        assert m.permissions == []
        assert m.agents == []
        assert m.dependencies == {}

    def test_with_full_data(self) -> None:
        m = PluginManifest(
            name="my-plugin",
            version="1.2.3",
            description="A test plugin",
            author="test author",
            homepage="https://example.com",
            license="MIT",
            repository="https://github.com/test/plugin",
            permissions=[PluginPermission(tool_name="bash")],
            agents=["agent1"],
            mcp_servers=[{"name": "mcp1"}],
            commands=[{"name": "test-cmd"}],
            hooks=[{"event": "PreToolUse"}],
            skills=["skill1"],
            dependencies={"other-plugin": ">=1.0"},
            min_claude_version="2.1.0",
        )
        assert m.name == "my-plugin"
        assert m.version == "1.2.3"
        assert len(m.permissions) == 1
        assert m.permissions[0].tool_name == "bash"


class TestPluginPermission:
    def test_defaults(self) -> None:
        p = PluginPermission(tool_name="read")
        assert p.tool_name == "read"
        assert p.description == ""


class TestInstalledPlugin:
    def test_defaults(self) -> None:
        p = InstalledPlugin(
            name="test",
            path="/tmp/test",
            manifest=PluginManifest(name="test"),
        )
        assert p.enabled is True
        assert p.source == "local"


class TestLoadedPlugin:
    def test_defaults(self) -> None:
        p = LoadedPlugin()
        assert p.name == ""
        assert p.enabled is True
        assert p.commands == []
        assert p.agents == []
        assert p.has_load_error is False
        assert p.load_error is None


class TestPluginLoadResult:
    def test_defaults(self) -> None:
        r = PluginLoadResult()
        assert r.loaded == []
        assert r.errors == []

    def test_with_data(self) -> None:
        p = LoadedPlugin(name="test")
        r = PluginLoadResult(loaded=[p], errors=[PluginError("err")])
        assert len(r.loaded) == 1
        assert len(r.errors) == 1


class TestBuiltinPluginDefinition:
    def test_creation(self) -> None:
        d = BuiltinPluginDefinition(
            name="builtin1", description="A built-in", plugin_root="/tmp"
        )
        assert d.name == "builtin1"
        assert d.description == "A built-in"
        assert d.plugin_root == "/tmp"


class TestPluginConfig:
    def test_defaults(self) -> None:
        cfg = PluginConfig()
        assert cfg.enabled_plugins == []
        assert cfg.disabled_plugins == []
        assert cfg.marketplace_sources == []

    def test_with_data(self) -> None:
        cfg = PluginConfig(
            enabled_plugins=["p1", "p2"],
            disabled_plugins=["p3"],
            marketplace_sources=["https://market.example.com"],
        )
        assert len(cfg.enabled_plugins) == 2
        assert "p3" in cfg.disabled_plugins


class TestPluginRepository:
    def test_creation(self) -> None:
        repo = PluginRepository(
            name="official",
            url="https://plugins.example.com",
            description="Official marketplace",
        )
        assert repo.name == "official"
        assert repo.plugins == []


# ---------------------------------------------------------------------------
# Session types tests
# ---------------------------------------------------------------------------


class TestSessionInfo:
    def test_creation(self) -> None:
        s = SessionInfo(
            session_id="abc123",
            created_at=123456.0,
            updated_at=123457.0,
            model="sonnet",
            message_count=10,
            title="Test Session",
            project_dir="/tmp/test",
        )
        assert s.session_id == "abc123"
        assert s.model == "sonnet"
        assert s.message_count == 10

    def test_default_values(self) -> None:
        s = SessionInfo(session_id="default")
        assert s.created_at == 0.0
        assert s.model == ""
        assert s.title == ""


class TestSessionHistory:
    def test_add_session_inserts_at_front(self) -> None:
        hist = SessionHistory()
        s1 = SessionInfo(session_id="1")
        s2 = SessionInfo(session_id="2")
        hist.add_session(s1)
        hist.add_session(s2)
        assert hist.sessions[0].session_id == "2"
        assert hist.sessions[1].session_id == "1"

    def test_get_session_found(self) -> None:
        hist = SessionHistory()
        s = SessionInfo(session_id="find-me")
        hist.add_session(s)
        result = hist.get_session("find-me")
        assert result is s

    def test_get_session_not_found(self) -> None:
        hist = SessionHistory()
        assert hist.get_session("nonexistent") is None

    def test_list_recent_respects_limit(self) -> None:
        hist = SessionHistory()
        for i in range(30):
            hist.add_session(SessionInfo(session_id=str(i)))
        assert len(hist.list_recent(10)) == 10
        assert len(hist.list_recent(20)) == 20
        assert len(hist.list_recent(50)) == 30  # only 30 exist

    def test_list_recent_default_limit(self) -> None:
        hist = SessionHistory()
        for i in range(25):
            hist.add_session(SessionInfo(session_id=str(i)))
        result = hist.list_recent()
        assert len(result) == 20  # default limit

    def test_get_session_returns_none_on_empty(self) -> None:
        hist = SessionHistory()
        assert hist.get_session("any") is None


# ---------------------------------------------------------------------------
# Log types tests
# ---------------------------------------------------------------------------


class TestLogEntry:
    def test_creation(self) -> None:
        entry = LogEntry(
            level="info",
            message="test message",
            timestamp=123456.0,
            source="test",
        )
        assert entry.level == "info"
        assert entry.message == "test message"
        assert entry.source == "test"


class TestSerializedMessage:
    def test_defaults(self) -> None:
        msg = SerializedMessage()
        assert msg.type == ""
        assert msg.session_id == ""
        assert msg.uuid == ""
        assert msg.is_sidechain is False
        assert msg.is_virtual is False

    def test_creation_with_data(self) -> None:
        msg = SerializedMessage(
            type="user",
            session_id="s1",
            uuid="u1",
            message={"role": "user", "content": "hello"},
        )
        assert msg.type == "user"
        assert msg.session_id == "s1"
        assert msg.message["content"] == "hello"


class TestLogOption:
    def test_creation(self) -> None:
        opt = LogOption(
            date="2025-01-01",
            full_path="/tmp/sessions/test.jsonl",
            value=1700000000.0,
            session_id="s1",
            message_count=42,
        )
        assert opt.date == "2025-01-01"
        assert opt.session_id == "s1"
        assert opt.message_count == 42

    def test_default_values(self) -> None:
        opt = LogOption()
        assert opt.messages == []
        assert opt.is_sidechain is False
        assert opt.custom_title is None


class TestTranscriptMessage:
    def test_creation(self) -> None:
        msg = TranscriptMessage(
            type="assistant",
            session_id="s1",
            uuid="u1",
            message={"role": "assistant", "content": "hi"},
        )
        assert msg.type == "assistant"
        assert msg.is_sidechain is False


class TestTranscriptEntryVariants:
    def test_summary_message(self) -> None:
        msg = SummaryMessage(
            session_id="s1", summary="compacted summary", num_messages=10
        )
        assert msg.type == "summary"
        assert msg.num_messages == 10

    def test_tag_message(self) -> None:
        msg = TagMessage(session_id="s1", tag="important")
        assert msg.type == "tag"
        assert msg.tag == "important"

    def test_title_message(self) -> None:
        msg = TitleMessage(session_id="s1", title="New Title")
        assert msg.type == "title"
        assert msg.title == "New Title"

    def test_mode_message(self) -> None:
        msg = ModeMessage(session_id="s1", mode="plan")
        assert msg.type == "mode"
        assert msg.mode == "plan"

    def test_agent_name_message(self) -> None:
        msg = AgentNameMessage(session_id="s1", name="Agent007")
        assert msg.type == "agent_name"
        assert msg.name == "Agent007"

    def test_agent_color_message(self) -> None:
        msg = AgentColorMessage(session_id="s1", color="#ff0000")
        assert msg.type == "agent_color"
        assert msg.color == "#ff0000"

    def test_pr_link_message(self) -> None:
        msg = PRLinkMessage(session_id="s1", url="https://github.com/pr/1")
        assert msg.type == "pr_link"
        assert msg.url == "https://github.com/pr/1"

    def test_attribution_snapshot_message(self) -> None:
        msg = AttributionSnapshotMessage(
            session_id="s1", attribution={"authors": ["alice"]}
        )
        assert msg.type == "attribution_snapshot"

    def test_worktree_state_entry(self) -> None:
        entry = WorktreeStateEntry(
            session_id="s1", worktree_name="wt1", original_cwd="/tmp"
        )
        assert entry.type == "worktree_state"
        assert entry.worktree_name == "wt1"

    def test_content_replacement_entry(self) -> None:
        entry = ContentReplacementEntry(
            session_id="s1", replacements=[{"old": "a", "new": "b"}]
        )
        assert entry.type == "content_replacement"
        assert len(entry.replacements) == 1


class TestIsTranscriptMessage:
    def test_user_message(self) -> None:
        assert is_transcript_message({"type": "user"}) is True

    def test_assistant_message(self) -> None:
        assert is_transcript_message({"type": "assistant"}) is True

    def test_system_message(self) -> None:
        assert is_transcript_message({"type": "system"}) is True

    def test_progress_message(self) -> None:
        assert is_transcript_message({"type": "progress"}) is True

    def test_summary_is_not_transcript(self) -> None:
        assert is_transcript_message({"type": "summary"}) is False

    def test_tag_is_not_transcript(self) -> None:
        assert is_transcript_message({"type": "tag"}) is False

    def test_unknown_type(self) -> None:
        assert is_transcript_message({"type": "unknown"}) is False


# ---------------------------------------------------------------------------
# ID types tests
# ---------------------------------------------------------------------------


class TestIds:
    def test_as_agent_id(self) -> None:
        agent_id = as_agent_id("agent-1")
        assert isinstance(agent_id, str)
        assert agent_id == "agent-1"

    def test_as_session_id(self) -> None:
        session_id = as_session_id("session-1")
        assert isinstance(session_id, str)
        assert session_id == "session-1"

    def test_agent_id_type_compatibility(self) -> None:
        # AgentId is a NewType, so it's still a str at runtime
        aid: AgentId = AgentId("test")
        assert aid == "test"
        assert isinstance(aid, str)

    def test_session_id_type_compatibility(self) -> None:
        sid: SessionId = SessionId("test")
        assert sid == "test"
        assert isinstance(sid, str)
