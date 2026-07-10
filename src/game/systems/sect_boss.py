"""Tông Môn sect boss — Trấn Sơn Thú weekly co-op guardian (pure logic).

A per-sect weekly raid, unlocked at sect L3 (``sect_levels.json``). One
instance per (sect, ISO week), lazily spawned the first time anyone opens
``/tongmon boss`` that week; a cog sweep expires unkilled instances when the
week rolls over so the participation tier still pays out.

Structural clone of the world-boss machinery: shared HP pool, atomic damage
with a per-attack cap, participation rows, claim races. Differences:
* the boss is selected from ``sect_bosses.json`` — a **new enemy family**
  (Trấn Sơn Thạch Quỷ → Hắc Phong Lang Vương → Cửu U Huyết Giao), tiered by
  ``min_sect_level`` and scaled by sect level + roster size;
* rewards are chest ITEMS + Cống Hiến (data-driven per boss) instead of
  loot-table rolls, plus a one-time sect-side EXP/funds grant on the kill.

DB-facing atomics live in ``src.db.repositories.sect_boss_repo``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ── Combat knobs (⚙ docs §3.9) ───────────────────────────────────────────────

SECT_BOSS_MIN_LEVEL = 3          # sect level that unlocks the weekly boss
ATTACKS_PER_WEEK = 3             # per member
ATTACK_ROUND_LIMIT = 15          # combat rounds per attack session
PER_ATTACK_DMG_CAP_PCT = 0.10    # max fraction of hp_max one attack may land
PARTICIPATION_MIN_DMG_PCT = 0.005  # 0.5% of hp_max qualifies for rewards


def boss_defs() -> dict[str, dict]:
    from src.data.registry import registry
    return registry.sect_bosses


def get_boss(boss_key: str) -> dict | None:
    return boss_defs().get(boss_key)


def select_boss(sect_level: int) -> dict | None:
    """The strongest boss whose ``min_sect_level`` the sect satisfies —
    None below ``SECT_BOSS_MIN_LEVEL``."""
    if sect_level < SECT_BOSS_MIN_LEVEL:
        return None
    eligible = [
        b for b in boss_defs().values()
        if int(b.get("min_sect_level", SECT_BOSS_MIN_LEVEL)) <= int(sect_level)
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda b: int(b.get("min_sect_level", 0)))


def compute_hp_max(
    boss_data: dict, sect_level: int, member_count: int, member_cap: int
) -> int:
    """Weekly HP pool (docs §3.9): per-level growth above the boss's tier
    floor, then the roster factor ``0.6 + 0.4 × members/cap`` so small sects
    face a proportionally smaller wall."""
    base = int(boss_data.get("base_hp", 1_000_000))
    per_level = float(boss_data.get("hp_per_level", 0.15))
    min_level = int(boss_data.get("min_sect_level", SECT_BOSS_MIN_LEVEL))
    level_mult = 1.0 + per_level * max(0, int(sect_level) - min_level)

    cap = max(1, int(member_cap))
    roster = max(0.0, min(1.0, member_count / cap))
    roster_mult = 0.6 + 0.4 * roster
    return max(1, int(base * level_mult * roster_mult))


def next_week_start(now: datetime) -> datetime:
    """Monday 00:00 UTC of the FOLLOWING ISO week — the instance's expiry."""
    from src.game.systems.sect import week_start_utc
    return week_start_utc(now) + timedelta(days=7)


def build_boss_combatant(boss_data: dict, hp_current: int, hp_max: int, player_realm_total: int):
    """Boss Combatant with the instance's member-scaled HP pool.

    Reuses ``build_world_boss_combatant`` verbatim (realm-scaled stats,
    hard-CC immunity, fixed skill_pool) — the builder derives ``hp_max``
    from ``base_hp × hp_scale``, so we substitute the instance's stored
    ``hp_max`` there instead of the tier's raw ``base_hp``.
    """
    from src.game.systems.combat import build_world_boss_combatant
    effective = {**boss_data, "base_hp": int(hp_max), "hp_scale": 1.0}
    return build_world_boss_combatant(effective, int(hp_current), player_realm_total)


# ── Rewards ───────────────────────────────────────────────────────────────────

def reward_block(boss_data: dict) -> dict:
    return boss_data.get("reward", {}) or {}


def participation_threshold(hp_max: int) -> int:
    return int(hp_max * PARTICIPATION_MIN_DMG_PCT)


def reward_tier(rank: int, damage_dealt: int, hp_max: int) -> str:
    """"top" (rank ≤ 3), "participant", or "none" (below the damage floor).
    The finisher gets the kill log/banner, not an extra item tier — with
    3 attacks/member the killing blow is almost always already top-3."""
    if damage_dealt < participation_threshold(hp_max):
        return "none"
    return "top" if rank <= 3 else "participant"
