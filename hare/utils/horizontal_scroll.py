"""Horizontal tab strip scrolling window (`horizontalScroll.ts`)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HorizontalScrollWindow:
    start_index: int
    end_index: int
    show_left_arrow: bool
    show_right_arrow: bool


def calculate_horizontal_scroll_window(
    item_widths: list[float],
    available_width: float,
    arrow_width: float,
    selected_idx: int,
    first_item_has_separator: bool = True,
) -> HorizontalScrollWindow:
    total_items = len(item_widths)
    if total_items == 0:
        return HorizontalScrollWindow(0, 0, False, False)

    clamped_selected = max(0, min(selected_idx, total_items - 1))
    total_width = sum(item_widths)
    if total_width <= available_width:
        return HorizontalScrollWindow(0, total_items, False, False)

    cumulative: list[float] = [0.0]
    for w in item_widths:
        cumulative.append(cumulative[-1] + w)

    def range_width(start: int, end: int) -> float:
        base = cumulative[end] - cumulative[start]
        if first_item_has_separator and start > 0:
            return base - 1.0
        return base

    def effective_width(start: int, end: int) -> float:
        w = available_width
        if start > 0:
            w -= arrow_width
        if end < total_items:
            w -= arrow_width
        return w

    start_index = 0
    end_index = 1
    while end_index < total_items and range_width(
        start_index, end_index + 1
    ) <= effective_width(start_index, end_index + 1):
        end_index += 1

    if clamped_selected >= start_index and clamped_selected < end_index:
        return HorizontalScrollWindow(
            start_index,
            end_index,
            start_index > 0,
            end_index < total_items,
        )

    if clamped_selected >= end_index:
        end_index = clamped_selected + 1
        start_index = clamped_selected
        while start_index > 0 and range_width(
            start_index - 1, end_index
        ) <= effective_width(start_index - 1, end_index):
            start_index -= 1
    else:
        start_index = clamped_selected
        end_index = clamped_selected + 1
        while end_index < total_items and range_width(
            start_index, end_index + 1
        ) <= effective_width(start_index, end_index + 1):
            end_index += 1

    return HorizontalScrollWindow(
        start_index,
        end_index,
        start_index > 0,
        end_index < total_items,
    )
