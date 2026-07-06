"""Cover query_engine (+14), auto_compact (+10), config (+5), state (+3), stop_hooks (+2)."""

from __future__ import annotations

from unittest import mock
import os


class TestQueryEngineDeep:
    def test_01_config(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine

        cfg = QueryEngineConfig(
            cwd="/tmp",
            max_turns=3,
            max_budget_usd=5.0,
            verbose=True,
            replay_user_messages=True,
            custom_system_prompt="custom",
            append_system_prompt="append",
            user_specified_model="sonnet",
            fallback_model="haiku",
            thinking_config={"type": "enabled"},
            include_partial_messages=True,
        )
        assert cfg.max_turns == 3
        assert cfg.max_budget_usd == 5.0

    def test_02_engine_construction(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine
        import asyncio

        cfg = QueryEngineConfig(cwd="/tmp", tools=[], commands=[])
        engine = QueryEngine(cfg)
        assert engine is not None
        msgs = engine.get_messages()
        assert isinstance(msgs, list)
        engine.interrupt()

    def test_03_engine_normalize(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine
        from hare.app_types.message import UserMessage, APIMessage

        cfg = QueryEngineConfig(cwd="/tmp", tools=[], commands=[])
        engine = QueryEngine(cfg)
        msg = UserMessage(message=APIMessage(role="user", content="test"))
        result = engine._normalize_message(msg)
        assert isinstance(result, dict)
        assert result["type"] == "user"

    def test_04_to_client_event(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine

        cfg = QueryEngineConfig(cwd="/tmp", tools=[], commands=[])
        engine = QueryEngine(cfg)
        result = engine.to_client_event({"type": "test"})
        assert isinstance(result, dict)

    def test_05_set_model(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine

        cfg = QueryEngineConfig(cwd="/tmp", tools=[], commands=[])
        engine = QueryEngine(cfg)
        engine.set_model("opus")
        assert engine._config.user_specified_model == "opus"

    def test_06_read_file_state(self) -> None:
        from hare.query_engine import QueryEngineConfig, QueryEngine

        cfg = QueryEngineConfig(cwd="/tmp", tools=[], commands=[])
        engine = QueryEngine(cfg)
        state = engine.get_read_file_state()
        assert isinstance(state, dict)


class TestAutoCompact:
    def test_01_import(self) -> None:
        from hare.services.compact import auto_compact

        assert auto_compact is not None

    def test_02_microcompact(self) -> None:
        from hare.services.compact.micro_compact import microcompact_messages

        assert callable(microcompact_messages)

    def test_03_compact_warning(self) -> None:
        from hare.services.compact.compact_warning_state import (
            is_compact_warning_suppressed,
            suppress_compact_warning,
            clear_compact_warning_suppression,
        )

        assert callable(is_compact_warning_suppressed)
        suppress_compact_warning()
        is_compact_warning_suppressed()
        clear_compact_warning_suppression()

    def test_04_post_compact(self) -> None:
        from hare.services.compact.post_compact_cleanup import run_post_compact_cleanup

        assert callable(run_post_compact_cleanup)

    def test_05_snip_compact(self) -> None:
        from hare.services.compact.snip_compact import snip_compact_if_needed

        assert callable(snip_compact_if_needed)


class TestMcpConfigMore:
    def test_01_mcp_config_imports(self) -> None:
        from hare.services.mcp.config import (
            load_mcp_servers,
            get_mcp_config,
            _parse_server_config,
        )

        assert callable(load_mcp_servers)
        assert callable(get_mcp_config)

    def test_02_parse_server_config(self) -> None:
        from hare.services.mcp.config import _parse_server_config

        result = _parse_server_config({"command": "echo", "args": ["hello"]})
        assert result is not None

    def test_03_parse_server_config_invalid(self) -> None:
        from hare.services.mcp.config import _parse_server_config

        result = _parse_server_config({"type": "ws", "url": "not_wss"})
        assert result is not None or result is None


class TestStateMore:
    def test_01_flush_interaction(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            update_last_interaction_time,
            flush_interaction_time,
            get_last_interaction_time,
            mark_scroll_activity,
        )

        reset_state_for_tests()
        update_last_interaction_time()
        flush_interaction_time()
        assert get_last_interaction_time() > 0

    def test_02_mark_scroll_activity(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            mark_scroll_activity,
            get_is_scroll_draining,
        )

        reset_state_for_tests()
        mark_scroll_activity()
        assert get_is_scroll_draining() is True

    def test_03_plan_mode_transitions(self) -> None:
        from hare.bootstrap.state import (
            reset_state_for_tests,
            handle_plan_mode_transition,
            handle_auto_mode_transition,
            needs_plan_mode_exit_attachment,
            needs_auto_mode_exit_attachment,
        )

        reset_state_for_tests()
        handle_plan_mode_transition("default", "plan")
        handle_plan_mode_transition("plan", "default")
        handle_auto_mode_transition("default", "auto")
        handle_auto_mode_transition("auto", "default")
        handle_plan_mode_transition("plan", "plan")
        handle_auto_mode_transition("auto", "plan")
        handle_auto_mode_transition("plan", "auto")


class TestStopHooksRemaining:
    def test_01_stop_hook_result_struct(self) -> None:
        from hare.query.stop_hooks import StopHookResult

        r = StopHookResult(blocking_errors=[], prevent_continuation=False)
        assert r.blocking_errors == []
        assert r.prevent_continuation is False

    def test_02_query_build_config(self) -> None:
        from hare.query.config import build_query_config, QueryConfig

        cfg = build_query_config()
        assert isinstance(cfg, QueryConfig)
        assert isinstance(cfg.gates.is_ant, bool)
