"""Phase 2 validation tests for the Constitution Process persistence repo.

Infra approach mirrors ``test_skill_mastery_repo.py``: a real in-memory async
SQLite engine (``aiosqlite``), creating ONLY the two tables the repo touches
(``players``, ``character_constitution_progress``) via
``Base.metadata.create_all(tables=[...])``. The repo only does row I/O — no
inventory, no RNG — so a slim two-table schema exercises it end-to-end.

The pure core (``apply_combat_xp`` / ``resolve_breakthrough``) is covered by its
own unit tests; here we assert the persistence *decisions* the repo layers on
top of it, plus the swap-retains-progress invariant (two constitution_keys for
one player coexist independently).
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
from src.game.systems.constitution_process import apply_combat_xp, resolve_breakthrough


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


async def _new_player(session, discord_id: int = 1) -> int:
    player = Player(discord_id=discord_id, name=f"p{discord_id}")
    session.add(player)
    await session.flush()
    return player.id


async def _row(session, player_id: int, constitution_key: str):
    return (
        await session.execute(
            select(CharacterConstitutionProgress).where(
                CharacterConstitutionProgress.player_id == player_id,
                CharacterConstitutionProgress.constitution_key == constitution_key,
            )
        )
    ).scalar_one_or_none()


# ── 1. create and read back ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_returns_level_one_then_reads_back(session):
    player_id = await _new_player(session)

    created = await repo.get_or_create(session, player_id, "ConstitutionKim")
    assert created.level == 1
    assert created.xp == 0
    assert created.gate_fails == 0

    fetched = await repo.get_progress(session, player_id, "ConstitutionKim")
    assert fetched is not None
    assert fetched.id == created.id
    assert (fetched.level, fetched.xp, fetched.gate_fails) == (1, 0, 0)


@pytest.mark.asyncio
async def test_get_progress_absent_returns_none(session):
    player_id = await _new_player(session)
    assert await repo.get_progress(session, player_id, "ConstitutionMissing") is None


# ── 2. get_or_create idempotence ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_idempotent_no_duplicate_row(session):
    player_id = await _new_player(session)

    first = await repo.get_or_create(session, player_id, "ConstitutionKim")
    second = await repo.get_or_create(session, player_id, "ConstitutionKim")
    assert second.id == first.id

    rows = (
        await session.execute(
            select(CharacterConstitutionProgress).where(
                CharacterConstitutionProgress.player_id == player_id,
                CharacterConstitutionProgress.constitution_key == "ConstitutionKim",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


# ── 3. uniqueness per (player, key) is enforced by the DB constraint ────────


@pytest.mark.asyncio
async def test_unique_constraint_rejects_duplicate_player_key(session):
    from sqlalchemy.exc import IntegrityError

    player_id = await _new_player(session)
    await repo.get_or_create(session, player_id, "ConstitutionKim")

    # Bypass the repo's get-or-create guard to prove the DB constraint holds.
    session.add(
        CharacterConstitutionProgress(
            player_id=player_id, constitution_key="ConstitutionKim", level=1, xp=0
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


# ── 4. get_all for a player ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_all_returns_only_this_players_rows(session):
    player_id = await _new_player(session)
    await repo.get_or_create(session, player_id, "ConstitutionKim")
    await repo.get_or_create(session, player_id, "ConstitutionMoc")

    # A second player's row must not leak into the first player's list.
    other_id = await _new_player(session, discord_id=2)
    await repo.get_or_create(session, other_id, "ConstitutionThuy")

    rows = await repo.get_all(session, player_id)
    keys = {r.constitution_key for r in rows}
    assert keys == {"ConstitutionKim", "ConstitutionMoc"}


@pytest.mark.asyncio
async def test_get_all_empty_for_player_without_progress(session):
    player_id = await _new_player(session)
    assert await repo.get_all(session, player_id) == []


# ── 5. XP-result persistence ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_combat_xp_result_writes_level_and_xp(session):
    player_id = await _new_player(session)

    # Pure core computes the transition; the repo only persists it.
    xp_result = apply_combat_xp(1, 0, 10)
    new_level, new_xp, _at_ceiling = xp_result

    returned = await repo.persist_combat_xp_result(
        session, player_id, "ConstitutionKim", xp_result
    )
    assert (returned.level, returned.xp) == (new_level, new_xp)

    row = await _row(session, player_id, "ConstitutionKim")
    assert (row.level, row.xp) == (new_level, new_xp)


@pytest.mark.asyncio
async def test_persist_combat_xp_result_updates_existing_row(session):
    player_id = await _new_player(session)
    await repo.get_or_create(session, player_id, "ConstitutionKim")

    first = apply_combat_xp(1, 0, 3)
    await repo.persist_combat_xp_result(session, player_id, "ConstitutionKim", first)

    # Feed the previous state forward — same row mutates, no duplicate created.
    second = apply_combat_xp(first[0], first[1], 5)
    await repo.persist_combat_xp_result(session, player_id, "ConstitutionKim", second)

    rows = await repo.get_all(session, player_id)
    assert len(rows) == 1
    assert (rows[0].level, rows[0].xp) == (second[0], second[1])


# ── 6. breakthrough-result persistence ──────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_breakthrough_success_advances_level_resets_fails(session):
    player_id = await _new_player(session)
    prog = await repo.get_or_create(session, player_id, "ConstitutionKim")
    prog.level = 2
    prog.gate_fails = 3
    await session.flush()

    # roll below the base success at gate 2 → success, level 2 -> 3, fails reset.
    result = resolve_breakthrough(2, 3, ho_the_phu=False, dinh_the_chau=False, roll=0.0)
    assert result["success"] is True

    await repo.persist_breakthrough_result(
        session, player_id, "ConstitutionKim", result
    )
    row = await _row(session, player_id, "ConstitutionKim")
    assert row.level == 3
    assert row.gate_fails == 0


@pytest.mark.asyncio
async def test_persist_breakthrough_fail_holds_level_increments_fails(session):
    player_id = await _new_player(session)
    prog = await repo.get_or_create(session, player_id, "ConstitutionKim")
    prog.level = 5
    prog.gate_fails = 1
    await session.flush()

    # roll above the success chance at gate 5 → fail: level held, fails +1.
    result = resolve_breakthrough(5, 1, ho_the_phu=False, dinh_the_chau=False, roll=0.999)
    assert result["success"] is False

    await repo.persist_breakthrough_result(
        session, player_id, "ConstitutionKim", result
    )
    row = await _row(session, player_id, "ConstitutionKim")
    assert row.level == 5
    assert row.gate_fails == 2


# ── 7. swap-retains-progress invariant ──────────────────────────────────────


@pytest.mark.asyncio
async def test_two_constitution_keys_coexist_independently(session):
    """Two bodies' progress is stored separately so a body-swap keeps each level.

    Advancing one constitution must NOT touch the other's row — this is the
    persistence guarantee that lets a player swap away and back later and resume
    the old body's progression.
    """
    player_id = await _new_player(session)

    # Body A reaches level 3 via a breakthrough; body B sits at L1 with some XP.
    a = await repo.get_or_create(session, player_id, "ConstitutionKim")
    a.level = 2
    await session.flush()
    a_break = resolve_breakthrough(2, 0, ho_the_phu=False, dinh_the_chau=False, roll=0.0)
    await repo.persist_breakthrough_result(session, player_id, "ConstitutionKim", a_break)

    b_xp = apply_combat_xp(1, 0, 4)
    await repo.persist_combat_xp_result(session, player_id, "ConstitutionMoc", b_xp)

    # Each row is independent and intact.
    a_row = await _row(session, player_id, "ConstitutionKim")
    b_row = await _row(session, player_id, "ConstitutionMoc")
    assert a_row.level == 3
    assert (b_row.level, b_row.xp) == (b_xp[0], b_xp[1])

    # Mutating B again leaves A untouched — proves no cross-key bleed.
    b_more = apply_combat_xp(b_row.level, b_row.xp, 3)
    await repo.persist_combat_xp_result(session, player_id, "ConstitutionMoc", b_more)
    a_row_after = await _row(session, player_id, "ConstitutionKim")
    assert a_row_after.level == 3
