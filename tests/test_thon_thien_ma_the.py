"""Tests for Thôn Thiên Ma Tâm → Thôn Thiên Ma Thể progression chain.

Covers:
  * Stat-drain aura at combat start (drain 20% / absorb same amount).
  * Idempotence via ``stat_drain_aura_applied``.
  * Activation requirements: Ma Tâm equipped + Ma Thần Công learned.
  * Registry presence of the seed, evolved form, and gateway skill.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from src.data.registry import registry
from src.game.systems.combat import CombatSession
from src.game.systems.combatant import Combatant
from src.game.systems.the_chat import check_requirements


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def make_combatant(key: str = "p", **overrides) -> Combatant:
    defaults = dict(
        name=key,
        hp=10_000, hp_max=10_000,
        mp=500, mp_max=500,
        spd=10, element=None,
        atk=100, matk=100, def_stat=20,
    )
    defaults.update(overrides)
    return Combatant(key=key, **defaults)


def make_session(player: Combatant, enemy: Combatant, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed),
        max_turns=5,
    )


# ── Aura mechanic ────────────────────────────────────────────────────────────


def test_aura_drains_pct_and_transfers_to_holder():
    holder = make_combatant("p", atk=100, matk=200, def_stat=50, spd=10, stat_drain_aura_pct=0.20)
    target = make_combatant("e", atk=500, matk=400, def_stat=100, spd=20)
    session = make_session(holder, target)

    session._apply_stat_drain_aura(holder, target)

    # 20% drains: atk 100 / matk 80 / def 20 / spd 4
    assert target.atk == 400
    assert target.matk == 320
    assert target.def_stat == 80
    assert target.spd == 16
    assert holder.atk == 200
    assert holder.matk == 280
    assert holder.def_stat == 70
    assert holder.spd == 14
    assert holder.stat_drain_aura_applied is True


def test_aura_idempotent_via_applied_flag():
    holder = make_combatant("p", atk=100, stat_drain_aura_pct=0.20)
    target = make_combatant("e", atk=500)
    session = make_session(holder, target)

    session._apply_stat_drain_aura(holder, target)
    first_holder_atk = holder.atk
    first_target_atk = target.atk

    session._apply_stat_drain_aura(holder, target)  # second call, should be no-op
    assert holder.atk == first_holder_atk
    assert target.atk == first_target_atk


def test_aura_no_op_without_pct():
    holder = make_combatant("p", atk=100, stat_drain_aura_pct=0.0)
    target = make_combatant("e", atk=500)
    session = make_session(holder, target)

    session._apply_stat_drain_aura(holder, target)
    assert holder.atk == 100
    assert target.atk == 500
    assert holder.stat_drain_aura_applied is False


def test_aura_fires_on_first_step():
    """End-to-end: ``step()`` invokes the aura at turn 1."""
    holder = make_combatant("p", atk=100, matk=100, stat_drain_aura_pct=0.20)
    target = make_combatant("e", atk=500, matk=300)
    session = make_session(holder, target)

    session.step()
    assert holder.stat_drain_aura_applied is True
    assert target.atk == 400
    assert holder.atk == 200


def test_aura_spd_floors_at_one():
    holder = make_combatant("p", spd=10, stat_drain_aura_pct=0.50)
    target = make_combatant("e", spd=1)  # already minimum
    session = make_session(holder, target)

    session._apply_stat_drain_aura(holder, target)
    # 50% of 1 = 0 → no change
    assert target.spd == 1
    # Holder's spd unchanged because nothing was drained
    assert holder.spd == 10


# ── Activation requirements ─────────────────────────────────────────────────


def _the_data() -> dict:
    c = registry.get_constitution("ConstitutionThonThienMaThe")
    assert c is not None
    return c


def _player(constitution_type: str = "", skill_keys: list[str] | None = None):
    skills = [SimpleNamespace(skill_key=k) for k in (skill_keys or [])]
    return SimpleNamespace(
        constitution_type=constitution_type,
        skills=skills,
        body_realm=8, qi_realm=8, formation_realm=8,
        dao_ti_unlocked=True,
    )


def test_requires_ma_tam_equipped():
    player = _player(constitution_type="ConstitutionVanTuong", skill_keys=["SkillMaThanCong_R9"])
    err = check_requirements(player, _the_data(), registry.constitutions)
    assert err is not None
    assert "Thôn Thiên Ma Tâm" in err


def test_requires_ma_than_cong_learned():
    player = _player(constitution_type="ConstitutionThonThienMaTam", skill_keys=[])
    err = check_requirements(player, _the_data(), registry.constitutions)
    assert err is not None
    assert "Ma Thần Công" in err


def test_requirements_pass_when_both_satisfied():
    player = _player(
        constitution_type="ConstitutionThonThienMaTam",
        skill_keys=["SkillMaThanCong_R9"],
    )
    err = check_requirements(player, _the_data(), registry.constitutions)
    assert err is None


# ── Registry sanity ─────────────────────────────────────────────────────────


def test_ma_tam_registered_with_low_stats():
    c = registry.get_constitution("ConstitutionThonThienMaTam")
    assert c is not None
    assert c["rarity"] == "legendary"
    assert c["element"] == "am"
    bonuses = c["stat_bonuses"]
    # "Low stats" — every numeric bonus stays below 0.10 (≤10%)
    for k, v in bonuses.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert v <= 0.10, f"{k}={v} too high for the seed body"


def test_ma_the_registered_with_aura_and_progression():
    c = registry.get_constitution("ConstitutionThonThienMaThe")
    assert c is not None
    assert c["rarity"] == "legendary"
    assert c["element"] == "am"
    assert c["stat_bonuses"]["stat_drain_aura_pct"] == pytest.approx(0.20)
    assert c["progresses_from"] == "ConstitutionThonThienMaTam"
    reqs = c["special_requirements"]
    assert "requires_thon_thien_ma_tam" in reqs
    assert "requires_skill_ma_than_cong" in reqs


def test_ma_than_cong_skill_registered():
    s = registry.get_skill("SkillMaThanCong_R9")
    assert s is not None
    assert s["realm"] == 9
    assert s["scroll_grade"] == 4
    assert s["element"] == "am"
