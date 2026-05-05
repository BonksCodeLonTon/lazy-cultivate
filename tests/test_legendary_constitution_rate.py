"""Tests for the rate of obtaining a Legendary (Truyền Thuyết) Thể Chất.

Two distinct rate mechanisms exist:

1. **Starter roll** — players spin for a starting body at registration. A flat
   ``LEGENDARY_STARTER_RATE`` (0.5%) pre-roll gates the legendary pool;
   otherwise the standard weighted pick (``roll_weight``) runs.
2. **Activation roll** — players grind materials + merit, then roll a success
   chance to imprint a Thể Chất. Legendary base = 35%, Thể Tu adds +20%, and
   per-entry ``activation_chance`` can override the rarity default.
"""
from __future__ import annotations

import random
from typing import Iterable

import pytest

from src.data.registry import registry
from src.db.repositories.player_repo import (
    LEGENDARY_STARTER_RATE,
    _roll_starter_constitution,
)
from src.game.systems.the_chat import (
    HON_DON_KEY,
    THE_TU_SUCCESS_BONUS,
    _BASE_SUCCESS,
    activation_chance,
    roll_activation,
)


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Activation chance: per-rarity defaults ──────────────────────────────────


def _legendary_entry(activation_override: float | None = None) -> dict:
    entry: dict = {"key": "TestLegendary", "vi": "Test", "rarity": "legendary"}
    if activation_override is not None:
        entry["activation_chance"] = activation_override
    return entry


def test_legendary_base_rate_is_35_percent_for_non_the_tu():
    entry = _legendary_entry()
    chance = activation_chance(entry, body_realm=0, qi_realm=5, formation_realm=5)
    assert chance == pytest.approx(_BASE_SUCCESS["legendary"])
    assert chance == pytest.approx(0.35)


def test_legendary_rate_adds_the_tu_bonus():
    entry = _legendary_entry()
    chance = activation_chance(entry, body_realm=5, qi_realm=5, formation_realm=5)
    assert chance == pytest.approx(_BASE_SUCCESS["legendary"] + THE_TU_SUCCESS_BONUS)
    assert chance == pytest.approx(0.55)


def test_legendary_explicit_activation_chance_overrides_rarity_default():
    entry = _legendary_entry(activation_override=0.20)
    chance = activation_chance(entry, body_realm=0, qi_realm=5, formation_realm=5)
    assert chance == pytest.approx(0.20)


def test_legendary_explicit_chance_still_receives_the_tu_bonus():
    entry = _legendary_entry(activation_override=0.20)
    chance = activation_chance(entry, body_realm=5, qi_realm=5, formation_realm=5)
    assert chance == pytest.approx(0.40)


def test_activation_chance_is_clamped_to_one():
    entry = _legendary_entry(activation_override=0.95)
    chance = activation_chance(entry, body_realm=5, qi_realm=5, formation_realm=5)
    assert chance == 1.0


def test_activation_chance_is_clamped_to_zero():
    entry = _legendary_entry(activation_override=-0.5)
    chance = activation_chance(entry, body_realm=0, qi_realm=5, formation_realm=5)
    assert chance == 0.0


# ── Activation roll: empirical rate matches expected ────────────────────────


def _empirical_rate(entry: dict, *, realms: tuple[int, int, int], trials: int, seed: int) -> float:
    rng = random.Random(seed)
    body, qi, formation = realms
    successes = sum(
        1 for _ in range(trials)
        if roll_activation(entry, body, qi, formation, rng=rng)
    )
    return successes / trials


def test_legendary_empirical_rate_matches_35_percent_non_the_tu():
    entry = _legendary_entry()
    rate = _empirical_rate(entry, realms=(0, 5, 5), trials=20_000, seed=42)
    assert rate == pytest.approx(0.35, abs=0.02)


def test_legendary_empirical_rate_matches_55_percent_the_tu():
    entry = _legendary_entry()
    rate = _empirical_rate(entry, realms=(5, 5, 5), trials=20_000, seed=42)
    assert rate == pytest.approx(0.55, abs=0.02)


def test_hon_don_empirical_rate_matches_20_percent():
    """Hỗn Độn Đạo Thể overrides with ``activation_chance: 0.2`` — even Thể Tu
    gets only 40%, the highest gate in the game."""
    hon_don = registry.get_constitution(HON_DON_KEY)
    assert hon_don is not None, "Hỗn Độn Đạo Thể should be registered"
    rate = _empirical_rate(hon_don, realms=(0, 5, 5), trials=20_000, seed=42)
    assert rate == pytest.approx(0.20, abs=0.02)


# ── Starter roll: legendary appears at LEGENDARY_STARTER_RATE ───────────────


def _all_legendary_keys() -> set[str]:
    return {
        c["key"] for c in registry.constitutions.values()
        if c.get("rarity") == "legendary"
    }


def test_legendary_starter_rate_is_half_percent():
    assert LEGENDARY_STARTER_RATE == pytest.approx(0.005)


def test_legendary_kept_out_of_weighted_rollable_pool():
    """The 0.5% gate is a separate pre-roll — legendaries must still have
    ``roll_weight=0`` so they don't double-dip via the weighted pick."""
    rollable_keys = {c["key"] for c in registry.rollable_constitutions()}
    legendary_keys = _all_legendary_keys()
    assert legendary_keys, "Expected at least one legendary constitution in registry"
    assert rollable_keys.isdisjoint(legendary_keys), (
        "Legendary constitutions must keep roll_weight=0: "
        f"leak = {rollable_keys & legendary_keys}"
    )


def test_starter_roll_legendary_rate_matches_half_percent():
    """Empirical: across 50k rolls the legendary share is ~0.5%."""
    legendary_keys = _all_legendary_keys()
    random.seed(123)
    trials = 50_000
    legendary_hits = sum(
        1 for _ in range(trials)
        if _roll_starter_constitution(["hoa", "thuy", "kim"]) in legendary_keys
    )
    rate = legendary_hits / trials
    assert rate == pytest.approx(LEGENDARY_STARTER_RATE, abs=0.001)


def test_starter_roll_legendary_pool_respects_element_filter():
    """A legendary hit must still match the player's linh căn — no cross-element
    leaks (e.g., a Hoa-only player should never roll a Thủy legendary)."""
    legendary_keys = _all_legendary_keys()
    random.seed(456)
    trials = 200_000
    seen_legendaries: set[str] = set()
    for _ in range(trials):
        key = _roll_starter_constitution(["hoa"])
        if key in legendary_keys:
            seen_legendaries.add(key)

    for key in seen_legendaries:
        const = registry.get_constitution(key)
        elem = const.get("element") if const else None
        assert elem in (None, "hoa"), (
            f"Legendary {key} has element {elem!r} — cannot appear for Hoa-only player"
        )


def test_starter_roll_legendary_pool_excludes_special_requirements():
    """Hỗn Độn Đạo Thể and any other gated legendary must never appear in the
    starter roll, even though it's legendary rarity."""
    random.seed(789)
    trials = 200_000
    elements: Iterable[str] = ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am")
    for elem in elements:
        for _ in range(trials // len(tuple(elements))):
            key = _roll_starter_constitution([elem])
            const = registry.get_constitution(key)
            assert not (const and const.get("special_requirements")), (
                f"Gated constitution {key} surfaced in starter roll for {elem}"
            )
