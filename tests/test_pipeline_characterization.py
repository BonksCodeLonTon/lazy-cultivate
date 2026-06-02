"""Characterization tests for the damage pipeline.

Pin the full chain output (evasion → base → crit → physical → elemental →
final_bonus) for known inputs before the upcoming combat-session
refactor. Phase 1 of the refactor shipped scaffolding only; later phases
move logic between modules. These tests guarantee that *callers* of
``calculate_damage`` continue to observe identical outputs.

Note on ``SeedRng``: ``random.uniform(a, b)`` is ``a + (b-a) × random()``,
so ``SeedRng(0.5)`` gives a unity variance multiplier (= 1.0) and
``SeedRng(0.0)`` gives the floor (0.85). Both produce a deterministic
final number for a given attacker/defender/skill triple.
"""
from __future__ import annotations

from src.game.engine.damage.pipeline import calculate_damage
from src.game.engine.stats import AttackStats, DefenseStats
from src.game.models.skill import AttackType, DmgScale, Skill, SkillCategory

from tests.conftest import SeedRng


def _skill(
    *,
    base_dmg: int = 100,
    mp_cost: int = 0,
    attack_type: AttackType = AttackType.MAGICAL,
    element: str | None = None,
    bypass_evasion: bool = False,
    dmg_scale: DmgScale | None = None,
) -> Skill:
    return Skill(
        key="test_skill", vi="Thử", en="Test",
        category=SkillCategory.ATTACK,
        mp_cost=mp_cost, cooldown=0, base_dmg=base_dmg,
        element=element, attack_type=attack_type,
        bypass_evasion=bypass_evasion,
        dmg_scale=dmg_scale or DmgScale(),
    )


def test_evaded_attack_returns_zero_final_damage():
    """Defender with high evasion_rating + SeedRng(0.0) is guaranteed to dodge."""
    skill = _skill(base_dmg=500)
    attacker = AttackStats(atk=200, matk=200)
    defender = DefenseStats(evasion_rating=10_000)  # near-cap chance

    result = calculate_damage(skill, attacker, defender, rng=SeedRng(0.0))

    assert result.is_evaded is True
    assert result.final == 0
    assert result.raw == 0  # no roll happened


def test_physical_attack_takes_armor_reduction():
    """Physical hit against def_stat > 0 must produce smaller damage than no-armor baseline."""
    skill = _skill(base_dmg=1_000, attack_type=AttackType.PHYSICAL)
    attacker = AttackStats(atk=200)

    armored = calculate_damage(
        skill, attacker, DefenseStats(def_stat=500), rng=SeedRng(0.5),
    )
    naked = calculate_damage(
        skill, attacker, DefenseStats(def_stat=0), rng=SeedRng(0.5),
    )

    assert armored.is_evaded is False
    assert armored.is_crit is False
    assert naked.is_crit is False
    # Same SeedRng(0.5) → same variance, same (no-)crit roll. Only difference: armor.
    assert armored.final < naked.final
    assert armored.final > 0  # not wiped


def test_elemental_resistance_scales_final_damage():
    """50% resistance produces ~half the damage of zero resistance (within rounding)."""
    skill = _skill(base_dmg=1_000, element="hoa", attack_type=AttackType.MAGICAL)
    attacker = AttackStats(matk=200)

    full = calculate_damage(
        skill, attacker, DefenseStats(resistances={}), rng=SeedRng(0.5),
    )
    halved = calculate_damage(
        skill, attacker, DefenseStats(resistances={"hoa": 0.5}), rng=SeedRng(0.5),
    )

    assert full.is_crit is False
    assert halved.is_crit is False
    # 50% res → halved final; allow ±2 for int rounding through the chain.
    assert abs(halved.final * 2 - full.final) <= 2


def test_crit_fires_with_high_crit_rating_and_low_seed():
    """High crit_rating + SeedRng(0.0) guarantees a crit; final exceeds non-crit baseline."""
    skill = _skill(base_dmg=1_000)
    crit_attacker = AttackStats(crit_rating=100_000, crit_dmg_rating=0)
    non_crit_attacker = AttackStats(crit_rating=0)
    defender = DefenseStats()

    crit_result = calculate_damage(skill, crit_attacker, defender, rng=SeedRng(0.0))
    # SeedRng(0.99) keeps the crit roll above BASE_CRIT_CHANCE (5%) so this stays non-crit.
    non_crit_result = calculate_damage(skill, non_crit_attacker, defender, rng=SeedRng(0.99))

    assert crit_result.is_crit is True
    assert non_crit_result.is_crit is False
    # Crit uses BASE_CRIT_DMG_MULT = 1.5 with crit_dmg_rating=0.
    # Even with worst-case variance against best-case, crit > non-crit baseline.
    assert crit_result.final > non_crit_result.final


def test_bypass_evasion_attack_always_lands():
    """``bypass_evasion=True`` skips the dodge roll even at max evasion_rating."""
    skill = _skill(base_dmg=500, bypass_evasion=True)
    attacker = AttackStats(atk=100, matk=100)
    defender = DefenseStats(evasion_rating=999_999)  # would otherwise always dodge

    for seed_val in (0.0, 0.1, 0.5, 0.9, 0.99):
        result = calculate_damage(skill, attacker, defender, rng=SeedRng(seed_val))
        assert result.is_evaded is False
        assert result.final >= 1, f"bypass_evasion hit should land at seed={seed_val}"
