"""Port of: src/commands/heapdump/. Capture and analyze heap/memory dumps."""

from __future__ import annotations

import os
import sys
import tempfile
import tracemalloc
from typing import Any

COMMAND_NAME = "heapdump"
DESCRIPTION = "Capture a heap/memory snapshot and print top allocations"
ALIASES: list[str] = ["heap", "memdump", "memory"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Capture a Python heap snapshot using tracemalloc.

    By default prints the top 10 memory allocations. Pass --snapshot <path>
    to write a full pickle snapshot to disk for later comparison.
    Pass --top <N> to change the number of entries shown (default 10).
    Pass --compare <file> to compare against a previously saved snapshot.
    """
    # Attempt to start tracemalloc if it is not already tracing
    if not tracemalloc.is_tracing():
        tracemalloc.start(25)

    lines: list[str] = ["## Heap Dump", ""]

    # Parse arguments
    top_n = 10
    snapshot_path: str | None = None
    compare_path: str | None = None
    raw_args = args if isinstance(args, list) else args.split()

    i = 0
    while i < len(raw_args):
        a = raw_args[i].strip()
        if a in ("--top", "-n") and i + 1 < len(raw_args):
            i += 1
            try:
                top_n = int(raw_args[i])
            except ValueError:
                lines.append(f"(invalid --top value, using default {top_n})")
        elif a in ("--snapshot", "-s") and i + 1 < len(raw_args):
            i += 1
            snapshot_path = raw_args[i]
        elif a in ("--compare", "-c") and i + 1 < len(raw_args):
            i += 1
            compare_path = raw_args[i]
        i += 1

    # --- Comparison mode ---
    if compare_path and os.path.exists(compare_path):
        try:
            import pickle

            with open(compare_path, "rb") as f:
                prev_snapshot = pickle.load(f)
            current = tracemalloc.take_snapshot()
            stats = current.compare_to(prev_snapshot, "lineno")
            lines.append(f"### Delta vs {compare_path}")
            lines.append("")
            lines.append("```")
            for stat in stats[:top_n]:
                lines.append(str(stat))
            lines.append("```")
            return {"type": "text", "value": "\n".join(lines)}
        except Exception as exc:
            lines.append(f"(failed to compare: {exc})")
            lines.append("")

    # --- Snapshot mode ---
    snapshot = tracemalloc.take_snapshot()

    if snapshot_path:
        try:
            import pickle

            os.makedirs(os.path.dirname(snapshot_path) or ".", exist_ok=True)
            with open(snapshot_path, "wb") as f:
                pickle.dump(snapshot, f)
            lines.append(f"Snapshot saved to `{snapshot_path}`")
        except Exception as exc:
            # fallback to a temp file
            try:
                fd, alt = tempfile.mkstemp(suffix=".heapdump", prefix="hare_heap_")
                os.close(fd)
                import pickle

                with open(alt, "wb") as f:
                    pickle.dump(snapshot, f)
                lines.append(f"(could not write to {snapshot_path!r}; saved to {alt})")
            except Exception:
                lines.append(f"(could not write snapshot: {exc})")
        lines.append("")

    # --- Print top allocations ---
    lines.append(f"### Top {top_n} allocations (tracemalloc)")
    lines.append("")
    top_stats = snapshot.statistics("lineno")
    lines.append("```")
    for stat in top_stats[:top_n]:
        lines.append(str(stat))
    lines.append("```")

    # --- Summary ---
    total_blocks = sum(s.count for s in top_stats)
    total_kb = sum(s.size for s in top_stats) / 1024
    lines.append("")
    lines.append(
        f"**Total:** {total_blocks:,} blocks, {total_kb:,.1f} KiB across "
        f"{len(top_stats)} allocation sites (traced frames: {snapshot.traceback_limit})"
    )

    # --- Hints ---
    lines.append("")
    lines.append("### More tools")
    lines.append("- `/heapdump --snapshot before.hprof` — save baseline")
    lines.append("- `/heapdump --snapshot after.hprof --compare before.hprof` — delta")
    lines.append("- `/heapdump --top 25` — show more entries")
    lines.append("- `python -m memory_profiler <script>` — line-by-line memory usage")
    lines.append("- `pip install objgraph guppy3` — object-graph and heapy inspection")

    return {"type": "text", "value": "\n".join(lines)}
