"""Sát Thương Chuẩn (true damage) tests — covers the centralized helper at
``src/game/engine/damage/true_damage.py`` and the formula pivot from
``target.hp_max × pct`` to ``base_damage × pct``.

Verifies:
  * Bonus scales with the *hit's* damage, not the target's hp_max.
  * Skill + actor pct contributions stack additively.
  * Crit applies a × 1.5 multiplier on the bonus.
  * The combined pct is clamped at ``TRUE_DMG_PCT_CAP``.
  * No-op cases (zero pct, dead target, zero base damage) return 0
    cleanly without mutating state.
  * Target HP is decremented by the returned amount (the helper actually
    deals damage, not just reports it).
"""
from __future__ import annotations

import pytest

from src.game.constants.balance import (
    TRUE_DMG_CRIT_MULT,
    TRUE_DMG_OUTPUT_MULT,
    TRUE_DMG_PCT_CAP,
)
from src.game.engine.damage.true_damage import apply_true_damage
from src.game.systems.combatant import Combatant


def _make(name: str = "Dummy", hp: int = 100_000, atk: int = 100, matk: int = 100) -> Combatant:
    return Combatant(
        key=name.lower(), name=name, hp=hp, hp_max=hp,
        mp=200, mp_max=200, spd=10, element=None, atk=atk, matk=matk,
    )


# ── Formula correctness ──────────────────────────────────────────────────────

class TestFormulaScalesWithHitDamage:
    """The whole point of the refactor: bonus is ``base_damage × pct``,
    NOT ``target.hp_max × pct``. The defender's HP pool is no longer in
    the equation.
    """

    def test_bonus_uses_hit_damage_not_target_hp_max(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        target = _make("T", hp=100_000)
        applied = apply_true_damage(
            attacker, target, base_damage=5_000, is_crit=False, log=[],
        )
        # 5_000 × 0.20 × TRUE_DMG_OUTPUT_MULT
        assert applied == int(5_000 * 0.20 * TRUE_DMG_OUTPUT_MULT)

    def test_target_hp_is_decremented_by_the_returned_amount(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.10
        target = _make("T", hp=100_000)
        hp_before = target.hp
        applied = apply_true_damage(
            attacker, target, base_damage=10_000, is_crit=False, log=[],
        )
        assert applied == int(10_000 * 0.10 * TRUE_DMG_OUTPUT_MULT)
        assert target.hp == hp_before - applied

    def test_bigger_hit_yields_proportionally_bigger_bonus(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        small_target = _make("S", hp=100_000)
        big_target = _make("B", hp=100_000)
        small = apply_true_damage(attacker, small_target, 1_000, False, log=[])
        big = apply_true_damage(attacker, big_target, 10_000, False, log=[])
        # 10× the hit damage → 10× the true-damage bonus.
        assert big == 10 * small

    def test_target_hp_pool_does_not_change_bonus(self):
        # Same attacker, same hit, two targets with very different HP pools.
        # Under the new model the bonus is identical — the target's max
        # HP is no longer a factor.
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        small_hp = _make("Small", hp=10_000)
        huge_hp = _make("Huge", hp=10_000_000)
        a = apply_true_damage(attacker, small_hp, 5_000, False, log=[])
        b = apply_true_damage(attacker, huge_hp, 5_000, False, log=[])
        assert a == b


# ── Stacking + crit + cap ────────────────────────────────────────────────────

class TestPctStackingAndCap:
    def test_skill_pct_stacks_additively_with_actor_pct(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.10  # passive
        target = _make("T", hp=100_000)
        applied = apply_true_damage(
            attacker, target, base_damage=10_000, is_crit=False, log=[],
            skill_pct=0.05,  # +5 % from the skill itself
        )
        # 10_000 × (0.10 + 0.05) × TRUE_DMG_OUTPUT_MULT
        assert applied == int(10_000 * 0.15 * TRUE_DMG_OUTPUT_MULT)

    def test_crit_multiplies_the_bonus(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        target = _make("T", hp=100_000)
        applied = apply_true_damage(
            attacker, target, base_damage=5_000, is_crit=True, log=[],
        )
        # 5_000 × 0.20 × TRUE_DMG_OUTPUT_MULT × TRUE_DMG_CRIT_MULT
        base_bonus = int(5_000 * 0.20 * TRUE_DMG_OUTPUT_MULT)
        assert applied == int(base_bonus * TRUE_DMG_CRIT_MULT)

    def test_combined_pct_is_capped_at_TRUE_DMG_PCT_CAP(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.40
        target = _make("T", hp=100_000)
        applied = apply_true_damage(
            attacker, target, base_damage=10_000, is_crit=False, log=[],
            skill_pct=0.30,
        )
        # raw 0.70 → clamped to TRUE_DMG_PCT_CAP, then × output mult
        assert applied == int(10_000 * TRUE_DMG_PCT_CAP * TRUE_DMG_OUTPUT_MULT)


# ── No-op / safety branches ──────────────────────────────────────────────────

class TestNoOps:
    def test_zero_pct_returns_zero(self):
        attacker = _make("A")  # default true_dmg_pct = 0
        target = _make("T")
        applied = apply_true_damage(
            attacker, target, base_damage=5_000, is_crit=False, log=[],
        )
        assert applied == 0
        assert target.hp == target.hp_max  # untouched

    def test_dead_target_returns_zero_without_mutating(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        target = _make("T", hp=100_000)
        target.hp = 0
        applied = apply_true_damage(
            attacker, target, base_damage=5_000, is_crit=False, log=[],
        )
        assert applied == 0
        assert target.hp == 0

    def test_zero_base_damage_returns_zero(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        target = _make("T", hp=100_000)
        applied = apply_true_damage(
            attacker, target, base_damage=0, is_crit=False, log=[],
        )
        assert applied == 0
        assert target.hp == target.hp_max

    def test_negative_base_damage_returns_zero(self):
        # Defensive — caller shouldn't pass negative damage, but if a
        # bugged caller does, the helper must not silently heal the target.
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        target = _make("T", hp=100_000)
        applied = apply_true_damage(
            attacker, target, base_damage=-100, is_crit=False, log=[],
        )
        assert applied == 0


# ── Logging contract ─────────────────────────────────────────────────────────

class TestLogging:
    def test_applied_damage_appends_one_log_line(self):
        attacker = _make("A")
        attacker.true_dmg_pct = 0.20
        target = _make("T", hp=100_000)
        log: list[str] = []
        applied = apply_true_damage(attacker, target, 5_000, False, log)
        assert applied > 0
        assert len(log) == 1
        # Vietnamese label + the bonus amount should be in the log line.
        assert "Sát Thương Chuẩn" in log[0]
        assert f"{applied:,}" in log[0]

    def test_no_op_does_not_log(self):
        attacker = _make("A")  # no pct
        target = _make("T", hp=100_000)
        log: list[str] = []
        apply_true_damage(attacker, target, 5_000, False, log)
        assert log == []
