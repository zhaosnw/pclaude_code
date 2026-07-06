"""
Render FPS sampling for performance diagnostics.

Port of: src/utils/fpsTracker.ts
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class FpsMetrics:
    average_fps: float
    low_1_pct_fps: float


class FpsTracker:
    def __init__(self) -> None:
        self._frame_durations: list[float] = []
        self._first_render_time: float | None = None
        self._last_render_time: float | None = None

    def record(self, duration_ms: float) -> None:
        now = time.perf_counter() * 1000
        if self._first_render_time is None:
            self._first_render_time = now
        self._last_render_time = now
        self._frame_durations.append(duration_ms)

    def get_metrics(self) -> FpsMetrics | None:
        if (
            not self._frame_durations
            or self._first_render_time is None
            or self._last_render_time is None
        ):
            return None
        total_time_ms = self._last_render_time - self._first_render_time
        if total_time_ms <= 0:
            return None
        total_frames = len(self._frame_durations)
        average_fps = total_frames / (total_time_ms / 1000)
        sorted_d = sorted(self._frame_durations, reverse=True)
        p99_index = max(0, int(__import__("math").ceil(len(sorted_d) * 0.01) - 1))
        p99_frame_ms = sorted_d[p99_index]
        low_1_pct_fps = 1000 / p99_frame_ms if p99_frame_ms > 0 else 0.0
        return FpsMetrics(
            average_fps=round(average_fps * 100) / 100,
            low_1_pct_fps=round(low_1_pct_fps * 100) / 100,
        )
