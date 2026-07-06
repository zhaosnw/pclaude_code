"""
Tests for project_onboarding_state.py — onboarding wizard state.
"""

from __future__ import annotations

import os
import tempfile
from unittest import mock

from hare.project_onboarding_state import (
    Step,
    get_steps,
    is_project_onboarding_complete,
    maybe_mark_project_onboarding_complete,
    should_show_project_onboarding,
    increment_project_onboarding_seen_count,
)


class TestStep:
    def test_step_creation(self) -> None:
        s = Step(
            key="test",
            text="test step",
            is_complete=False,
            is_completable=True,
            is_enabled=True,
        )
        assert s.key == "test"
        assert s.text == "test step"
        assert s.is_complete is False
        assert s.is_completable is True
        assert s.is_enabled is True


class TestGetSteps:
    def test_returns_list(self) -> None:
        steps = get_steps()
        assert isinstance(steps, list)
        assert len(steps) >= 2

    def test_has_workspace_step(self) -> None:
        steps = get_steps()
        workspace = next(s for s in steps if s.key == "workspace")
        assert workspace.is_completable is True
        assert workspace.is_complete is False

    def test_has_hare_md_step(self) -> None:
        steps = get_steps()
        hare_md = next(s for s in steps if s.key == "hare_md")
        assert hare_md.is_completable is True

    def test_empty_dir_enables_workspace_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("os.getcwd", return_value=tmp):
                with mock.patch("os.listdir", return_value=[]):
                    with mock.patch("os.path.isfile", return_value=False):
                        steps = get_steps()
                        workspace = next(s for s in steps if s.key == "workspace")
                        assert workspace.is_enabled is True

    def test_non_empty_dir_disables_workspace_step(self) -> None:
        with mock.patch("os.listdir", return_value=["file1.py"]):
            with mock.patch("os.path.isfile", return_value=False):
                steps = get_steps()
                workspace = next(s for s in steps if s.key == "workspace")
                assert workspace.is_enabled is False


class TestIsProjectOnboardingComplete:
    def test_not_complete_when_no_hare_md(self) -> None:
        with mock.patch("os.path.isfile", return_value=False):
            with mock.patch("os.listdir", return_value=["file.py"]):
                assert is_project_onboarding_complete() is False

    def test_complete_when_hare_md_exists(self) -> None:
        with mock.patch("os.path.isfile", return_value=True):
            with mock.patch("os.listdir", return_value=["file.py"]):
                assert is_project_onboarding_complete() is True


class TestShouldShowProjectOnboarding:
    def test_returns_false_when_demo(self) -> None:
        with mock.patch.dict(os.environ, {"IS_DEMO": "true"}):
            assert should_show_project_onboarding() is False

    def test_returns_false_when_complete(self) -> None:
        with mock.patch("os.path.isfile", return_value=True):
            with mock.patch("os.listdir", return_value=["file.py"]):
                with mock.patch.dict(os.environ, {}, clear=True):
                    assert should_show_project_onboarding() is False

    def test_returns_true_when_not_complete(self) -> None:
        with mock.patch("os.path.isfile", return_value=False):
            with mock.patch("os.listdir", return_value=[]):
                with mock.patch.dict(os.environ, {}, clear=True):
                    assert should_show_project_onboarding() is True


class TestMaybeMarkProjectOnboardingComplete:
    def test_is_noop(self) -> None:
        # Function is a no-op stub
        result = maybe_mark_project_onboarding_complete()
        assert result is None


class TestIncrementProjectOnboardingSeenCount:
    def test_is_noop(self) -> None:
        # Function is a no-op stub
        result = increment_project_onboarding_seen_count()
        assert result is None
