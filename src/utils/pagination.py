"""Pagination helpers for Discord ``Select`` components.

Discord caps each ``Select`` at 25 options. When a player's bag (pills,
elixirs, gems, forge materials, equipment, …) grows past that, anything
past index 24 becomes unreachable from the picker. The helpers here let
a view show one page of ≤ 25 items at a time and add prev/next buttons
that re-render the same view with the next slice.

Each affected view stores a ``_page`` int and rebuilds its select
options from the slice for that page.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Sequence, TypeVar

import discord


PAGE_SIZE = 25

T = TypeVar("T")


def page_slice(items: Sequence[T], page: int, *, per_page: int = PAGE_SIZE) -> list[T]:
    """Return the slice for ``page`` (clamped) of size ``per_page``."""
    if not items:
        return []
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return list(items[start:start + per_page])


def total_pages(total: int, *, per_page: int = PAGE_SIZE) -> int:
    """Number of pages needed to display ``total`` items."""
    if total <= 0:
        return 1
    return (total + per_page - 1) // per_page


def add_page_controls(
    view: discord.ui.View,
    *,
    page: int,
    total: int,
    on_change: Callable[[discord.Interaction, int], Awaitable[None]],
    row: int = 4,
    per_page: int = PAGE_SIZE,
) -> None:
    """Attach ``◀ Trang trước`` / ``Trang sau ▶`` buttons to ``view``.

    ``on_change`` is invoked with the new page index when either button
    is clicked. Does nothing when ``total <= per_page`` (single page).
    """
    if total <= per_page:
        return

    pages = total_pages(total, per_page=per_page)
    page = max(0, min(page, pages - 1))

    prev_btn = discord.ui.Button(
        label=f"◀ Trang {page} / {pages}",
        style=discord.ButtonStyle.secondary,
        row=row,
        disabled=(page <= 0),
    )

    async def _prev(interaction: discord.Interaction) -> None:
        await on_change(interaction, page - 1)

    prev_btn.callback = _prev
    view.add_item(prev_btn)

    next_btn = discord.ui.Button(
        label=f"Trang {page + 2} / {pages} ▶",
        style=discord.ButtonStyle.secondary,
        row=row,
        disabled=(page >= pages - 1),
    )

    async def _next(interaction: discord.Interaction) -> None:
        await on_change(interaction, page + 1)

    next_btn.callback = _next
    view.add_item(next_btn)
