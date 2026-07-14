"""Deterministic fake model backed by a fixture file.

Mirrors the contract of ``hare.services.api.claude.query_model_with_streaming``:
a callable taking a single ``payload`` dict and returning an async iterator of
items the query loop understands. Each successive call consumes the next fixture
response, so a single fixture drives a whole multi-turn session deterministically.

The fake yields ``{"type": "assistant", ...}`` dicts rather than ``StreamEvent``
objects: ``hare/query/core.py`` only drives the tool loop off ``AssistantMessage``
(see ``_extract_tool_use_blocks``), and ``_coerce_query_yield`` turns an assistant
dict into exactly that. Yielding raw ``StreamEvent`` would never trigger tool
execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator, Callable


def load_fixture(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("kind") not in {"scripted", "replay"}:
        raise ValueError(
            f"fixture kind must be scripted|replay, got {data.get('kind')!r}"
        )
    if not isinstance(data.get("responses"), list):
        raise ValueError("fixture must have a 'responses' list")
    return data


def fixture_call_model(
    fixture: dict[str, Any],
    cursor_path: str | None = None,
) -> Callable[..., AsyncGenerator[Any, None]]:
    """Return a stateful ``call_model`` that yields the next response per call.

    ``cursor_path`` makes the replay position shared rather than per-callable.
    The reference replays everything — extra invocations of a ``--resume`` case,
    a subagent's own loop, a compaction summary — against ONE mock server whose
    response stream keeps advancing. hare builds a separate call_model per
    engine (and per process), so each would otherwise restart at response 0 and
    replay earlier turns. The cursor is re-read on every call, not cached, so a
    child engine and its parent see each other's progress.
    """
    responses = list(fixture["responses"])
    index = {"i": 0}

    def _read_index() -> int:
        if not cursor_path:
            return index["i"]
        try:
            with open(cursor_path, encoding="utf-8") as handle:
                return int(handle.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write_index(value: int) -> None:
        index["i"] = value
        if not cursor_path:
            return
        try:
            with open(cursor_path, "w", encoding="utf-8") as handle:
                handle.write(str(value))
        except OSError:
            pass

    def call_model(*_args: Any, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        async def _gen() -> AsyncGenerator[Any, None]:
            i = _read_index()
            if i >= len(responses):
                raise AssertionError(
                    f"fixture exhausted: model called {i + 1} times but fixture "
                    f"only has {len(responses)} responses"
                )
            _write_index(i + 1)
            r = responses[i]
            usage = r.get("usage", {"input_tokens": 0, "output_tokens": 0})
            stop_reason = r.get("stop_reason", "end_turn")
            content = r.get("content", [{"type": "text", "text": ""}])

            # Emit the streaming envelope the real model produces, so the engine
            # accumulates usage and captures stop_reason (it reads these only from
            # message_start/message_delta/message_stop). Without these the result
            # event would show usage=0 / stop_reason=null — a fixture artifact, not
            # real hare behavior.
            yield {
                "type": "stream_event",
                "event": {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": usage.get("input_tokens", 0)}},
                },
            }
            yield {
                "type": "stream_event",
                "event": {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason},
                    "usage": {"output_tokens": usage.get("output_tokens", 0)},
                },
            }
            yield {"type": "stream_event", "event": {"type": "message_stop"}}
            # assistant dict — _coerce_query_yield 转成 AssistantMessage 驱动工具循环
            yield {
                "type": "assistant",
                "content": content,
                "stop_reason": stop_reason,
                "usage": usage,
            }

        return _gen()

    return call_model
