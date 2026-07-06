"""Scan memory directory headers (port of src/memdir/memoryScan.ts)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from hare.memdir.memory_types import MemoryType, parse_memory_type

MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30


@dataclass
class MemoryHeader:
    filename: str
    file_path: str
    mtime_ms: float
    description: str | None
    type: MemoryType | None


def _parse_frontmatter_simple(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not content.startswith("---"):
        return out
    parts = content.split("---", 2)
    if len(parts) < 2:
        return out
    fm = parts[1]
    for line in fm.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


async def scan_memory_files(
    memory_dir: str, _signal: object | None = None
) -> list[MemoryHeader]:
    try:
        md_files: list[str] = []
        for root, _, files in os.walk(memory_dir):
            for f in files:
                if f.endswith(".md") and f != "MEMORY.md":
                    rel = os.path.relpath(os.path.join(root, f), memory_dir)
                    md_files.append(rel.replace("\\", "/"))
        results: list[MemoryHeader] = []
        for rel in md_files:
            fp = os.path.join(memory_dir, rel)
            try:
                st = os.stat(fp)
                with open(fp, encoding="utf-8") as fh:
                    lines = []
                    for i, line in enumerate(fh):
                        if i >= FRONTMATTER_MAX_LINES:
                            break
                        lines.append(line)
                content = "".join(lines)
                fm = _parse_frontmatter_simple(content)
                desc = fm.get("description")
                t = parse_memory_type(fm.get("type"))
                results.append(
                    MemoryHeader(
                        filename=rel,
                        file_path=fp,
                        mtime_ms=st.st_mtime * 1000,
                        description=desc,
                        type=t,
                    )
                )
            except OSError:
                continue
        results.sort(key=lambda m: m.mtime_ms, reverse=True)
        return results[:MAX_MEMORY_FILES]
    except Exception:
        return []


def format_memory_manifest(memories: list[MemoryHeader]) -> str:
    lines: list[str] = []
    for m in memories:
        tag = f"[{m.type}] " if m.type else ""
        ts = (
            __import__("datetime")
            .datetime.utcfromtimestamp(m.mtime_ms / 1000)
            .isoformat()
            + "Z"
        )
        if m.description:
            lines.append(f"- {tag}{m.filename} ({ts}): {m.description}")
        else:
            lines.append(f"- {tag}{m.filename} ({ts})")
    return "\n".join(lines)
