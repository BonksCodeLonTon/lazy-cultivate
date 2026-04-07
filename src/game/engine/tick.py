"""Offline tick processor — computes AFK progress when player reconnects."""
from __future__ import annotations

from datetime import datetime, timezone

from src.game.constants.currencies import (
    CURRENCY_CAP,
    KARMA_ACCUM_CAP,
    KARMA_PER_NORMAL_TURN,
    MERIT_PER_BONUS_TURN,
    MERIT_PER_NORMAL_TURN,
    TURNS_PER_DAY,
    KARMA_TITLE_THRESHOLDS,
)
from src.game.models.character import Character
from src.game.systems.cultivation import advance_cultivation_xp


def compute_offline_ticks(character: Character, last_tick_at: datetime) -> dict:
    """Calculate turns elapsed since *last_tick_at*, apply currency gains and
    advance cultivation XP on the character's *active_axis*.

    Returns a summary dict of all changes applied.
    """
    now = datetime.now(timezone.utc)
    elapsed_minutes = int((now - last_tick_at).total_seconds() / 60)
    turns = min(elapsed_minutes, TURNS_PER_DAY - character.turns_today)

    if turns <= 0:
        return {"turns": 0, "merit_gained": 0, "karma_gained": 0, "cult_result": {}}

    bonus_turns_used = min(turns, character.bonus_turns_remaining)
    normal_turns     = turns - bonus_turns_used

    merit_gained = (
        bonus_turns_used * MERIT_PER_BONUS_TURN
        + normal_turns * MERIT_PER_NORMAL_TURN
    )
    karma_gained = normal_turns * KARMA_PER_NORMAL_TURN

    # Apply currencies first (so formation level-ups can draw from fresh merit)
    new_merit = min(character.merit + merit_gained, CURRENCY_CAP)
    actual_merit = new_merit - character.merit
    character.merit = new_merit
    character.karma_accum  = min(character.karma_accum  + karma_gained, KARMA_ACCUM_CAP)
    character.karma_usable = min(character.karma_usable + karma_gained, CURRENCY_CAP)
    character.turns_today += turns
    character.bonus_turns_remaining = max(0, character.bonus_turns_remaining - bonus_turns_used)

    evil_title = _compute_evil_title(character.karma_accum)
    if evil_title:
        character.evil_title = evil_title

    # Advance cultivation XP on the active axis
    cult_result = advance_cultivation_xp(character, turns)

    return {
        "turns":        turns,
        "merit_gained": actual_merit,
        "karma_gained": karma_gained,
        "evil_title":   evil_title,
        "cult_result":  cult_result,
    }


def _compute_evil_title(karma_accum: int) -> str | None:
    result = None
    for threshold, title_key in sorted(KARMA_TITLE_THRESHOLDS.items()):
        if karma_accum >= threshold:
            result = title_key
    return result
