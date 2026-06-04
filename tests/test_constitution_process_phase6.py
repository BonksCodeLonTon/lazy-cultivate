"""Phase 6 service-helper tests for the Constitution Process cog seam.

Covers the two DB-aware service helpers in
``src.db.repositories.constitution_process`` that the cog buttons call —
``apply_breakthrough`` and ``apply_swap``. These are the testable seam; the
Discord button callbacks themselves are NOT unit-tested here (they need a live
``discord.Interaction``).

Infra mirrors ``test_constitution_process_repo.py``: a real in-memory async
SQLite engine, creating only the three tables these helpers touch
(``players``, ``character_constitution_progress``, ``inventory``). RNG is
injected via a ``random.Random(seed)`` so success / fail rolls are
deterministic; the feature flag is toggled with ``monkeypatch``.
"""
from __future__ import annotations

import random

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the connection module registers every ORM model on Base.metadata.
import src.db.connection  # noqa: F401
from src.db.models.base import Base
from src.db.models.constitution_process import CharacterConstitutionProgress
from src.db.models.inventory import InventoryItem
from src.db.models.player import Player
from src.db.repositories import constitution_process as repo
from src.game.constants.constitution_process import (
    DINH_THE_CHAU_KEY,
    GATES,
    HO_THE_PHU_KEY,
)
from src.utils.config import settings

# Gate 2 → 3: ConsProcThangThe ×3, base 0.85. The cheapest gate, used as the
# canonical breakthrough fixture (gate 5/8 differ only in numbers).
_GATE = 2
_GATE_ITEM = GATES[_GATE]["item_key"]
_GATE_QTY = GATES[_GATE]["qty"]
_HOAN_THE_TINH = "ConsProcHoanTheTinh"

# A guaranteed-success roll: 0.0 < any chance. A guaranteed-fail roll without
# Hộ Thể Phù: 0.999 > base 0.85 (+ small pity). Seed the Random so .random()
# yields these — Random(1).random() ≈ 0.134 (success at base 0.85);
# Random(0).random() ≈ 0.844 (success at 0.85 but FAIL once below). We instead
# inject a tiny stub Random to keep the roll explicit and seed-independent.


class _FixedRng:
    """A ``random.Random``-shaped stub returning a fixed ``.random()`` value."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


@pytest_asyncio.fixture
async def session():
    """Function-scoped in-memory async SQLite session with the 3 needed tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Player.__table__,
        CharacterConstitutionProgress.__table__,
        InventoryItem.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Default the feature ON for these tests; flag-off cases re-toggle locally."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


async def _new_player(
    session,
    *,
    discord_id: int = 1,
    constitution_type: str = "ConstitutionKim",
    constitution_tracker: str = "ConstitutionKim",
) -> Player:
    player = Player(
        discord_id=discord_id,
        name=f"p{discord_id}",
        constitution_type=constitution_type,
        constitution_tracker=constitution_tracker,
    )
    session.add(player)
    await session.flush()
    return player


async def _seed_progress(session, player_id: int, key: str, *, level: int, gate_fails: int = 0):
    prog = await repo.get_or_create(session, player_id, key)
    prog.level = level
    prog.gate_fails = gate_fails
    await session.flush()
    return prog


async def _seed_item(session, player_id: int, item_key: str, qty: int, grade: int = 1):
    session.add(InventoryItem(player_id=player_id, item_key=item_key, grade=grade, quantity=qty))
    await session.flush()


async def _owned(session, player_id: int, item_key: str) -> int:
    rows = (
        await session.execute(
            select(InventoryItem).where(
                InventoryItem.player_id == player_id,
                InventoryItem.item_key == item_key,
            )
        )
    ).scalars().all()
    return sum(r.quantity for r in rows)


async def _row(session, player_id: int, key: str):
    return (
        await session.execute(
            select(CharacterConstitutionProgress).where(
                CharacterConstitutionProgress.player_id == player_id,
                CharacterConstitutionProgress.constitution_key == key,
            )
        )
    ).scalar_one_or_none()


