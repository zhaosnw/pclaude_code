"""
Group adjacent tool_use assistant messages for compact rendering.

Port of: src/utils/groupToolUses.ts
"""

from __future__ import annotations

from typing import Any


def _tool_use_info(msg: dict[str, Any]) -> tuple[str, str, str] | None:
    if msg.get("type") != "assistant":
        return None
    content = (msg.get("message") or {}).get("content")
    if not isinstance(content, list) or not content:
        return None
    b0 = content[0]
    if not isinstance(b0, dict) or b0.get("type") != "tool_use":
        return None
    mid = (msg.get("message") or {}).get("id")
    tid = b0.get("id")
    name = b0.get("name")
    if not mid or not tid or not name:
        return None
    return (str(mid), str(tid), str(name))


def apply_grouping(
    messages: list[dict[str, Any]],
    tools: list[Any],
    verbose: bool = False,
) -> dict[str, list[Any]]:
    if verbose:
        return {"messages": list(messages)}
    names = {
        getattr(t, "name", None)
        for t in tools
        if getattr(t, "render_grouped_tool_use", None)
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        info = _tool_use_info(msg)
        if not info:
            continue
        mid, _tid, name = info
        if name not in names:
            continue
        key = f"{mid}:{name}"
        groups.setdefault(key, []).append(msg)
    valid = {k: g for k, g in groups.items() if len(g) >= 2}
    grouped_tool_use_ids: set[str] = set()
    for g in valid.values():
        for m in g:
            inf = _tool_use_info(m)
            if inf:
                grouped_tool_use_ids.add(inf[1])
    results_by_id: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if msg.get("type") != "user":
            continue
        for c in (msg.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                tuid = c.get("tool_use_id")
                if tuid and str(tuid) in grouped_tool_use_ids:
                    results_by_id[str(tuid)] = msg
    out: list[Any] = []
    emitted: set[str] = set()
    for msg in messages:
        info = _tool_use_info(msg)
        if info:
            mid, tid, name = info
            key = f"{mid}:{name}"
            grp = valid.get(key)
            if grp and key not in emitted:
                emitted.add(key)
                results = []
                for am in grp:
                    inf = _tool_use_info(am)
                    if inf:
                        r = results_by_id.get(inf[1])
                        if r is not None:
                            results.append(r)
                first = grp[0]
                out.append(
                    {
                        "type": "grouped_tool_use",
                        "toolName": name,
                        "messages": grp,
                        "results": results,
                        "displayMessage": first,
                        "uuid": f"grouped-{first.get('uuid')}",
                        "timestamp": first.get("timestamp"),
                        "messageId": mid,
                    }
                )
                continue
        if msg.get("type") == "user":
            trs = [
                c
                for c in (msg.get("message") or {}).get("content") or []
                if isinstance(c, dict) and c.get("type") == "tool_result"
            ]
            if trs and all(
                str(c.get("tool_use_id")) in grouped_tool_use_ids for c in trs
            ):
                continue
        out.append(msg)
    return {"messages": out}
