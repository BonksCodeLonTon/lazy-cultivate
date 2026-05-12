"""Thể Chất system — path-aware multi-slot logic.

Most cultivation paths (Qi / Formation) keep a single Thể Chất. **Thể Tu**
(body cultivators — players whose ``active_axis == "body"``) unlock one
additional Thể Chất slot per body realm breakthrough, capped at 8.

The Hỗn Độn Đạo Thể is a special *9th* slot that activates only when all 8
standard slots already hold a Legendary Thể Chất.

Storage: ``player.constitution_type`` is a single string column parsed as
a **comma-separated list** of Thể Chất keys. A single-entry value (e.g.
``"ConstitutionVanTuong"``) parses as a 1-slot list.

Path identity follows ``Player.active_axis`` directly so a player who
cultivates body becomes Thể Tu the moment they switch focus, and reverts
to a single-slot Khí/Trận Tu the instant they pivot away — no realm-comparison
desync. Bonus pipelines clamp to the current cap automatically; constitutions
beyond the cap stay persisted but contribute nothing while off-path.
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


# ── Tracker (permanent unlocks) ───────────────────────────────────────────────


def get_tracker(raw: str | None) -> list[str]:
    """Split ``player.constitution_tracker`` (every activated Thể Chất) into
    its list form. Returned in storage order (activation order)."""
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def set_tracker(keys: list[str]) -> str:
    """Inverse of ``get_tracker`` — preserves order, drops empties."""
    return ",".join(k for k in keys if k)


def add_to_tracker(raw: str | None, key: str) -> str:
    """Append ``key`` to the tracker if not already present.

    De-duplication keeps the column from bloating when the same Thể Chất is
    activated multiple times (re-rolls, edge cases). Activation-order is
    preserved so the tracker doubles as a discovery log.
    """
    keys = get_tracker(raw)
    if key and key not in keys:
        keys.append(key)
    return set_tracker(keys)


def remove_from_tracker(raw: str | None, key: str) -> str:
    """Drop ``key`` from the tracker. Used when a progression chain consumes
    its predecessor (e.g. Thôn Thiên Ma Tâm → Thôn Thiên Ma Thể).
    """
    keys = [k for k in get_tracker(raw) if k != key]
    return set_tracker(keys)


def is_the_tu(active_axis: str | None) -> bool:
    """Thể Tu = the player has chosen body cultivation as their active axis."""
    return (active_axis or "") == "body"


def max_slots(active_axis: str | None, body_realm: int) -> int:
    """Number of standard Thể Chất slots the player currently owns.

    - Off-path (qi / formation focus): always 1.
    - Thể Tu (active_axis == "body"): ``1 + body_realm`` (so body 0 → 1 slot,
      body 7+ → 8 slots), hard-capped at ``MAX_BODY_SLOTS`` (8).
    - Hỗn Độn is a special *9th* slot on top of these, not counted here.
    """
    if not is_the_tu(active_axis):
        return 1
    return min(MAX_BODY_SLOTS, 1 + body_realm)


def effective_constitutions(
    constitution_type: str | None,
    active_axis: str | None,
    body_realm: int,
) -> list[str]:
    """Return the slice of equipped constitutions that actually contribute
    bonuses given the current ``active_axis``.

    Standard slots are clamped to ``max_slots(active_axis, body_realm)`` in
    storage order. Hỗn Độn (the special 9th slot) is preserved separately —
    it never counts against the cap and is appended last when present.
    """
    keys = get_constitutions(constitution_type)
    if not keys:
        return []
    cap = max_slots(active_axis, body_realm)
    standard = [k for k in keys if k != HON_DON_KEY]
    effective = standard[:cap]
    if HON_DON_KEY in keys:
        effective.append(HON_DON_KEY)
    return effective


def legendary_equipped_count(equipped: list[str], constitutions_index: dict) -> int:
    """How many of the currently equipped entries are Legendary rarity."""
    total = 0
    for k in equipped:
        c = constitutions_index.get(k)
        if c and c.get("rarity") == "legendary":
            total += 1
    return total


def activation_chance(const_data: dict, active_axis: str | None) -> float:
    """Return the clamped [0.0, 1.0] probability of a successful activation.

    Priority:
      1. explicit per-entry ``activation_chance`` field in JSON
      2. rarity-based default from ``_BASE_SUCCESS``

    Thể Tu (``active_axis == "body"``) always adds ``THE_TU_SUCCESS_BONUS``
    on top.
    """
    explicit = const_data.get("activation_chance")
    if isinstance(explicit, (int, float)):
        base = float(explicit)
    else:
        rarity = const_data.get("rarity", "common")
        base = _BASE_SUCCESS.get(rarity, 0.5)
    if is_the_tu(active_axis):
        base += THE_TU_SUCCESS_BONUS
    return max(0.0, min(1.0, base))


def roll_activation(
    const_data: dict,
    active_axis: str | None,
    rng: random.Random | None = None,
) -> bool:
    """Return True if the activation succeeds. Uses ``activation_chance``."""
    rng = rng or random.Random()
    return rng.random() < activation_chance(const_data, active_axis)


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
    # Implicit predecessor check: any entry with ``progresses_from`` requires
    # that predecessor to be currently equipped (the activation flow removes
    # the predecessor on success — see constitution cog). Without this, a
    # player could leap-frog mid-chain stages.
    predecessor = const_data.get("progresses_from")
    if predecessor and predecessor not in get_constitutions(player.constitution_type):
        pred_data = constitutions_index.get(predecessor) or {}
        pred_name = pred_data.get("vi", predecessor)
        return (
            f"**{const_data['vi']}** yêu cầu đã trang bị **{pred_name}** "
            f"trước khi tiến hóa."
        )
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
            if not is_the_tu(getattr(player, "active_axis", None)):
                return (
                    f"**{const_data['vi']}** chỉ dành cho **Thể Tu** — "
                    f"phải đang tu luyện theo trục Luyện Thể."
                )
        elif req == "requires_thon_thien_ma_tam":
            if "ConstitutionThonThienMaTam" not in get_constitutions(player.constitution_type):
                return (
                    f"**{const_data['vi']}** yêu cầu đang trang bị "
                    f"**Thôn Thiên Ma Tâm** trước khi tiến hóa."
                )
        elif req == "requires_skill_ma_than_cong":
            learned = {
                getattr(s, "skill_key", None)
                for s in (getattr(player, "skills", None) or [])
            }
            if "SkillMaThanCong_R9" not in learned:
                return (
                    f"**{const_data['vi']}** yêu cầu đã lĩnh ngộ kỹ năng "
                    f"**Ma Thần Công** (R9 hệ Ám)."
                )
    return None
