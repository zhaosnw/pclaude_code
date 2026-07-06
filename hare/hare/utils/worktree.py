"""Git worktree helpers (`worktree.ts` — partial port)."""

from __future__ import annotations

import re

VALID_WORKTREE_SLUG_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
MAX_WORKTREE_SLUG_LENGTH = 64


def validate_worktree_slug(slug: str) -> None:
    if len(slug) > MAX_WORKTREE_SLUG_LENGTH:
        raise ValueError(
            f"Invalid worktree name: must be {MAX_WORKTREE_SLUG_LENGTH} characters or fewer (got {len(slug)})"
        )
    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ValueError(
                f'Invalid worktree name "{slug}": must not contain "." or ".." path segments'
            )
        if not segment or not VALID_WORKTREE_SLUG_SEGMENT.match(segment):
            raise ValueError(f'Invalid worktree name segment "{segment}"')
