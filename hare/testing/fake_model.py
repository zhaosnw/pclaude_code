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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only; Windows falls back to no locking
    fcntl = None  # type: ignore[assignment]


@contextmanager
def _cross_process_lock(lock_path: str) -> Iterator[None]:
    """Serialize a read-modify-write cycle against ``lock_path`` across processes.

    ``cursor_path``/``consumed_path`` below are read-modify-write shared state
    files, deliberately designed (see ``fixture_call_model``'s docstring) to
    be read and written by more than one ``python -m hare`` invocation replaying
    the same fixture. Without a lock, two invocations can both read the same
    cursor/consumed value, both decide independently, and the second write
    clobbers the first — serving a "once" response twice, or replaying the
    same cursor index. ``fcntl.flock`` is POSIX-only; best-effort elsewhere
    since this is test/replay infrastructure, not shipped runtime code.
    """
    if fcntl is None:  # pragma: no cover - not exercised on POSIX CI
        # mypy's typeshed stub for `fcntl` is gated on sys.platform != "win32",
        # so on this (POSIX) type-checking machine it treats the module as
        # always importable and flags this branch unreachable. It is real at
        # runtime on Windows, where `import fcntl` raises ImportError.
        yield  # type: ignore[unreachable]
        return
    with open(lock_path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _noop_lock() -> Iterator[None]:
    yield


def load_fixture(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("kind") not in {"scripted", "replay", "content-matched"}:
        raise ValueError(
            "fixture kind must be scripted|replay|content-matched, got "
            f"{data.get('kind')!r}"
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

    def _claim_next_index() -> int:
        # Locked so two invocations replaying the same fixture (a
        # background subagent's own call_model and its parent, both bound
        # to the same cursor_path) can't both read index i, both decide to
        # serve responses[i], and race to write i+1 — losing a response or
        # replaying one twice. No-op wrapper when cursor_path is unset
        # (single in-process closure, no shared file to race over).
        with _cross_process_lock(f"{cursor_path}.lock") if cursor_path else _noop_lock():
            i = _read_index()
            if i >= len(responses):
                raise AssertionError(
                    f"fixture exhausted: model called {i + 1} times but "
                    f"fixture only has {len(responses)} responses"
                )
            _write_index(i + 1)
            return i

    content_matched = fixture.get("kind") == "content-matched"

    def _select_by_content(payload: Any) -> dict[str, Any]:
        # Match the request against the fixture the way the mock server does,
        # so a concurrent/async flow (parent + subagent drawing from one
        # fixture) is deterministic instead of order-dependent. Consumed
        # "once" responses are tracked next to the shared cursor file so a
        # child engine and its parent agree on what has been served. Locked
        # for the same reason as _claim_next_index: read-consumed, decide,
        # write-consumed must be atomic across processes sharing this file,
        # or a "once" response can be served twice.
        import os

        from scripts.fixture_matching import select_response

        consumed_path = f"{cursor_path}.consumed" if cursor_path else None
        with _cross_process_lock(f"{consumed_path}.lock") if consumed_path else _noop_lock():
            consumed: set[int] = set()
            if consumed_path and os.path.exists(consumed_path):
                try:
                    with open(consumed_path, encoding="utf-8") as handle:
                        consumed = {int(x) for x in handle.read().split() if x.strip()}
                except (OSError, ValueError):
                    consumed = set()
            selection = select_response(
                responses, payload if isinstance(payload, dict) else {}, consumed
            )
            if selection is None:
                raise AssertionError("no fixture response matched the request")
            idx, resp = selection
            if resp.get("once") and consumed_path:
                consumed.add(idx)
                try:
                    with open(consumed_path, "w", encoding="utf-8") as handle:
                        handle.write(" ".join(str(x) for x in sorted(consumed)))
                except OSError:
                    pass
            return resp

    def call_model(*_args: Any, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        async def _gen() -> AsyncGenerator[Any, None]:
            if content_matched:
                payload = _args[0] if _args else _kwargs.get("payload", {})
                r = _select_by_content(payload)
            else:
                i = _claim_next_index()
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
