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
from src.game.systems.pill_buffs import (
    PILL_BUFF_CAP,
    PILL_BUFF_STATS,
    increment_count,
    is_buff_pill,
)
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

def _furnace_score(furnace: dict) -> tuple[int, float, int]:
    """Rank furnaces so the 'best' owned furnace for a given tier wins.

    Sort key is (is_unique, total_quality_bonus, furnace_tier). Unique
    furnaces always beat normal ones; among furnaces with equal uniqueness
    and bonus total, the higher-tier one wins. Without the tier tiebreaker,
    a player who owns G1..G4 normal furnaces (all bonus_total = 0) would
    auto-pick whichever the iteration visits first — usually the lowest tier.
    """
    unique = 1 if furnace.get("is_unique") else 0
    bonus_total = sum(furnace.get("quality_bonus", {}).values())
    tier = int(furnace.get("furnace_tier", 0))
    return (unique, bonus_total, tier)


def _pick_best_furnace(
    owned_furnace_keys: Iterable[str],
    required_tier: int,
) -> dict | None:
    """Return the best furnace the player owns that satisfies ``required_tier``.

    Pretty-prints to None when no owned furnace qualifies.
    """
    best: dict | None = None
    best_score: tuple[int, float, int] = (-1, -1.0, -1)
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
    pill_buff_increment: Optional[str] = None  # effect_key whose counter just ticked up


