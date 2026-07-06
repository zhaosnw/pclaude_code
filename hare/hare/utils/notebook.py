"""
Jupyter notebook parsing for tool results. Port of src/utils/notebook.ts.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Literal, TypedDict

from hare.tools_impl.BashTool.prompt import BASH_TOOL_NAME
from hare.utils.path_utils import expand_path

LARGE_OUTPUT_THRESHOLD = 10_000


class NotebookOutputImage(TypedDict):
    image_data: str
    media_type: Literal["image/png", "image/jpeg"]


class NotebookCellSourceOutput(TypedDict, total=False):
    output_type: str
    text: str
    image: NotebookOutputImage


class NotebookCellSource(TypedDict, total=False):
    cellType: str
    source: str
    execution_count: int | None
    cell_id: str
    language: str
    outputs: list[NotebookCellSourceOutput]


def _format_output(raw: str) -> tuple[str, Any]:
    """Match BashTool formatOutput truncated content."""
    from hare.utils.format import truncate_text

    max_len = 50_000
    if len(raw) <= max_len:
        return raw, None
    return truncate_text(raw, max_len), None


def _process_output_text(text: str | list[str] | None) -> str:
    if not text:
        return ""
    raw = "".join(text) if isinstance(text, list) else text
    truncated, _ = _format_output(raw)
    return truncated


def _extract_image(data: dict[str, Any]) -> NotebookOutputImage | None:
    png = data.get("image/png")
    if isinstance(png, str):
        return {"image_data": re.sub(r"\s", "", png), "media_type": "image/png"}
    jpeg = data.get("image/jpeg")
    if isinstance(jpeg, str):
        return {"image_data": re.sub(r"\s", "", jpeg), "media_type": "image/jpeg"}
    return None


def _process_output(output: dict[str, Any]) -> NotebookCellSourceOutput | None:
    ot = output.get("output_type")
    if ot == "stream":
        return {
            "output_type": "stream",
            "text": _process_output_text(output.get("text")),
        }
    if ot in ("execute_result", "display_data"):
        data = output.get("data") or {}
        return {
            "output_type": ot,
            "text": _process_output_text(data.get("text/plain")),
            **({"image": img} if (img := _extract_image(data)) else {}),
        }
    if ot == "error":
        tb = output.get("traceback") or []
        tb_s = "\n".join(tb) if isinstance(tb, list) else str(tb)
        msg = f"{output.get('ename')}: {output.get('evalue')}\n{tb_s}"
        return {"output_type": "error", "text": _process_output_text(msg)}
    return None


def _is_large_outputs(outputs: list[NotebookCellSourceOutput | None]) -> bool:
    size = 0
    for o in outputs:
        if not o:
            continue
        t = o.get("text") or ""
        img = o.get("image")
        img_len = len(img["image_data"]) if img else 0
        size += len(t) + img_len
        if size > LARGE_OUTPUT_THRESHOLD:
            return True
    return False


def _process_cell(
    cell: dict[str, Any],
    index: int,
    code_language: str,
    include_large_outputs: bool,
) -> NotebookCellSource:
    cell_id = cell.get("id") or f"cell-{index}"
    src = cell.get("source")
    if isinstance(src, list):
        src = "".join(src)
    elif src is None:
        src = ""
    cell_data: NotebookCellSource = {
        "cellType": cell["cell_type"],
        "source": src,
        "cell_id": cell_id,
    }
    if cell["cell_type"] == "code":
        cell_data["execution_count"] = cell.get("execution_count") or None
        cell_data["language"] = code_language
    if cell["cell_type"] == "code" and cell.get("outputs"):
        outs_raw = cell["outputs"]
        outputs: list[NotebookCellSourceOutput] = []
        for o in outs_raw:
            if isinstance(o, dict):
                po = _process_output(o)
                if po:
                    outputs.append(po)
        if not include_large_outputs and _is_large_outputs(outputs):
            cell_data["outputs"] = [
                {
                    "output_type": "stream",
                    "text": (
                        f"Outputs are too large to include. Use {BASH_TOOL_NAME} with: "
                        f"cat <notebook_path> | jq '.cells[{index}].outputs'"
                    ),
                }
            ]
        else:
            cell_data["outputs"] = outputs
    return cell_data


def _cell_content_to_tool_result(cell: NotebookCellSource) -> dict[str, Any]:
    metadata: list[str] = []
    if cell["cellType"] != "code":
        metadata.append(f"<cell_type>{cell['cellType']}</cell_type>")
    if cell.get("language") != "python" and cell["cellType"] == "code":
        metadata.append(f"<language>{cell.get('language')}</language>")
    cid = cell["cell_id"]
    inner = "".join(metadata) + cell["source"]
    text = f'<cell id="{cid}">{inner}</cell id="{cid}">'
    return {"text": text, "type": "text"}


def _cell_output_to_tool_result(
    output: NotebookCellSourceOutput,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if output.get("text"):
        out.append({"text": f"\n{output['text']}", "type": "text"})
    if output.get("image"):
        im = output["image"]
        out.append(
            {
                "type": "image",
                "source": {
                    "data": im["image_data"],
                    "media_type": im["media_type"],
                    "type": "base64",
                },
            }
        )
    return out


def _tool_result_from_cell(cell: NotebookCellSource) -> list[dict[str, Any]]:
    blocks = [_cell_content_to_tool_result(cell)]
    for o in cell.get("outputs") or []:
        blocks.extend(_cell_output_to_tool_result(o))
    return blocks


async def read_notebook(
    notebook_path: str, cell_id: str | None = None
) -> list[NotebookCellSource]:
    full_path = expand_path(notebook_path)
    raw = await asyncio.to_thread(lambda: Path(full_path).read_bytes())
    content = raw.decode("utf-8")
    notebook = json.loads(content)
    cells = notebook.get("cells") or []
    lang = (notebook.get("metadata") or {}).get("language_info") or {}
    language = lang.get("name") if isinstance(lang, dict) else None
    code_language = language or "python"
    if cell_id:
        for i, c in enumerate(cells):
            if c.get("id") == cell_id:
                return [_process_cell(c, i, code_language, True)]
        raise ValueError(f'Cell with ID "{cell_id}" not found in notebook')
    return [_process_cell(c, i, code_language, False) for i, c in enumerate(cells)]


def map_notebook_cells_to_tool_result(
    data: list[NotebookCellSource], tool_use_id: str
) -> dict[str, Any]:
    all_results: list[dict[str, Any]] = []
    for cell in data:
        all_results.extend(_tool_result_from_cell(cell))
    merged: list[dict[str, Any]] = []
    for curr in all_results:
        if not merged:
            merged.append(curr)
            continue
        prev = merged[-1]
        if prev.get("type") == "text" and curr.get("type") == "text":
            prev["text"] = prev["text"] + "\n" + curr["text"]
        else:
            merged.append(curr)
    return {"tool_use_id": tool_use_id, "type": "tool_result", "content": merged}


def parse_cell_id(cell_id: str) -> int | None:
    m = re.match(r"^cell-(\d+)$", cell_id)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None
