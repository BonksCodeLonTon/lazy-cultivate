"""Đại Chiến Khoáng Mạch — spirit-stone mine wars (pure logic, no DB).

The world map holds 9 linh-thạch mines in 4 tiers (``mines.json``). An
occupying sect earns **funds hourly** (lazy whole-hour accrual + a sweep
backstop). Sects fight over them in 24-hour war windows:

* **unoccupied mine** → PvE siege: the declaring sect raids the mine's NPC
  garrison (shared HP pool, sect-boss-style atomic damage). Garrison dead
  inside the window → immediate capture.
* **occupied mine** → async PvP: members of both sects spend attempts
  dueling *defense snapshots* of random opposing members (the persisted
  build fights back via the combat AI — no defender presence needed).
  Higher points at window end flips the mine; ties keep the defender.

DB-facing atomics live in ``src.db.repositories.sect_mine_repo``.
Lock-order contract there: **sect → mine** and **war → mine** (never take a
mine lock first when a sect/war lock is also needed).
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ── War knobs (⚙ docs §3.10) ─────────────────────────────────────────────────

MINE_WAR_MIN_SECT_LEVEL = 4      # sect level required to declare
DECLARE_FEE_FUNDS = 30_000       # deducted from the attacker's treasury
WAR_WINDOW_HOURS = 24
ATTEMPTS_PER_WAR = 5             # per member, per war (both sides)
POINTS_WIN = 10
POINTS_LOSS = 3                  # participation points for a lost duel
DEFENSE_SHIELD_HOURS = 48        # after a successful defense
REDECLARE_COOLDOWN_HOURS = 72    # same attacker → same mine after a loss
OCCUPANCY_CAP = 1                # mines one sect may hold

DUEL_MAX_TURNS = 50              # PvP snapshot duels (arena convention)
SIEGE_ROUND_LIMIT = 15           # PvE garrison raids (boss convention)
GARRISON_DMG_CAP_PCT = 0.10      # per-attack cap on the garrison pool

WAR_ACTIVE = "active"
WAR_RESOLVED = "resolved"

SIDE_ATTACK = "attack"
SIDE_DEFEND = "defend"

TIER_LABELS = {
    "thap": "Cấp Thấp",
    "trung": "Trung Cấp",
    "cao": "Cao Cấp",
    "cuc_pham": "Cực Phẩm",
}
TIER_ICONS = {
    "thap": "🪨",
    "trung": "⛏️",
    "cao": "💎",
    "cuc_pham": "🌟",
}


def mine_defs() -> dict[str, dict]:
    from src.data.registry import registry
    return registry.sect_mines


def get_mine(mine_key: str) -> dict | None:
    return mine_defs().get(mine_key)


def all_mines() -> list[dict]:
    """Definition order = display order (JSON is tier-grouped)."""
    return list(mine_defs().values())


def funds_per_hour(mine_key: str) -> int:
    d = get_mine(mine_key)
    return int(d.get("funds_per_hour", 0)) if d else 0


# ── Payout accrual ────────────────────────────────────────────────────────────

def payout_hours(last_payout_at: datetime, now: datetime) -> tuple[int, datetime]:
    """Whole hours accrued since ``last_payout_at`` and the advanced marker.

    Advances by exactly ``hours × 1h`` (never to ``now``) so the fractional
    remainder keeps accumulating — the same consumed-seconds trick as
    ``apply_offline_ticks``.
    """
    if last_payout_at is None or now <= last_payout_at:
        return 0, last_payout_at
    hours = int((now - last_payout_at).total_seconds() // 3600)
    if hours <= 0:
        return 0, last_payout_at
    return hours, last_payout_at + timedelta(hours=hours)


# ── Garrison ──────────────────────────────────────────────────────────────────

def garrison_block(mine_def: dict) -> dict:
    return mine_def.get("garrison", {}) or {}


def garrison_hp_max(mine_def: dict) -> int:
    return int(garrison_block(mine_def).get("base_hp", 1_000_000))


def build_garrison_combatant(mine_def: dict, hp_current: int, player_realm_total: int):
    """Garrison Combatant — same construction path as the sect/world boss
    (realm-scaled stats, CC immunity, fixed skill pool)."""
    from src.game.systems.combat import build_world_boss_combatant
    g = garrison_block(mine_def)
    effective = {
        **g,
        "key": f"Garrison_{mine_def.get('key', '?')}",
        "vi": g.get("vi", "Thủ Vệ Khoáng Mạch"),
        "hp_scale": 1.0,
    }
    return build_world_boss_combatant(effective, int(hp_current), player_realm_total)


# ── Duel outcome → points ─────────────────────────────────────────────────────

def duel_points(won: bool) -> int:
    return POINTS_WIN if won else POINTS_LOSS