# Per-effect base magnitudes for non-XP pills. ``exp_luyen_the`` and
# ``exp_qi`` are intentionally NOT here — their base XP scales with the
# pill's own grade via ``_pill_xp_for_grade`` so a Grade-N Hoàn pill grants
# the same XP regardless of consumer realm. The flat 400-XP table that
# lived here used to clear Luyện Thể 0 in three pills.
_EFFECT_BASE_MAGNITUDE = {
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

# Effect keys that aren't combat-buff pills but had no implementation —
# treat them as a small dual-axis XP + merit consumable so consume isn't a
# no-op. (lure_beast / repel_beast were "spawn manipulation" placeholders
# we never wired up to dungeon spawn rolls; they're useful as panic XP.)
_GENERIC_BOOST_EFFECTS: frozenset[str] = frozenset({
    "lure_beast", "repel_beast",
})

_GENERIC_BOOST_PCT_OF_PILL: float = 0.25  # share of a dedicated XP pill
_GENERIC_BOOST_MERIT: int = 50            # flat merit reward per consume
_GENERIC_BOOST_DAN_DOC_REDUCE: int = 2    # tiny detox to offset accrued tox

# Pills-per-realm at Hoàn quality. Quadratic growth (≈ ``10 + 15·R²``) so
# early realms only need a handful of pills while endgame demands ≈1000 —
# pills are a viable boost at low cultivation and an expensive supplement
# at high cultivation, never a primary advancement source past mid-game.
# Index = ``axis_realm`` (0..8). Higher-quality pills divide this count by
# their implicit_mult (Thiên ×1.5 → ~660 pills at R8).
_TARGET_PILLS_PER_REALM: tuple[int, ...] = (
    20,    # R0  Luyện Huyết / Luyện Khí — ~1.5× early-stage cost: small
    50,    # R1  Luyện Bì    / Trúc Cơ      enough that a fresh player can
    130,   # R2  Luyện Cân   / Kim Đan      still reach R2 from a starter
    220,   # R3  Luyện Cốt   / Nguyên Anh   bag, but no single-realm clears
    350,   # R4  Luyện Phủ   / Hóa Thần     past R0. Taper back to the
    500,   # R5  Pháp Tướng  / Luyện Hư     sanctioned ~1000 ceiling at R8
    650,   # R6  Kim Thân    / Hợp Đạo      so endgame stays a real grind.
    820,   # R7  Siêu Phàm   / Đại Thừa
    1000,  # R8  Nhập Thánh  / Đăng Tiên
)


def _pill_xp_for_grade(axis: str, pill_grade: int) -> int:
    """Realm-scaled base XP a Hoàn pill of the given grade grants on ``axis``.

    Pill grade (1..9) maps to realm idx (0..8). Magnitude is computed so
    ``_TARGET_PILLS_PER_REALM[grade-1]`` Hoàn pills clear that realm — XP
    follows the pill, not the consumer. A Grade-5 pill at Luyện Khí 0
    grants the same XP as the same pill at Hóa Thần 4, so high-grade pills
    stay valuable to early-game players who get them via drops/trade and
    low-grade pills don't scale up indefinitely with the consumer's realm.
    """
    from src.game.constants.realms import get_realm
    realm_idx = max(0, min(pill_grade - 1, len(_TARGET_PILLS_PER_REALM) - 1))
    realm = get_realm(axis, realm_idx)
    if realm is None:
        return 0
    target_pills = _TARGET_PILLS_PER_REALM[realm_idx]
    return max(1, realm.level_exp_table[-1] // target_pills)


# Effect keys gated by player cultivation grade vs. pill grade. Once the
# player's matching realm meets/exceeds the pill grade, the consume is
# refused — the body has surpassed what this pill can offer. ``reduce_toxicity``
# is treated separately because Đan Độc is global; it checks the highest
# realm across body + qi (the two axes that accumulate toxicity from pills).
_GRADE_GATED_AXIS: dict[str, str] = {
    "exp_luyen_the": "body",
    "exp_qi":        "qi",
}

_AXIS_LABEL_VI: dict[str, str] = {
    "body": "Luyện Thể",
    "qi":   "Luyện Khí",
}


def _player_axis_realm(char: Character, axis: str) -> int:
    return int(getattr(char, f"{axis}_realm", 0) or 0)


def _grade_gated_refusal(
    char: Character,
    pill: dict,
    pill_grade: int,
    effect_key: str,
) -> Optional[PillEffect]:
    """Refuse consume when the player has surpassed the pill grade.

    Returns ``None`` when the pill is at-tier or above the player and the
    consume should proceed. The refused ``PillEffect`` carries
    ``applied=False`` so the cog leaves the stack untouched in inventory —
    the player isn't burning a pill on something their body can no longer
    absorb. ``reduce_toxicity`` checks ``max(body_realm, qi_realm)`` since
    Đan Độc is a single global stat with no obvious axis correspondence.
    """
    if effect_key in _GRADE_GATED_AXIS:
        axis = _GRADE_GATED_AXIS[effect_key]
        player_realm = _player_axis_realm(char, axis)
        axis_label = _AXIS_LABEL_VI[axis]
    elif effect_key == "reduce_toxicity":
        player_realm = max(
            _player_axis_realm(char, "body"),
            _player_axis_realm(char, "qi"),
        )
        axis_label = "tu vi"
    else:
        return None

    if player_realm < pill_grade:
        return None

    return PillEffect(
        applied=False,
        message=(
            f"❌ Cảnh giới {axis_label} (Cấp {player_realm + 1}) đã vượt qua "
            f"phẩm cấp đan dược **{pill['vi']}** (Cấp {pill_grade}) — "
            f"không còn hấp thu được hiệu quả."
        ),
    )


def _is_combat_buff_pill(effect_key: str) -> bool:
    """Combat-buff pills are tracked per-key with a hard consumption cap —
    delegated to ``pill_buffs.is_buff_pill`` so the registry of buff effects
    lives in one place."""
    return is_buff_pill(effect_key)


_STAT_LABEL_VI: dict[str, str] = {
    "spd":      "Tốc Độ",
    "def_stat": "Phòng Thủ",
    "atk":      "Công Kích",
}


def _format_buff_stat(stat_name: str, total_amount: float) -> str:
    """Render a single per-pill stat increment for the success message."""
    if stat_name.startswith("element_dmg_bonus_"):
        elem = stat_name.removeprefix("element_dmg_bonus_").capitalize()
        return f"+{total_amount * 100:.1f}% sát thương Hệ {elem}"
    label = _STAT_LABEL_VI.get(stat_name, stat_name)
    return f"+{total_amount:g} {label}"


def _apply_combat_buff_pill(
    char: Character,
    pill: dict,
    effect_key: str,
    quality_tier: int,
    base_doc: int,
    mult: float,
) -> PillEffect:
    """Permanent-buff path: increment ``pill_buff_counts[effect_key]`` (if
    under cap) and surface a one-line summary of the new total bonus.

    Returns a ``PillEffect`` with ``pill_buff_increment`` set so the cog
    can persist the updated counter dict back to the player ORM. If the
    cap is already reached, no counter mutation occurs and the result
    reports it (no toxicity gained either — refusing the consume).
    """
    counts = getattr(char, "pill_buff_counts", None) or {}
    ok, new_counts = increment_count(counts, effect_key)
    if not ok:
        return PillEffect(
            applied=False,
            message=(
                f"❌ **{pill['vi']}** đã đạt giới hạn **{PILL_BUFF_CAP}** lần "
                f"dùng — không thể tăng thêm hiệu ứng vĩnh viễn từ loại đan này."
            ),
        )
    char.pill_buff_counts = new_counts
    new_count = new_counts[effect_key]
    # Render every per-pill stat bonus this effect grants (most are single-
    # stat; element pills only have one entry too).
    stat_lines: list[str] = []
    for stat_name, per_pill in PILL_BUFF_STATS[effect_key].items():
        cumulative = per_pill * new_count
        stat_lines.append(_format_buff_stat(stat_name, cumulative))
    quality = {1: "hoan", 2: "huyen", 3: "dia", 4: "thien"}.get(quality_tier, "hoan")
    reduction = 1.0 - (quality_tier - 1) * 0.2
    doc_delta = max(0, int(round(base_doc * reduction)))
    msg = (
        f"✨ Dùng **{pill['vi']}** ({QUALITY_LABELS.get(quality)}) — "
        f"cộng dồn ({new_count}/{PILL_BUFF_CAP} lần): "
        + ", ".join(stat_lines)
    )
    return PillEffect(
        applied=True,
        message=msg,
        dan_doc_delta=doc_delta,
        pill_buff_increment=effect_key,
    )


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
    pill_grade = int(pill.get("grade", 1))
    base_doc = int(pill.get("dan_doc", 0))
    mult = implicit_multiplier({1: "hoan", 2: "huyen", 3: "dia", 4: "thien"}.get(quality_tier, "hoan"))

    # Refuse pills the player has outgrown — the stack stays in inventory
    # rather than being burned for zero benefit. Mirrors how the buff-cap
    # path bails out before any state mutation.
    refusal = _grade_gated_refusal(char, pill, pill_grade, effect_key)
    if refusal is not None:
        return refusal

    reduction = 1.0 - (quality_tier - 1) * 0.2
    doc_delta = max(0, int(round(base_doc * reduction)))

    effect_body_xp = 0
    effect_qi_xp = 0
    effect_heal = 0
    notes: list[str] = []

    merit_delta = 0

    # Đan Độc penalty on pill EXP — full stop at saturation. A poisoned
    # body literally can't absorb more cultivation essence; the player
    # must detox before further pill-based progression. Healing, merit
    # rewards, and combat-buff stat counters are NOT scaled by this.
    from src.game.systems.toxicity import pill_exp_multiplier
    _xp_mult = pill_exp_multiplier(int(getattr(char, "dan_doc", 0) or 0))

    def _scale_xp(raw: int) -> int:
        """Apply both quality + toxicity scalars to a raw XP magnitude."""
        return int(round(raw * mult * _xp_mult))

    if effect_key == "exp_luyen_the":
        magnitude = _scale_xp(_pill_xp_for_grade("body", pill_grade))
        effect_body_xp = magnitude
        notes.append(f"+{magnitude:,} EXP Luyện Thể")
    elif effect_key == "exp_qi":
        magnitude = _scale_xp(_pill_xp_for_grade("qi", pill_grade))
        effect_qi_xp = magnitude
        notes.append(f"+{magnitude:,} EXP Luyện Khí")
    elif effect_key in _EFFECT_BASE_MAGNITUDE:
        kind, base = _EFFECT_BASE_MAGNITUDE[effect_key]
        if kind == "body_xp":
            magnitude = _scale_xp(base)
            effect_body_xp = magnitude
            notes.append(f"+{magnitude:,} EXP Luyện Thể")
        elif kind == "qi_xp":
            magnitude = _scale_xp(base)
            effect_qi_xp = magnitude
            notes.append(f"+{magnitude:,} EXP Luyện Khí")
        elif kind == "heal":
            magnitude = int(round(base * mult))
            effect_heal = magnitude
            notes.append(f"+{magnitude:,} HP")
        elif kind == "dan_doc_reduce":
            # Detox magnitude scales with pill grade — endgame players
            # accumulate toxicity from 1000-pill realms, so a Grade-2
            # purifier doesn't cut it. Grade-2 base (40) is preserved at
            # the floor; Grade-9 reaches +180 per Hoàn pill.
            magnitude = int(round(base * pill_grade / 2 * mult))
            doc_delta = -magnitude
            notes.append(f"Thanh lọc −{magnitude} Đan Độc")
    elif _is_combat_buff_pill(effect_key):
        return _apply_combat_buff_pill(char, pill, effect_key, quality_tier, base_doc, mult)
    elif effect_key in _GENERIC_BOOST_EFFECTS:
        body_part = max(0, _scale_xp(int(round(_pill_xp_for_grade("body", pill_grade) * _GENERIC_BOOST_PCT_OF_PILL))))
        qi_part = max(0, _scale_xp(int(round(_pill_xp_for_grade("qi", pill_grade) * _GENERIC_BOOST_PCT_OF_PILL))))
        effect_body_xp = body_part
        effect_qi_xp = qi_part
        merit_delta = int(round(_GENERIC_BOOST_MERIT * mult))
        doc_delta -= int(round(_GENERIC_BOOST_DAN_DOC_REDUCE * mult))
        notes.append(
            f"+{body_part:,} EXP Luyện Thể, +{qi_part:,} EXP Luyện Khí, "
            f"+{merit_delta:,} Công Đức"
        )
    else:
        notes.append(pill.get("effect_vi", "Hiệu ứng chưa áp dụng"))

    if _xp_mult <= 0.0 and (effect_body_xp == 0 and effect_qi_xp == 0):
        # Surface why nothing came of an XP pill so a Mãn-Độc player
        # immediately understands they must detox before consuming more.
        notes.append("⚠️ Mãn Độc — cơ thể không thể hấp thu EXP từ đan dược.")

    char.body_xp = int(getattr(char, "body_xp", 0) or 0) + effect_body_xp
    char.qi_xp = int(getattr(char, "qi_xp", 0) or 0) + effect_qi_xp
    if merit_delta:
        char.merit = int(getattr(char, "merit", 0) or 0) + merit_delta

    msg = f"✨ Dùng **{pill['vi']}** ({QUALITY_LABELS.get({1:'hoan',2:'huyen',3:'dia',4:'thien'}.get(quality_tier,'hoan'))}) — " + ", ".join(notes)
    return PillEffect(
        applied=True,
        message=msg,
        dan_doc_delta=doc_delta,
        merit_delta=merit_delta,
        qi_xp_delta=effect_qi_xp,
        body_xp_delta=effect_body_xp,
        heal_delta=effect_heal,
    )
