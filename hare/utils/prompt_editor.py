"""
External editor integration for prompts and files. Port of src/utils/promptEditor.ts.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

from hare.utils.editor import classify_gui_editor, get_external_editor
from hare.utils.fs_operations import get_fs_implementation

EDITOR_OVERRIDES: dict[str, str] = {
    "code": "code -w",
    "subl": "subl --wait",
}


@dataclass
class EditorResult:
    content: str | None
    error: str | None = None


def _is_gui_editor(editor: str) -> bool:
    return classify_gui_editor(editor) is not None


def edit_file_in_editor(file_path: str) -> EditorResult:
    """
    Open `file_path` in $EDITOR / configured editor; return updated contents.
    Ink pause/resume is omitted in the Python port — callers run off the REPL.
    """
    fs = get_fs_implementation()
    editor = get_external_editor()
    if not editor:
        return EditorResult(content=None)
    try:
        fs.stat_sync(file_path)
    except OSError:
        return EditorResult(content=None)
    cmd = EDITOR_OVERRIDES.get(editor, editor)
    try:
        subprocess.run(f'{cmd} "{file_path}"', shell=True, check=True)  # nosec B602
    except subprocess.CalledProcessError as e:
        return EditorResult(
            content=None, error=f"{editor} exited with code {e.returncode}"
        )
    except OSError:
        return EditorResult(content=None)
    try:
        return EditorResult(content=fs.read_file_sync(file_path))
    except OSError:
        return EditorResult(content=None)


def _recollapse_pasted_content(
    edited_prompt: str,
    pasted_contents: dict[int, dict[str, str]],
) -> str:
    collapsed = edited_prompt
    for sid, content in pasted_contents.items():
        if content.get("type") != "text":
            continue
        text = content.get("content") or ""
        if not text:
            continue
        idx = collapsed.find(text)
        if idx != -1:
            lines = text.count("\n") + 1
            ref = f"[Pasted text {sid} +{lines} lines]"
            collapsed = collapsed[:idx] + ref + collapsed[idx + len(text) :]
    return collapsed


def edit_prompt_in_editor(
    current_prompt: str,
    pasted_contents: dict[int, dict[str, str]] | None = None,
) -> EditorResult:
    """Write prompt to a temp file, launch editor, read back (optional paste re-collapse)."""
    fs = get_fs_implementation()
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(current_prompt)
        path = tmp.name
    try:
        result = edit_file_in_editor(path)
        if result.content is None:
            return result
        final = result.content
        if final.endswith("\n") and not final.endswith("\n\n"):
            final = final[:-1]
        if pasted_contents:
            final = _recollapse_pasted_content(final, pasted_contents)
        return EditorResult(content=final)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
