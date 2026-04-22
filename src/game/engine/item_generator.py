"""Item instance generator — creates ItemInstance dicts from bases/affixes/uniques."""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

def _pick_affix_tier(affix: dict, grade: int) -> dict | None:
    """Return the best eligible tier for this grade, or None if none qualify."""
    eligible = [t for t in affix["tiers"] if t["grade_req"] <= grade]
    return eligible[-1] if eligible else None


def _slot_allows(affix: dict, slot: str) -> bool:
    allowed = affix["allowed_slots"]
    return allowed == ["*"] or slot in allowed


def _roll_value(tier: dict, rng: random.Random) -> float:
    lo, hi = tier["min"], tier["max"]
    val = rng.uniform(lo, hi)
    # Keep pct stats at 4 dp; integer stats rounded
    if isinstance(lo, float) or isinstance(hi, float):
        return round(val, 4)
    return round(val)


def _build_display_name(base_vi: str, affixes: list[dict], registry) -> str:
    prefix_vi = next(
        (registry.get_affix(a["key"])["vi"] for a in affixes if a["type"] == "prefix"),
        None,
    )
    suffix_vi = next(
        (registry.get_affix(a["key"])["vi"] for a in affixes if a["type"] == "suffix"),
        None,
    )
    name = base_vi
    if prefix_vi:
        name = f"{prefix_vi} {name}"
    if suffix_vi:
        name = f"{name} {suffix_vi}"
    return name


def generate_item(
    base_key: str,
    grade: int,
    rng: random.Random,
    *,
    num_prefixes: int = 1,
    num_suffixes: int = 1,
) -> dict:
    """Generate a dict suitable for creating an ItemInstance.

    Returns a plain dict (not an ORM object) with all fields needed.
    Grade controls which affix tiers are accessible:
      grade 1 → tier 1 only
      grade 2 → tier 1-2
      grade 3 → all tiers
    """
    from src.data.registry import registry

    base = registry.get_base(base_key)
    if not base:
        raise ValueError(f"Unknown base key: {base_key!r}")

    slot = base["slot"]
    computed_stats: dict[str, float] = {}

    # Start with implicit stats
    for stat, val in base.get("implicit_stats", {}).items():
        computed_stats[stat] = float(val)

    chosen_affixes: list[dict] = []

    def _pick_affixes(affix_type: str, count: int) -> None:
        candidates = [
            a for a in registry.affixes.values()
            if a["type"] == affix_type and _slot_allows(a, slot)
        ]
        # Exclude affix keys already rolled (avoid duplication)
        used_keys = {a["key"] for a in chosen_affixes}
        candidates = [a for a in candidates if a["key"] not in used_keys]
        # Also avoid rolling the same stat twice
        used_stats = {a["stat"] for a in chosen_affixes}
        candidates = [a for a in candidates if a["stat"] not in used_stats]

        rng.shuffle(candidates)
        added = 0
        for affix in candidates:
            if added >= count:
                break
            tier = _pick_affix_tier(affix, grade)
            if tier is None:
                continue
            val = _roll_value(tier, rng)
            chosen_affixes.append({"key": affix["key"], "stat": affix["stat"], "value": val, "type": affix_type})
            computed_stats[affix["stat"]] = computed_stats.get(affix["stat"], 0.0) + val
            added += 1

    _pick_affixes("prefix", num_prefixes)
    _pick_affixes("suffix", num_suffixes)

    display_name = _build_display_name(base["vi"], chosen_affixes, registry)

    return {
        "base_key": base_key,
        "unique_key": None,
        "grade": grade,
        "slot": slot,
        "affixes": chosen_affixes,
        "computed_stats": computed_stats,
        "display_name": display_name,
        "location": "bag",
    }


def generate_unique(unique_key: str) -> dict:
    """Generate a dict for a unique item instance."""
    from src.data.registry import registry

    uniq = registry.get_unique(unique_key)
    if not uniq:
        raise ValueError(f"Unknown unique key: {unique_key!r}")

    computed_stats = {k: float(v) for k, v in uniq["fixed_stats"].items()}

    return {
        "base_key": uniq.get("base"),
        "unique_key": unique_key,
        "grade": uniq["grade"],
        "slot": uniq["slot"],
        "affixes": [],
        "computed_stats": computed_stats,
        "display_name": uniq["vi"],
        "location": "bag",
    }

def grade_from_realm(realm_total: int) -> int:
    """Determine item grade based on average cultivation level."""
    if realm_total < 18:
        return 1
    if realm_total < 45:
        return 2
    return 3
