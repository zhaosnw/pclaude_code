"""Strip nondeterminism from runner output before golden comparison.

Both the recorded golden (from the TS reference) and hare's live output are
passed through this same function, so timestamps/uuids/paths/cost-jitter never
cause spurious diffs. Keep this conservative: only mask things that are
*provably* nondeterministic — masking real behavior hides bugs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)
_MS_RE = re.compile(r"\b\d+(?:\.\d+)?ms\b")
_COST_RE = re.compile(r"\$\d+\.\d+")
_VOLATILE_KEYS = {"session_id", "uuid", "request_id", "message_id"}
_DURATION_KEYS = {
    "duration_ms",
    "duration_api_ms",
    "ttft_ms",
    "created_at",
    "timestamp",
}
_DROP_TOPLEVEL = {"duration_ms"}
_RUNTIME_STATE_DIRS = (".hare", ".claude")


def _scrub_str(s: str, sandbox_root: str | None) -> str:
    if sandbox_root:
        s = s.replace(sandbox_root, "<SANDBOX>")
    s = _UUID_RE.sub("<UUID>", s)
    s = _MS_RE.sub("<DURATION>", s)
    s = _COST_RE.sub("<COST>", s)
    return s


def _scrub(obj: Any, sandbox_root: str | None) -> Any:
    if isinstance(obj, str):
        return _scrub_str(obj, sandbox_root)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _VOLATILE_KEYS:
                out[k] = "<UUID>"
            elif k in _DURATION_KEYS:
                out[k] = "<DURATION>"
            else:
                out[k] = _scrub(v, sandbox_root)
        return out
    if isinstance(obj, list):
        return [_scrub(x, sandbox_root) for x in obj]
    return obj


def normalize_result(
    result: dict[str, Any], sandbox_root: str | None = None
) -> dict[str, Any]:
    out = {k: v for k, v in result.items() if k not in _DROP_TOPLEVEL}
    # The file snapshot is already captured environment-independently by
    # snapshot_files (sandbox root scrubbed to <SANDBOX>). Pass it through
    # untouched: the generic scrubber's UUID regex would otherwise corrupt the
    # 64-hex-char sha256 digests (masking them to <UUID> and defeating the
    # binary-file fallback comparison).
    files = out.pop("files", None)
    out = _scrub(out, sandbox_root)
    if files is not None:
        out["files"] = files
    return out


def snapshot_files(root: Path) -> list[dict[str, Any]]:
    """Capture every file under ``root`` as a sorted list of
    ``{"path", "sha256", "text"}`` entries. ``path`` is relative to ``root``;
    ``text`` is the utf-8-decoded content with ``root`` scrubbed to <SANDBOX>
    (None when the bytes aren't valid utf-8). Used on both sides of the
    differential (hare's sandbox and the TS reference's), so the recorded
    snapshot is environment-independent and content-comparable."""
    root = Path(root)
    root_str = str(root)
    snap: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_path = p.relative_to(root)
        if rel_path.parts and rel_path.parts[0] in _RUNTIME_STATE_DIRS:
            continue
        data = p.read_bytes()
        try:
            text: str | None = data.decode("utf-8").replace(root_str, "<SANDBOX>")
        except UnicodeDecodeError:
            text = None
        snap.append(
            {
                "path": str(rel_path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "text": text,
            }
        )
    return snap


def compare_file_effects(
    actual_files: list[dict[str, Any]],
    golden_files: list[dict[str, Any]],
) -> str | None:
    """Diff two post-run sandbox file snapshots. Returns None when they match,
    else a human-readable mismatch description.

    Each entry is ``{"path": str, "sha256": str, "text": str | None}`` where
    ``text`` is the decoded file content with each side's own sandbox root
    already scrubbed to ``<SANDBOX>`` at capture time (so the comparison is
    environment-independent). When ``text`` is None on either side (binary /
    undecodable), the entries are compared by ``sha256`` instead.
    """
    actual = {f["path"]: f for f in actual_files}
    golden = {f["path"]: f for f in golden_files}

    problems: list[str] = []
    for path in sorted(set(golden) - set(actual)):
        problems.append(f"missing file (expected, not produced): {path!r}")
    for path in sorted(set(actual) - set(golden)):
        problems.append(f"unexpected file (produced, not expected): {path!r}")

    for path in sorted(set(actual) & set(golden)):
        a, g = actual[path], golden[path]
        a_text, g_text = a.get("text"), g.get("text")
        if a_text is not None and g_text is not None:
            if a_text != g_text:
                problems.append(
                    f"content mismatch for {path!r}:\n"
                    f"  --- expected (reference) ---\n{g_text!r}\n"
                    f"  --- actual (hare) ---\n{a_text!r}"
                )
        elif a.get("sha256") != g.get("sha256"):
            problems.append(
                f"content mismatch for {path!r} (binary): "
                f"sha256 {a.get('sha256')!r} != {g.get('sha256')!r}"
            )

    return "\n".join(problems) if problems else None
