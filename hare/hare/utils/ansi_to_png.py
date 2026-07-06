"""
ANSI to PNG (`ansiToPng.ts`).

The TypeScript build embeds a full bitmap font and zlib-deflated RGBA blit.
That pipeline is not ported; :func:`ansi_to_png` returns a minimal valid PNG
placeholder. Use :mod:`hare.utils.ansi_to_svg` for faithful text rendering, or
wire Pillow/skia in production.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from hare.utils.ansi_to_svg import DEFAULT_BG, AnsiColor


@dataclass
class AnsiToPngOptions:
    scale: int = 1
    padding_x: int = 48
    padding_y: int = 48
    border_radius: int = 16
    background: AnsiColor | None = None


def _minimal_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Single-IDAT RGBA PNG."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk_ihdr = _png_chunk(b"IHDR", ihdr)
    raw = b"".join(
        b"\x00" + rgba[i * width * 4 : (i + 1) * width * 4] for i in range(height)
    )
    compressed = zlib.compress(raw, level=9)
    chunk_idat = _png_chunk(b"IDAT", compressed)
    chunk_iend = _png_chunk(b"IEND", b"")
    return sig + chunk_ihdr + chunk_idat + chunk_iend


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def ansi_to_png(ansi_text: str, options: AnsiToPngOptions | None = None) -> bytes:
    del ansi_text  # placeholder; real renderer would parse ANSI and blit glyphs
    opts = options or AnsiToPngOptions()
    bg = opts.background or DEFAULT_BG
    w, h = max(1, 32 * opts.scale), max(1, 32 * opts.scale)
    pixel = bytes([bg.r, bg.g, bg.b, 255])
    rgba = pixel * (w * h)
    return _minimal_png_rgba(w, h, rgba)
