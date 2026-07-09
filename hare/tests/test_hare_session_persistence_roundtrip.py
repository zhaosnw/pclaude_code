"""Session persistence round-trip: a turn must write a transcript that
load_conversation_for_resume can read back.

hare's QueryEngine computed `persist_session` but never used it and never called
record_transcript, so normal runs wrote nothing to disk — making `-p` then
`--continue` find no session. This pins the contract: after a turn, the
transcript JSONL exists and resume recovers the user prompt + assistant reply.

Driven in-process with a fixture model (injected via production_deps) and an
isolated transcript base, so it's deterministic and touches no real config.
"""

from __future__ import annotations

import asyncio
import json

import pytest


def _run_one_turn(tmp_base, prompt: str, assistant_text: str) -> None:
    import hare.query.core as core
    import hare.query.deps as deps
    import hare.utils.session_storage as ss
    from hare.bootstrap.state import (
        set_session_id,
        set_session_persistence_disabled,
    )
    from hare.query.deps import QueryDeps

    # Isolate the on-disk transcript location (module global, not HARE_CONFIG_DIR).
    ss._transcript_base = tmp_base
    ss.reset_session_file_pointer()
    set_session_id("roundtrip-sess")
    set_session_persistence_disabled(False)

    def _make():
        async def call_model(payload, *a, **k):
            yield {
                "type": "assistant",
                "content": [{"type": "text", "text": assistant_text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        return call_model

    orig = deps.production_deps

    def patched():
        d = orig()
        return QueryDeps(
            call_model=_make(),
            microcompact=d.microcompact,
            autocompact=d.autocompact,
            uuid=d.uuid,
        )

    deps.production_deps = patched
    core.production_deps = patched
    try:

        async def run():
            from hare.sdk import HareClient, HareClientOptions
            from hare.utils.cwd import get_cwd

            c = await HareClient.create(HareClientOptions(cwd=get_cwd()))
            async for _ in c.stream(prompt):
                pass

        asyncio.run(run())
    finally:
        deps.production_deps = orig
        core.production_deps = orig
        set_session_persistence_disabled(False)


@pytest.mark.integration
def test_turn_persists_transcript_file(tmp_path):
    _run_one_turn(tmp_path, "remember the number 42", "noted: 42")
    transcript = tmp_path / "roundtrip-sess.jsonl"
    assert transcript.exists(), "the turn did not persist a transcript file"
    body = transcript.read_text()
    assert "remember the number 42" in body, "user prompt not persisted"
    assert "noted: 42" in body, "assistant reply not persisted"


@pytest.mark.integration
def test_resume_recovers_persisted_conversation(tmp_path):
    _run_one_turn(tmp_path, "remember the number 42", "noted: 42")

    import hare.utils.session_storage as ss

    ss.reset_session_file_pointer()  # fresh read, not the write cache

    from hare.utils.conversation_recovery import load_conversation_for_resume

    result = asyncio.run(load_conversation_for_resume("roundtrip-sess"))
    assert result is not None, "resume found no session for the persisted turn"
    messages = result.get("messages") or []
    joined = json.dumps(messages, default=str)
    assert "remember the number 42" in joined, "resume lost the user prompt"
    assert "noted: 42" in joined, "resume lost the assistant reply"

    # Hydration: resume must return live Message OBJECTS (not envelope dicts) so
    # the object-oriented query loop can consume them (msg.type attribute access).
    assert messages, "no messages recovered"
    assert all(not isinstance(m, dict) for m in messages), (
        "resume returned raw dicts; the query loop needs hydrated Message objects"
    )
    types = [getattr(m, "type", None) for m in messages]
    assert "user" in types and "assistant" in types, (
        f"expected user+assistant messages, got types {types}"
    )