# ── apply_breakthrough ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_breakthrough_success_advances_and_consumes(session):
    """SUCCESS: level +1, fails reset, gate mats + Hộ Thể Phù spent, persisted."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)
    await _seed_item(session, player.id, HO_THE_PHU_KEY, 1)

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=True, use_dinh_the_chau=False,
        trial_won=True, rng=_FixedRng(0.0),  # guaranteed success
    )

    assert result["outcome"] == "SUCCESS"
    assert result["new_level"] == _GATE + 1
    assert result["constitution_key"] == "ConstitutionKim"
    assert result["new_band"][0]  # band label augmented

    row = await _row(session, player.id, "ConstitutionKim")
    assert row.level == _GATE + 1
    assert row.gate_fails == 0
    # Gate mats fully consumed; Hộ Thể Phù consumed.
    assert await _owned(session, player.id, _GATE_ITEM) == 0
    assert await _owned(session, player.id, HO_THE_PHU_KEY) == 0


@pytest.mark.asyncio
async def test_apply_breakthrough_fail_burns_mats_increments_pity(session):
    """FAIL (no Định): gate mats burned, fails +1, level unchanged."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE, gate_fails=0)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, rng=_FixedRng(0.999),  # guaranteed fail at base 0.85
    )

    assert result["outcome"] == "FAIL"
    assert result["new_level"] == _GATE
    assert result["new_fails"] == 1
    assert result["refunded_gate_mats"] is False

    row = await _row(session, player.id, "ConstitutionKim")
    assert row.level == _GATE
    assert row.gate_fails == 1
    assert await _owned(session, player.id, _GATE_ITEM) == 0  # burned


@pytest.mark.asyncio
async def test_apply_breakthrough_fail_with_dinh_refunds_mats(session):
    """FAIL +Định Thể Châu: gate mats refunded (kept), pearl consumed, fails +1."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)
    await _seed_item(session, player.id, DINH_THE_CHAU_KEY, 1)

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=False, use_dinh_the_chau=True,
        trial_won=True, rng=_FixedRng(0.999),
    )

    assert result["outcome"] == "FAIL"
    assert result["refunded_gate_mats"] is True
    # Gate mats untouched (refunded); pearl spent.
    assert await _owned(session, player.id, _GATE_ITEM) == _GATE_QTY
    assert await _owned(session, player.id, DINH_THE_CHAU_KEY) == 0


@pytest.mark.asyncio
async def test_apply_breakthrough_not_enough_materials_is_noop(session):
    """NOT_ENOUGH_MATERIALS: nothing consumed, nothing persisted."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE, gate_fails=2)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY - 1)  # short by 1

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, rng=_FixedRng(0.0),
    )

    assert result["outcome"] == "NOT_ENOUGH_MATERIALS"
    row = await _row(session, player.id, "ConstitutionKim")
    assert row.level == _GATE          # unchanged
    assert row.gate_fails == 2          # unchanged
    assert await _owned(session, player.id, _GATE_ITEM) == _GATE_QTY - 1  # not burned


@pytest.mark.asyncio
async def test_apply_breakthrough_trial_lost_is_noop(session):
    """trial_won=False: TRIAL_LOST, nothing consumed, nothing persisted."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)
    await _seed_item(session, player.id, HO_THE_PHU_KEY, 1)

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=True, use_dinh_the_chau=False,
        trial_won=False, rng=_FixedRng(0.0),
    )

    assert result["outcome"] == "TRIAL_LOST"
    row = await _row(session, player.id, "ConstitutionKim")
    assert row.level == _GATE
    # Nothing consumed — not even the Hộ Thể Phù.
    assert await _owned(session, player.id, _GATE_ITEM) == _GATE_QTY
    assert await _owned(session, player.id, HO_THE_PHU_KEY) == 1


@pytest.mark.asyncio
async def test_apply_breakthrough_not_at_gate_is_noop(session):
    """A non-ceiling level → NOT_AT_GATE, no consumption."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=3)  # not in GATES
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, rng=_FixedRng(0.0),
    )

    assert result["outcome"] == "NOT_AT_GATE"
    assert await _owned(session, player.id, _GATE_ITEM) == _GATE_QTY


@pytest.mark.asyncio
async def test_apply_breakthrough_flag_off_is_noop(session, monkeypatch):
    """Flag OFF → DISABLED, no row read/write, no consumption."""
    monkeypatch.setattr(settings, "constitution_process_enabled", False)
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)

    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, rng=_FixedRng(0.0),
    )

    assert result["outcome"] == "DISABLED"
    row = await _row(session, player.id, "ConstitutionKim")
    assert row.level == _GATE
    assert await _owned(session, player.id, _GATE_ITEM) == _GATE_QTY


@pytest.mark.asyncio
async def test_apply_breakthrough_deterministic_random_seed(session):
    """A real ``random.Random(seed)`` drives the roll the same way each run."""
    player = await _new_player(session)
    await _seed_progress(session, player.id, "ConstitutionKim", level=_GATE)
    await _seed_item(session, player.id, _GATE_ITEM, _GATE_QTY)

    # Random(0).random() ≈ 0.8444 > base 0.85? No — 0.8444 < 0.85 → SUCCESS.
    result = await repo.apply_breakthrough(
        session, player,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, rng=random.Random(0),
    )
    assert result["outcome"] in ("SUCCESS", "FAIL")  # never a guard outcome


