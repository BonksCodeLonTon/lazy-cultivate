"""Thể Chất system — path-aware multi-slot logic.

Most cultivation paths (Qi / Formation) keep a single Thể Chất. **Thể Tu**
(body cultivators — players whose ``body_realm`` ≥ every other axis) unlock
one additional Thể Chất slot per body realm breakthrough, capped at 8.

The Hỗn Độn Đạo Thể is a special *9th* slot that activates only when all 8
standard slots already hold a Legendary Thể Chất.

Storage: ``player.constitution_type`` is still a single string column, but
it is now parsed as a **comma-separated list** of Thể Chất keys. A legacy
single-entry value (e.g. ``"ConstitutionVanTuong"``) works unchanged.
"""
from __future__ import annotations

import random

MAX_BODY_SLOTS = 8
HON_DON_KEY = "ConstitutionHonDon"

# Success chance by rarity for non-Thể Tu. Thể Tu gets a flat bonus on top.
_BASE_SUCCESS: dict[str, float] = {
    "common":    0.90,
    "uncommon":  0.80,
    "rare":      0.65,
    "epic":      0.50,
    "legendary": 0.35,
}
THE_TU_SUCCESS_BONUS = 0.20


def get_constitutions(raw: str | None) -> list[str]:
    """Split ``player.constitution_type`` into its list form."""
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def set_constitutions(keys: list[str]) -> str:
    """Inverse of ``get_constitutions`` — serialize a list back to the column."""
    return ",".join(k for k in keys if k)


def is_the_tu(body_realm: int, qi_realm: int, formation_realm: int) -> bool:
    """Thể Tu = body cultivator — body_realm at least as high as every other axis."""
    return body_realm >= max(qi_realm, formation_realm)


def max_slots(body_realm: int, qi_realm: int, formation_realm: int) -> int:
    """Number of standard Thể Chất slots the player currently owns.

    - Non-Thể Tu: always 1.
    - Thể Tu: ``1 + body_realm`` (so body 0 → 1 slot, body 7+ → 8 slots),
      hard-capped at ``MAX_BODY_SLOTS`` (8).
    - Hỗn Độn is a special *9th* slot on top of these, not counted here.
    """
    if not is_the_tu(body_realm, qi_realm, formation_realm):
        return 1
    return min(MAX_BODY_SLOTS, 1 + body_realm)


def legendary_equipped_count(equipped: list[str], constitutions_index: dict) -> int:
    """How many of the currently equipped entries are Legendary rarity."""
    total = 0
    for k in equipped:
        c = constitutions_index.get(k)
        if c and c.get("rarity") == "legendary":
            total += 1
    return total


def activation_chance(
    const_data: dict,
    body_realm: int,
    qi_realm: int,
    formation_realm: int,
) -> float:
    """Return the clamped [0.0, 1.0] probability of a successful activation.

    Priority:
      1. explicit per-entry ``activation_chance`` field in JSON
      2. rarity-based default from ``_BASE_SUCCESS``

    Thể Tu always adds ``THE_TU_SUCCESS_BONUS`` on top.
    """
    explicit = const_data.get("activation_chance")
    if isinstance(explicit, (int, float)):
        base = float(explicit)
    else:
        rarity = const_data.get("rarity", "common")
        base = _BASE_SUCCESS.get(rarity, 0.5)
    if is_the_tu(body_realm, qi_realm, formation_realm):
        base += THE_TU_SUCCESS_BONUS
    return max(0.0, min(1.0, base))


def roll_activation(
    const_data: dict,
    body_realm: int,
    qi_realm: int,
    formation_realm: int,
    rng: random.Random | None = None,
) -> bool:
    """Return True if the activation succeeds. Uses ``activation_chance``."""
    rng = rng or random.Random()
    return rng.random() < activation_chance(
        const_data, body_realm, qi_realm, formation_realm,
    )


# ── Requirement checks ────────────────────────────────────────────────────────


def requirements_as_list(const_data: dict) -> list[str]:
    """Normalize ``special_requirements`` — accepts str, list, or None."""
    req = const_data.get("special_requirements")
    if req is None:
        return []
    if isinstance(req, str):
        return [req]
    if isinstance(req, list):
        return [str(x) for x in req]
    return []


def check_requirements(
    player, const_data: dict, constitutions_index: dict,
) -> str | None:
    """Return a human-readable error if any special requirement isn't met;
    otherwise None. Does NOT check slots, merit, or materials — those are
    validated separately in the cog.
    """
    for req in requirements_as_list(const_data):
        if req in ("requires_dao_ti_yang", "requires_dao_ti_yin"):
            if not player.dao_ti_unlocked:
                return (
                    f"**{const_data['vi']}** yêu cầu mở khóa **Đạo Thể**. "
                    f"Đạo Thể mở khi Luyện Thể đột phá đến Cảnh Giới 9."
                )
        elif req == "requires_all_dao_ti":
            if not player.dao_ti_unlocked:
                return (
                    f"**{const_data['vi']}** yêu cầu giác ngộ toàn bộ Đạo Thể "
                    f"(Nhập Thánh cả ba hướng)."
                )
        elif req == "requires_all_legendary_equipped":
            equipped = [
                k for k in get_constitutions(player.constitution_type)
                if k != HON_DON_KEY
            ]
            need = MAX_BODY_SLOTS
            if len(equipped) < need:
                return (
                    f"**{const_data['vi']}** yêu cầu đã trang bị đủ **{need} "
                    f"Thể Chất Truyền Thuyết** trước khi khai mở (đang có "
                    f"{len(equipped)}/{need})."
                )
            leg_count = legendary_equipped_count(equipped, constitutions_index)
            if leg_count < need:
                return (
                    f"**{const_data['vi']}** yêu cầu toàn bộ 8 Thể Chất đang "
                    f"trang bị phải là Truyền Thuyết — hiện có {leg_count}/{need}."
                )
        elif req == "requires_the_tu":
            if not is_the_tu(
                player.body_realm, player.qi_realm, player.formation_realm,
            ):
                return (
                    f"**{const_data['vi']}** chỉ dành cho **Thể Tu** — "
                    f"body_realm phải cao nhất trong ba hướng."
                )
    return None
