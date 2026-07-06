"""
LSP Diagnostic Registry — collect, query, and report diagnostics.

Port of: src/services/lsp/LSPDiagnosticRegistry.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Diagnostic:
    file: str
    line: int
    col: int
    message: str
    severity: str = "error"
    source: str = ""
    code: str = ""
    end_line: int = 0
    end_col: int = 0


@dataclass
class FileDiagnostics:
    file: str
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    hints: list[Diagnostic] = field(default_factory=list)
    info: list[Diagnostic] = field(default_factory=list)


@dataclass
class LSPDiagnosticRegistry:
    """Registry for collecting and querying LSP diagnostics."""

    _diagnostics: dict[str, list[Diagnostic]] = field(default_factory=dict)
    _listeners: list[Any] = field(default_factory=list, repr=False)

    def set_diagnostics(self, file: str, diagnostics: list[Diagnostic]) -> None:
        """Replace diagnostics for a file."""
        self._diagnostics[file] = diagnostics
        self._notify_listeners(file, diagnostics)

    def add_diagnostics(self, file: str, diagnostics: list[Diagnostic]) -> None:
        """Add diagnostics for a file (merge with existing)."""
        existing = self._diagnostics.get(file, [])
        existing_ids = {(d.line, d.col, d.message) for d in existing}
        for d in diagnostics:
            if (d.line, d.col, d.message) not in existing_ids:
                existing.append(d)
                existing_ids.add((d.line, d.col, d.message))
        self._diagnostics[file] = existing

    def get_diagnostics(self, file: str) -> list[Diagnostic]:
        return self._diagnostics.get(file, [])

    def get_all_diagnostics(self) -> dict[str, list[Diagnostic]]:
        return dict(self._diagnostics)

    def get_by_file(self, file: str) -> FileDiagnostics:
        diags = self._diagnostics.get(file, [])
        result = FileDiagnostics(file=file)
        for d in diags:
            if d.severity == "error":
                result.errors.append(d)
            elif d.severity == "warning":
                result.warnings.append(d)
            elif d.severity in ("hint", "info"):
                result.hints.append(d)
            else:
                result.info.append(d)
        return result

    def clear(self, file: str = "") -> None:
        if file:
            self._diagnostics.pop(file, None)
        else:
            self._diagnostics.clear()

    def get_error_count(self, file: str = "") -> int:
        if file:
            diags = self._diagnostics.get(file, [])
            return sum(1 for d in diags if d.severity == "error")
        return sum(1 for diags in self._diagnostics.values()
                   for d in diags if d.severity == "error")

    def get_warning_count(self, file: str = "") -> int:
        if file:
            diags = self._diagnostics.get(file, [])
            return sum(1 for d in diags if d.severity == "warning")
        return sum(1 for diags in self._diagnostics.values()
                   for d in diags if d.severity == "warning")

    def get_summary(self) -> dict[str, int]:
        errors = 0; warnings = 0; hints = 0
        for diags in self._diagnostics.values():
            for d in diags:
                if d.severity == "error":
                    errors += 1
                elif d.severity == "warning":
                    warnings += 1
                else:
                    hints += 1
        return {"errors": errors, "warnings": warnings, "hints": hints, "files": len(self._diagnostics)}

    def format_for_display(self, file: str = "", max_per_file: int = 10) -> str:
        """Format diagnostics as a human-readable string."""
        if file:
            files_diags = [(file, self._diagnostics.get(file, []))]
        else:
            files_diags = list(self._diagnostics.items())

        lines: list[str] = []
        for fname, diags in files_diags:
            if not diags:
                continue
            shown = diags[:max_per_file]
            lines.append(f"\n## {fname}")
            for d in shown:
                icon = {"error": "✗", "warning": "⚠", "hint": "ℹ", "info": "ℹ"}.get(d.severity, "?")
                lines.append(f"  {icon} L{d.line+1}:{d.col+1} — {d.message}")
            if len(diags) > max_per_file:
                lines.append(f"  ... and {len(diags) - max_per_file} more")
        return "\n".join(lines) if lines else "No diagnostics."

    def add_listener(self, listener: Any) -> None:
        """Add a listener that receives (file, diagnostics) on changes."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Any) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify_listeners(self, file: str, diagnostics: list[Diagnostic]) -> None:
        for listener in self._listeners:
            try:
                if callable(listener):
                    listener(file, diagnostics)
            except Exception:
                pass


# Global singleton
_instance: LSPDiagnosticRegistry | None = None


def get_diagnostic_registry() -> LSPDiagnosticRegistry:
    global _instance
    if _instance is None:
        _instance = LSPDiagnosticRegistry()
    return _instance
