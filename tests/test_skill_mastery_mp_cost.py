"""Mastery mp-cost growth: the dedicated ``mp_cost_mult`` curve + its
integration into ``effective_mp_cost`` (flag-gated, player-only).

Because the damage formula reads ``mp_cost``, scaling it in ``effective_mp_cost``
makes a mastered skill cost more MP *and* hit harder through that term.
"""
from __future__ import annotations

import pytest

from src.utils.config import settings
from src.game.constants.skill_mastery import MAX_LEVEL
from src.game.systems.skill_mastery import mp_cost_mult
from src.game.systems.combat.helpers import effective_mp_cost


# ── Curve ───────────────────────────────────────────────────────────────────

def test_mp_cost_mult_curve():
    assert mp_cost_mult(1) == 1.0                       # lvl 1 = no overhead
    assert mp_cost_mult(10) == pytest.approx(1.54)
    assert mp_cost_mult(25) == pytest.approx(2.44)      # +6%/level


def test_mp_cost_mult_clamps_and_monotonic():
    assert mp_cost_mult(0) == mp_cost_mult(1) == 1.0
    assert mp_cost_mult(99) == mp_cost_mult(MAX_LEVEL)
    for n in range(1, MAX_LEVEL):
        assert mp_cost_mult(n) <= mp_cost_mult(n + 1)


# ── effective_mp_cost integration ───────────────────────────────────────────

class _Actor:
    def __init__(self, key="player", mastery=None):
        self.key = key
        self.element_mp_cost_mult = {}
        self.skill_mastery = mastery or {}


def _skill(mp=90, key="SkillX", element=None):
    return {"key": key, "mp_cost": mp, "element": element}


def test_inert_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "skill_mastery_enabled", False)
    assert effective_mp_cost(_Actor(mastery={"SkillX": 25}), _skill(90)) == 90


def test_scales_with_mastery_level(monkeypatch):
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    # 90 × 2.44 = 219.6 → ceil 220
    assert effective_mp_cost(_Actor(mastery={"SkillX": 25}), _skill(90)) == 220
    # 90 × 1.54 = 138.6 → ceil 139
    assert effective_mp_cost(_Actor(mastery={"SkillX": 10}), _skill(90)) == 139


def test_lvl1_mastery_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    assert effective_mp_cost(_Actor(mastery={"SkillX": 1}), _skill(90)) == 90


def test_enemy_inert(monkeypatch):
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    a = _Actor(key="enemy_kim", mastery={"SkillX": 25})
    assert effective_mp_cost(a, _skill(90)) == 90


def test_no_mastery_entry_inert(monkeypatch):
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    assert effective_mp_cost(_Actor(mastery={}), _skill(90)) == 90
