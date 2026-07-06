"""
Normalize ``commands_impl`` ``call`` signatures to ``(raw_line, context)``.

TS-recovered modules use mixed shapes: ``(args: str, **ctx)``, ``(tokens, ctx)``,
``(args, messages, **ctx)``, etc. REPL and ``LocalCommand`` always invoke
``await call(raw_slash_line, context_dict)``.
"""

from __future__ import annotations

import inspect
import shlex
from typing import Any, Awaitable, Callable

__all__ = ["adapt_command_call"]


async def adapt_command_call(
    fn: Callable[..., Awaitable[Any]],
    raw_line: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    var_kw = next((p for p in params if p.kind == inspect.Parameter.VAR_KEYWORD), None)
    pos = [
        p
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]

    if var_kw is not None and len(pos) == 1:
        out = await fn(raw_line, **context)
        return out if isinstance(out, dict) else {"type": "text", "text": str(out)}

    if (
        var_kw is not None
        and len(pos) == 2
        and pos[1].name in ("messages", "msgs", "message_list")
    ):
        msgs: list[Any] = list(context.get("messages") or [])
        try:
            out = await fn(raw_line, msgs, **context)
        except TypeError:
            out = await fn(raw_line, msgs)
        return out if isinstance(out, dict) else {"type": "text", "text": str(out)}

    if len(pos) == 0:
        out = await fn()
        return out if isinstance(out, dict) else {"type": "text", "text": str(out)}

    if len(pos) == 1:
        n0 = pos[0].name
        if n0 in ("context", "ctx"):
            out = await fn(context)
        else:
            out = await fn(raw_line)
        return out if isinstance(out, dict) else {"type": "text", "text": str(out)}

    p1 = pos[1]
    if p1.name in ("messages", "msgs", "message_list"):
        msgs_ctx: list[Any] = list(context.get("messages") or [])
        try:
            out = await fn(raw_line, msgs_ctx, **context)
        except TypeError:
            out = await fn(raw_line, msgs_ctx)
        return out if isinstance(out, dict) else {"type": "text", "text": str(out)}

    ann0 = pos[0].annotation
    list_first = getattr(ann0, "__origin__", None) is list or ann0 is list
    if list_first or pos[0].name in ("argv", "tokens"):
        parts = shlex.split(raw_line, posix=False)
        tokens = parts[1:] if len(parts) > 1 else []
        out = await fn(tokens, context)
        return out if isinstance(out, dict) else {"type": "text", "text": str(out)}

    out = await fn(raw_line, context)
    return out if isinstance(out, dict) else {"type": "text", "text": str(out)}
