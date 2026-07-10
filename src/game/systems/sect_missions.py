"""Tông Môn daily missions — definitions from the registry, per-member
progress in the ``sect_members.mission_progress`` JSON column.

Progress shape: ``{"date": "2026-07-08", "counts": {key: n}, "claimed": [key]}``
— lazily reset when ``date`` differs from today's UTC date (same rollover
convention as tick.py / donation windows).

``record_event`` is called from exactly four gameplay sites (dungeon clear,
world-boss attack, alchemy craft, arena duel). It MUST never break the
calling reward flow: sect-less players are a single indexed SELECT miss, and
any unexpected error is swallowed with a log line. Claiming is the atomic
part and lives in ``SectRepository.claim_missions_atomic`` (this module must
not import the repo — the repo imports us).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select, update

log = logging.getLogger(__name__)

# Event keys — the only values ``record_event`` accepts (must match
# ``sect_missions.json`` mission keys).
EVENT_DUNGEON_CLEAR = "dungeon_clear"
EVENT_WORLD_BOSS_ATTACK = "world_boss_attack"
EVENT_ALCHEMY_CRAFT = "alchemy_craft"
EVENT_ARENA_DUEL = "arena_duel"

MISSIONS_MIN_SECT_LEVEL = 2      # sect_levels.json L2 unlock ("missions")
_COUNT_CAP = 9_999               # keeps the JSON column tiny


def mission_defs() -> list[dict]:
    from src.data.registry import registry
    return registry.sect_missions


def mission_def(key: str) -> dict | None:
    for d in mission_defs():
        if d.get("key") == key:
            return d
    return None


# ── Progress state (pure) ─────────────────────────────────────────────────────

def parse_progress(raw: str | None, today: date) -> dict:
    """Decode the JSON column; a stale date (or garbage) resets to a fresh day."""
    try:
        state = json.loads(raw or "{}")
    except (ValueError, TypeError):
        state = {}
    if not isinstance(state, dict) or state.get("date") != today.isoformat():
        return {"date": today.isoformat(), "counts": {}, "claimed": []}
    counts = state.get("counts")
    claimed = state.get("claimed")
    return {
        "date": today.isoformat(),
        "counts": dict(counts) if isinstance(counts, dict) else {},
        "claimed": list(claimed) if isinstance(claimed, list) else [],
    }


def encode_progress(state: dict) -> str:
    return json.dumps(state, separators=(",", ":"), ensure_ascii=False)


def increment(state: dict, event_key: str, amount: int = 1) -> dict:
    """Return a new state with ``event_key`` bumped (immutably)."""
    counts = dict(state.get("counts", {}))
    counts[event_key] = min(_COUNT_CAP, int(counts.get(event_key, 0)) + max(0, int(amount)))
    return {**state, "counts": counts}


def progress_of(state: dict, mdef: dict) -> tuple[int, int]:
    """(current, target) for a mission — current clamps at target for display."""
    target = max(1, int(mdef.get("target", 1)))
    current = min(target, int(state.get("counts", {}).get(mdef.get("key"), 0)))
    return current, target


def is_claimed(state: dict, key: str) -> bool:
    return key in state.get("claimed", [])


def claimable_keys(state: dict) -> list[str]:
    """Missions that are complete and not yet claimed, in definition order."""
    out: list[str] = []
    for mdef in mission_defs():
        key = str(mdef.get("key"))
        cur, target = progress_of(state, mdef)
        if cur >= target and not is_claimed(state, key):
            out.append(key)
    return out


# ── Event recording (repo-coupled, failure-proof) ─────────────────────────────

async def record_event(session, player_id: int, event_key: str, amount: int = 1) -> None:
    """Bump a mission counter for ``player_id`` inside the caller's session.

    No-op when the player is sect-less or the key is unknown. Progress is
    recorded regardless of sect level — the CLAIM path gates on
    ``MISSIONS_MIN_SECT_LEVEL``. Swallows every exception: a mission-
    bookkeeping failure must never fail a dungeon/boss/craft/duel reward
    flow (mirrors the skill-mastery award guard).
    """
    if amount <= 0 or mission_def(event_key) is None:
        return
    try:
        from src.db.models.sect import SectMember

        row = (
            await session.execute(
                select(SectMember.id, SectMember.mission_progress).where(
                    SectMember.player_id == player_id
                )
            )
        ).first()
        if row is None:
            return

        member_id, raw = row
        today = datetime.now(timezone.utc).date()
        state = increment(parse_progress(raw, today), event_key, amount)
        await session.execute(
            update(SectMember)
            .where(SectMember.id == member_id)
            .values(mission_progress=encode_progress(state))
            .execution_options(synchronize_session=False)
        )
    except Exception as e:  # noqa: BLE001 — deliberately failure-proof
        log.warning("sect_missions.record_event(%s, %s) failed: %s", player_id, event_key, e)
