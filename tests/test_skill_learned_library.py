"""Tests for the persistent learned-skill library.

Infra approach: real in-memory async SQLite (``aiosqlite`` is installed),
mirroring ``tests/test_skill_mastery_repo.py``. A function-scoped async engine
creates ONLY the tables the repo/migration touch — ``players``,
``character_skills``, and ``character_learned_skills`` — via
``Base.metadata.create_all(tables=[...])``. WHY selective: the full schema pulls
in Postgres-leaning tables we don't need; the three under test use only
SQLite-compatible column types (Integer/SmallInteger/BigInteger/String/Boolean),
so they create and exercise cleanly on SQLite.

We exercise the real ``skill_learned_repo`` end-to-end (no session mocking) plus
simulate the cog's forget/overwrite slot manipulation and the migration backfill
SQL at the DB layer, since the live Discord cog callbacks can't be unit-tested.

The pure helper ``_learn_status`` is tested in isolation by building ``state``
dicts by hand — no DB, no registry.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the connection module registers every ORM model on Base.metadata.
import src.db.connection  # noqa: F401
from src.bot.cogs.skills import _learn_status
from src.db.models.base import Base
from src.db.models.player import Player
from src.db.models.skill import CharacterLearnedSkill, CharacterSkill
from src.db.repositories.skill_learned_repo import (
    add_learned,
    get_learned_keys,
    is_learned,
)


@pytest_asyncio.fixture
async def session():
    """Function-scoped in-memory async SQLite session with the 3 needed tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Player.__table__,
        CharacterSkill.__table__,
        CharacterLearnedSkill.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _new_player(session, discord_id: int = 1) -> int:
    player = Player(discord_id=discord_id, name=f"p{discord_id}")
    session.add(player)
    await session.flush()
    return player.id


async def _add_slot(session, player_id: int, skill_key: str, slot_index: int) -> None:
    session.add(
        CharacterSkill(
            player_id=player_id, skill_key=skill_key, slot_index=slot_index
        )
    )
    await session.flush()


async def _slot_row(session, player_id: int, slot_index: int):
    return (
        await session.execute(
            select(CharacterSkill).where(
                CharacterSkill.player_id == player_id,
                CharacterSkill.slot_index == slot_index,
            )
        )
    ).scalar_one_or_none()


async def _learned_count(session, player_id: int, skill_key: str) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(CharacterLearnedSkill)
            .where(
                CharacterLearnedSkill.player_id == player_id,
                CharacterLearnedSkill.skill_key == skill_key,
            )
        )
    ).scalar_one()


# ── Repo: skill_learned_repo ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_learned_then_is_learned(session):
    player_id = await _new_player(session)

    await add_learned(session, player_id, "S")

    assert await is_learned(session, player_id, "S") is True
    # A key never recorded is not learned.
    assert await is_learned(session, player_id, "OTHER") is False


@pytest.mark.asyncio
async def test_add_learned_is_idempotent(session):
    player_id = await _new_player(session)

    await add_learned(session, player_id, "S")
    # Second call must NOT raise (no unique-constraint poisoning).
    await add_learned(session, player_id, "S")

    # Exactly one row survives: in the key set once, and the count == 1.
    keys = await get_learned_keys(session, player_id)
    assert keys == {"S"}
    assert await _learned_count(session, player_id, "S") == 1


@pytest.mark.asyncio
async def test_get_learned_keys_roundtrip_and_player_isolation(session):
    a_id = await _new_player(session, discord_id=1)
    b_id = await _new_player(session, discord_id=2)

    for key in ("A1", "A2", "A3"):
        await add_learned(session, a_id, key)
    await add_learned(session, b_id, "B1")

    # A sees exactly its own set; B's row does not leak in.
    assert await get_learned_keys(session, a_id) == {"A1", "A2", "A3"}
    # B's library is isolated to its own single key.
    assert await get_learned_keys(session, b_id) == {"B1"}


# ── "Never lose a skill" data-level guarantee ────────────────────────────────


