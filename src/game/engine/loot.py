"""Loot table composition — derives drop entries from registry data.

Sibling of ``drop.py``: where ``drop`` *resolves* a finished table into
concrete items, this module *builds* the table — specifically, it injects
dynamic scroll drops on top of the static zone JSON so that:

  * adding or re-grading a skill flows through to drops without editing
    zone loot JSON, and
  * grade-3 / grade-4 scroll drop *shares* stay constant across zones
    whose static weight totals and qualifying-scroll counts vary widely
    (zone 1 has ~2M static + 4 g3 scrolls; zone 9 has ~3.6M + 91).

Pure functions — no registry import, no global state. The caller passes
the registry's ``items`` and ``skills`` dicts in directly so this module
has no dependency on ``GameRegistry`` and can be unit-tested in isolation.
"""
from __future__ import annotations

# Target *aggregate* drop share per loot roll for any scroll of the given
# grade. Per-scroll weights are derived dynamically from these so the share
# holds across zones (see ``inject_scroll_drops``).
SCROLL_DROP_TARGET_SHARE: dict[int, float] = {3: 0.003125, 4: 0.000625}

# ── Global world drops ─────────────────────────────────────────────────────────
# Entries appended to EVERY non-empty loot table by ``GameRegistry.
# get_loot_table`` — the "can drop anywhere" lane. Independent-mode weights
# are ABSOLUTE (out of drop.POOL_RANGE = 1,000,000), so the chance is uniform
# per roll regardless of the host table: 100 = 0.01% ≈ one per ~10k kills —
# roughly a week of nonstop AFK farming (1440 turns/day). Loot-luck applies.
GLOBAL_WORLD_DROPS: list[dict] = [
    # Thiên Mệnh Thạch — unlocks / swaps a Thể Chất (see the constitution cog).
    {"item_key": "MatThienMenhThach", "weight": 100, "qty_min": 1, "qty_max": 1},
]


def inject_global_drops(static: list[dict]) -> list[dict]:
    """Return the world-drop entries to append to a loot table.

    Empty static tables stay empty — an unresolvable/placeholder table key
    must not become a stone fountain. Entries are copied so callers can't
    mutate the module-level template.
    """
    if not static:
        return []
    return [dict(e) for e in GLOBAL_WORLD_DROPS]


def inject_scroll_drops(
    items: dict[str, dict],
    skills: dict[str, dict],
    zone_realm: int,
    static: list[dict],
    *,
    target_share: dict[int, float] | None = None,
) -> list[dict]:
    """Return drop entries for grade 3-4 scrolls eligible for ``zone_realm``.

    Per-scroll weights are derived from ``target_share`` (defaults to
    ``SCROLL_DROP_TARGET_SHARE``) so that, after injection, *any* grade-G
    scroll lands at the configured share of the loot roll. Solved from:

        share = injected_total / (static_total + injected_total)
      ⇒ injected_total = static_total × share / (1 − Σ shares)

    Then divided evenly across the qualifying scrolls of that grade.
    Rounded to int with a floor of 1 so tiny static totals still produce
    representable weights.

    Args:
        items:       Registry's items dict (key → item).
        skills:      Registry's skills dict (key → skill).
        zone_realm:  Zone number — kept for the call signature, no longer
                     gates which scrolls are eligible (skills no longer
                     carry a realm field).
        static:      The zone's static drop entries (used to size injection).
        target_share: Optional override for share targets. Default keeps
                     grade-3 at 0.3125% and grade-4 at 0.0625% per roll
                     (kept low because auto/AFK farm rolls ~90% of kills).

    Returns:
        List of drop entries (``item_key``, ``weight``, ``qty_min/max``).
        Empty when the zone has no static weight or no qualifying scrolls.
    """
    del zone_realm  # accepted for back-compat; ignored after realm removal
    shares = target_share if target_share is not None else SCROLL_DROP_TARGET_SHARE
    leftover = 1.0 - sum(shares.values())
    if leftover <= 0:
        return []  # misconfigured shares — refuse to inject

    static_total = sum(int(e.get("weight", 0)) for e in static)
    if static_total <= 0:
        return []

    by_grade: dict[int, list[dict]] = {g: [] for g in shares}
    for item in items.values():
        if item.get("type") != "scroll":
            continue
        skill_key = item.get("taught_skill")
        if not skill_key:
            continue
        grade = int(item.get("grade", 0))
        if grade not in by_grade:
            continue
        if skill_key not in skills:
            continue
        by_grade[grade].append(item)

    out: list[dict] = []
    for grade, scrolls in by_grade.items():
        if not scrolls:
            continue
        injected_total = static_total * shares[grade] / leftover
        per_scroll = max(1, round(injected_total / len(scrolls)))
        for item in scrolls:
            out.append({
                "item_key": item["key"],
                "weight": per_scroll,
                "qty_min": 1,
                "qty_max": 1,
            })
    return out
