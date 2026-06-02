"""Skill system — pure logic for skill learning, filtering, and validation.

Discord-layer concerns (embeds, views, interaction handling) live in
``src/bot/cogs/skills.py``. This module is import-safe from anywhere; it
has no Discord or SQLAlchemy session dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.data.registry import registry
from src.db.models.skill import MAX_SKILL_SLOTS
from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
from src.game.constants.linh_can import parse_linh_can
from src.game.systems.cultivation import (
    formation_reserve_reduction,
    get_active_formations,
)


def scroll_key_for_skill(skill_key: str) -> str:
    """Canonical inventory key for the scroll that teaches ``skill_key``."""
    return f"Scroll_{skill_key}"


def is_formation_skill(skill_data: dict | None) -> bool:
    return bool(skill_data) and skill_data.get("category") == "formation"


def find_skill_scroll(inv_items, skill_key: str):
    """Return the inventory row holding the scroll for ``skill_key``, or None."""
    target = scroll_key_for_skill(skill_key)
    for inv in inv_items:
        if inv.item_key == target and inv.quantity > 0:
            return inv
    return None


def filtered_skills(
    category: str | None = None,
    element: str | None = None,
    linh_can: list[str] | None = None,
) -> list[dict]:
    """All player-learnable skills matching the given filters.

    Hides NPC-only skills — both enemy and boss skills, which the registry
    tags with ``_npc_only`` (loaded from ``skills/enemy/`` and ``skills/boss/``).
    A boss skill key (e.g. ``ChungYen_TanThe``) doesn't start with ``Enemy``, so
    the flag is the only reliable filter. When ``linh_can`` is given, restricts
    elemental skills to the player's roots (formations and non-elemental skills
    are always allowed).
    """
    skills = [s for s in registry.skills.values() if not s.get("_npc_only")]
    if category:
        skills = [s for s in skills if s.get("category") == category]
    if element:
        skills = [s for s in skills if s.get("element") == element]
    if linh_can is not None:
        skills = [
            s for s in skills
            if s.get("element") is None
            or s.get("category") == "formation"
            or s.get("element") in linh_can
        ]
    return sorted(
        skills,
        key=lambda s: (
            int(s.get("scroll_grade", 1)),
            s.get("category", ""),
            s.get("mp_cost", 0),
        ),
    )


def next_formation_slot(player) -> int:
    """First unused slot_index ≥ MAX_SKILL_SLOTS — formations live in an
    open-ended bar so the only cap is MP reservation, not slot count.
    """
    used = {s.slot_index for s in (player.skills or [])}
    i = MAX_SKILL_SLOTS
    while i in used:
        i += 1
    return i


def formation_activation_would_exceed_cap(
    player, picked_formation_keys: list[str],
) -> tuple[bool, float]:
    """Return ``(would_exceed, projected_pct)`` for activating ``picked_formation_keys``.

    Each picked formation contributes the ``reserved_mp_pct`` of its
    ``formation_skill_key``. Trận Đạo reduction applies, then we compare
    the raw total against ``FORMATION_MAX_RESERVE_PCT``. The cap moved
    from learn time to activation time once formation skills became
    formation unlocks rather than slot-equipped skills.
    """
    stages = (player.formation_realm or 0) * 9 + (player.formation_level or 0)
    raw_total = 0.0
    for formation_key in picked_formation_keys or []:
        form = registry.get_formation(formation_key)
        if not form:
            continue
        skill_key = form.get("formation_skill_key")
        if not skill_key:
            continue
        sk = registry.get_skill(skill_key)
        if not sk or sk.get("category") != "formation":
            continue
        raw_total += float(sk.get("reserved_mp_pct", 0.0))
    reduced = raw_total * formation_reserve_reduction(stages)
    return reduced > FORMATION_MAX_RESERVE_PCT, min(reduced, 1.0)


def formation_key_for_skill(skill_data: dict | None) -> str | None:
    """Resolve the formation a formation-category skill unlocks, if any."""
    if not skill_data or skill_data.get("category") != "formation":
        return None
    formation_key = skill_data.get("formation_key")
    if not formation_key:
        return None
    return formation_key if registry.get_formation(formation_key) else None


# ── Learn validation ─────────────────────────────────────────────────────────


class LearnError(Enum):
    """Reason a skill cannot be learned."""
    WRONG_LINH_CAN = "wrong_linh_can"


@dataclass(frozen=True)
class LearnValidation:
    """Result of pre-inventory learn validation (Linh Căn gate).

    On failure, the relevant detail field is populated so the caller can
    render a precise error message without re-deriving values.
    """
    ok: bool
    error: LearnError | None = None
    missing_element: str | None = None


def validate_learn_eligibility(player, skill_data: dict) -> LearnValidation:
    """Check the Linh Căn gate for learning ``skill_data``.

    Formation skills bypass the Linh Căn gate (formations are universal).
    Power-tier gating is handled by ``scroll_grade`` — drop rarity controls
    when a player can actually obtain a higher-tier scroll.
    """
    skill_elem = skill_data.get("element")
    skill_category = skill_data.get("category")
    if skill_elem is not None and skill_category != "formation":
        player_lc = parse_linh_can(player.linh_can or "")
        if skill_elem not in player_lc:
            return LearnValidation(
                ok=False,
                error=LearnError.WRONG_LINH_CAN,
                missing_element=skill_elem,
            )
    return LearnValidation(ok=True)
