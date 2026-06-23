"""Coverage for the five opt-in skill mechanics in ``skill_extras``.

Each test pins one mechanic in isolation: multi-hit, charge bonus, chain
skill, auto-cast on stacks, and summons. The combat session is exercised
end-to-end (cast through the skill registry / casting pipeline) so any
breakage in the wiring shows up here, not at runtime.
"""
from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from src.data.registry import registry
from src.game.systems.combat import CombatSession
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.skill_extras import (
    apply_charge_bonus, cast_chain_skill, consume_auto_cast_stacks,
    find_auto_cast_skill, maybe_spawn_summon, tick_summons,
)
from src.game.systems.combatant import Combatant


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def make_combatant(key: str = "p", **overrides) -> Combatant:
    defaults = dict(
        name=key, hp=10_000, hp_max=10_000, mp=2_000, mp_max=2_000,
        spd=10, element=None, atk=200, matk=200, def_stat=20,
    )
    defaults.update(overrides)
    return Combatant(key=key, **defaults)


def make_session(player: Combatant, enemy: Combatant, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed), max_turns=5,
    )


# ── 1. Multi-hit ─────────────────────────────────────────────────────────────

def test_hit_count_runs_damage_path_n_times():
    actor = make_combatant("a")
    target = make_combatant("t", hp=10_000, hp_max=10_000)
    skill = {
        "key": "TestMulti", "vi": "Triple Strike", "element": None,
        "attack_type": "physical", "base_dmg": 200, "mp_cost": 0,
        "cooldown": 1, "dmg_scale": {"atk": 0.5, "matk": 0.0},
        "hit_count": 3, "effects": [], "effect_chances": {},
    }
    session = make_session(actor, target, seed=42)
    hp_before = target.hp
    cast_skill(session, actor, target, "TestMulti", skill, mp_cost=0)
    # 3 hits should deal noticeably more than a single ~200-dmg hit.
    dealt = hp_before - target.hp
    assert dealt > 400, f"hit_count=3 should land 3 hits; only dealt {dealt}"
    # Three "dùng *Triple Strike*" log lines (1 main + 2 follow-ups)
    main_lines = [l for l in session.log if "Triple Strike" in l]
    assert len(main_lines) >= 3


# ── 2. Charge bonus ──────────────────────────────────────────────────────────

def test_charge_bonus_fires_every_n_casts():
    actor = make_combatant("a")
    target = make_combatant("t", hp=100_000, hp_max=100_000)
    skill_data = {"charge_bonus": {"every": 3, "amount": 500}}
    session = make_session(actor, target)
    # Cast 1 + 2: counter increments, no detonation
    apply_charge_bonus(session, actor, target, "K", skill_data, base_dmg_dealt=100)
    apply_charge_bonus(session, actor, target, "K", skill_data, base_dmg_dealt=100)
    assert target.hp == 100_000
    assert actor.skill_cast_counts["K"] == 2
    # Cast 3: detonates +500, counter resets
    apply_charge_bonus(session, actor, target, "K", skill_data, base_dmg_dealt=100)
    assert target.hp == 99_500
    assert actor.skill_cast_counts["K"] == 0


def test_charge_bonus_isolated_per_skill():
    actor = make_combatant("a")
    target = make_combatant("t", hp=100_000, hp_max=100_000)
    spec = {"charge_bonus": {"every": 2, "amount": 100}}
    session = make_session(actor, target)
    apply_charge_bonus(session, actor, target, "Skill1", spec, 0)
    apply_charge_bonus(session, actor, target, "Skill2", spec, 0)
    # Each skill counts independently — neither has charged yet.
    assert target.hp == 100_000
    assert actor.skill_cast_counts == {"Skill1": 1, "Skill2": 1}


# ── 3. Chain skill ───────────────────────────────────────────────────────────

def test_chain_skill_casts_follow_up_at_scaled_damage():
    actor = make_combatant("a")
    # Chain target must be in actor.skill_keys per the equipping gate.
    actor.skill_keys = ["Parent", "Followup"]
    target = make_combatant("t", hp=100_000, hp_max=100_000)
    fake_chain = {
        "key": "Followup", "vi": "Followup", "element": None,
        "attack_type": "physical", "base_dmg": 1000, "mp_cost": 0,
        "cooldown": 1, "dmg_scale": {"atk": 0.0, "matk": 0.0},
        "effects": [], "effect_chances": {},
    }
    skill_data = {"chain_skill": {"key": "Followup", "pct": 0.5}}
    session = make_session(actor, target)
    with patch.object(registry, "get_skill", return_value=fake_chain):
        cast_chain_skill(session, actor, target, "Parent", skill_data)
    # Followup logged with 50% damage tag
    assert any("Followup" in line and "50%" in line for line in session.log), session.log


