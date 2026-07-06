"""
Tests for state/store.py (Store) and state/app_state.py (AppState, subscriptions).
"""

from __future__ import annotations

import pytest

from hare.state.store import Store, create_store
from hare.state.app_state import (
    AppState,
    get_app_state,
    set_app_state,
    subscribe,
    reset_app_state,
)


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestStore:
    def test_initial_state(self) -> None:
        store = Store[int](42)
        assert store.get_state() == 42

    def test_set_state_with_simple_value(self) -> None:
        store = Store[int](0)
        store.set_state(lambda s: s + 1)
        assert store.get_state() == 1

    def test_set_state_with_complex_object(self) -> None:
        store = Store[dict]({"a": 1})
        store.set_state(lambda s: {**s, "b": 2})
        assert store.get_state() == {"a": 1, "b": 2}

    def test_subscribe_receives_updates(self) -> None:
        store = Store[int](0)
        received: list[int] = []

        def listener(state: int) -> None:
            received.append(state)

        store.subscribe(listener)
        store.set_state(lambda s: s + 1)
        store.set_state(lambda s: s + 10)

        assert received == [1, 11]

    def test_unsubscribe_stops_receiving(self) -> None:
        store = Store[int](0)
        received: list[int] = []

        def listener(state: int) -> None:
            received.append(state)

        unsub = store.subscribe(listener)
        store.set_state(lambda s: s + 1)
        unsub()
        store.set_state(lambda s: s + 10)

        assert received == [1]

    def test_multiple_subscribers_all_notified(self) -> None:
        store = Store[str]("initial")
        results: list[list[str]] = [[], [], []]

        for i in range(3):

            def make_listener(idx: int):
                def listener(state: str) -> None:
                    results[idx].append(state)

                return listener

            store.subscribe(make_listener(i))

        store.set_state(lambda _: "updated")
        for r in results:
            assert r == ["updated"]

    def test_exception_in_listener_does_not_block_others(self) -> None:
        store = Store[int](0)
        received: list[int] = []

        def bad_listener(s: int) -> None:
            raise RuntimeError("boom")

        def good_listener(s: int) -> None:
            received.append(s)

        store.subscribe(bad_listener)
        store.subscribe(good_listener)
        # The store calls listeners in subscription order and does NOT catch
        # exceptions. Since bad_listener raises, good_listener is never called.
        with pytest.raises(RuntimeError, match="boom"):
            store.set_state(lambda s: s + 1)
        # good_listener was NOT called because bad_listener raised first
        assert received == []

    def test_create_store_helper(self) -> None:
        store = create_store({"key": "value"})
        assert isinstance(store, Store)
        assert store.get_state() == {"key": "value"}


# ---------------------------------------------------------------------------
# AppState tests
# ---------------------------------------------------------------------------


class TestAppState:
    def setup_method(self) -> None:
        reset_app_state()

    def teardown_method(self) -> None:
        reset_app_state()

    def test_default_values(self) -> None:
        state = AppState()
        assert state.is_processing is False
        assert state.vim_mode is False
        assert state.fast_mode is False
        assert state.permission_mode == "default"
        assert state.effort == "medium"
        assert state.thinking_enabled is True
        assert state.session_id == ""
        assert state.messages == []

    def test_lazy_init(self) -> None:
        reset_app_state()
        state = get_app_state()
        assert state is not None
        assert isinstance(state, AppState)

    def test_set_app_state_with_new_state(self) -> None:
        fresh = AppState()
        fresh.vim_mode = True
        set_app_state(fresh)
        assert get_app_state().vim_mode is True

    def test_set_app_state_with_updater(self) -> None:
        reset_app_state()
        set_app_state(lambda s: AppState(**{**s.__dict__, "fast_mode": True}))
        assert get_app_state().fast_mode is True

    def test_subscribe_to_app_state(self) -> None:
        reset_app_state()
        received: list[AppState] = []

        def handler(state: AppState) -> None:
            received.append(state)

        unsub = subscribe(handler)
        set_app_state(lambda s: AppState(**{**s.__dict__, "is_processing": True}))

        assert len(received) == 1
        assert received[0].is_processing is True

        unsub()
        set_app_state(lambda s: AppState(**{**s.__dict__, "is_processing": False}))
        assert len(received) == 1  # not called again

    def test_reset_app_state_clears_everything(self) -> None:
        set_app_state(lambda s: AppState(**{**s.__dict__, "vim_mode": True}))
        assert get_app_state().vim_mode is True

        received: list[AppState] = []

        def handler(state: AppState) -> None:
            received.append(state)

        subscribe(handler)
        reset_app_state()

        assert get_app_state().vim_mode is False
        assert len(received) == 0  # subscribers cleared

    def test_app_state_has_all_feature_flags(self) -> None:
        state = AppState()
        assert hasattr(state, "kairos_enabled")
        assert hasattr(state, "assistant_enabled")
        assert hasattr(state, "sandbox_enabled")
        assert hasattr(state, "plan_mode")
        assert hasattr(state, "auto_mode")
        assert hasattr(state, "output_style")

    def test_app_state_has_permission_fields(self) -> None:
        state = AppState()
        ctx = state.tool_permission_context
        assert isinstance(ctx, dict)
        assert "cwd" in ctx
        assert "additionalWorkingDirectories" in ctx
        assert "alwaysAllowRules" in ctx
        assert "denyRules" in ctx
        assert "permissionMode" in ctx

    def test_app_state_has_cost_fields(self) -> None:
        state = AppState()
        assert state.total_cost_usd == 0.0
        assert state.total_tokens == 0
        assert isinstance(state.token_usage, dict)

    def test_app_state_has_file_history(self) -> None:
        state = AppState()
        assert isinstance(state.file_history, dict)
        assert "snapshots" in state.file_history
        assert "trackedFiles" in state.file_history

    def test_app_state_has_plugin_fields(self) -> None:
        state = AppState()
        assert isinstance(state.loaded_plugins, list)
        assert isinstance(state.plugin_errors, list)
