"""NotebookEdit aligned to the 2.1.88 schema: cell_id + new_source + edit_mode
(replace|insert|delete) + cell_type. (Was a divergent cell_index/old_string model.)"""

import asyncio
import json
from pathlib import Path

from hare.tools_impl.NotebookEditTool.notebook_edit_tool import call, input_schema


def _nb(path):
    path.write_text(json.dumps({
        "cells": [
            {"id": "c1", "cell_type": "code", "source": ["print(1)\n"], "metadata": {},
             "outputs": [], "execution_count": None},
            {"id": "c2", "cell_type": "markdown", "source": ["# title\n"], "metadata": {}},
        ],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_matches_2188_param_model():
    s = input_schema()
    assert set(s["properties"]) == {
        "notebook_path", "cell_id", "new_source", "cell_type", "edit_mode"}
    assert set(s["required"]) == {"notebook_path", "new_source"}


def test_replace_cell_source(tmp_path):
    p = tmp_path / "nb.ipynb"
    _nb(p)
    asyncio.run(call(notebook_path=str(p), cell_id="c1", new_source="print(42)",
                     edit_mode="replace"))
    nb = _read(p)
    assert "".join(nb["cells"][0]["source"]) == "print(42)"
    assert nb["cells"][0]["id"] == "c1"


def test_insert_cell_after(tmp_path):
    p = tmp_path / "nb.ipynb"
    _nb(p)
    asyncio.run(call(notebook_path=str(p), cell_id="c1", new_source="# new",
                     cell_type="markdown", edit_mode="insert"))
    nb = _read(p)
    assert len(nb["cells"]) == 3
    assert "".join(nb["cells"][1]["source"]) == "# new"
    assert nb["cells"][1]["cell_type"] == "markdown"


def test_delete_cell(tmp_path):
    p = tmp_path / "nb.ipynb"
    _nb(p)
    asyncio.run(call(notebook_path=str(p), cell_id="c2", new_source="",
                     edit_mode="delete"))
    nb = _read(p)
    assert len(nb["cells"]) == 1
    assert nb["cells"][0]["id"] == "c1"
