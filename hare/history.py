"""Prompt history and paste references (port of src/history.ts — partial)."""

from __future__ import annotations

import re


def get_pasted_text_ref_num_lines(text: str) -> int:
    return len(re.findall(r"\r\n|\r|\n", text))


def format_pasted_text_ref(id_: int, num_lines: int) -> str:
    if num_lines == 0:
        return f"[Pasted text #{id_}]"
    return f"[Pasted text #{id_} +{num_lines} lines]"


def format_image_ref(id_: int) -> str:
    return f"[Image #{id_}]"


def parse_references(input_text: str) -> list[dict[str, int | str]]:
    pattern = re.compile(
        r"\[(Pasted text|Image|\.\.\.Truncated text) #(\d+)(?: \+\d+ lines)?(\.)*\]"
    )
    out: list[dict[str, int | str]] = []
    for m in pattern.finditer(input_text):
        out.append({"id": int(m.group(2)), "match": m.group(0), "index": m.start()})
    return [x for x in out if x["id"] > 0]  # type: ignore[misc]
