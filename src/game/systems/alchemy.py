"""Luyện Đan (Alchemy) — craft pills from herbs and beast materials.

Mirrors the shape of :mod:`src.game.systems.forge`:

* Recipes live as registry data (``src/data/recipes/pill_recipes.json``).
* ``check_requirements`` validates realm, merit, and ingredient availability
  but does not mutate state.
* ``craft_pill`` rolls a quality tier via the shared quality module and
  returns an :class:`AlchemyResult` describing the outcome. The caller
  (``src/bot/cogs/alchemy.py``) is responsible for deducting herbs/merit
  and adding the resulting pill to the player's inventory in one DB
  transaction — same separation as the forge cog.

Pills stack in the existing inventory by ``(item_key, grade)`` where
``grade`` is the quality tier (1 Hoàng → 4 Thiên). The pill's intrinsic
recipe grade (1–9) is embedded in its ``item_key`` and does not occupy
an inventory column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from src.data.registry import registry
from src.game.engine.quality import (
    QUALITY_LABELS,
    implicit_multiplier,
    quality_tier_index,
    roll_quality,
    special_label,
)
from src.game.models.character import Character


# ── Public types ────────────────────────────────────────────────────────────

@dataclass
class IngredientPick:
    """Ingredient chosen from one of a recipe slot's alternative options."""
    slot_role: str               # "chu" (chủ dược) / "phu" (phụ dược) / "dan" (dẫn dược)
    key: str                     # herb/yeu_thu item key
    qty: int


@dataclass
class AlchemyResult:
    success: bool
    message: str
    pill_key: Optional[str] = None
    quality: Optional[str] = None           # hoan / huyen / dia / thien
    quality_tier: int = 0                   # 1..4
    dan_doc_delta: int = 0                  # toxicity to add to player
    consumed: list[IngredientPick] = field(default_factory=list)
    cost_cong_duc: int = 0
    furnace_key: Optional[str] = None       # furnace used to craft (display only)


# ── Recipe helpers ──────────────────────────────────────────────────────────

def get_recipe(key: str) -> dict | None:
    return registry.get_pill_recipe(key)


# ── Furnace helpers ─────────────────────────────────────────────────────────

def _furnace_score(furnace: dict) -> tuple[int, float]:
    """Rank furnaces so the 'best' owned furnace for a given tier wins.

    Sort key is (is_unique, total_quality_bonus). Unique furnaces always beat
    normal ones at the same tier; among unique furnaces the one with the
    highest total quality bonus wins.
    """
    unique = 1 if furnace.get("is_unique") else 0
    bonus_total = sum(furnace.get("quality_bonus", {}).values())
    return (unique, bonus_total)


def _pick_best_furnace(
    owned_furnace_keys: Iterable[str],
    required_tier: int,
) -> dict | None:
    """Return the best furnace the player owns that satisfies ``required_tier``.

    Pretty-prints to None when no owned furnace qualifies.
    """
    best: dict | None = None
    best_score = (-1, -1.0)
    for key in owned_furnace_keys:
        f = registry.get_furnace(key)
        if not f:
            continue
        if int(f.get("furnace_tier", 0)) < required_tier:
            continue
        score = _furnace_score(f)
        if score > best_score:
            best = f
            best_score = score
    return best


def apply_furnace_bonus(
    quality_chances: dict[str, float],
    furnace: dict | None,
) -> dict[str, float]:
    """Return a new chances map with the furnace's quality_bonus folded in.

    Bonuses are additive on the respective tier's weight; the Hoàng weight is
    reduced by the sum of added bonuses (clamped at 0) so that the total
    roughly sums to the original total. The shared :func:`roll_quality`
    renormalises anyway, so small numeric drift is harmless.
    """
    if not furnace or not furnace.get("quality_bonus"):
        return dict(quality_chances)
    bonus = furnace["quality_bonus"]
    out = dict(quality_chances)
    added = 0.0
    for tier_key, add in bonus.items():
        out[tier_key] = out.get(tier_key, 0.0) + float(add)
        added += float(add)
    out["hoan"] = max(0.0, out.get("hoan", 0.0) - added)
    return out


def _select_ingredient_option(
    slot: dict,
    inventory_map: dict[str, int],
) -> IngredientPick | None:
    """Pick the first option in a recipe slot the player can actually satisfy."""
    for option in slot["options"]:
        if inventory_map.get(option["key"], 0) >= option["qty"]:
            return IngredientPick(
                slot_role=slot["role"],
                key=option["key"],
                qty=option["qty"],
            )
    return None


