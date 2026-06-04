"""Phase 4 (XP earn) tests for the Constitution Process combat-XP award path.

Exercises the extracted ``award_constitution_xp`` helper — the flag-gated,
primary-body-only accrual the dungeon / world-boss cogs call post-combat —
against the same in-memory async SQLite harness used by
``test_constitution_process_repo.py``. The cog wiring itself (which session is
reused, which grade is chosen) is deliberately NOT booted here; we test the
pure-ish helper directly so the EARN/persist contract is verifiable without a
Discord harness.

The pure ``combat_xp_gain`` table and ``apply_combat_xp`` roll-up are covered in
``test_constitution_process.py``; this file focuses on the integration the
helper layers on top: flag gate, primary-body selection, ceiling stall, and the
trash-grade no-op.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the connection module registers every ORM model on Base.metadata.
import src.db.connection  # noqa: F401
from src.db.models.base import Base
from src.db.models.constitution_process import CharacterConstitutionProgress
from src.db.models.player import Player
from src.db.repositories import constitution_process as repo
from src.game.systems.the_chat import set_constitutions
from src.utils.config import settings


@pytest_asyncio.fixture
async def session():
    """Function-scoped in-memory async SQLite session with the 2 needed tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Player.__table__,
        CharacterConstitutionProgress.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def feature_on(monkeypatch):
    """Flip the (default-OFF) feature flag ON for the duration of one test."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


async def _new_player(session, equipped: list[str], discord_id: int = 1) -> Player:
    player = Player(
        discord_id=discord_id,
        name=f"p{discord_id}",
        constitution_type=set_constitutions(equipped),
    )
    session.add(player)
    await session.flush()
    return player


async def _row(session, player_id: int, constitution_key: str):
    return (
        await session.execute(
            select(CharacterConstitutionProgress).where(
                CharacterConstitutionProgress.player_id == player_id,
                CharacterConstitutionProgress.constitution_key == constitution_key,
            )
        )
    ).scalar_one_or_none()


# ── 1. Flag OFF → no award (helper is a no-op) ──────────────────────────────


@pytest.mark.asyncio
async def test_flag_off_is_no_op(session):
    """With the feature flag OFF (default), the helper writes nothing."""
    assert settings.constitution_process_enabled is False
    player = await _new_player(session, ["ConstitutionKim"])

    result = await repo.award_constitution_xp(session, player, "boss", won=True)
    assert result is None
    assert await repo.get_all(session, player.id) == []


# ── 2. No equipped body → nothing awarded ───────────────────────────────────


@pytest.mark.asyncio
async def test_no_equipped_body_is_no_op(session, feature_on):
    """Phàm Thể / empty constitution_type earns nothing even with the flag ON."""
    player = await _new_player(session, [])  # no equipped constitution

    result = await repo.award_constitution_xp(session, player, "boss", won=True)
    assert result is None
    assert await repo.get_all(session, player.id) == []


# ── 3. Award advances the primary body's row ────────────────────────────────


@pytest.mark.asyncio
async def test_award_advances_primary_row(session, feature_on):
    """A graded win rolls XP onto a fresh primary body, creating its row."""
    player = await _new_player(session, ["ConstitutionKim"])

    # normal win = (1 + 1) × 1.0 = 2 XP; L1 needs 8 → stays L1 with 2 XP.
    row = await repo.award_constitution_xp(session, player, "normal", won=True)
    assert row is not None
    assert (row.level, row.xp) == (1, 2)

    persisted = await _row(session, player.id, "ConstitutionKim")
    assert (persisted.level, persisted.xp) == (1, 2)


# ── 4. Primary-body selection: only slot 0 advances ─────────────────────────


@pytest.mark.asyncio
async def test_only_primary_body_advances(session, feature_on):
    """With two equipped bodies, only slot 0's row earns XP; slot 1 is untouched."""
    player = await _new_player(session, ["ConstitutionKim", "ConstitutionMoc"])

    await repo.award_constitution_xp(session, player, "elite", won=True)  # 4 XP

    primary = await _row(session, player.id, "ConstitutionKim")
    secondary = await _row(session, player.id, "ConstitutionMoc")
    assert primary is not None
    assert (primary.level, primary.xp) == (1, 4)
    # Slot 1 never gets a row — only the primary earns under the single-active
    # body model.
    assert secondary is None


# ── 5. Trash grade → 0 XP → no row change / no-op ───────────────────────────


@pytest.mark.asyncio
async def test_trash_grade_is_no_op(session, feature_on):
    """A ``trash`` grade scales to 0 XP — no row is created, none advances."""
    player = await _new_player(session, ["ConstitutionKim"])

    # Fresh body: trash earns 0 → no row created.
    result = await repo.award_constitution_xp(session, player, "trash", won=True)
    assert result is None
    assert await _row(session, player.id, "ConstitutionKim") is None

    # Existing body: seed a row, then a trash award must leave it byte-identical.
    seeded = await repo.get_or_create(session, player.id, "ConstitutionKim")
    seeded.level, seeded.xp = 1, 5
    await session.flush()

    result = await repo.award_constitution_xp(session, player, "trash", won=False)
    assert result is None
    after = await _row(session, player.id, "ConstitutionKim")
    assert (after.level, after.xp) == (1, 5)


# ── 6. XP rolls up but STALLS at the first ceiling (level 2) ─────────────────


@pytest.mark.asyncio
async def test_xp_stalls_at_first_ceiling(session, feature_on):
    """Repeated awards roll L1 → L2 then stop — XP can't cross the gate."""
    player = await _new_player(session, ["ConstitutionKim"])

    # Hammer the body with world_boss wins (8 XP each). L1 needs only 8 XP to
    # reach the ceiling at L2, where rollover halts (gate-locked) and overshoot
    # is discarded.
    last = None
    for _ in range(5):
        last = await repo.award_constitution_xp(
            session, player, "world_boss", won=True
        )

    assert last is not None
    # Ceiling 2 reached; XP discarded to 0 — can't advance to 3 without a
    # breakthrough (Phase 5).
    assert (last.level, last.xp) == (2, 0)

    row = await _row(session, player.id, "ConstitutionKim")
    assert (row.level, row.xp) == (2, 0)

    # Further awards while stuck at the ceiling stay pinned at (2, 0).
    again = await repo.award_constitution_xp(session, player, "world_boss", won=True)
    assert (again.level, again.xp) == (2, 0)
