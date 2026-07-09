"""Regression: --resume/--continue must seed the loaded conversation into the
engine.

`_resume_existing_session` loads prior messages from disk (via
load_conversation_for_resume) and prints "Loaded N messages", but it built the
QueryEngine without passing them as initial_messages — so the restored history
was counted and then discarded, and the next turn started with an empty
conversation. QueryEngineConfig.initial_messages exists and __init__ seeds
_mutable_messages from it; the resume path just never set it.

This drives the real function with the disk load and the REPL stubbed out, and
captures the QueryEngineConfig it builds.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_resume_seeds_loaded_messages_into_engine(monkeypatch):
    loaded = [
        {"type": "user", "message": {"role": "user", "content": "first turn"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "reply"}},
    ]

    async def fake_load(source, source_jsonl_file=None):
        return {
            "messages": loaded,
            "sessionId": "sess-test",
            "turnInterruptionState": {},
        }

    monkeypatch.setattr(
        "hare.utils.conversation_recovery.load_conversation_for_resume", fake_load
    )
    # adopt_resumed_session_file touches the global session pointer/disk — neutralize.
    monkeypatch.setattr(
        "hare.utils.session_storage.adopt_resumed_session_file", lambda: None
    )

    captured: dict = {}

    class _FakeEngine:
        def __init__(self, config):
            captured["config"] = config

    monkeypatch.setattr("hare.query_engine.QueryEngine", _FakeEngine)

    # The resume path ends in an interactive `while True: input()` loop; EOF on the
    # first read exits it cleanly so the test doesn't block.
    def _eof(*_a, **_k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    import asyncio

    from hare.main import _resume_existing_session

    asyncio.run(_resume_existing_session(use_continue=True))

    cfg = captured.get("config")
    assert cfg is not None, "resume never built a QueryEngine"
    assert cfg.initial_messages == loaded, (
        "resume must seed the loaded conversation into the engine "
        f"(initial_messages); got {cfg.initial_messages!r}"
    )


@pytest.mark.integration
def test_headless_continue_runs_prompt_and_does_not_repl(monkeypatch):
    """`hare -p "x" --continue` must run the prompt headlessly against the
    restored conversation and exit — not ignore -p and drop into an interactive
    REPL. The engine that runs the turn must be seeded with the loaded history."""
    loaded = [
        {"type": "user", "message": {"role": "user", "content": "first turn"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "reply"}},
    ]

    async def fake_load(source, source_jsonl_file=None):
        return {
            "messages": loaded,
            "sessionId": "sess-test",
            "turnInterruptionState": {},
        }

    monkeypatch.setattr(
        "hare.utils.conversation_recovery.load_conversation_for_resume", fake_load
    )
    monkeypatch.setattr(
        "hare.utils.session_storage.adopt_resumed_session_file", lambda: None
    )

    captured: dict = {}

    class _FakeEngine:
        def __init__(self, config):
            captured["initial_messages"] = config.initial_messages

        async def submit_message(self, prompt, **_kw):
            captured["prompt"] = prompt
            yield {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "is_error": False,
            }

    monkeypatch.setattr("hare.query_engine.QueryEngine", _FakeEngine)

    def _no_repl(*_a, **_k):
        raise AssertionError("headless resume must not enter the interactive REPL")

    monkeypatch.setattr("builtins.input", _no_repl)

    import asyncio

    from hare.main import cli_main

    asyncio.run(cli_main(["-p", "follow up question", "--continue"]))

    assert captured.get("prompt") == "follow up question", (
        "the -p prompt was not run headlessly after resume"
    )
    assert captured.get("initial_messages") == loaded, (
        "the headless resume turn was not seeded with the loaded conversation"
    )
