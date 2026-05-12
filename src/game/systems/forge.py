"""Forge system — craft equipment from materials."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from src.data.registry import registry
from src.game.engine.quality import (
    QUALITY_LABELS,
    QUALITY_SPECIAL,
    roll_quality as _roll_quality_shared,
)
from src.game.models.character import Character


# Quality tiers are defined in src/game/engine/quality.py and re-exported
# here for backward compatibility with callers that import them from forge.
__all__ = [
    "QUALITY_LABELS",
    "QUALITY_SPECIAL",
    "QUALITY_AFFIX_COUNT",
    "get_affix_count",
    "max_affix_total",
    "distribute_qty",
    "ForgeResult",
    "forge_equipment",
]

# Equipment-specific: how many (prefix, suffix) affixes each quality rolls.
# This is a forge concern and does not belong in the shared quality module.
#
# Thiên gets a universal +1 prefix slot over Địa at every grade. Grade 9
# pushes it a step further with an extra suffix on top.
QUALITY_AFFIX_COUNT: dict[str, tuple[int, int]] = {
    "hoan":  (1, 1),
    "huyen": (2, 1),
    "dia":   (2, 2),
    "thien": (3, 2),   # 5 total affixes at Thiên — applies to ALL grades
}

# Per-grade overrides keyed by (grade, quality). Only entries that differ
# from QUALITY_AFFIX_COUNT need to be listed; fall back to the base table.
_GRADE_AFFIX_OVERRIDES: dict[tuple[int, str], tuple[int, int]] = {
    (9, "thien"): (3, 3),   # 6 total affixes — G9 keeps its extra suffix
}


def get_affix_count(grade: int, quality: str) -> tuple[int, int]:
    """Return (n_prefix, n_suffix) for a given grade+quality combination.

    Thiên rolls 5 affixes (3p,2s) at every grade; Grade 9 Thiên bumps to
    6 (3p,3s). All other combinations use the base ``QUALITY_AFFIX_COUNT``.
    """
    return _GRADE_AFFIX_OVERRIDES.get((grade, quality), QUALITY_AFFIX_COUNT[quality])


def max_affix_total(grade: int) -> int:
    """Max total affixes achievable at this forge grade (Thiên quality).

    Used to derive material qty so cost scales with customization ceiling.
    """
    n_prefix, n_suffix = get_affix_count(grade, "thien")
    return n_prefix + n_suffix


@dataclass
class ForgeResult:
    success: bool
    message: str
    item_data: Optional[dict] = None   # None on failure


# ── Public API ───────────────────────────────────────────────────────────────

def get_recipe(grade: int) -> dict | None:
    """Return the forge recipe for `grade` (1-9) from registry."""
    for rec in registry.forge_recipes:
        if rec["grade"] == grade:
            return rec
    return None


def get_material_grade(material_key: str) -> int | None:
    """Return the grade of a forge material, or None if not a forge material.

    Forge eligibility is gated on ``type == "forge_material"`` — that's the
    single source of truth for the rebalance after ``elemental_materials.json``
    was merged into ``forge_materials.json``. Realm-breakthrough materials
    (now ``type == "constitution_material"``) are deliberately excluded so
    they can't be burned for forging.
    """
    item = registry.get_item(material_key)
    if item and item.get("type") == "forge_material":
        return int(item.get("grade", 0))
    return None


def check_forge_requirements(
    char: Character,
    grade: int,
    materials_in_bag: dict[str, int],  # {material_key: qty_owned}
) -> tuple[bool, str, dict | None]:
    """
    Validate that the player meets all forge requirements for `grade`.

    Returns (ok, message, chosen_option) where chosen_option is the first
    satisfiable option from the recipe, or None on failure.
    """
    recipe = get_recipe(grade)
    if not recipe:
        return False, f"Không tìm thấy công thức rèn cấp {grade}.", None

    if char.qi_realm < recipe["min_qi_realm"]:
        return (
            False,
            f"Cần đạt cảnh giới Luyện Khí thứ {recipe['min_qi_realm'] + 1} "
            f"để rèn trang bị cấp {grade}.",
            None,
        )

    if char.merit < recipe["cost_cong_duc"]:
        return (
            False,
            f"Cần {recipe['cost_cong_duc']:,} Công Đức "
            f"(hiện có {char.merit:,}).",
            None,
        )

    # Required material qty is derived from the max affix total at this grade —
    # so a G9 forge (max 6 affixes) needs 6 materials, while G1-G7 need 4.
    # The JSON recipe's qty field is treated as a display hint but always
    # overridden here to keep code as the single source of truth.
    required_qty = max_affix_total(grade)

    # No more per-grade gate — any forge_material counts towards the total.
    # ``materials_in_bag`` is pre-filtered to ``type == "forge_material"`` by
    # the cog, so summing all values gives the total available.
    total_owned = sum(materials_in_bag.values())
    if total_owned >= required_qty and recipe["options"]:
        return True, "", _normalize_option(recipe["options"][0], required_qty)

    return (
        False,
        f"Thiếu nguyên liệu. Cần **{required_qty}** vật liệu luyện khí "
        f"(hiện có {total_owned}).",
        None,
    )


def distribute_qty(picks: list[tuple[str, int]], required: int) -> dict[str, int] | None:
    """Allocate ``required`` units across picked materials.

    ``picks`` is ``[(material_key, owned_qty), ...]`` in user-pick order.
    Returns ``{key: take_qty}`` summing to ``required``, or ``None`` if the
    combined owned quantity falls short.

    Allocation rule — keep it simple and predictable:
      1. Each pick gets at least 1 unit (it was picked deliberately).
      2. Distribute the remainder, preferring picks with more owned first
         so a deficit on a low-stock pick doesn't block the forge.

    The bag-aware fallback exists because a pure ceil-split would error
    when, say, the user picks A (owned=1) + B (owned=4) for required=5.
    """
    if not picks:
        return None
    n = len(picks)
    if sum(owned for _, owned in picks) < required:
        return None

    take: dict[str, int] = {key: 0 for key, _ in picks}
    owned_map: dict[str, int] = {key: q for key, q in picks}
    remaining = required

    # Phase 1: 1 unit each
    for key, _ in picks:
        if remaining <= 0:
            break
        take[key] += 1
        remaining -= 1

    # Phase 2: distribute the rest, taking from highest-owned first
    by_owned = sorted(picks, key=lambda x: -x[1])
    for key, _ in by_owned:
        if remaining <= 0:
            break
        slack = owned_map[key] - take[key]
        bump = min(slack, remaining)
        take[key] += bump
        remaining -= bump

    return take if remaining == 0 else None


def _normalize_option(option: dict, required_qty: int) -> dict:
    """Return a copy of ``option`` with every material qty set to ``required_qty``."""
    return {
        **option,
        "materials": [
            {**req, "qty": required_qty} for req in option["materials"]
        ],
    }


def roll_implicit_stats(base: dict, grade: int, quality: str = "hoan") -> dict[str, float]:
    """Roll implicit stat values, then apply quality implicit multiplier."""
    result: dict[str, float] = {}
    mult = QUALITY_SPECIAL[quality]["implicit_mult"]
    idx = grade - 1
    for stat, ranges in base["implicit_by_realm"].items():
        lo, hi = ranges[idx]
        if isinstance(lo, float) and lo < 1:
            raw = random.uniform(lo, hi)
            result[stat] = round(raw * mult, 5)
        else:
            raw = random.randint(int(lo), int(hi))
            result[stat] = round(raw * mult)
    return result


def _eligible_affixes(slot: str, affix_type: str) -> list[dict]:
    """Affixes of `affix_type` ('prefix'/'suffix') that can roll on `slot`."""
    return [
        a for a in registry.affixes.values()
        if a["type"] == affix_type
        and ("all" in a["slots"] or slot in a["slots"])
    ]


def _get_affix_bias_weights(material_keys: list[str] | str | None) -> dict[str, float]:
    """Return per-affix selection-weight multipliers from picked materials.

    Higher-grade materials pull harder on their bias affix:

    * Grade 1 → 6×
    * Grade 2 → 9×
    * Grade 3 → 12×
    * Grade 4 → 15×

    Multiple materials biased to the same affix do **not** stack — we keep
    the strongest pull so a single grade-4 pick beats a stack of grade-1s.
    Materials with no ``affix_bias`` (the de-duplicated 38) contribute
    nothing here; total qty still counts toward the recipe gate.
    """
    if not material_keys:
        return {}
    keys = [material_keys] if isinstance(material_keys, str) else material_keys
    weights: dict[str, float] = {}
    for key in keys:
        if not key:
            continue
        item = registry.get_item(key)
        if not item or item.get("type") != "forge_material":
            continue
        grade = int(item.get("grade", 1))
        mult = float(3 * max(1, grade) + 3)
        for affix_key in item.get("affix_bias") or []:
            if mult > weights.get(affix_key, 0.0):
                weights[affix_key] = mult
    return weights


def roll_affixes(
    slot: str,
    grade: int,
    quality: str,
    material_keys: list[str] | str | None = None,
    two_handed: bool = False,
) -> list[dict]:
    """Roll affixes, applying quality floor and guaranteed-max special effects.

    Biased affixes get a selection-weight multiplier scaling with the
    biasing material's grade — 6× / 9× / 12× / 15× for grade 1 / 2 / 3 / 4.
    Pass a single key for a single-material forge or a list for
    mixed-material forges; same-affix bids from multiple materials don't
    stack, only the strongest pull wins.

    When ``two_handed`` is True, both prefix and suffix counts are doubled —
    this is how 2H weapons earn their slot lockout: twice the customizable
    power compared to a 1H weapon of the same quality.
    """
    n_prefix, n_suffix = get_affix_count(grade, quality)
    if two_handed:
        n_prefix *= 2
        n_suffix *= 2
    spec = QUALITY_SPECIAL[quality]
    floor_frac: float = spec["affix_floor"]
    guaranteed_max: bool = spec["guaranteed_max"]
    idx = grade - 1
    bias_weights: dict[str, float] = _get_affix_bias_weights(material_keys)
    rolled: list[dict] = []

    def _roll_one(a: dict, force_max: bool = False) -> dict:
        lo, hi = a["by_realm"][idx]
        effective_lo = lo + (hi - lo) * floor_frac
        if force_max:
            val = hi
        elif a["is_pct"]:
            val = round(random.uniform(effective_lo, hi), 5)
        else:
            val = random.randint(int(effective_lo), int(hi))
        return {"key": a["key"], "stat": a["stat"], "value": val, "type": a["type"]}

    def _roll(pool: list[dict], n: int, reserve_max_slot: int = -1) -> list[dict]:
        if bias_weights:
            # Biased affixes get a grade-scaled selection weight (6×–15×).
            weights = [bias_weights.get(a["key"], 1.0) for a in pool]
            chosen: list[dict] = []
            remaining = pool[:]
            remaining_w = weights[:]
            for _ in range(min(n, len(remaining))):
                picked = random.choices(remaining, weights=remaining_w, k=1)[0]
                chosen.append(picked)
                i = remaining.index(picked)
                remaining.pop(i)
                remaining_w.pop(i)
        else:
            chosen = random.sample(pool, min(n, len(pool)))
        return [_roll_one(a, force_max=(i == reserve_max_slot)) for i, a in enumerate(chosen)]

    # guaranteed_max applies to the first prefix (most impactful slot)
    if n_prefix:
        rolled += _roll(
            _eligible_affixes(slot, "prefix"),
            n_prefix,
            reserve_max_slot=0 if guaranteed_max else -1,
        )
    if n_suffix:
        rolled += _roll(_eligible_affixes(slot, "suffix"), n_suffix)
    return rolled


def compute_stats(implicit: dict[str, float], affixes: list[dict]) -> dict[str, float]:
    """Sum implicit + affix values into one computed_stats dict."""
    total: dict[str, float] = dict(implicit)
    for aff in affixes:
        stat, val = aff["stat"], aff["value"]
        total[stat] = total.get(stat, 0) + val
    return total


def _roll_quality(recipe: dict, comprehension: int = 0) -> str:
    """Roll item quality from a forge recipe (thin wrapper over the shared roll)."""
    return _roll_quality_shared(recipe["quality_chances"], comprehension)


def _build_display_name(base: dict, quality: str, affixes: list[dict]) -> str:
    """Generate a display name like 'Uy Mãnh Trường Kiếm của Trường Thọ'.

    ``type="super"`` affixes (super-material grants) are excluded — they're
    not part of the prefix/suffix slot system and have no localized name in
    ``registry.affixes``; the description belongs in the item's flavor text,
    not the title.
    """
    prefix_names = [a["key"] for a in affixes if a["type"] == "prefix"]
    suffix_names = [a["key"] for a in affixes if a["type"] == "suffix"]

    # Resolve vi names from registry
    def vi(key: str) -> str:
        aff = registry.affixes.get(key)
        return aff["vi"] if aff else key

    parts: list[str] = []
    if prefix_names:
        parts.append(" ".join(vi(k) for k in prefix_names))
    parts.append(base["vi"])
    if suffix_names:
        parts.append(" ".join(vi(k) for k in suffix_names))
    return " ".join(parts)


def forge_equipment(
    char: Character,
    base_key: str,
    grade: int,
    consumed_materials: list[tuple[str, int]],  # [(material_key, qty)]
    super_material_key: str | None = None,
) -> ForgeResult:
    """
    Core forge logic. Assumes requirements have already been validated and
    materials have been deducted from inventory. Deducts Công Đức in-place.

    ``super_material_key`` is an optional reference to a single super-rare
    forge material (``type == 'super_material'``). The material's
    ``granted_passive`` dict is grafted onto the forged item and merged into
    the wearer's stat totals at equip time (see ``equipment.py``). Only one
    super material can be consumed per forge operation — this is enforced by
    the singular argument type.

    Returns ForgeResult with `item_data` ready for EquipmentRepository.add_to_bag().
    """
    base = registry.get_base(base_key)
    if not base:
        return ForgeResult(False, f"Không tìm thấy loại trang bị '{base_key}'.")
    if "implicit_by_realm" not in base:
        return ForgeResult(False, f"Trang bị '{base_key}' chưa hỗ trợ hệ thống rèn cấp.")

    recipe = get_recipe(grade)
    if not recipe:
        return ForgeResult(False, f"Không tìm thấy công thức rèn cấp {grade}.")

    # Validate super material if provided. Rejects unknown keys and enforces
    # the per-material minimum item grade (e.g. R7 super materials refuse to
    # be used in a R5 forge).
    super_mat: dict | None = None
    if super_material_key:
        super_mat = registry.get_super_material(super_material_key)
        if not super_mat:
            return ForgeResult(False, f"Vật liệu siêu hiếm '{super_material_key}' không tồn tại.")
        min_item_grade = int(super_mat.get("min_item_grade", super_mat.get("grade", 1)))
        if grade < min_item_grade:
            return ForgeResult(
                False,
                f"Vật liệu **{super_mat['vi']}** chỉ dùng được khi rèn trang bị "
                f"từ cấp {min_item_grade} trở lên.",
            )

    # Deduct Công Đức
    char.merit -= recipe["cost_cong_duc"]

    # Mixed-material forges union every consumed material's affix_bias so
    # each one contributes its biased keys to the 6×–15× roll weighting.
    consumed_keys = [k for k, _ in consumed_materials] if consumed_materials else []

    quality = _roll_quality(recipe, char.stats.comprehension)
    spec = QUALITY_SPECIAL[quality]
    implicit = roll_implicit_stats(base, grade, quality)
    affixes = roll_affixes(
        base["slot"], grade, quality,
        material_keys=consumed_keys,
        two_handed=bool(base.get("two_handed", False)),
    )
    # Super-material grants are now stamped as ``type="super"`` affix entries
    # so the bonus is visible in the item's affix list (not just an
    # equip-time merge). Numeric grants flow through ``compute_stats`` like
    # any other affix; bool grants stay out of the affix list since
    # ``compute_stats`` sums numerics only — those are still applied via the
    # equip-time merge in ``compute_equipment_stats``.
    if super_mat:
        for stat, val in (super_mat.get("granted_passive") or {}).items():
            if isinstance(val, bool):
                continue
            affixes.append({
                "key": f"super_{stat}",
                "stat": stat,
                "value": val,
                "type": "super",
            })
    computed = compute_stats(implicit, affixes)
    name = _build_display_name(base, quality, affixes)

    item_data = {
        "slot": base["slot"],
        "base_key": base_key,
        "grade": grade,
        "quality": quality,
        "special_label": spec["special_label"],
        "implicit_stats": implicit,
        "affixes": affixes,
        "computed_stats": computed,
        "display_name": name,
        "super_material_key": super_material_key,
    }

    suffix = f"\n{spec['special_label']}" if spec["special_label"] else ""
    if super_mat:
        # List the super-material's grant inline so the forge result shows
        # the actual stats picked up, not just the marketing line.
        from src.game.engine.equipment import format_stat as _fmt_stat, STAT_LABELS as _STAT_LABELS
        bullets: list[str] = []
        for stat, val in (super_mat.get("granted_passive") or {}).items():
            if isinstance(val, bool):
                if val:
                    bullets.append(f"✓ {_STAT_LABELS.get(stat, stat)}")
            else:
                bullets.append(_fmt_stat(stat, val))
        bullet_block = ("\n  • " + "\n  • ".join(bullets)) if bullets else ""
        suffix += f"\n✨ **{super_mat['vi']}** — Đặc Biệt:{bullet_block}"
    return ForgeResult(
        success=True,
        message=(
            f"✅ Rèn thành công: **{name}** "
            f"[Cấp {grade} — {QUALITY_LABELS[quality]}]{suffix}"
        ),
        item_data=item_data,
    )


# ── Helper: describe a recipe for display ────────────────────────────────────

def describe_recipe(grade: int) -> str:
    recipe = get_recipe(grade)
    if not recipe:
        return f"Không có công thức cấp {grade}."

    lines = [
        f"**Rèn trang bị Cấp {grade}**",
        f"• Cảnh giới tối thiểu: Luyện Khí cảnh thứ {recipe['min_qi_realm'] + 1}",
        f"• Chi phí: {recipe['cost_cong_duc']:,} Công Đức ✨",
        "",
        "**Nguyên liệu:**",
    ]
    required_qty = max_affix_total(grade)
    lines.append(f"  • {required_qty}x Vật liệu luyện khí (bất kỳ phẩm)")

    c = recipe["quality_chances"]
    lines += [
        "",
        "**Tỷ lệ phẩm chất:**",
        f"• Hoàng: {c['hoan']*100:.0f}%  Huyền: {c['huyen']*100:.0f}%  "
        f"Địa: {c['dia']*100:.0f}%  Thiên: {c['thien']*100:.0f}%",
    ]
    return "\n".join(lines)
