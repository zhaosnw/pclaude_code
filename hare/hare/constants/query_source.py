"""Query-source enumeration.

Port of: src/constants/querySource.ts.

Identifies which surface initiated a `query()` call. Used by stopHooks etc.
to gate background bookkeeping (e.g. only main-thread queries save cache-safe
params and trigger prompt-suggestion / extract-memories).
"""

from __future__ import annotations

from typing import Literal

# Mirrors TS enum string-literal union exactly.
QuerySource = Literal[
    "repl_main_thread",
    "sdk",
    "subagent",
    "side_question",
    "background",
]