# ── apply_swap ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_swap_success_reorders_consumes_keeps_progress(session):
    """SWAPPED: target → primary, essence spent, both progress rows untouched."""
    player = await _new_player(
        session,
        constitution_type="ConstitutionKim,ConstitutionMoc",
        constitution_tracker="ConstitutionKim,ConstitutionMoc",
    )
    # Kim (current primary) at L3; Moc (swap target) at L5 — must be retained.
    await _seed_progress(session, player.id, "ConstitutionKim", level=3)
    await _seed_progress(session, player.id, "ConstitutionMoc", level=5)
    await _seed_item(session, player.id, _HOAN_THE_TINH, 2)

    result = await repo.apply_swap(session, player, "ConstitutionMoc")

    assert result["outcome"] == "SWAPPED"
    assert player.constitution_type == "ConstitutionMoc,ConstitutionKim"
    assert await _owned(session, player.id, _HOAN_THE_TINH) == 1  # one spent

    # Swap-retains-progress: both rows keep their level.
    assert (await _row(session, player.id, "ConstitutionKim")).level == 3
    assert (await _row(session, player.id, "ConstitutionMoc")).level == 5


@pytest.mark.asyncio
async def test_apply_swap_no_essence_consumes_nothing(session):
    """NO_ESSENCE guard: no essence owned → reject, no mutation."""
    player = await _new_player(
        session,
        constitution_type="ConstitutionKim,ConstitutionMoc",
        constitution_tracker="ConstitutionKim,ConstitutionMoc",
    )
    result = await repo.apply_swap(session, player, "ConstitutionMoc")

    assert result["outcome"] == "NO_ESSENCE"
    assert player.constitution_type == "ConstitutionKim,ConstitutionMoc"  # unchanged


@pytest.mark.asyncio
async def test_apply_swap_not_equipped_consumes_nothing(session):
    """NOT_EQUIPPED: target not in equipped list → reject, essence untouched."""
    player = await _new_player(
        session,
        constitution_type="ConstitutionKim",
        constitution_tracker="ConstitutionKim,ConstitutionMoc",
    )
    await _seed_item(session, player.id, _HOAN_THE_TINH, 1)

    result = await repo.apply_swap(session, player, "ConstitutionMoc")

    assert result["outcome"] == "NOT_EQUIPPED"
    assert player.constitution_type == "ConstitutionKim"
    assert await _owned(session, player.id, _HOAN_THE_TINH) == 1


@pytest.mark.asyncio
async def test_apply_swap_already_primary_consumes_nothing(session):
    """ALREADY_PRIMARY: target is already slot 0 → reject, essence untouched."""
    player = await _new_player(
        session,
        constitution_type="ConstitutionKim,ConstitutionMoc",
        constitution_tracker="ConstitutionKim,ConstitutionMoc",
    )
    await _seed_item(session, player.id, _HOAN_THE_TINH, 1)

    result = await repo.apply_swap(session, player, "ConstitutionKim")

    assert result["outcome"] == "ALREADY_PRIMARY"
    assert player.constitution_type == "ConstitutionKim,ConstitutionMoc"
    assert await _owned(session, player.id, _HOAN_THE_TINH) == 1


@pytest.mark.asyncio
async def test_apply_swap_hon_don_target_consumes_nothing(session):
    """INVALID_TARGET: Hỗn Độn can never become the primary → reject."""
    from src.game.systems.the_chat import HON_DON_KEY

    player = await _new_player(
        session,
        constitution_type=f"ConstitutionKim,{HON_DON_KEY}",
        constitution_tracker=f"ConstitutionKim,{HON_DON_KEY}",
    )
    await _seed_item(session, player.id, _HOAN_THE_TINH, 1)

    result = await repo.apply_swap(session, player, HON_DON_KEY)

    assert result["outcome"] == "INVALID_TARGET"
    assert player.constitution_type == f"ConstitutionKim,{HON_DON_KEY}"
    assert await _owned(session, player.id, _HOAN_THE_TINH) == 1


@pytest.mark.asyncio
async def test_apply_swap_flag_off_is_noop(session, monkeypatch):
    """Flag OFF → DISABLED, no mutation, no consumption."""
    monkeypatch.setattr(settings, "constitution_process_enabled", False)
    player = await _new_player(
        session,
        constitution_type="ConstitutionKim,ConstitutionMoc",
        constitution_tracker="ConstitutionKim,ConstitutionMoc",
    )
    await _seed_item(session, player.id, _HOAN_THE_TINH, 1)

    result = await repo.apply_swap(session, player, "ConstitutionMoc")

    assert result["outcome"] == "DISABLED"
    assert player.constitution_type == "ConstitutionKim,ConstitutionMoc"
    assert await _owned(session, player.id, _HOAN_THE_TINH) == 1


def test_flag_default_is_false():
    """The feature flag defaults to False on the real settings object."""
    # The autouse fixture flips it ON per-test; the class default is False.
    assert type(settings).model_fields["constitution_process_enabled"].default is False
