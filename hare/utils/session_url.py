"""Parse session resume identifiers — UUID, URL, or .jsonl path (port of sessionUrl.ts)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_uuid(maybe: object) -> str | None:
    if not isinstance(maybe, str):
        return None
    return maybe if _UUID_RE.match(maybe) else None


@dataclass
class ParsedSessionUrl:
    session_id: str
    ingress_url: str | None
    is_url: bool
    jsonl_file: str | None
    is_jsonl_file: bool


def parse_session_identifier(resume_identifier: str) -> ParsedSessionUrl | None:
    if resume_identifier.lower().endswith(".jsonl"):
        return ParsedSessionUrl(
            session_id=str(uuid.uuid4()),
            ingress_url=None,
            is_url=False,
            jsonl_file=resume_identifier,
            is_jsonl_file=True,
        )

    vu = validate_uuid(resume_identifier)
    if vu:
        return ParsedSessionUrl(
            session_id=vu,
            ingress_url=None,
            is_url=False,
            jsonl_file=None,
            is_jsonl_file=False,
        )

    try:
        parsed = urlparse(resume_identifier)
        if parsed.scheme and parsed.netloc:
            return ParsedSessionUrl(
                session_id=str(uuid.uuid4()),
                ingress_url=parsed.geturl(),
                is_url=True,
                jsonl_file=None,
                is_jsonl_file=False,
            )
    except Exception:
        pass

    return None
