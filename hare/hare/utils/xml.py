"""Minimal XML escaping helpers (`xml.ts`)."""

from __future__ import annotations

import html


def escape_xml(text: str) -> str:
    """Escape ``&``, ``<``, ``>``, ``"`` for SVG/XML text nodes."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def escape_xml_alt(text: str) -> str:
    """Use stdlib html.escape (attribute-safe)."""
    return html.escape(text, quote=True)
