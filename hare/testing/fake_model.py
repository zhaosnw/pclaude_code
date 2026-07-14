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

    ``cursor_path`` persists the position across processes. A multi-invocation
    case (``--resume``) runs one hare process per turn, while the TS reference
    replays against a single mock server whose response stream keeps advancing.
    Without a shared cursor the second hare process restarts at response 0 and
    replays the first turn, which is not what the reference does.
    """
    responses = list(fixture["responses"])
    index = {"i": 0}
    if cursor_path:
        try:
            with open(cursor_path, encoding="utf-8") as handle:
                index["i"] = int(handle.read().strip() or 0)
        except (OSError, ValueError):
            index["i"] = 0

    def call_model(*_args: Any, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        async def _gen() -> AsyncGenerator[Any, None]:
            i = index["i"]
            if i >= len(responses):
                raise AssertionError(
                    f"fixture exhausted: model called {i + 1} times but fixture "
                    f"only has {len(responses)} responses"
                )
            index["i"] = i + 1
            if cursor_path:
                try:
                    with open(cursor_path, "w", encoding="utf-8") as handle:
                        handle.write(str(index["i"]))
                except OSError:
                    pass
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
