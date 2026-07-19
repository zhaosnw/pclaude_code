"""Regression test: fixture_call_model's shared cursor/consumed files must
serialize their read-modify-write across processes.

hare/testing/fake_model.py's fixture_call_model docstring says the cursor
file "keeps the replay position across processes" — a background subagent's
own `python -m hare` invocation and its parent both point ``cursor_path`` at
the same file so they advance one shared response stream instead of each
restarting at response 0. Before the file-locking fix, the read → decide →
write sequence (for both the scripted cursor index and the content-matched
consumed set) had no synchronization: two processes racing it could both
read the same value, decide independently, and the loser's write clobbers
the winner's — serving a "once" response twice, or two processes both
claiming the same cursor index while another index is never served.

This drives fixture_call_model from genuinely separate OS processes (not
asyncio tasks, which never truly interleave a lock-free critical section
that has zero ``await`` points) hammering the same cursor files
concurrently, and asserts every response is claimed exactly once.
"""

from __future__ import annotations

import json
import multiprocessing


def _claim_one(fixture_path: str, cursor_path: str, barrier, queue) -> None:
    import asyncio

    from hare.testing.fake_model import fixture_call_model, load_fixture

    fixture = load_fixture(fixture_path)
    call_model = fixture_call_model(fixture, cursor_path=cursor_path)

    async def _run() -> str:
        last = None
        async for msg in call_model({"messages": []}):
            last = msg
        return last["content"][0]["text"]

    barrier.wait()
    try:
        queue.put(asyncio.run(_run()))
    except AssertionError as exc:
        queue.put(f"error:{exc}")


def _run_concurrently(fixture_path, cursor_path, n: int) -> list[str]:
    # "spawn", not "fork": pytest's process is multi-threaded (capture/
    # plugin threads), and fork()ing a multi-threaded process risks the
    # child deadlocking on a lock some other thread held at fork time.
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(n)
    queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_claim_one, args=(str(fixture_path), str(cursor_path), barrier, queue)
        )
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    results = [queue.get(timeout=30) for _ in range(n)]
    for p in procs:
        p.join(timeout=30)
    return results


def test_concurrent_processes_never_duplicate_a_scripted_cursor_index(tmp_path):
    n = 8
    fixture_path = tmp_path / "fx.json"
    fixture_path.write_text(
        json.dumps(
            {
                "kind": "scripted",
                "responses": [
                    {
                        "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": f"resp-{i}"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                    for i in range(n)
                ],
            }
        ),
        encoding="utf-8",
    )
    cursor_path = tmp_path / "cursor"

    results = _run_concurrently(fixture_path, cursor_path, n)

    # Every response index must be claimed by exactly one process — a race
    # would show up as a duplicate (two processes serving "resp-3") and/or
    # a gap (no process serving "resp-5").
    assert sorted(results) == sorted(f"resp-{i}" for i in range(n))


def test_concurrent_processes_never_double_serve_a_once_response(tmp_path):
    n = 8
    fixture_path = tmp_path / "fx.json"
    fixture_path.write_text(
        json.dumps(
            {
                "kind": "content-matched",
                "responses": [
                    {
                        "once": True,
                        "content": [{"type": "text", "text": "once-only"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    {
                        "content": [{"type": "text", "text": "fallback"}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    cursor_path = tmp_path / "cursor"

    results = _run_concurrently(fixture_path, cursor_path, n)

    assert results.count("once-only") == 1
    assert results.count("fallback") == n - 1