@pytest.mark.asyncio
async def test_forget_preserves_library_row(session):
    """Forget = library-record then drop the slot row → still 'learned'."""
    player_id = await _new_player(session)
    # Seed a regular slot occupant + its library row (the learned state).
    await _add_slot(session, player_id, "S", slot_index=2)
    await add_learned(session, player_id, "S")

    # Simulate the cog forget sequence: record (idempotent), then delete slot.
    await add_learned(session, player_id, "S")
    slot = await _slot_row(session, player_id, slot_index=2)
    await session.delete(slot)
    await session.flush()

    # Combat bar lost the skill...
    assert await _slot_row(session, player_id, slot_index=2) is None
    # ...but the library still has it — re-equip stays free.
    assert await is_learned(session, player_id, "S") is True


@pytest.mark.asyncio
async def test_overwrite_preserves_displaced_occupant(session):
    """Overwriting a slot must not erase the displaced skill from the library."""
    player_id = await _new_player(session)
    await _add_slot(session, player_id, "OLD", slot_index=1)
    await add_learned(session, player_id, "OLD")

    # Simulate overwrite: record displaced occupant, delete its slot row,
    # then install the new skill at the same slot.
    await add_learned(session, player_id, "OLD")
    old_slot = await _slot_row(session, player_id, slot_index=1)
    await session.delete(old_slot)
    await session.flush()
    await _add_slot(session, player_id, "NEW", slot_index=1)

    # Displaced skill survives in the library.
    assert await is_learned(session, player_id, "OLD") is True
    # Slot now holds the new skill.
    new_slot = await _slot_row(session, player_id, slot_index=1)
    assert new_slot is not None
    assert new_slot.skill_key == "NEW"


# ── Migration backfill semantics ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_only_regular_slots(session):
    """Backfill copies only regular slots (< 6), not formation markers."""
    player_id = await _new_player(session)
    # Regular skill in a combat slot...
    await _add_slot(session, player_id, "REGULAR", slot_index=0)
    # ...and a formation marker stored at a higher pseudo-slot.
    await _add_slot(session, player_id, "FORMATION_MARK", slot_index=7)

    await session.execute(
        text(
            "INSERT INTO character_learned_skills (player_id, skill_key) "
            "SELECT DISTINCT player_id, skill_key FROM character_skills "
            "WHERE slot_index < 6"
        )
    )
    await session.flush()

    keys = await get_learned_keys(session, player_id)
    assert keys == {"REGULAR"}
    assert "FORMATION_MARK" not in keys


# ── Pure helper: _learn_status ───────────────────────────────────────────────


def _state(*, equipped=None, learned=None, scrolls=None, linh_can=None) -> dict:
    return {
        "equipped": set(equipped or ()),
        "learned": set(learned or ()),
        "owned_scrolls": set(scrolls or ()),
        "linh_can": list(linh_can or ()),
    }


@pytest.mark.asyncio
async def test_learn_status_precedence():
    regular = {"key": "S", "element": "hoa", "category": "attack"}

    # Equipped (regular) → ✓ (highest precedence).
    assert _learn_status(regular, _state(equipped={"S"}, learned={"S"})) == "✓"

    # Learned only (regular) → 📥 (re-equip free).
    assert _learn_status(regular, _state(learned={"S"})) == "📥"

    # Not learned + scroll owned + element fits Linh Căn → ✨ (ready).
    ready = _state(scrolls={"S"}, linh_can=["hoa", "kim"])
    assert _learn_status(regular, ready) == "✨"

    # Not learned + scroll owned + element NOT in Linh Căn → 📜 (gated).
    gated = _state(scrolls={"S"}, linh_can=["kim"])
    assert _learn_status(regular, gated) == "📜"

    # Not learned + no scroll → "" (nothing to show).
    assert _learn_status(regular, _state()) == ""

    # Formation skill that's in learned → ✓ (NOT 📥).
    formation = {"key": "F", "element": "hoa", "category": "formation"}
    assert _learn_status(formation, _state(learned={"F"})) == "✓"
