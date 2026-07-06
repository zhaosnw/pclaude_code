"""
NotebookEditTool – edit Jupyter notebook cells.

Port of: src/tools/NotebookEditTool/NotebookEditTool.ts (2.1.88 schema:
cell_id + new_source + edit_mode[replace|insert|delete] + cell_type).
"""

from __future__ import annotations

import json
import os
from typing import Any

TOOL_NAME = "NotebookEdit"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "The absolute path to the Jupyter notebook file to edit",
            },
            "cell_id": {
                "type": "string",
                "description": (
                    "The id of the cell to edit. For insert, the new cell is placed "
                    "after this cell (or at the top if omitted)."
                ),
            },
            "new_source": {
                "type": "string",
                "description": "The new source for the cell",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown"],
                "description": (
                    "The type of the cell. Defaults to the current cell type; "
                    "required when edit_mode=insert."
                ),
            },
            "edit_mode": {
                "type": "string",
                "enum": ["replace", "insert", "delete"],
                "description": "The type of edit to make. Defaults to replace.",
            },
        },
        "required": ["notebook_path", "new_source"],
    }


def _find_index(cells: list[dict[str, Any]], cell_id: str | None) -> int:
    if cell_id is None:
        return -1
    for i, c in enumerate(cells):
        if str(c.get("id", "")) == str(cell_id):
            return i
    return -1


async def call(
    notebook_path: str,
    new_source: str,
    cell_id: str | None = None,
    cell_type: str | None = None,
    edit_mode: str = "replace",
    **kwargs: Any,
) -> dict[str, Any]:
    if not os.path.isabs(notebook_path):
        notebook_path = os.path.join(os.getcwd(), notebook_path)
    try:
        with open(notebook_path, "r", encoding="utf-8") as f:
            nb = json.load(f)
        cells = nb.get("cells", [])
        source_lines = new_source.split("\n") if new_source else []

        if edit_mode == "insert":
            if not cell_type:
                return {"error": "cell_type is required when edit_mode=insert"}
            new_cell: dict[str, Any] = {
                "cell_type": cell_type,
                "metadata": {},
                "source": source_lines,
            }
            if cell_type == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None
            at = _find_index(cells, cell_id)
            insert_at = (at + 1) if at >= 0 else 0
            cells.insert(insert_at, new_cell)
            msg = f"Inserted {cell_type} cell"
        elif edit_mode == "delete":
            idx = _find_index(cells, cell_id)
            if idx < 0:
                return {"error": f"Cell with id {cell_id!r} not found"}
            cells.pop(idx)
            msg = f"Deleted cell {cell_id}"
        else:  # replace
            idx = _find_index(cells, cell_id)
            if idx < 0:
                return {"error": f"Cell with id {cell_id!r} not found"}
            cell = cells[idx]
            cell["source"] = source_lines
            if cell_type:
                cell["cell_type"] = cell_type
            msg = f"Replaced source of cell {cell_id}"

        nb["cells"] = cells
        with open(notebook_path, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
        return {"data": msg}
    except Exception as e:
        return {"error": str(e)}
