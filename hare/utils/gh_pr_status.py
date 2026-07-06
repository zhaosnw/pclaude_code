"""
GitHub PR review status via `gh pr view`.

Port of: src/utils/ghPrStatus.ts
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from hare.utils.exec_file_no_throw import (
    exec_file_no_throw,
    exec_file_no_throw_with_cwd,
)

GH_TIMEOUT_MS = 5000

PrReviewState = Literal[
    "approved", "pending", "changes_requested", "draft", "merged", "closed"
]


@dataclass
class PrStatus:
    number: int
    url: str
    review_state: PrReviewState


def derive_review_state(is_draft: bool, review_decision: str) -> PrReviewState:
    if is_draft:
        return "draft"
    if review_decision == "APPROVED":
        return "approved"
    if review_decision == "CHANGES_REQUESTED":
        return "changes_requested"
    return "pending"


async def _get_default_branch(cwd: str) -> str | None:
    r = await exec_file_no_throw_with_cwd(
        "git",
        ["symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=cwd,
        preserve_output_on_error=False,
    )
    if r["code"] != 0:
        return None
    ref = (r["stdout"] or "").strip()
    if not ref:
        return None
    return ref.split("/")[-1]


async def fetch_pr_status() -> PrStatus | None:
    from hare.utils.git import find_git_root, get_current_branch

    root = await find_git_root()
    if not root:
        return None
    branch = await get_current_branch(root)
    default_branch = await _get_default_branch(root)
    if not branch or branch == default_branch:
        return None
    r = await exec_file_no_throw(
        "gh",
        [
            "pr",
            "view",
            "--json",
            "number,url,reviewDecision,isDraft,headRefName,state",
        ],
        {"timeout": GH_TIMEOUT_MS, "preserve_output_on_error": False},
    )
    if r["code"] != 0 or not (r["stdout"] or "").strip():
        return None
    try:
        data = json.loads(r["stdout"])
        head = data.get("headRefName")
        if head in (default_branch, "main", "master"):
            return None
        if data.get("state") in ("MERGED", "CLOSED"):
            return None
        return PrStatus(
            number=int(data["number"]),
            url=str(data["url"]),
            review_state=derive_review_state(
                bool(data.get("isDraft")), str(data.get("reviewDecision", ""))
            ),
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
