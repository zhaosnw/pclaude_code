"""
Tests for backend_stdio.py — BackendSession, stdin/stdout protocol handling.

These tests validate the NDJSON protocol, session initialization, prompt
submission, command handling, and interrupt flow.
"""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from hare.backend_stdio import (
    BackendSession,
    _emit,
    _to_jsonable,
    _error_event,
)
from hare.bootstrap.state import reset_state_for_tests


# ---------------------------------------------------------------------------
# _to_jsonable tests
# ---------------------------------------------------------------------------


class TestToJsonable:
    def test_primitive_values(self) -> None:
        assert _to_jsonable(42) == 42
        assert _to_jsonable("hello") == "hello"
        assert _to_jsonable(True) is True
        assert _to_jsonable(None) is None

    def test_list_conversion(self) -> None:
        assert _to_jsonable([1, 2, 3]) == [1, 2, 3]

    def test_tuple_conversion(self) -> None:
        assert _to_jsonable((1, 2)) == [1, 2]

    def test_dict_conversion(self) -> None:
        result = _to_jsonable({"a": 1, "b": "x"})
        assert result == {"a": 1, "b": "x"}

    def test_nested_structure(self) -> None:
        data = {"list": [1, {"nested": "value"}], "tuple": (3, 4)}
        result = _to_jsonable(data)
        assert result == {"list": [1, {"nested": "value"}], "tuple": [3, 4]}


# ---------------------------------------------------------------------------
# _error_event tests
# ---------------------------------------------------------------------------


class TestErrorEvent:
    def test_error_event_without_request_id(self) -> None:
        event = _error_event("test error")
        assert event["type"] == "error"
        assert event["error"] == "test error"
        assert "request_id" not in event

    def test_error_event_with_request_id(self) -> None:
        event = _error_event("test error", request_id="req-123")
        assert event["type"] == "error"
        assert event["error"] == "test error"
        assert event["request_id"] == "req-123"


# ---------------------------------------------------------------------------
# BackendSession tests
# ---------------------------------------------------------------------------


class TestBackendSession:
    @pytest.fixture(autouse=True)
    def _reset_state(self):
        reset_state_for_tests()
        yield

    def test_session_creation(self) -> None:
        session = BackendSession(cwd="/tmp/test")
        assert session._cwd == "/tmp/test"
        assert session._model is None
        assert session._engine is None
        assert session._client is None
        assert session._active_task is None

    def test_session_with_all_options(self) -> None:
        session = BackendSession(
            cwd="/tmp",
            model="sonnet",
            max_turns=10,
            verbose=True,
            system_prompt="custom system",
            append_system_prompt="extra instructions",
        )
        assert session._model == "sonnet"
        assert session._max_turns == 10
        assert session._verbose is True
        assert session._system_prompt == "custom system"
        assert session._append_system_prompt == "extra instructions"

    def test_submit_prompt_when_not_initialized(self, capsys) -> None:
        session = BackendSession(cwd="/tmp")
        import asyncio

        async def _run():
            await session.submit_prompt("test", request_id="r1")

        asyncio.run(_run())
        captured = capsys.readouterr()
        stdout = captured.out.strip()
        assert "Backend session is not initialized" in stdout

    def test_handle_command_when_not_initialized(self, capsys) -> None:
        session = BackendSession(cwd="/tmp")
        import asyncio

        async def _run():
            await session.handle_command("/help", request_id="r1")

        asyncio.run(_run())
        captured = capsys.readouterr()
        stdout = captured.out.strip()
        assert "Backend session is not initialized" in stdout

    def test_interrupt_when_not_initialized(self, capsys) -> None:
        session = BackendSession(cwd="/tmp")
        session.interrupt(request_id="r1")
        captured = capsys.readouterr()
        stdout = captured.out.strip()
        assert "Backend session is not initialized" in stdout

    def test_init_payload_before_initialize(self) -> None:
        from hare.bootstrap.state import set_cwd, set_original_cwd

        set_cwd("/tmp/test")
        set_original_cwd("/tmp/test")
        session = BackendSession(cwd="/tmp/test")
        payload = session.init_payload()
        assert payload["type"] == "init"
        assert "session_id" in payload
        # init_payload uses get_cwd() which returns the global cwd state
        assert payload["cwd"] == "/tmp/test"
        assert "version" in payload

    def test_submit_prompt_rejects_concurrent_request(self, capsys) -> None:
        import asyncio

        reset_state_for_tests()

        async def _run():
            session = BackendSession(cwd="/tmp")
            # Create a mock engine with an active task
            session._engine = mock.AsyncMock()

            async def slow_task():
                await asyncio.sleep(10)

            session._active_task = asyncio.create_task(slow_task())

            await session.submit_prompt("test", request_id="r1")
            captured = capsys.readouterr()
            stdout = captured.out.strip()
            assert "already running" in stdout

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# _emit function tests
# ---------------------------------------------------------------------------


class TestEmit:
    def test_emit_writes_json_to_stdout(self, capsys) -> None:
        _emit({"type": "test", "key": "value"})
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["type"] == "test"
        assert parsed["key"] == "value"

    def test_emit_handles_non_ascii(self, capsys) -> None:
        _emit({"type": "test", "text": "你好"})
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["text"] == "你好"
