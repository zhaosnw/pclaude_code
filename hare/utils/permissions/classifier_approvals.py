"""Classifier approval / checking store (`classifierApprovals.ts`)."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from hare.utils.signal import create_signal

ClassifierKind = Literal["bash", "auto-mode"]


@dataclass
class ClassifierApproval:
    classifier: ClassifierKind
    matched_rule: str | None = None
    reason: str | None = None


_approvals: dict[str, ClassifierApproval] = {}
_classifier_checking: set[str] = set()
_classifier_checking_signal = create_signal()


def _feature_bash_classifier() -> bool:
    return os.environ.get("BASH_CLASSIFIER", "") == "1"


def _feature_transcript_classifier() -> bool:
    return os.environ.get("TRANSCRIPT_CLASSIFIER", "") == "1"


def set_classifier_approval(tool_use_id: str, matched_rule: str) -> None:
    if not _feature_bash_classifier():
        return
    _approvals[tool_use_id] = ClassifierApproval(
        classifier="bash", matched_rule=matched_rule
    )


def get_classifier_approval(tool_use_id: str) -> str | None:
    if not _feature_bash_classifier():
        return None
    a = _approvals.get(tool_use_id)
    if not a or a.classifier != "bash":
        return None
    return a.matched_rule


def set_yolo_classifier_approval(tool_use_id: str, reason: str) -> None:
    if not _feature_transcript_classifier():
        return
    _approvals[tool_use_id] = ClassifierApproval(classifier="auto-mode", reason=reason)


def get_yolo_classifier_approval(tool_use_id: str) -> str | None:
    if not _feature_transcript_classifier():
        return None
    a = _approvals.get(tool_use_id)
    if not a or a.classifier != "auto-mode":
        return None
    return a.reason


def set_classifier_checking(tool_use_id: str) -> None:
    if not _feature_bash_classifier() and not _feature_transcript_classifier():
        return
    _classifier_checking.add(tool_use_id)
    _classifier_checking_signal.emit()


def clear_classifier_checking(tool_use_id: str) -> None:
    if not _feature_bash_classifier() and not _feature_transcript_classifier():
        return
    _classifier_checking.discard(tool_use_id)
    _classifier_checking_signal.emit()


def subscribe_classifier_checking(cb: Callable[[], None]) -> Callable[[], None]:
    return _classifier_checking_signal.subscribe(cb)


def is_classifier_checking(tool_use_id: str) -> bool:
    return tool_use_id in _classifier_checking


def delete_classifier_approval(tool_use_id: str) -> None:
    _approvals.pop(tool_use_id, None)


def clear_classifier_approvals() -> None:
    _approvals.clear()
    _classifier_checking.clear()
    _classifier_checking_signal.emit()


def add_classifier_approval(key: str) -> None:
    set_classifier_approval(key, matched_rule="legacy")


def has_classifier_approval(key: str) -> bool:
    return key in _approvals
