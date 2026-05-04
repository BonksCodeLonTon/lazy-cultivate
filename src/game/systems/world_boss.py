"""World boss system — spawn scheduling, attack resolution, reward distribution.

World bosses are server-wide persistent fights. Unlike dungeons:
- HP persists across players — each player attacks dwindles a shared HP pool
- Bosses are immune to hard CC (stun/freeze/silence/interrupt/knock-up)
- Spawn on fixed times of day (realm-specific slots); each spawn lives for a
  fixed duration window unless killed earlier
- Each realm has multiple (~3) bosses that rotate
- Rewards distributed on kill:
    * Finisher:           rare tier chest (always)
    * Top-3 damage:       elite tier chest
    * All participants:   standard participation chest (only if total damage >= threshold)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable

from src.data.registry import registry

# ── Reward tiers (item keys appended on top of boss's own loot chest) ──────────
FINISHER_BONUS_ITEMS: dict[int, tuple[str, int]] = {
    # realm → (item_key, qty) — additional bonus for the killing blow
    1: ("ItemPhaCanh", 1),
    2: ("ItemPhaCanh", 1),
    3: ("ItemPhaCanh", 2),
    4: ("ItemHonNguyen", 1),
    5: ("ItemHonNguyen", 2),
    6: ("ItemHonNguyen", 3),
    7: ("ItemHonNguyen", 4),
    8: ("ItemHonNguyen", 5),
    9: ("ItemHonNguyen", 8),
}

# Minimum % of boss HP a player must damage to qualify for participation reward
PARTICIPATION_MIN_DMG_PCT = 0.005   # 0.5%

# Per-attack HP window — player fights the boss for this many combat rounds.
ATTACK_ROUND_LIMIT = 15

# Maximum damage a single attack session can apply to the shared HP pool,
# expressed as a fraction of the boss's ``hp_max``. Enforced both in the
# Discord cog (before write) and inside ``apply_damage_atomic`` (at the
# authoritative row-lock) so concurrent writes and client-side bypasses both
# hit the same wall. Keeps one whale from one-tapping a boss and prevents
# any single attacker from dominating the damage leaderboard.
PER_ATTACK_DMG_CAP_PCT = 0.05   # 5%


# ── Spawn scheduling ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpawnWindow:
    """One planned boss spawn: boss_key + when it goes live + when it expires."""
    boss_key: str
    realm: int
    spawned_at: datetime
    expires_at: datetime


def _parse_time(hhmm: str) -> time:
    h, m = hhmm.split(":", 1)
    return time(int(h), int(m), tzinfo=timezone.utc)


def upcoming_spawns(now: datetime, horizon: timedelta = timedelta(days=1)) -> list[SpawnWindow]:
    """Compute every boss spawn occurring in [now, now+horizon].

    Used by the scheduler to decide which bosses should be active right now.
    ``spawn_times`` in boss data is in UTC (24h clock, "HH:MM").
    """
    windows: list[SpawnWindow] = []
    for boss in registry.world_bosses.values():
        dur = timedelta(minutes=boss.get("duration_minutes", 60))
        for hhmm in boss.get("spawn_times", []):
            t = _parse_time(hhmm)
            for day_offset in (-1, 0, 1):
                day = (now + timedelta(days=day_offset)).date()
                spawn_dt = datetime.combine(day, t)
                if now - dur < spawn_dt < now + horizon:
                    windows.append(SpawnWindow(
                        boss_key=boss["key"],
                        realm=boss["realm"],
                        spawned_at=spawn_dt,
                        expires_at=spawn_dt + dur,
                    ))
    return sorted(windows, key=lambda w: w.spawned_at)


def is_boss_live_now(boss_data: dict, now: datetime) -> SpawnWindow | None:
    """Return the currently-live spawn window for a boss, or None."""
    dur = timedelta(minutes=boss_data.get("duration_minutes", 60))
    for hhmm in boss_data.get("spawn_times", []):
        t = _parse_time(hhmm)
        for day_offset in (-1, 0):
            day = (now + timedelta(days=day_offset)).date()
            spawn_dt = datetime.combine(day, t)
            if spawn_dt <= now < spawn_dt + dur:
                return SpawnWindow(
                    boss_key=boss_data["key"],
                    realm=boss_data["realm"],
                    spawned_at=spawn_dt,
                    expires_at=spawn_dt + dur,
                )
    return None


# ── Reward distribution ───────────────────────────────────────────────────────

@dataclass
class ParticipantReward:
    """Computed reward for one participant of a killed world boss."""
    player_id: int
    tier: str                  # "finisher" | "top" | "participant" | "none"
    rank: int                  # 1-based damage rank
    damage_pct: float          # fraction of boss HP this player dealt
    loot_table_keys: list[str] # loot tables to roll on claim
    bonus_items: list[tuple[str, int]]  # extra (item_key, qty) entries


def compute_rewards(
    boss_data: dict,
    boss_hp_max: int,
    participations: list,   # list[WorldBossParticipation] (duck-typed for testability)
    finisher_player_id: int | None,
) -> list[ParticipantReward]:
    """Return ParticipantReward list sorted by damage desc.

    Reward tiering:
      - Finisher (killing blow):   boss's primary loot_chest_key + finisher bonus items
      - Top 3 by damage:            boss's primary loot_chest_key (rare drops likely)
      - Participants (>= threshold): ChestHuyen/Dia participation drops
    """
    rewards: list[ParticipantReward] = []
    sorted_parts = sorted(participations, key=lambda p: p.damage_dealt, reverse=True)

    realm = boss_data.get("realm", 1)
    primary_chest = boss_data.get("loot_chest_key")
    bonus_item = FINISHER_BONUS_ITEMS.get(realm)
    threshold = int(boss_hp_max * PARTICIPATION_MIN_DMG_PCT)

    # Participation tier chest is tier-scaled by realm
    if realm <= 3:
        participant_chest = "LootChestHuyen"
    elif realm <= 6:
        participant_chest = "LootChestDia"
    else:
        participant_chest = "LootChestThien"

    for rank, part in enumerate(sorted_parts, start=1):
        dmg_pct = part.damage_dealt / boss_hp_max if boss_hp_max else 0.0
        loot_tables: list[str] = []
        bonuses: list[tuple[str, int]] = []
        is_finisher = part.player_id == finisher_player_id

        if is_finisher and primary_chest:
            # Finisher always gets the primary chest + finisher bonus items
            loot_tables.append(primary_chest)
            if bonus_item:
                bonuses.append(bonus_item)
            tier = "finisher"
        elif rank <= 3 and primary_chest and part.damage_dealt >= threshold:
            loot_tables.append(primary_chest)
            tier = "top"
        elif part.damage_dealt >= threshold:
            loot_tables.append(participant_chest)
            tier = "participant"
        else:
            tier = "none"

        rewards.append(ParticipantReward(
            player_id=part.player_id,
            tier=tier,
            rank=rank,
            damage_pct=dmg_pct,
            loot_table_keys=loot_tables,
            bonus_items=bonuses,
        ))

    return rewards


# ── Participation helpers ─────────────────────────────────────────────────────

def format_leaderboard(participations: Iterable, max_rows: int = 10) -> str:
    """Render a short damage leaderboard for display in a Discord embed."""
    sorted_parts = sorted(participations, key=lambda p: p.damage_dealt, reverse=True)
    if not sorted_parts:
        return "*(chưa có ai tấn công)*"
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(sorted_parts[:max_rows]):
        icon = medals[i] if i < len(medals) else f"`#{i+1}`"
        lines.append(f"{icon} Player #{p.player_id} — {p.damage_dealt:,} sát thương ({p.attack_count} đòn)")
    return "\n".join(lines)


# ── Loot / scheduler service helpers ──────────────────────────────────────────
# These wrap repository calls + game logic (loot rolling, scheduler tick)
# behind Discord-free entry points so the cog stays presentation-only.

import logging as _logging
import random as _random_mod

from src.game.constants.grades import Grade
from src.game.engine.drop import roll_drops
from src.game.systems.dungeon import merge_loot

_log = _logging.getLogger(__name__)


async def grant_loot_from_tables(
    irepo,
    player_id: int,
    table_keys: list[str],
    bonus_items: list[tuple[str, int]],
    rng: _random_mod.Random,
) -> list[dict]:
    """Roll all loot tables + add bonus items, merge duplicates, and grant.

    Returns the merged drop list so the caller can render it. Items are
    added at their registry-declared grade (default Hoàng = 1 if missing).
    """
    all_drops: list[dict] = []
    for table_key in table_keys:
        table = registry.get_loot_table(table_key)
        if not table:
            continue
        all_drops.extend(roll_drops(table, rng).merge())
    for item_key, qty in bonus_items:
        all_drops.append({"item_key": item_key, "quantity": qty})

    final = [{"item_key": k, "quantity": v} for k, v in merge_loot(all_drops).items()]
    for drop in final:
        data = registry.get_item(drop["item_key"])
        grade_val = data.get("grade", 1) if data else 1
        await irepo.add_item(player_id, drop["item_key"], Grade(grade_val), drop["quantity"])
    return final


async def flag_rewards_distributed(wb_repo, instance_id: int, boss_key: str) -> bool:
    """Atomically mark the boss instance's rewards as ready for claiming.

    Returns ``True`` if this call won the flip, ``False`` if a concurrent
    finisher already flagged it. Actual loot grants happen on /rewards.
    """
    won = await wb_repo.flag_rewards_distributed(instance_id)
    if won:
        _log.info(
            "World boss %s (instance=%s) rewards flagged ready.",
            boss_key, instance_id,
        )
    return won


async def scheduler_tick(wb_repo) -> None:
    """Single iteration of the world-boss spawn/expire scheduler.

    Expires any active instance past its deadline, then spawns any boss
    whose scheduled window is currently live and lacks an instance for
    that *specific* window — using ``has_instance_for_window`` so a boss
    killed mid-window does NOT respawn until the next scheduled time.
    """
    now = datetime.now(timezone.utc)

    active = await wb_repo.list_active()
    for inst in active:
        if inst.expires_at <= now and inst.hp_current > 0:
            await wb_repo.expire_instance(inst)

    for boss_data in registry.world_bosses.values():
        window = is_boss_live_now(boss_data, now)
        if window is None:
            continue
        if await wb_repo.has_instance_for_window(boss_data["key"], window.spawned_at):
            continue
        hp_max = int(boss_data["base_hp"] * boss_data.get("hp_scale", 1.0))
        await wb_repo.create_instance(
            boss_key=boss_data["key"],
            realm=boss_data.get("realm", 1),
            hp_max=hp_max,
            spawned_at=window.spawned_at,
            expires_at=window.expires_at,
        )
        _log.info("Spawned world boss %s (hp_max=%d)", boss_data["key"], hp_max)
