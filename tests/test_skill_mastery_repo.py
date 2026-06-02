"""Phase 2 validation tests for the Skill Mastery persistence repo.

Infra approach: real in-memory async SQLite (``aiosqlite`` is installed). We
build a function-scoped async engine and create ONLY the three tables the repo
touches (``players``, ``inventory``, ``character_skill_mastery``) via
``Base.metadata.create_all(tables=[...])``. WHY selective: the full schema pulls
in Postgres-leaning tables we don't need here; the three under test use only
SQLite-compatible column types (Integer/SmallInteger/BigInteger/String/Boolean),
so they create and exercise cleanly on SQLite.

This tests the real orchestration end-to-end: the repo loads/writes
``CharacterSkillMastery`` rows and spends real ``InventoryItem`` rows through a
real ``InventoryRepository`` — no mocking of the session or inventory layer.

The pure core (``apply_combat_xp`` / ``resolve_breakthrough``) is already
covered by its own unit tests; here we assert the persistence + inventory
*decisions* the repo layers on top of it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the connection module registers every ORM model on Base.metadata.
import src.db.connection  # noqa: F401
from src.db.models.base import Base
from src.db.models.inventory import InventoryItem
from src.db.models.player import Player
from src.db.models.skill_mastery import CharacterSkillMastery
from src.db.repositories import skill_mastery as repo
from src.game.constants.grades import Grade
from src.game.constants.skill_mastery import (
    DINH_DAO_CHAU_KEY,
    GATES,
    HO_DAO_PHU_KEY,
)
from src.game.systems.skill_mastery import apply_combat_xp

# Gate item keys for the bands exercised below.
GATE5 = GATES[5]["item_key"]   # MasteryLinhNgoPhu, qty 4
GATE5_QTY = GATES[5]["qty"]
GATE10 = GATES[10]["item_key"]  # MasteryTamDacNgoc, qty 6
GATE10_QTY = GATES[10]["qty"]
GATE20 = GATES[20]["item_key"]  # MasteryThongThienDaoQua, qty 1
GATE20_QTY = GATES[20]["qty"]

# Inventory rows store an int grade (1=Hoàng…). The repo counts/spends across
# any grade, so a single Hoàng-grade row per item is enough for these tests.
_HOANG = Grade.HOANG.value


@pytest_asyncio.fixture
async def session():
    """Function-scoped in-memory async SQLite session with the 3 needed tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Player.__table__,
        InventoryItem.__table__,
        CharacterSkillMastery.__table__,
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


async def _give(session, player_id: int, item_key: str, qty: int) -> None:
    session.add(
        InventoryItem(
            player_id=player_id, item_key=item_key, grade=_HOANG, quantity=qty
        )
    )
    await session.flush()


async def _qty_of(session, player_id: int, item_key: str) -> int:
    rows = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.player_id == player_id,
                InventoryItem.item_key == item_key,
            )
        )
    ).scalars().all()
    return sum(r.quantity for r in rows)


async def _mastery_row(session, player_id: int, skill_key: str):
    return (
        await session.execute(
            select(CharacterSkillMastery).where(
                CharacterSkillMastery.player_id == player_id,
                CharacterSkillMastery.skill_key == skill_key,
            )
        )
    ).scalar_one_or_none()


async def _set_level(session, player_id: int, skill_key: str, level: int):
    """Create the mastery row (if absent) and pin its level for gate tests."""
    mastery = await repo.get_or_create(session, player_id, skill_key)
    mastery.level = level
    await session.flush()
    return mastery