def test_chain_skill_one_step_only():
    """A chained skill must not chain again (no infinite recursion)."""
    actor = make_combatant("a")
    actor.skill_keys = ["Parent", "LoopChain"]
    target = make_combatant("t", hp=50_000, hp_max=50_000)
    # The chain target has its OWN chain spec — it should be stripped.
    looping_chain = {
        "key": "LoopChain", "vi": "LoopChain", "element": None,
        "attack_type": "physical", "base_dmg": 100, "mp_cost": 0,
        "cooldown": 1, "dmg_scale": {"atk": 0.0, "matk": 0.0},
        "chain_skill": {"key": "LoopChain", "pct": 1.0},
        "effects": [], "effect_chances": {},
    }
    skill_data = {"chain_skill": {"key": "LoopChain", "pct": 1.0}}
    session = make_session(actor, target)
    with patch.object(registry, "get_skill", return_value=looping_chain):
        cast_chain_skill(session, actor, target, "Parent", skill_data)
    # Exactly one chain announcement (🔗 prefix); the inner cast wouldn't
    # re-announce because its chain_skill was stripped.
    chain_announcements = [l for l in session.log if "🔗" in l]
    assert len(chain_announcements) == 1, (
        f"expected 1 chain step, got {len(chain_announcements)}"
    )


def test_chain_skill_skipped_when_target_not_equipped():
    """Equipping gate — chain target must be in actor.skill_keys."""
    actor = make_combatant("a")
    actor.skill_keys = ["Parent"]  # NO "Followup" — chain target missing
    target = make_combatant("t", hp=100_000, hp_max=100_000)
    fake_chain = {
        "key": "Followup", "vi": "Followup", "element": None,
        "attack_type": "physical", "base_dmg": 1000, "mp_cost": 0,
        "cooldown": 1, "dmg_scale": {"atk": 0.0, "matk": 0.0},
        "effects": [], "effect_chances": {},
    }
    skill_data = {"chain_skill": {"key": "Followup", "pct": 0.5}}
    session = make_session(actor, target)
    with patch.object(registry, "get_skill", return_value=fake_chain):
        cast_chain_skill(session, actor, target, "Parent", skill_data)
    # Chain did NOT fire — no 🔗 log line, no damage to target.
    assert not any("🔗" in line for line in session.log), session.log
    assert target.hp == target.hp_max


# ── 4. Auto-cast on stacks ───────────────────────────────────────────────────

def test_auto_cast_finds_skill_when_threshold_met():
    actor = make_combatant("a")
    actor.skill_keys = ["FinisherKey"]
    target = make_combatant("t")
    target.burn_stacks = 5
    finisher = {
        "auto_cast_on_stacks": {"stack": "burn", "threshold": 5, "consume": True},
    }
    with patch.object(registry, "get_skill", return_value=finisher):
        key, data = find_auto_cast_skill(actor, target)
    assert key == "FinisherKey"
    assert data is finisher


def test_auto_cast_returns_none_below_threshold():
    actor = make_combatant("a")
    actor.skill_keys = ["FinisherKey"]
    target = make_combatant("t")
    target.burn_stacks = 2
    finisher = {"auto_cast_on_stacks": {"stack": "burn", "threshold": 5}}
    with patch.object(registry, "get_skill", return_value=finisher):
        key, data = find_auto_cast_skill(actor, target)
    assert key is None and data is None


def test_auto_cast_consume_clears_stacks():
    actor = make_combatant("a")
    target = make_combatant("t")
    target.burn_stacks = 8
    skill_data = {"auto_cast_on_stacks": {"stack": "burn", "consume": True}}
    session = make_session(actor, target)
    consume_auto_cast_stacks(session, actor, target, skill_data)
    assert target.burn_stacks == 0


def test_auto_cast_picks_highest_threshold():
    """Two qualifying skills → the one with the higher threshold wins."""
    actor = make_combatant("a")
    actor.skill_keys = ["Low", "High"]
    target = make_combatant("t")
    target.burn_stacks = 9
    skills = {
        "Low":  {"auto_cast_on_stacks": {"stack": "burn", "threshold": 3}},
        "High": {"auto_cast_on_stacks": {"stack": "burn", "threshold": 7}},
    }
    with patch.object(registry, "get_skill", side_effect=lambda k: skills[k]):
        key, _ = find_auto_cast_skill(actor, target)
    assert key == "High"


# ── 5. Summon ────────────────────────────────────────────────────────────────

