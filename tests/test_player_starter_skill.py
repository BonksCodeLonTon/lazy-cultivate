"""Regression: a freshly created player starts with exactly one grade-1
attack skill matching their Linh Căn.

Guards the season-2 rework drift where ``PlayerRepository.create`` filtered the
starter pool on a ``realm`` field that the rework removed (plus ``mp_cost<=15``)
and so matched *nothing* — new characters silently spawned with an empty skill
bar. The fix keys the filter on ``scroll_grade == 1``; every element has a
grade-1 attack in the ladder, so even a single-element Linh Căn gets a starter.

Infra mirrors ``tests/test_skill_mastery_repo.py``: a function-scoped in-memory
async SQLite engine with only the tables ``create`` touches
(``players`` / ``turn_trackers`` / ``character_skills``).
"""
from __future__ import annotations

import random

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Importing the connection module registers every ORM model on Base.metadata.
import src.db.connection  # noqa: F401
from src.data.registry import registry
from src.db.models.base import Base
from src.db.models.player import Player
from src.db.models.skill import CharacterSkill
from src.db.models.turn_tracker import TurnTracker
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.linh_can import parse_linh_can


@pytest_asyncio.fixture
async def session():
    """In-memory async SQLite with only the tables ``create`` writes."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Player.__table__,
        TurnTracker.__table__,
        CharacterSkill.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _starter_rows(session, player_id: int) -> list[CharacterSkill]:
    res = await session.execute(
        select(CharacterSkill).where(CharacterSkill.player_id == player_id)
    )
    return list(res.scalars().all())


@pytest.mark.asyncio
async def test_new_player_gets_one_grade1_attack_starter(session):
    """Across 20 fresh players (incl. single-element Linh Căn), each lands
    exactly one grade-1 attack in slot 0, of an element they own."""
    random.seed(1234)  # deterministic Linh Căn / constitution / pick rolls
    repo = PlayerRepository(session)

    saw_single_element = False
    for did in range(1, 21):
        player = await repo.create(discord_id=did, name=f"p{did}")
        elements = parse_linh_can(player.linh_can)
        if len(elements) == 1:
            saw_single_element = True

        rows = await _starter_rows(session, player.id)
        assert len(rows) == 1, (
            f"player {did} (linh_can={elements}) got {len(rows)} starter "
            f"skills, expected exactly 1"
        )
        starter = rows[0]
        assert starter.slot_index == 0, "starter must occupy slot 0"

        skill = registry.get_skill(starter.skill_key)
        assert skill is not None, f"starter {starter.skill_key!r} not in registry"
        assert skill["category"] == "attack"
        assert skill["scroll_grade"] == 1
        assert skill["element"] in elements, (
            f"starter element {skill['element']!r} not in player Linh Căn {elements}"
        )

    assert saw_single_element, (
        "expected at least one single-element Linh Căn across 20 rolls — "
        "the single-element path is the one most at risk if a grade-1 attack "
        "is ever missing for an element"
    )


@pytest.mark.asyncio
async def test_every_element_has_a_grade1_attack_for_starters():
    """The starter fix relies on every element owning ≥1 grade-1 attack;
    assert that invariant directly so a future data edit that drops one is
    caught here rather than as a rare empty-skill-bar spawn."""
    by_element: dict[str, int] = {}
    for s in registry.skills.values():
        if (
            not s.get("key", "").startswith("Enemy")
            and s.get("category") == "attack"
            and s.get("scroll_grade") == 1
            and s.get("element")
        ):
            by_element[s["element"]] = by_element.get(s["element"], 0) + 1

    for elem in ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am"):
        assert by_element.get(elem, 0) >= 1, (
            f"element {elem!r} has no grade-1 attack skill — a new player whose "
            f"Linh Căn is only {elem!r} would spawn with no starter skill"
        )
