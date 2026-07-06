"""
Convert LSP diagnostics to Hare attachment format (passive feedback).

Port of: src/services/lsp/passiveFeedback.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote, urlparse


LspSeverity = Literal["Error", "Warning", "Info", "Hint"]


def map_lsp_severity(lsp_severity: int | None) -> LspSeverity:
    if lsp_severity == 1:
        return "Error"
    if lsp_severity == 2:
        return "Warning"
    if lsp_severity == 3:
        return "Info"
    if lsp_severity == 4:
        return "Hint"
    return "Error"


@dataclass
class DiagnosticEntry:
    message: str
    severity: LspSeverity
    range: dict[str, Any]
    source: str | None = None
    code: str | int | None = None


@dataclass
class DiagnosticFile:
    path: str
    diagnostics: list[DiagnosticEntry]


def _uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        p = urlparse(uri)
        return unquote(p.path)
    return uri


def format_diagnostics_for_attachment(params: dict[str, Any]) -> list[DiagnosticFile]:
    uri = _uri_to_path(str(params.get("uri", "")))
    raw_diags = params.get("diagnostics") or []
    entries: list[DiagnosticEntry] = []
    for d in raw_diags:
        if not isinstance(d, dict):
            continue
        rng = d.get("range") or {}
        start = rng.get("start") or {}
        end = rng.get("end") or {}
        entries.append(
            DiagnosticEntry(
                message=str(d.get("message", "")),
                severity=map_lsp_severity(d.get("severity")),
                range={
                    "start": {
                        "line": int(start.get("line", 0)),
                        "character": int(start.get("character", 0)),
                    },
                    "end": {
                        "line": int(end.get("line", 0)),
                        "character": int(end.get("character", 0)),
                    },
                },
                source=d.get("source") if isinstance(d.get("source"), str) else None,
                code=d.get("code"),
            )
        )
    return [DiagnosticFile(path=uri, diagnostics=entries)]