def test_summon_spec_appends_summon_with_expected_dmg():
    actor = make_combatant("a", matk=400)
    skill_data = {
        "summon_spec": {
            "vi": "Kiếm Hồn", "emoji": "🗡️", "element": "kim",
            "dmg_pct_of_matk": 0.5, "turns": 3,
        },
    }
    session = make_session(actor, make_combatant("t"))
    maybe_spawn_summon(session, actor, skill_data)
    assert len(actor.summons) == 1
    s = actor.summons[0]
    assert s["dmg"] == 200  # 400 * 0.5
    assert s["turns"] == 3
    assert s["element"] == "kim"


def test_tick_summons_damages_target_and_decrements_turns():
    actor = make_combatant("a")
    target = make_combatant("t", hp=10_000, hp_max=10_000)
    actor.summons = [{
        "vi": "Test", "emoji": "✨", "element": None,
        "dmg": 250, "turns": 2,
    }]
    session = make_session(actor, target)
    tick_summons(session, actor, target)
    assert target.hp == 9_750
    assert actor.summons[0]["turns"] == 1
    # Second tick — last turn, summon should expire after.
    tick_summons(session, actor, target)
    assert target.hp == 9_500
    assert actor.summons == []


def test_summon_aura_contributes_stat_bonus_via_modifiers():
    """A single summon with an aura contributes its buff's stat_bonus to
    ``get_combat_modifiers`` while alive — no apply_effect needed."""
    from src.game.engine.effects import get_combat_modifiers

    actor = make_combatant("a")
    skill_data = {
        "summon_spec": {
            "vi": "Light Spirit", "emoji": "🌟", "element": "quang",
            "dmg": 100, "turns": 3,
            "aura_buff": {"key": "BuffSinhCo"},
        },
    }
    session = make_session(actor, make_combatant("t"))
    maybe_spawn_summon(session, actor, skill_data)
    mods = get_combat_modifiers(actor)
    assert mods.get("hp_regen_pct") == pytest.approx(0.05)


def test_same_key_aura_does_not_stack_with_itself():
    """Three summons all carrying ``BuffSinhCo`` only grant ONE copy —
    duplicate-key auras don't compound; build diversity is the gate."""
    from src.game.engine.effects import get_combat_modifiers

    actor = make_combatant("a")
    spec = {
        "summon_spec": {
            "vi": "Light Spirit", "emoji": "🌟", "element": "quang",
            "dmg": 100, "turns": 5,
            "aura_buff": {"key": "BuffSinhCo"},
        },
    }
    session = make_session(actor, make_combatant("t"))
    maybe_spawn_summon(session, actor, spec)
    maybe_spawn_summon(session, actor, spec)
    maybe_spawn_summon(session, actor, spec)
    assert len(actor.summons) == 3
    mods = get_combat_modifiers(actor)
    # Three same-key auras → still only one copy of the buff.
    assert mods.get("hp_regen_pct") == pytest.approx(0.05)


def test_summon_aura_stacks_with_different_buffs():
    """Different aura keys add into different stat slots independently."""
    from src.game.engine.effects import get_combat_modifiers

    actor = make_combatant("a")
    target = make_combatant("t")
    session = make_session(actor, target)
    moc_summon = {"summon_spec": {
        "vi": "Vine", "emoji": "🌿", "element": "moc",
        "dmg": 50, "turns": 3,
        "aura_buff": {"key": "BuffSinhCo"},
    }}
    quang_summon = {"summon_spec": {
        "vi": "Light", "emoji": "🌟", "element": "quang",
        "dmg": 50, "turns": 3,
        "aura_buff": {"key": "BuffNhietTinh"},
    }}
    maybe_spawn_summon(session, actor, moc_summon)
    maybe_spawn_summon(session, actor, quang_summon)
    mods = get_combat_modifiers(actor)
    assert mods.get("hp_regen_pct") == pytest.approx(0.05)        # from BuffSinhCo
    assert mods.get("final_dmg_bonus") == pytest.approx(0.20)     # from BuffNhietTinh
    assert mods.get("crit_dmg_rating") == pytest.approx(200.0)    # from BuffNhietTinh


def test_summon_aura_drops_when_all_summons_expire():
    """Aura bonus disappears the moment the last contributing summon dies."""
    from src.game.engine.effects import get_combat_modifiers

    actor = make_combatant("a")
    target = make_combatant("t", hp=10_000, hp_max=10_000)
    actor.summons = [{
        "vi": "Spirit", "emoji": "✨", "element": None,
        "dmg": 100, "turns": 1,
        "aura_buff_key": "BuffSinhCo",
    }]
    session = make_session(actor, target)
    assert get_combat_modifiers(actor).get("hp_regen_pct") == pytest.approx(0.05)
    tick_summons(session, actor, target)
    assert actor.summons == []
    assert get_combat_modifiers(actor).get("hp_regen_pct", 0.0) == 0.0
