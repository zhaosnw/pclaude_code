"""
Pagination helpers for plugin listings.

Port of: src/commands/plugin/usePagination.ts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PaginationState:
    page: int = 0
    page_size: int = 20


def next_page(state: PaginationState) -> PaginationState:
    return PaginationState(page=state.page + 1, page_size=state.page_size)
