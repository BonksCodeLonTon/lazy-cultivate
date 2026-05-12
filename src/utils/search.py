"""Vietnamese-aware substring search helpers shared by inventory pickers.

Two pieces:

* ``normalize_vn(text)`` strips diacritics + casefolds so 'đan' matches
  'Đan', 'ban' matches 'Bạn', and 'CHO' matches 'cho'. ``Đ/đ`` aren't
  decomposable diacritics — they're letters in their own right — so they
  get a manual fallback to ``D/d`` for search purposes.
* ``matches(query, *fields)`` returns True when the (normalized) query
  is a substring of any (normalized) field. Empty query matches all.
* ``SearchModal`` is a single-input Discord Modal that re-invokes a
  callback with the entered query. Used by inventory pickers to drive
  per-page filtering without changing their pagination contract.
"""
from __future__ import annotations

import unicodedata
from typing import Awaitable, Callable

import discord


def normalize_vn(text: str) -> str:
    """Lowercase + strip Vietnamese diacritics for substring matching."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text)
    no_diacritics = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    # Đ / đ are standalone Latin letters in Vietnamese, not combining
    # diacritics — NFD won't decompose them. Manual fallback so
    # ``đan`` matches ``dan``.
    no_diacritics = no_diacritics.replace("Đ", "D").replace("đ", "d")
    return no_diacritics.casefold()


def matches(query: str, *fields: str) -> bool:
    """True when ``query`` (normalized) is a substring of any field
    (normalized). Empty / whitespace-only query matches everything so
    the caller can use it as a passthrough filter.
    """
    q = normalize_vn(query)
    if not q:
        return True
    return any(q in normalize_vn(f) for f in fields if f)


class SearchModal(discord.ui.Modal):
    """One-input modal used by picker views to capture a search query.

    ``on_submit_cb`` is awaited with ``(interaction, query_str)``. The
    field is intentionally optional (``required=False``) so submitting
    an empty modal acts as "clear filter" for callers that route empty
    strings through the same code path.
    """

    def __init__(
        self,
        title: str,
        on_submit_cb: Callable[[discord.Interaction, str], Awaitable[None]],
        placeholder: str = "Nhập tên vật phẩm cần tìm...",
        default: str = "",
    ) -> None:
        super().__init__(title=title)
        self._cb = on_submit_cb
        self._input = discord.ui.TextInput(
            label="Từ Khoá",
            placeholder=placeholder,
            default=default or "",
            required=False,
            max_length=64,
        )
        self.add_item(self._input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cb(interaction, str(self._input.value).strip())
