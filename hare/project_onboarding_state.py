"""Project onboarding wizard state (port of src/projectOnboardingState.ts)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Step:
    key: str
    text: str
    is_complete: bool
    is_completable: bool
    is_enabled: bool


def get_steps() -> list[Step]:
    has_hare_md = os.path.isfile(os.path.join(os.getcwd(), "HARE.md"))
    is_empty = not os.listdir(os.getcwd())
    return [
        Step(
            key="workspace",
            text="Ask Hare to create a new app or clone a repository",
            is_complete=False,
            is_completable=True,
            is_enabled=is_empty,
        ),
        Step(
            key="hare_md",
            text="Run /init to create a HARE.md file",
            is_complete=has_hare_md,
            is_completable=True,
            is_enabled=not is_empty,
        ),
    ]


def is_project_onboarding_complete() -> bool:
    steps = [s for s in get_steps() if s.is_completable and s.is_enabled]
    return all(s.is_complete for s in steps)


def maybe_mark_project_onboarding_complete() -> None:
    if is_project_onboarding_complete():
        return


def should_show_project_onboarding() -> bool:
    if os.environ.get("IS_DEMO"):
        return False
    return not is_project_onboarding_complete()


def increment_project_onboarding_seen_count() -> None:
    return
