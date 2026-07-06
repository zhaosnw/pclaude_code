"""User prompt keyword detectors (port of userPromptKeywords.ts)."""

from __future__ import annotations

import re

_NEGATIVE = re.compile(
    r"\b(wtf|wth|ffs|omfg|shit(ty|tiest)?|dumbass|horrible|awful|piss(ed|ing)? off|"
    r"piece of (shit|crap|junk)|what the (fuck|hell)|fucking? (broken|useless|terrible|awful|horrible)|"
    r"fuck you|screw (this|you)|so frustrating|this sucks|damn it)\b",
    re.IGNORECASE,
)
_KEEP_GOING = re.compile(r"\b(keep going|go on)\b", re.IGNORECASE)


def matches_negative_keyword(input_str: str) -> bool:
    return bool(_NEGATIVE.search(input_str.lower()))


def matches_keep_going_keyword(input_str: str) -> bool:
    lower = input_str.lower().strip()
    if lower == "continue":
        return True
    return bool(_KEEP_GOING.search(lower))
