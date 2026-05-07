"""Phân Giải Trang Bị (Recycle equipment) — turn an unwanted item back into
a single ``forge_material`` keyed to the item's grade.

Each recycle yields **one** random forge_material whose ``grade`` matches
the equipment's tier via ``_RECYCLE_MAT_GRADE_BY_EQUIP_GRADE`` — the same
mapping the cheaper option of each ``forge_recipes`` entry uses, so a
recycled Grade-N item can never produce a higher-tier material than the
recipe accepts at that grade. That asymmetry (forge takes 5–6, recycle
returns 1) makes recycling a net resource sink: useful for clearing junk
out of the bag and salvaging niche affix-bias materials, never an
infinite generator.

Pure module — no DB. Caller decides which equipment instances to
delete and persists the returned material rows itself, so the same logic
is reusable for single recycle and bulk-recycle paths.
"""
from __future__ import annotations

import random

from src.data.registry import registry


# Equipment grade → forge_material grade. Mirrors the cheaper option of
# each entry in ``src/data/equipment/forge_recipes.json`` — recycling
# never returns a higher-tier material than the recipe accepts at that
# grade, preventing players from farming better materials by recycling
# than by forging fresh.
_RECYCLE_MAT_GRADE_BY_EQUIP_GRADE: dict[int, int] = {
    1: 1, 2: 1, 3: 2, 4: 2, 5: 3,
    6: 3, 7: 4, 8: 5, 9: 6,
}


def get_recycle_material_grade(item_grade: int) -> int | None:
    """Return the forge_material grade a recycle of ``item_grade`` will yield.

    UI-side preview helper — pairs with ``recycle_equipment`` which actually
    performs the random pick. Returns ``None`` for grades outside 1..9.
    """
    return _RECYCLE_MAT_GRADE_BY_EQUIP_GRADE.get(int(item_grade))


def recycle_equipment(item_grade: int) -> str | None:
    """Return one random forge_material key for an equipment of ``item_grade``.

    Returns ``None`` only when no eligible material exists in the registry —
    defensive against incomplete data; the shipped JSON covers every
    forge_material grade 1..6 so every equipment grade 1..9 has at least
    one candidate.
    """
    mat_grade = get_recycle_material_grade(item_grade)
    if mat_grade is None:
        return None
    candidates = [
        key for key, item in registry.items.items()
        if item.get("type") == "forge_material"
        and int(item.get("grade", 0)) == mat_grade
    ]
    if not candidates:
        return None
    return random.choice(candidates)
