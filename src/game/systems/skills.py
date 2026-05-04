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
from src.game.systems.cultivation import formation_reserve_reduction


def scroll_key_for_skill(skill_key: str) -> str:
    """Canonical inventory key for the scroll that teaches ``skill_key``."""
    return f"Scroll_{skill_key}"


def is_formation_skill(skill_data: dict | None) -> bool:
    return bool(skill_data) and skill_data.get("category") == "formation"


def player_max_realm(player) -> int:
    """Highest realm index (0-based) across body / qi / formation axes."""
    return max(
        getattr(player, "body_realm", 0) or 0,
        getattr(player, "qi_realm", 0) or 0,
        getattr(player, "formation_realm", 0) or 0,
    )


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

    Hides Enemy_* skills. When ``linh_can`` is given, restricts elemental
    skills to the player's roots (formations and non-elemental skills are
    always allowed).
    """
    skills = [s for s in registry.skills.values() if not s.get("key", "").startswith("Enemy")]
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
        key=lambda s: (s.get("realm", 1), s.get("category", ""), s.get("mp_cost", 0)),
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


def formation_reservation_would_exceed_cap(
    player, new_skill_key: str,
) -> tuple[bool, float]:
    """Return ``(would_exceed, projected_pct)`` for adding ``new_skill_key``.

    Computes raw skill-reservation total (current learned + the new one),
    applies Trận Đạo reduction, and compares against
    ``FORMATION_MAX_RESERVE_PCT``. We don't reuse
    ``compute_formation_skill_reserve_pct`` directly because that helper
    already caps to MAX — losing the signal we need to block the equip.
    """
    stages = (player.formation_realm or 0) * 9 + (player.formation_level or 0)
    current_keys = [s.skill_key for s in (player.skills or [])]
    raw_total = 0.0
    for sk_key in current_keys + [new_skill_key]:
        sk = registry.get_skill(sk_key)
        if not sk or sk.get("category") != "formation":
            continue
        raw_total += float(sk.get("reserved_mp_pct", 0.0))
    reduced = raw_total * formation_reserve_reduction(stages)
    return reduced > FORMATION_MAX_RESERVE_PCT, min(reduced, 1.0)


# ── Learn validation ─────────────────────────────────────────────────────────


class LearnError(Enum):
    """Reason a skill cannot be learned."""
    REALM_TOO_LOW = "realm_too_low"
    WRONG_LINH_CAN = "wrong_linh_can"


@dataclass(frozen=True)
class LearnValidation:
    """Result of pre-inventory learn validation (realm + Linh Căn gates).

    On failure, the relevant detail field is populated so the caller can
    render a precise error message without re-deriving values.
    """
    ok: bool
    error: LearnError | None = None
    needed_realm_index: int | None = None
    missing_element: str | None = None


def validate_learn_eligibility(player, skill_data: dict) -> LearnValidation:
    """Check realm and Linh Căn gates for learning ``skill_data``.

    Skill ``realm`` is 1-based; player axis realms are 0-based — so a
    rank-1 skill is learnable at realm-index 0. Formation skills bypass
    the Linh Căn gate (formations are universal).
    """
    skill_realm = skill_data.get("realm", 1)
    if player_max_realm(player) + 1 < skill_realm:
        return LearnValidation(
            ok=False,
            error=LearnError.REALM_TOO_LOW,
            needed_realm_index=max(0, skill_realm - 1),
        )

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