# ── 1. get_or_create idempotency ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_returns_level_one_then_same_row(session):
    player_id = await _new_player(session)

    first = await repo.get_or_create(session, player_id, "S")
    assert first.level == 1
    assert first.xp == 0
    assert first.gate_fails == 0
    assert first.hidden_unlocked is False

    second = await repo.get_or_create(session, player_id, "S")
    # Same identity — no duplicate row created.
    assert second.id == first.id

    rows = (
        await session.execute(
            select(CharacterSkillMastery).where(
                CharacterSkillMastery.player_id == player_id,
                CharacterSkillMastery.skill_key == "S",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


# ── 2. add_combat_xp victory bonus ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_combat_xp_victory_bonus_applied(session):
    player_id = await _new_player(session)

    results = await repo.add_combat_xp(session, player_id, {"S": 10}, victory=True)

    # gained = 10 casts + 5 victory = 15
    exp_level, exp_xp, exp_ceiling = apply_combat_xp(1, 0, 15)
    level_before, xp_before, level_after, xp_after, at_ceiling = results["S"]
    assert (level_before, xp_before) == (1, 0)
    assert (level_after, xp_after, at_ceiling) == (exp_level, exp_xp, exp_ceiling)

    row = await _mastery_row(session, player_id, "S")
    assert (row.level, row.xp) == (exp_level, exp_xp)


@pytest.mark.asyncio
async def test_add_combat_xp_no_victory_bonus(session):
    player_id = await _new_player(session)

    results = await repo.add_combat_xp(session, player_id, {"S": 10}, victory=False)

    # gained = 10 casts + 0 = 10
    exp_level, exp_xp, exp_ceiling = apply_combat_xp(1, 0, 10)
    _, _, level_after, xp_after, at_ceiling = results["S"]
    assert (level_after, xp_after, at_ceiling) == (exp_level, exp_xp, exp_ceiling)

    row = await _mastery_row(session, player_id, "S")
    assert (row.level, row.xp) == (exp_level, exp_xp)


# ── 3. add_combat_xp skips 0-cast skills, handles multiple ──────────────────


@pytest.mark.asyncio
async def test_add_combat_xp_skips_zero_and_handles_multiple(session):
    player_id = await _new_player(session)

    results = await repo.add_combat_xp(
        session, player_id, {"A": 3, "B": 0, "C": 7}, victory=False
    )

    # 0-cast skill is neither in the result map nor persisted.
    assert "B" not in results
    assert (await _mastery_row(session, player_id, "B")) is None

    # The two cast skills are persisted independently.
    assert "A" in results and "C" in results
    a_row = await _mastery_row(session, player_id, "A")
    c_row = await _mastery_row(session, player_id, "C")
    exp_a = apply_combat_xp(1, 0, 3)
    exp_c = apply_combat_xp(1, 0, 7)
    assert (a_row.level, a_row.xp) == (exp_a[0], exp_a[1])
    assert (c_row.level, c_row.xp) == (exp_c[0], exp_c[1])


# ── 4. attempt_breakthrough not at a gate ───────────────────────────────────


@pytest.mark.asyncio
async def test_attempt_breakthrough_not_at_gate_no_mutation(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "S", 7)  # mid-band, not a ceiling
    await _give(session, player_id, GATE10, GATE10_QTY)  # plenty of items

    result = await repo.attempt_breakthrough(session, player_id, "S")

    assert result["ok"] is False
    assert result["reason"] == "not_at_gate"
    assert result["level"] == 7

    # Nothing changed: level pinned, no items spent.
    row = await _mastery_row(session, player_id, "S")
    assert row.level == 7
    assert row.gate_fails == 0
    assert await _qty_of(session, player_id, GATE10) == GATE10_QTY


# ── 5. attempt_breakthrough insufficient items consumes nothing ─────────────


@pytest.mark.asyncio
async def test_attempt_breakthrough_insufficient_items_consumes_nothing(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "S", 5)  # gate ceiling
    # Hold fewer than the required gate qty (need 4, give 3).
    await _give(session, player_id, GATE5, GATE5_QTY - 1)

    result = await repo.attempt_breakthrough(
        session, player_id, "S", roll=0.0
    )

    assert result["ok"] is False
    assert result["reason"] == "insufficient_items"
    assert GATE5 in result["missing"]

    # Inventory untouched, level unchanged.
    assert await _qty_of(session, player_id, GATE5) == GATE5_QTY - 1
    row = await _mastery_row(session, player_id, "S")
    assert row.level == 5
    assert row.gate_fails == 0


# ── 6. attempt_breakthrough success path ────────────────────────────────────


@pytest.mark.asyncio
async def test_attempt_breakthrough_success(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "S", 5)
    # Surplus stock to prove exactly the gate qty is consumed.
    await _give(session, player_id, GATE5, GATE5_QTY + 2)

    result = await repo.attempt_breakthrough(session, player_id, "S", roll=0.0)

    assert result["ok"] is True
    assert result["success"] is True
    assert result["new_level"] == 6

    row = await _mastery_row(session, player_id, "S")
    assert row.level == 6
    assert row.gate_fails == 0
    # Exactly GATES[5]["qty"] (=4) consumed; the +2 surplus remains.
    assert await _qty_of(session, player_id, GATE5) == 2


# ── 7. attempt_breakthrough fail path ───────────────────────────────────────


@pytest.mark.asyncio
async def test_attempt_breakthrough_fail_increments_fails_and_consumes(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "S", 10)
    await _give(session, player_id, GATE10, GATE10_QTY)

    # roll=0.999 exceeds the 70% base success at level 10 (no pity, no phu).
    result = await repo.attempt_breakthrough(session, player_id, "S", roll=0.999)

    assert result["ok"] is True
    assert result["success"] is False

    row = await _mastery_row(session, player_id, "S")
    assert row.level == 10           # unchanged on fail
    assert row.gate_fails == 1       # incremented
    # Gate qty consumed (no Định Đạo Châu to refund it).
    assert await _qty_of(session, player_id, GATE10) == 0


# ── 8. fail + Định Đạo Châu refunds gate, consumes chau ─────────────────────


@pytest.mark.asyncio
async def test_attempt_breakthrough_fail_with_dinh_dao_chau_refunds_gate(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "S", 10)
    await _give(session, player_id, GATE10, GATE10_QTY)
    await _give(session, player_id, DINH_DAO_CHAU_KEY, 1)

    result = await repo.attempt_breakthrough(
        session, player_id, "S", use_dinh_dao_chau=True, roll=0.999
    )

    assert result["ok"] is True
    assert result["success"] is False
    assert result["refunded"] is True

    # Gate stack refunded → still full; Định Đạo Châu consumed → gone.
    assert await _qty_of(session, player_id, GATE10) == GATE10_QTY
    assert await _qty_of(session, player_id, DINH_DAO_CHAU_KEY) == 0

    row = await _mastery_row(session, player_id, "S")
    assert row.level == 10
    assert row.gate_fails == 1


# ── 9. hidden_unlocked flips on a successful level-20 crossing ──────────────


@pytest.mark.asyncio
async def test_attempt_breakthrough_hidden_gate_unlocks_at_twenty(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "S", 20)
    await _give(session, player_id, GATE20, GATE20_QTY)

    result = await repo.attempt_breakthrough(session, player_id, "S", roll=0.0)

    assert result["ok"] is True
    assert result["success"] is True
    assert result["new_level"] == 21

    row = await _mastery_row(session, player_id, "S")
    assert row.level == 21
    assert row.hidden_unlocked is True


# ── 10. get_mastery_map returns {skill_key: level} ──────────────────────────


@pytest.mark.asyncio
async def test_get_mastery_map_returns_skill_to_level(session):
    player_id = await _new_player(session)
    await _set_level(session, player_id, "A", 3)
    await _set_level(session, player_id, "B", 12)

    # A second player's rows must not leak into the first player's map.
    other_id = await _new_player(session, discord_id=2)
    await _set_level(session, other_id, "Z", 9)

    mapping = await repo.get_mastery_map(session, player_id)
    assert mapping == {"A": 3, "B": 12}
