"""Shared fixtures for combat-system tests.

Existing test files keep their inline ``make_combatant`` / ``make_session``
copies (untouched to avoid disturbing the safety net). New tests added in
Phase 0 of the combat-session refactor should import the helpers below
instead of re-rolling their own.

Two combatant size profiles are exposed because existing files split
roughly into "small HP" (1k, used in ``test_combat_builds``) and "large
HP" (10k, used everywhere else). Default is the larger profile since
most characterization tests need enough HP buffer to observe ticks.
"""
from __future__ import annotations

import random
from typing import Any

import pytest

from src.data.registry import registry
from src.game.systems.combat import CombatSession
from src.game.systems.combatant import Combatant


@pytest.fixture(scope="session", autouse=True)
def _load_registry() -> None:
    """Load JSON data once per test session.

    Marked ``autouse`` so individual test modules don't need to repeat
    the registry-loading boilerplate that historically appeared in every
    combat-related test file.
    """
    registry.load()


def make_combatant(key: str = "p", *, profile: str = "large", **overrides: Any) -> Combatant:
    """Build a non-zero combatant for characterization tests.

    ``profile``:
      - ``"large"`` (default) — hp/hp_max = 10_000. Good for periodic
        / DoT / aura tests where the actor needs to survive several
        ticks.
      - ``"small"`` — hp/hp_max = 1_000. Matches the legacy default in
        ``test_combat_builds.py`` for tests that want a thin HP buffer.

    All other defaults are uniform across profiles and match the values
    used by existing inline ``make_combatant`` definitions.
    """
    hp = 10_000 if profile == "large" else 1_000
    defaults: dict[str, Any] = dict(
        name=key,
        hp=hp, hp_max=hp,
        mp=500, mp_max=500,
        spd=10, element=None,
        atk=100, matk=100, def_stat=20,
    )
    defaults.update(overrides)
    return Combatant(key=key, **defaults)


def make_session(
    player: Combatant,
    enemy: Combatant,
    *,
    seed: int = 0,
    max_turns: int = 5,
) -> CombatSession:
    """Build a minimal session wired with a deterministic seeded RNG."""
    return CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed),
        max_turns=max_turns,
    )


class SeedRng(random.Random):
    """``random.Random`` subclass whose ``random()`` always returns ``value``.

    Avoids the ``session.rng.random = lambda: 0.0`` method-assignment
    antipattern seen in existing tests, which type-checkers flag and
    which won't survive the upcoming refactor (where the session may
    receive a typed RNG).
    """

    def __init__(self, value: float = 0.0):
        super().__init__()
        self._value = value

    def random(self) -> float:  # type: ignore[override]
        return self._value


def aegis_buff(
    holder: Combatant,
    buff_key: str,
    *,
    duration: int = 5,
    shield_grant: dict[str, Any] | None = None,
    store_charge: dict[str, Any] | None = None,
    on_hit_inflict: dict[str, Any] | None = None,
    discharge: dict[str, Any] | None = None,
    stored_charge: int = 0,
) -> dict[str, Any]:
    """Install a defense-aegis buff on ``holder`` and return its override block.

    Matches the schema documented at the top of ``defense_aegis.py``.
    Returns the override dict so the test can poke ``_stored_charge``
    or read the final state after the system mutates it.
    """
    holder.effects[buff_key] = duration
    aegis_block: dict[str, Any] = {}
    if shield_grant is not None:
        aegis_block["shield_grant"] = shield_grant
    if store_charge is not None:
        aegis_block["store_charge"] = store_charge
    if on_hit_inflict is not None:
        aegis_block["on_hit_inflict"] = on_hit_inflict
    if discharge is not None:
        aegis_block["discharge"] = discharge
    override: dict[str, Any] = {"_aegis": aegis_block}
    if stored_charge:
        override["_stored_charge"] = stored_charge
    holder.effect_overrides[buff_key] = override
    return override
