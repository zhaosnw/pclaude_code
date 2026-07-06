"""
ANSI terminal text to SVG (`ansiToSvg.ts`).

Supports basic foreground colors, 256-color and 24-bit ANSI codes.
"""

from __future__ import annotations

from dataclasses import dataclass

from hare.utils.xml import escape_xml


@dataclass(frozen=True)
class AnsiColor:
    r: int
    g: int
    b: int


ANSI_COLORS: dict[int, AnsiColor] = {
    30: AnsiColor(0, 0, 0),
    31: AnsiColor(205, 49, 49),
    32: AnsiColor(13, 188, 121),
    33: AnsiColor(229, 229, 16),
    34: AnsiColor(36, 114, 200),
    35: AnsiColor(188, 63, 188),
    36: AnsiColor(17, 168, 205),
    37: AnsiColor(229, 229, 229),
    90: AnsiColor(102, 102, 102),
    91: AnsiColor(241, 76, 76),
    92: AnsiColor(35, 209, 139),
    93: AnsiColor(245, 245, 67),
    94: AnsiColor(59, 142, 234),
    95: AnsiColor(214, 112, 214),
    96: AnsiColor(41, 184, 219),
    97: AnsiColor(255, 255, 255),
}

DEFAULT_FG = AnsiColor(229, 229, 229)
DEFAULT_BG = AnsiColor(30, 30, 30)


@dataclass
class TextSpan:
    text: str
    color: AnsiColor
    bold: bool


ParsedLine = list[TextSpan]


def _get_256_color(index: int) -> AnsiColor:
    if index < 16:
        standard = [
            AnsiColor(0, 0, 0),
            AnsiColor(128, 0, 0),
            AnsiColor(0, 128, 0),
            AnsiColor(128, 128, 0),
            AnsiColor(0, 0, 128),
            AnsiColor(128, 0, 128),
            AnsiColor(0, 128, 128),
            AnsiColor(192, 192, 192),
            AnsiColor(128, 128, 128),
            AnsiColor(255, 0, 0),
            AnsiColor(0, 255, 0),
            AnsiColor(255, 255, 0),
            AnsiColor(0, 0, 255),
            AnsiColor(255, 0, 255),
            AnsiColor(0, 255, 255),
            AnsiColor(255, 255, 255),
        ]
        return standard[index] if index < len(standard) else DEFAULT_FG
    if index < 232:
        i = index - 16
        r = i // 36
        g = (i % 36) // 6
        b = i % 6
        return AnsiColor(
            0 if r == 0 else 55 + r * 40,
            0 if g == 0 else 55 + g * 40,
            0 if b == 0 else 55 + b * 40,
        )
    gray = (index - 232) * 10 + 8
    return AnsiColor(gray, gray, gray)


def parse_ansi(text: str) -> list[ParsedLine]:
    lines: list[ParsedLine] = []
    for raw_line in text.split("\n"):
        spans: list[TextSpan] = []
        current_color = DEFAULT_FG
        bold = False
        i = 0
        while i < len(raw_line):
            if (
                i + 1 < len(raw_line)
                and raw_line[i] == "\x1b"
                and raw_line[i + 1] == "["
            ):
                j = i + 2
                while j < len(raw_line) and not (raw_line[j].isalpha()):
                    j += 1
                if j < len(raw_line) and raw_line[j] == "m":
                    seq = raw_line[i + 2 : j]
                    codes = [
                        int(x) if x.isdigit() else 0 for x in seq.split(";") if x != ""
                    ]
                    k = 0
                    while k < len(codes):
                        code = codes[k]
                        if code == 0:
                            current_color = DEFAULT_FG
                            bold = False
                        elif code == 1:
                            bold = True
                        elif 30 <= code <= 37 or 90 <= code <= 97:
                            current_color = ANSI_COLORS.get(code, DEFAULT_FG)
                        elif code == 39:
                            current_color = DEFAULT_FG
                        elif code == 38 and k + 1 < len(codes):
                            if codes[k + 1] == 5 and k + 2 < len(codes):
                                current_color = _get_256_color(codes[k + 2])
                                k += 2
                            elif codes[k + 1] == 2 and k + 4 < len(codes):
                                current_color = AnsiColor(
                                    codes[k + 2], codes[k + 3], codes[k + 4]
                                )
                                k += 4
                        k += 1
                    i = j + 1
                    continue
            start = i
            while i < len(raw_line) and not (
                i + 1 < len(raw_line)
                and raw_line[i] == "\x1b"
                and raw_line[i + 1] == "["
            ):
                i += 1
            chunk = raw_line[start:i]
            if chunk:
                spans.append(TextSpan(text=chunk, color=current_color, bold=bold))
        if not spans:
            spans.append(TextSpan(text="", color=DEFAULT_FG, bold=False))
        lines.append(spans)
    return lines


@dataclass
class AnsiToSvgOptions:
    font_family: str = "Menlo, Monaco, monospace"
    font_size: int = 14
    line_height: int = 22
    padding_x: int = 24
    padding_y: int = 24
    background_color: str | None = None
    border_radius: int = 8


def ansi_to_svg(ansi_text: str, options: AnsiToSvgOptions | None = None) -> str:
    opts = options or AnsiToSvgOptions()
    bg = opts.background_color or f"rgb({DEFAULT_BG.r}, {DEFAULT_BG.g}, {DEFAULT_BG.b})"
    lines = parse_ansi(ansi_text)
    while lines and all(not s.text.strip() for s in lines[-1]):
        lines.pop()
    char_w = opts.font_size * 0.6
    max_len = max((sum(len(s.text) for s in sp) for sp in lines), default=0)
    width = int(max_len * char_w + opts.padding_x * 2)
    height = len(lines) * opts.line_height + opts.padding_y * 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n',
        f'  <rect width="100%" height="100%" fill="{bg}" rx="{opts.border_radius}" ry="{opts.border_radius}"/>\n',
        "  <style>\n",
        f"    text {{ font-family: {opts.font_family}; font-size: {opts.font_size}px; white-space: pre; }}\n",
        "    .b { font-weight: bold; }\n",
        "  </style>\n",
    ]
    for line_index, spans in enumerate(lines):
        y = (
            opts.padding_y
            + (line_index + 1) * opts.line_height
            - (opts.line_height - opts.font_size) / 2
        )
        parts.append(f'  <text x="{opts.padding_x}" y="{y}" xml:space="preserve">')
        for span in spans:
            if not span.text:
                continue
            rgb = f"rgb({span.color.r}, {span.color.g}, {span.color.b})"
            cls = ' class="b"' if span.bold else ""
            parts.append(f'<tspan fill="{rgb}"{cls}>{escape_xml(span.text)}</tspan>')
        parts.append("</text>\n")
    parts.append("</svg>")
    return "".join(parts)
