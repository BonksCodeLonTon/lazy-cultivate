"""Persistent learned-skill library repository.

Thin DB I/O over ``CharacterLearnedSkill`` — the permanent record of every
*regular* skill a player has ever learned. Separate from ``CharacterSkill``
(the equipped combat bar): forgetting/overwriting a slot deletes the
``CharacterSkill`` row but the library row here survives, so re-equipping an
already-learned skill is free (no scroll).

Functions are module-level and take an ``AsyncSession`` as the first arg
(callers wrap them in ``get_session()``), mirroring ``skill_mastery``'s style.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.skill import CharacterLearnedSkill


async def get_learned_keys(session: AsyncSession, player_id: int) -> set[str]:
    """Return the set of skill keys the player has ever learned."""
    result = await session.execute(
        select(CharacterLearnedSkill.skill_key).where(
            CharacterLearnedSkill.player_id == player_id
        )
    )
    return {row[0] for row in result.all()}


async def is_learned(session: AsyncSession, player_id: int, skill_key: str) -> bool:
    """True if ``skill_key`` is already in the player's library."""
    result = await session.execute(
        select(CharacterLearnedSkill.id).where(
            CharacterLearnedSkill.player_id == player_id,
            CharacterLearnedSkill.skill_key == skill_key,
        )
    )
    return result.scalar_one_or_none() is not None


async def add_learned(session: AsyncSession, player_id: int, skill_key: str) -> None:
    """Record ``skill_key`` in the player's library — IDEMPOTENT.

    Checks for an existing row first so it's safe to call when the skill is
    already learned (a no-op then). Avoids relying on a unique-constraint
    violation, which would poison the surrounding ``get_session()`` transaction.
    """
    if await is_learned(session, player_id, skill_key):
        return
    session.add(CharacterLearnedSkill(player_id=player_id, skill_key=skill_key))
    await session.flush()
