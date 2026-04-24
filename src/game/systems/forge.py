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
    """Return the grade of a forge material, or None if not a forge material."""
    item = registry.get_item(material_key)
    if item and item.get("type") == "material":
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

    # Check each option — use first satisfiable one
    for option in recipe["options"]:
        mats_needed = option["materials"]
        ok = all(
            sum(
                qty
                for key, qty in materials_in_bag.items()
                if get_material_grade(key) == req["mat_grade"]
            ) >= required_qty
            for req in mats_needed
        )
        if ok:
            # Normalize the returned option so downstream consumption uses the
            # computed qty rather than the stale JSON value.
            return True, "", _normalize_option(option, required_qty)

    cheapest = recipe["options"][0]
    lines = [
        f"• {required_qty}x Vật liệu luyện khí phẩm {req['mat_grade']}"
        for req in cheapest["materials"]
    ]
    return (
        False,
        "Thiếu nguyên liệu. Cần:\n" + "\n".join(lines),
        None,
    )


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
    for stat, ranges in base["implicit_by_grade"].items():
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


def _get_affix_bias(material_key: str | None) -> set[str]:
    """Return the set of biased affix keys for a forge material, or empty set."""
    if not material_key:
        return set()
    item = registry.get_item(material_key)
    if item and item.get("type") == "material":
        return set(item.get("affix_bias", []))
    return set()


def roll_affixes(
    slot: str,
    grade: int,
    quality: str,
    material_key: str | None = None,
    two_handed: bool = False,
) -> list[dict]:
    """Roll affixes, applying quality floor and guaranteed-max special effects.

    Biased affixes (from the consumed material) receive 3× selection weight.

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
    bias: set[str] = _get_affix_bias(material_key)
    rolled: list[dict] = []

    def _roll_one(a: dict, force_max: bool = False) -> dict:
        lo, hi = a["by_grade"][idx]
        effective_lo = lo + (hi - lo) * floor_frac
        if force_max:
            val = hi
        elif a["is_pct"]:
            val = round(random.uniform(effective_lo, hi), 5)
        else:
            val = random.randint(int(effective_lo), int(hi))
        return {"key": a["key"], "stat": a["stat"], "value": val, "type": a["type"]}

    def _roll(pool: list[dict], n: int, reserve_max_slot: int = -1) -> list[dict]:
        if bias:
            # Biased affixes get 3× selection weight
            weights = [3.0 if a["key"] in bias else 1.0 for a in pool]
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
    """Generate a display name like 'Uy Mãnh Trường Kiếm của Trường Thọ'."""
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
    if "implicit_by_grade" not in base:
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

    # Use the first consumed material's key to bias affix selection
    primary_material = consumed_materials[0][0] if consumed_materials else None

    quality = _roll_quality(recipe, char.stats.comprehension)
    spec = QUALITY_SPECIAL[quality]
    implicit = roll_implicit_stats(base, grade, quality)
    affixes = roll_affixes(
        base["slot"], grade, quality,
        material_key=primary_material,
        two_handed=bool(base.get("two_handed", False)),
    )
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
        suffix += f"\n✨ Dung hợp **{super_mat['vi']}** — nhận thêm hiệu ứng đặc biệt."
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
        "**Nguyên liệu (chọn 1 trong các tùy chọn):**",
    ]
    required_qty = max_affix_total(grade)
    for i, opt in enumerate(recipe["options"], 1):
        mat_parts = [
            f"{required_qty}x Vật liệu luyện khí phẩm {r['mat_grade']}"
            for r in opt["materials"]
        ]
        lines.append(f"  Phương án {i}: " + ", ".join(mat_parts))

    c = recipe["quality_chances"]
    lines += [
        "",
        "**Tỷ lệ phẩm chất:**",
        f"• Hoàng: {c['hoan']*100:.0f}%  Huyền: {c['huyen']*100:.0f}%  "
        f"Địa: {c['dia']*100:.0f}%  Thiên: {c['thien']*100:.0f}%",
    ]
    return "\n".join(lines)
