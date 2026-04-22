"""Forge system — craft equipment from materials."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from src.data.registry import registry
from src.game.models.character import Character


# ── Quality tiers (equipment rarities) ──────────────────────────────────────

QUALITY_LABELS: dict[str, str] = {
    "hoan": "Hoàng Phẩm",
    "huyen": "Huyền Phẩm",
    "dia": "Địa Phẩm",
    "thien": "Thiên Phẩm",
}

# How many (prefix, suffix) affixes each quality can roll
QUALITY_AFFIX_COUNT: dict[str, tuple[int, int]] = {
    "hoan": (1, 0),
    "huyen": (1, 1),
    "dia": (2, 1),
    "thien": (2, 2),
}

# Special effects applied on top of normal rolling
# implicit_mult  — multiplies all rolled implicit stat values
# affix_floor    — fraction of range used as new minimum (0.0 = normal, 0.5 = top-half rolls)
# guaranteed_max — one affix rolls at exactly its grade max
# special_label  — display label shown on the item
QUALITY_SPECIAL: dict[str, dict] = {
    "hoan":  {
        "implicit_mult": 1.0,
        "affix_floor": 0.0,
        "guaranteed_max": False,
        "special_label": None,
    },
    "huyen": {
        "implicit_mult": 1.15,
        "affix_floor": 0.0,
        "guaranteed_max": False,
        "special_label": "✦ Linh Khí Tăng Cường",        # +15% implicit stats
    },
    "dia":   {
        "implicit_mult": 1.30,
        "affix_floor": 0.5,                               # affixes roll in top 50% of range
        "guaranteed_max": False,
        "special_label": "✦✦ Thiên Địa Tinh Hoa",        # +30% implicit + elevated affixes
    },
    "thien": {
        "implicit_mult": 1.50,
        "affix_floor": 0.75,                              # affixes roll in top 25% of range
        "guaranteed_max": True,                           # one affix always at max value
        "special_label": "✦✦✦ Vô Thượng Thần Vật",      # +50% implicit + near-max affixes
    },
}


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

    # Check each option — use first satisfiable one
    for option in recipe["options"]:
        mats_needed = option["materials"]
        # Player must provide at least one material that satisfies the grade requirement
        # We validate by looking at what's in their bag matching the grade
        ok = all(
            sum(
                qty
                for key, qty in materials_in_bag.items()
                if get_material_grade(key) == req["mat_grade"]
            ) >= req["qty"]
            for req in mats_needed
        )
        if ok:
            return True, "", option

    # Build a helpful error showing the cheapest option
    cheapest = recipe["options"][0]
    lines = []
    for req in cheapest["materials"]:
        lines.append(f"• {req['qty']}x Vật liệu luyện khí phẩm {req['mat_grade']}")
    return (
        False,
        "Thiếu nguyên liệu. Cần:\n" + "\n".join(lines),
        None,
    )


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
) -> list[dict]:
    """Roll affixes, applying quality floor and guaranteed-max special effects.

    Biased affixes (from the consumed material) receive 3× selection weight.
    """
    n_prefix, n_suffix = QUALITY_AFFIX_COUNT[quality]
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
    """Roll item quality, with a small bonus from comprehension stat."""
    bonus = min(comprehension * 0.0005, 0.1)  # max +10% thien chance
    chances = dict(recipe["quality_chances"])
    chances["thien"] = min(chances["thien"] + bonus, 1.0)

    # Re-normalise (keep relative proportions for non-thien tiers)
    total = sum(chances.values())
    roll = random.random() * total
    cumulative = 0.0
    for quality, weight in chances.items():
        cumulative += weight
        if roll <= cumulative:
            return quality
    return "hoan"


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
) -> ForgeResult:
    """
    Core forge logic. Assumes requirements have already been validated and
    materials have been deducted from inventory. Deducts Công Đức in-place.

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

    # Deduct Công Đức
    char.merit -= recipe["cost_cong_duc"]

    # Use the first consumed material's key to bias affix selection
    primary_material = consumed_materials[0][0] if consumed_materials else None

    quality = _roll_quality(recipe, char.stats.comprehension)
    spec = QUALITY_SPECIAL[quality]
    implicit = roll_implicit_stats(base, grade, quality)
    affixes = roll_affixes(base["slot"], grade, quality, material_key=primary_material)
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
    }

    suffix = f"\n{spec['special_label']}" if spec["special_label"] else ""
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
    for i, opt in enumerate(recipe["options"], 1):
        mat_parts = [f"{r['qty']}x Vật liệu luyện khí phẩm {r['mat_grade']}" for r in opt["materials"]]
        lines.append(f"  Phương án {i}: " + ", ".join(mat_parts))

    c = recipe["quality_chances"]
    lines += [
        "",
        "**Tỷ lệ phẩm chất:**",
        f"• Hoàng: {c['hoan']*100:.0f}%  Huyền: {c['huyen']*100:.0f}%  "
        f"Địa: {c['dia']*100:.0f}%  Thiên: {c['thien']*100:.0f}%",
    ]
    return "\n".join(lines)
