"""Strip XML-like blocks from display titles (`displayTags.ts`)."""

from __future__ import annotations

import re

_XML_TAG_BLOCK_PATTERN = re.compile(
    r"<([a-z][\w-]*)(?:\s[^>]*)?>[\s\S]*?</\1>\n?",
    re.MULTILINE,
)
_IDE_CONTEXT_TAGS_PATTERN = re.compile(
    r"<(ide_opened_file|ide_selection)(?:\s[^>]*)?>[\s\S]*?</\1>\n?",
    re.MULTILINE,
)


def strip_display_tags(text: str) -> str:
    result = _XML_TAG_BLOCK_PATTERN.sub("", text).strip()
    return result if result else text


def strip_display_tags_allow_empty(text: str) -> str:
    return _XML_TAG_BLOCK_PATTERN.sub("", text).strip()


def strip_ide_context_tags(text: str) -> str:
    return _IDE_CONTEXT_TAGS_PATTERN.sub("", text).strip()