def _describe_missing_slot(slot: dict) -> str:
    """Format a human-readable 'missing ingredient' hint for a slot."""
    names = []
    for option in slot["options"]:
        item = registry.get_item(option["key"])
        display = item["vi"] if item else option["key"]
        names.append(f"{display}×{option['qty']}")
    label = {"chu": "Chủ Dược", "phu": "Phụ Dược", "dan": "Dẫn Dược"}.get(slot["role"], slot["role"])
    return f"{label}: {' hoặc '.join(names)}"


def check_requirements(
    char: Character,
    recipe: dict,
    inventory_map: dict[str, int],
    owned_furnace_keys: Iterable[str] = (),
) -> tuple[bool, str, list[IngredientPick], dict | None]:
    """Validate realm, merit, ingredient stock, and furnace tier for a recipe.

    ``inventory_map`` maps ``item_key → total_quantity`` across all grades
    (for stackable herb/yeu_thu items this is just the quantity row value).
    ``owned_furnace_keys`` is an iterable of furnace item_keys currently in
    the player's bag; the best qualifying one is selected and returned.
    Returns ``(ok, error_message, chosen_ingredients, chosen_furnace)``.
    """
    min_qi = int(recipe.get("min_qi_realm", 0))
    if char.qi_realm < min_qi:
        return (
            False,
            f"Cần đạt Luyện Khí cảnh thứ {min_qi + 1} để luyện đan phương này.",
            [],
            None,
        )

    cost = int(recipe.get("cost_cong_duc", 0))
    if char.merit < cost:
        return (
            False,
            f"Cần {cost:,} Công Đức (hiện có {char.merit:,}).",
            [],
            None,
        )

    required_tier = int(recipe.get("furnace_tier", 1))
    furnace = _pick_best_furnace(owned_furnace_keys, required_tier)
    if furnace is None:
        return (
            False,
            (
                f"Cần Đan Lô tối thiểu **Cấp {required_tier}** để luyện đan phương này.\n"
                f"Mua Đan Lô thường tại Phường Thị hoặc tìm Đan Lô độc bản trong rương."
            ),
            [],
            None,
        )

    picks: list[IngredientPick] = []
    missing: list[str] = []
    for slot in recipe.get("ingredients", []):
        choice = _select_ingredient_option(slot, inventory_map)
        if choice is None:
            missing.append(_describe_missing_slot(slot))
        else:
            picks.append(choice)

    if missing:
        return (
            False,
            "Thiếu nguyên liệu:\n" + "\n".join(f"• {m}" for m in missing),
            [],
            furnace,
        )

    return True, "", picks, furnace


# ── Crafting ────────────────────────────────────────────────────────────────

def craft_pill(
    char: Character,
    recipe_key: str,
    inventory_map: dict[str, int],
    owned_furnace_keys: Iterable[str] = (),
) -> AlchemyResult:
    """Pure crafting step — validates, rolls quality, returns result.

    Does not touch the DB: the caller deducts ingredients/merit and inserts
    the pill into the inventory inside its own transaction (mirrors the
    pattern used by :mod:`src.game.systems.forge`).
    """
    recipe = get_recipe(recipe_key)
    if not recipe:
        return AlchemyResult(False, f"Không tìm thấy đan phương '{recipe_key}'.")

    ok, err, picks, furnace = check_requirements(
        char, recipe, inventory_map, owned_furnace_keys
    )
    if not ok:
        return AlchemyResult(False, err)

    pill_key = recipe["output_pill"]
    pill_item = registry.get_pill(pill_key)
    if not pill_item:
        return AlchemyResult(False, f"Không tìm thấy đan dược '{pill_key}'.")

    comprehension = int(getattr(char.stats, "comprehension", 0) or 0)
    # Unique furnaces tilt the roll toward higher qualities.
    effective_chances = apply_furnace_bonus(recipe["quality_chances"], furnace)
    quality = roll_quality(effective_chances, comprehension)
    tier = quality_tier_index(quality)

    # Higher quality = cleaner refining → less toxicity builds up in the
    # consumer's body. Quality 1 → full base toxicity, 4 → 40% of base.
    base_doc = int(pill_item.get("dan_doc", 0))
    reduction = 1.0 - (tier - 1) * 0.2    # 1.0, 0.8, 0.6, 0.4
    dan_doc_delta = max(0, int(round(base_doc * reduction)))

    cost = int(recipe.get("cost_cong_duc", 0))
    char.merit -= cost

    label = QUALITY_LABELS.get(quality, quality)
    special = special_label(quality)
    suffix = f"\n{special}" if special else ""
    furnace_line = f"\n🔥 Đan Lô: {furnace['vi']}" if furnace else ""
    msg = (
        f"✅ Luyện đan thành công: **{pill_item['vi']}** "
        f"[Cấp {pill_item.get('grade', '?')} — {label}]{suffix}{furnace_line}"
    )

    return AlchemyResult(
        success=True,
        message=msg,
        pill_key=pill_key,
        quality=quality,
        quality_tier=tier,
        dan_doc_delta=dan_doc_delta,
        consumed=picks,
        cost_cong_duc=cost,
        furnace_key=furnace["key"] if furnace else None,
    )


