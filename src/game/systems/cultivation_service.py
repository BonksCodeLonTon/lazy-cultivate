"""Cultivation service — repo-coupled orchestration helpers.

The pure cultivation rules live in ``src.game.systems.cultivation`` (no DB).
This module sits one layer up: takes a repository handle and persists the
results of a tick or breakthrough back to the player ORM. Used by every cog
that needs to settle pending offline progress before reading the player's
state (combat, dungeon, status, the cultivation cog itself).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db.repositories.player_repo import _player_to_model
from src.game.constants.currencies import SECONDS_PER_TURN
from src.game.engine.tick import compute_offline_ticks


def pre_breakthrough_realm(player, axis: str) -> int:
    """Current realm index for the given axis on a player ORM object."""
    if axis == "body":
        return player.body_realm
    if axis == "qi":
        return player.qi_realm
    return player.formation_realm


async def apply_offline_ticks(player, repo, axis: str) -> dict:
    """Materialize accumulated offline progress on ``player`` for ``axis``.

    Updates ``active_axis`` to ``axis``, runs ``compute_offline_ticks``
    against the player's last-tick timestamp, then writes the resulting
    XP / merit / karma / level changes back to the ORM and advances
    ``last_tick_at`` by exactly the consumed seconds. Returns the raw
    tick-result dict so callers can render summary embeds. Returns ``{}``
    when there's no tracker yet or no time has elapsed since the last tick.
    """
    player.active_axis = axis
    tracker = player.turn_tracker
    result: dict = {}

    if not tracker:
        await repo.save(player)
        return result

    now = datetime.now(timezone.utc)

    if not tracker.last_tick_at:
        tracker.last_tick_at = now
        await repo.save(player)
        return result

    char = _player_to_model(player)
    result = compute_offline_ticks(char, tracker.last_tick_at)

    player.merit        = char.merit
    player.karma_accum  = char.karma_accum
    player.karma_usable = char.karma_usable
    if char.evil_title:
        player.evil_title = char.evil_title

    player.body_xp      = char.body_xp
    player.qi_xp        = char.qi_xp
    player.formation_xp = char.formation_xp

    player.body_level      = char.body_level
    player.qi_level        = char.qi_level
    player.formation_level = char.formation_level

    tracker.turns_today           = char.turns_today
    tracker.bonus_turns_remaining = char.bonus_turns_remaining

    elapsed_seconds = (now - tracker.last_tick_at).total_seconds()
    consumed_seconds = int(elapsed_seconds // SECONDS_PER_TURN) * SECONDS_PER_TURN
    tracker.last_tick_at = tracker.last_tick_at + timedelta(seconds=consumed_seconds)

    await repo.save(player)
    return result
