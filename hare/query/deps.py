"""Dependency injection for the query() loop.

Port of: src/query/deps.ts (line-by-line).

The TS source uses `typeof fn` to keep dep signatures synced with the real
implementations automatically. Python lacks `typeof`; we fall back to
`Callable[..., Any]` aliases on the production functions, which preserves
the test-injection ergonomics without runtime cost.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from hare.services.api.claude import query_model_with_streaming
from hare.services.compact.auto_compact import auto_compact_if_needed
from hare.services.compact.micro_compact import microcompact_messages

# -- src/query/deps.ts L6-20
#
# I/O dependencies for query(). Passing a `deps` override into QueryParams
# lets tests inject fakes directly instead of spyOn-per-module — the most
# common mocks (callModel, autocompact) are each spied in 6-8 test files
# today with module-import-and-spy boilerplate.
#
# Using `typeof fn` keeps signatures in sync with the real implementations
# automatically. This file imports the real functions for both typing and
# the production factory — tests that import this file for typing are
# already importing query.ts (which imports everything), so there's no
# new module-graph cost.
#
# Scope is intentionally narrow (4 deps) to prove the pattern. Followup
# PRs can add runTools, handleStopHooks, logEvent, queue ops, etc.


# -- src/query/deps.ts L21-31
@dataclass
class QueryDeps:
    # -- model
    call_model: Callable[..., Awaitable[Any]]

    # -- compaction
    microcompact: Callable[..., Awaitable[Any]]
    autocompact: Callable[..., Awaitable[Any]]

    # -- platform
    uuid: Callable[[], str] = field(default=lambda: str(_uuid.uuid4()))


# -- src/query/deps.ts L33-40
def production_deps() -> QueryDeps:
    import os

    call_model: Callable[..., Awaitable[Any]] = query_model_with_streaming
    fixture_path = os.environ.get("HARE_MODEL_FIXTURE")
    if fixture_path:
        # Test-only deterministic backend. Never set in production. Lets the
        # CLI subprocess (python -m hare) replay a fixture instead of the API.
        # HARE_MODEL_FIXTURE_CURSOR keeps the replay position across processes
        # so a multi-invocation case advances the stream once, like the TS
        # reference's shared mock server does.
        from hare.testing.fake_model import fixture_call_model, load_fixture

        call_model = fixture_call_model(
            load_fixture(fixture_path),
            cursor_path=os.environ.get("HARE_MODEL_FIXTURE_CURSOR"),
        )

    # Compaction summarizes through the same model callable as the main loop
    # (the reference spends one model turn on the summary), so bind it here
    # rather than letting auto_compact reach for a separate API path — under a
    # fixture that would bypass the replay entirely.
    async def autocompact(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("call_model", call_model)
        return await auto_compact_if_needed(*args, **kwargs)

    return QueryDeps(
        call_model=call_model,
        microcompact=microcompact_messages,
        autocompact=autocompact,
        uuid=lambda: str(_uuid.uuid4()),
    )