# ── Consumption ─────────────────────────────────────────────────────────────

@dataclass
class PillEffect:
    applied: bool
    message: str
    dan_doc_delta: int = 0          # +toxicity added (or - for purifiers)
    merit_delta: int = 0            # applied to Character.merit
    qi_xp_delta: int = 0
    body_xp_delta: int = 0
    heal_delta: int = 0             # HP gained (if a healing pill is consumed outside combat)


# Effect magnitudes are shaped so even a Hoàng-quality pill is useful
# and Thiên-quality yields a notable bonus. Per-effect scaling is
# deliberately coarse for v1 — refining the balance is follow-up work.
_EFFECT_BASE_MAGNITUDE = {
    "exp_luyen_the":     ("body_xp", 400),
    "exp_qi":            ("qi_xp",   400),
    "restore_hp":        ("heal",    500),
    "restore_mp":        ("heal",      0),   # MP pills handled separately in combat
    "reduce_toxicity":   ("dan_doc_reduce", 40),
    "breakthrough_truc_co":     ("qi_xp", 2000),
    "breakthrough_kim_dan":     ("qi_xp", 5000),
    "breakthrough_nguyen_anh":  ("qi_xp", 12000),
    "breakthrough_hoa_than":    ("qi_xp", 30000),
    "breakthrough_luyen_hu":    ("qi_xp", 80000),
    "breakthrough_hop_dao":     ("qi_xp", 200000),
    "breakthrough_dai_thua":    ("qi_xp", 500000),
}


def consume_pill(
    char: Character,
    pill_key: str,
    quality_tier: int,
) -> PillEffect:
    """Apply a pill's effect to a Character model (in-memory only).

    The caller persists the resulting deltas back to the Player ORM row —
    ``dan_doc``, ``qi_xp``, ``body_xp``, etc. This keeps the system layer
    pure and lets the cog decide what to commit.
    """
    pill = registry.get_pill(pill_key)
    if not pill:
        return PillEffect(False, f"Không tìm thấy đan dược '{pill_key}'.")

    effect_key = pill.get("effect_key", "misc_vi_label")
    base_doc = int(pill.get("dan_doc", 0))
    mult = implicit_multiplier({1: "hoan", 2: "huyen", 3: "dia", 4: "thien"}.get(quality_tier, "hoan"))

    reduction = 1.0 - (quality_tier - 1) * 0.2
    doc_delta = max(0, int(round(base_doc * reduction)))

    effect_body_xp = 0
    effect_qi_xp = 0
    effect_heal = 0
    notes: list[str] = []

    if effect_key in _EFFECT_BASE_MAGNITUDE:
        kind, base = _EFFECT_BASE_MAGNITUDE[effect_key]
        magnitude = int(round(base * mult))
        if kind == "body_xp":
            effect_body_xp = magnitude
            notes.append(f"+{magnitude:,} EXP Luyện Thể")
        elif kind == "qi_xp":
            effect_qi_xp = magnitude
            notes.append(f"+{magnitude:,} EXP Luyện Khí")
        elif kind == "heal":
            effect_heal = magnitude
            notes.append(f"+{magnitude:,} HP")
        elif kind == "dan_doc_reduce":
            doc_delta = -magnitude
            notes.append(f"Thanh lọc −{magnitude} Đan Độc")
    else:
        # Unknown/generic effect — still consumes the pill and accrues toxicity.
        notes.append(pill.get("effect_vi", "Hiệu ứng chưa áp dụng"))

    # Commit into the Character model for UI display; DB-side persistence
    # is handled by the caller.
    char.body_xp = int(getattr(char, "body_xp", 0) or 0) + effect_body_xp
    char.qi_xp = int(getattr(char, "qi_xp", 0) or 0) + effect_qi_xp

    msg = f"✨ Dùng **{pill['vi']}** ({QUALITY_LABELS.get({1:'hoan',2:'huyen',3:'dia',4:'thien'}.get(quality_tier,'hoan'))}) — " + ", ".join(notes)
    return PillEffect(
        applied=True,
        message=msg,
        dan_doc_delta=doc_delta,
        qi_xp_delta=effect_qi_xp,
        body_xp_delta=effect_body_xp,
        heal_delta=effect_heal,
    )
