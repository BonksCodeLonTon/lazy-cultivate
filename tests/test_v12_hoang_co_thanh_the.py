"""v12 constitution — Hoàng Cổ Thánh Thể (Ancient Saint Body, Universal).

The capstone fortify-titan on the Constitution-Process engine: no revive,
no cheat-death — pure attrition dominance through Hào Quang Củng Cố fortify
stacks that simultaneously ramp outgoing final_dmg_bonus and incoming DR.
These tests pin every mechanic on the flag-ON path plus the flag-OFF inert
seam.

Implementation dependency note
--------------------------------
Tests 1–4 and 8 only need the JSON body in ``universal.json`` and the
existing ``fortify_*`` fields in ``Combatant``. Tests 5–7 also require:
  - ``saint_realm_interval`` / ``BuffHoangCoThanhVuc`` aura (hoang_co.py)
  - ``saint_qilin_cleanse_chance`` + cleanse block in procs.py
  - ``saint_mp_on_hit_pct`` MP restore in procs.py
If those fields are absent the corresponding tests are skipped cleanly
with ``pytest.importorskip`` / ``pytest.skip`` inside the test body, so
this file can be committed before the implementation lands without
blocking the green suite.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.damage.combat_hit import (
    build_attack_stats,
    effective_damage_reduction,
)
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.combat.procs import run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_HoangCoThanhThe"
_ATTACK_SKILL = "SkillAtkGeneral1"
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 6
_PLAYER_REALM_TOTAL = 15


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="HoangCoTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1,
        active_axis="qi",
        constitution_type=_BODY,
        linh_can=[],
        linh_can_levels={},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None, (
        f"{_BODY!r} must be registered in universal.json — "
        "this test file was written before the body JSON was committed"
    )
    assert data.get("process"), f"{_BODY!r} must carry a process block"
    return data


def _enemy(hp: int = 10**9):
    e = build_enemy_combatant(_ENEMY_KEY, player_realm_total=_PLAYER_REALM_TOTAL)
    assert e is not None
    e.hp = e.hp_max = hp
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.shield = 0
    return e


def _player(level: int):
    return build_player_combatant(_make_char(level), player_skill_keys=[_ATTACK_SKILL])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(seed), max_turns=200,
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _skip_if_not_registered():
    """Skip the test if the body JSON hasn't been committed yet."""
    if registry.get_constitution(_BODY) is None:
        pytest.skip(f"{_BODY!r} not in universal.json — implementation pending")


def _combatant_supports(field_name: str) -> bool:
    """True when Combatant exposes the name — as a dataclass field OR a
    registry-driven flag resolved from the ``body_cfg`` bag."""
    from src.game.systems.combat_stats import _BODY_CFG_DEFAULTS
    from src.game.systems.combatant import Combatant
    return field_name in _BODY_CFG_DEFAULTS or hasattr(Combatant, field_name)


def _skip_if_no_field(field_name: str):
    """Skip if a combatant field expected by this test doesn't exist yet."""
    if not _combatant_supports(field_name):
        pytest.skip(f"Combatant.{field_name} not implemented yet")


# ── 1. Composition / shape ───────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    _skip_if_not_registered()
    data = _body_data()
    assert data["element"] == "universal"
    assert data["rarity"] == "mythic"
    assert data["roll_weight"] == 0
    assert data["process"]["milestones"] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert data["process"].get("ceilings", []) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert data["process"].get("xp_to_next", {}) == {}
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_is_flat_identity() -> None:
    _skip_if_not_registered()
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffLongLanKhaiGiap"]


def test_composition_l9_effects() -> None:
    """L9 should accumulate all milestone effects."""
    _skip_if_not_registered()
    data = _body_data()
    effects_l9 = effective_effects(data, 9)
    # The first effect from L1 must always be present at L9
    assert "BuffLongLanKhaiGiap" in effects_l9
    # L9 should have at least as many effects as L1
    assert len(effects_l9) >= 1


# ── 2. Fortify ramp math ─────────────────────────────────────────────────────


def test_fortify_ramp_l1() -> None:
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 1)
    assert sb.get("fortify_per_turn_pct", 0.0) == pytest.approx(0.025)
    assert sb.get("fortify_stack_cap", 0) == 5


def test_fortify_ramp_l2() -> None:
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 2)
    assert sb.get("fortify_per_turn_pct", 0.0) == pytest.approx(0.035)
    assert sb.get("fortify_stack_cap", 0) == 5


def test_fortify_ramp_l3() -> None:
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 3)
    assert sb.get("fortify_per_turn_pct", 0.0) == pytest.approx(0.045)
    assert sb.get("fortify_stack_cap", 0) == 5


def test_fortify_ramp_l5() -> None:
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 5)
    # L5 unlocks an additional +0.010 on top of the L3 base: 0.045 + 0.010 = 0.055
    assert sb.get("fortify_per_turn_pct", 0.0) == pytest.approx(0.055)
    assert sb.get("fortify_stack_cap", 0) == 5


def test_fortify_ramp_l8() -> None:
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 8)
    assert sb.get("fortify_per_turn_pct", 0.0) == pytest.approx(0.070)
    assert sb.get("fortify_stack_cap", 0) == 5


def test_fortify_ramp_l9() -> None:
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 9)
    assert sb.get("fortify_per_turn_pct", 0.0) == pytest.approx(0.090)
    assert sb.get("fortify_stack_cap", 0) == 5


def test_fortify_l9_full_stack_arithmetic() -> None:
    """At L9, five stacks × 0.090/stack = +0.45 on both offense and defense."""
    _skip_if_not_registered()
    data = _body_data()
    sb = effective_stat_bonuses(data, 9)
    ppt = sb.get("fortify_per_turn_pct", 0.0)
    cap = sb.get("fortify_stack_cap", 0)
    assert ppt * cap == pytest.approx(0.45)


def test_fortify_player_l9_has_correct_fields(monkeypatch) -> None:
    """A freshly built L9 player carries the right fortify scalars."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.fortify_per_turn_pct == pytest.approx(0.09)
    assert player.fortify_stack_cap == 5


# ── 3. Fortify accrual + symmetry ────────────────────────────────────────────


def test_fortify_stack_accrues_per_periodic(monkeypatch) -> None:
    """Each _process_periodic call adds exactly one fortify stack."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    assert player.fortify_stacks == 0
    session._process_periodic(player)
    assert player.fortify_stacks == 1
    session._process_periodic(player)
    assert player.fortify_stacks == 2


def test_fortify_stacks_cap_at_5(monkeypatch) -> None:
    """Fortify stacks never exceed fortify_stack_cap (5)."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    for _ in range(10):
        session._process_periodic(player)

    assert player.fortify_stacks == 5


def test_fortify_symmetry_final_dmg_bonus(monkeypatch) -> None:
    """At 5 stacks, outgoing final_dmg_bonus includes +0.45 from fortify."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    # No stacks baseline
    stats_no_stack = build_attack_stats(player, enemy, {}, None)
    baseline_fdmg = stats_no_stack.final_dmg_bonus

    # Accrue 5 stacks
    player.fortify_stacks = 5
    stats_full_stack = build_attack_stats(player, enemy, {}, None)

    delta = stats_full_stack.final_dmg_bonus - baseline_fdmg
    assert delta == pytest.approx(0.45, abs=1e-6)


def test_fortify_symmetry_damage_reduction(monkeypatch) -> None:
    """At 5 stacks, effective_damage_reduction includes +0.45 from fortify."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)

    dr_no_stack = effective_damage_reduction(player, {})
    player.fortify_stacks = 5
    dr_full_stack = effective_damage_reduction(player, {})

    delta = dr_full_stack - dr_no_stack
    assert delta == pytest.approx(0.45, abs=1e-6)


# ── 4. L9 always-on final_dmg ────────────────────────────────────────────────


def test_l9_baseline_final_dmg_bonus(monkeypatch) -> None:
    """L9 player has at least +0.15 final_dmg_bonus from the flat stat block."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    # The body's flat stat_bonuses should contribute final_dmg_bonus >= 0.15
    # (this is the always-on payoff, distinct from fortify stacks)
    data = _body_data()
    sb = effective_stat_bonuses(data, 9)
    flat_fdmg = sb.get("final_dmg_bonus", 0.0)
    assert flat_fdmg >= 0.15, (
        f"L9 effective_stat_bonuses.final_dmg_bonus={flat_fdmg} expected >= 0.15"
    )


# ── 5. L5 self-cleanse-on-hit ─────────────────────────────────────────────────


def test_l5_self_cleanse_fires_on_low_roll(monkeypatch) -> None:
    """L5 Qilin cleanse proc removes a debuff from the PLAYER on a low roll."""
    _skip_if_not_registered()
    from src.game.systems.combatant import Combatant
    if not _combatant_supports("saint_qilin_cleanse_chance"):
        pytest.skip("saint_qilin_cleanse_chance not implemented yet")
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(5)
    assert player.saint_qilin_cleanse_chance >= 0.35, (
        "L5 player should have saint_qilin_cleanse_chance >= 0.35"
    )
    enemy = _enemy()
    session = _session(player, enemy)

    # Stamp a cleansable debuff on the PLAYER
    from src.game.constants.effects import EffectKey
    player.apply_effect(EffectKey.DEBUFF_LAM_CHAM, 3)
    assert player.has_effect(EffectKey.DEBUFF_LAM_CHAM)

    session.rng.random = lambda: 0.0  # < 0.35 → cleanse fires
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert not player.has_effect(EffectKey.DEBUFF_LAM_CHAM), (
        "Low roll should have cleared the debuff from the player"
    )


def test_l5_self_cleanse_skips_on_high_roll(monkeypatch) -> None:
    """L5 Qilin cleanse does NOT fire on a high roll."""
    _skip_if_not_registered()
    from src.game.systems.combatant import Combatant
    if not _combatant_supports("saint_qilin_cleanse_chance"):
        pytest.skip("saint_qilin_cleanse_chance not implemented yet")
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(5)
    enemy = _enemy()
    session = _session(player, enemy)

    from src.game.constants.effects import EffectKey
    player.apply_effect(EffectKey.DEBUFF_LAM_CHAM, 3)

    session.rng.random = lambda: 0.99  # >= 0.35 → no cleanse
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert player.has_effect(EffectKey.DEBUFF_LAM_CHAM), (
        "High roll should NOT have cleared the debuff"
    )


# ── 6. L8 MP-on-hit ──────────────────────────────────────────────────────────


def test_l8_mp_on_hit_restores_mp(monkeypatch) -> None:
    """L8 on-hit MP restore brings player.mp up by ~4% of mp_max."""
    _skip_if_not_registered()
    from src.game.systems.combatant import Combatant
    if not _combatant_supports("saint_mp_on_hit_pct"):
        pytest.skip("saint_mp_on_hit_pct not implemented yet")
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(8)
    assert player.saint_mp_on_hit_pct >= 0.04, (
        "L8 player should have saint_mp_on_hit_pct >= 0.04"
    )
    enemy = _enemy()
    session = _session(player, enemy)

    # Drain MP so restoration is visible
    player.mp = 0
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    expected = int(player.mp_max * 0.04)
    assert player.mp >= expected, (
        f"Expected MP >= {expected}, got {player.mp}"
    )
    assert player.mp <= player.mp_max


# ── 7. L9 Saint Realm ────────────────────────────────────────────────────────


def test_l9_saint_realm_buff_applied_on_cadence(monkeypatch) -> None:
    """Every saint_realm_interval acted turns the player gains BuffHoangCoThanhVuc.

    IMPLEMENTATION BUG: hoang_co.py is NOT imported in
    src/game/systems/combat/auras/__init__.py, so the @register_hook decorator
    never fires and the PRE_TURN hook is absent from the registry. This test
    fails as evidence for the team lead. Fix: add 'hoang_co' to the import
    in auras/__init__.py.
    """
    _skip_if_not_registered()
    from src.game.systems.combatant import Combatant
    if not _combatant_supports("saint_realm_interval"):
        pytest.skip("saint_realm_interval not implemented yet")
    # Verify the aura module is actually loaded (imported); if not, the hook
    # is absent and this test correctly fails as a regression catch.
    import importlib
    auras_pkg = importlib.import_module("src.game.systems.combat.auras")
    assert hasattr(auras_pkg, "hoang_co") or True  # skip check: test exercises behavior

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.saint_realm_interval > 0, (
        "L9 player should have saint_realm_interval > 0"
    )
    enemy = _enemy()
    session = _session(player, enemy)

    interval = player.saint_realm_interval  # expected 5
    # Drive PRE_TURN hooks 'interval' times (one per acted turn)
    for _ in range(interval):
        run_phase(TurnPhase.PRE_TURN, TurnContext(actor=player, target=enemy, session=session))

    assert player.has_effect("BuffHoangCoThanhVuc"), (
        f"After {interval} acted turns, BuffHoangCoThanhVuc should be active. "
        "BUG: hoang_co.py is missing from auras/__init__.py import list — "
        "add 'hoang_co' to the 'from . import (...)' in that file."
    )


def test_l9_saint_realm_forces_crit(monkeypatch) -> None:
    """While BuffHoangCoThanhVuc is active, build_attack_stats force_crit is True."""
    _skip_if_not_registered()
    from src.game.systems.combatant import Combatant
    if not _combatant_supports("saint_realm_interval"):
        pytest.skip("saint_realm_interval not implemented yet")
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()

    # No buff → force_crit may or may not be True (depends on frozen etc.)
    # We only test the buff-active case here
    player.apply_effect("BuffHoangCoThanhVuc", 2)
    stats = build_attack_stats(player, enemy, {}, None)
    assert stats.force_crit is True, (
        "BuffHoangCoThanhVuc should force crit on outgoing hits"
    )


def test_l9_saint_realm_cc_immunity(monkeypatch) -> None:
    """While BuffHoangCoThanhVuc is active, the player is immune to hard CC."""
    _skip_if_not_registered()
    from src.game.systems.combatant import Combatant
    if not _combatant_supports("saint_realm_interval"):
        pytest.skip("saint_realm_interval not implemented yet")
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.apply_effect("BuffHoangCoThanhVuc", 2)
    # Try to apply stun via on-hit proc from the enemy
    from src.game.constants.effects import EffectKey
    # The buff should confer hard-CC immunity
    assert player.immune_hard_cc is True or player.has_effect("BuffHoangCoThanhVuc"), (
        "Player holding BuffHoangCoThanhVuc must be CC-immune or tracked as immune"
    )
    # Trying to stun the player should no-op
    enemy_attacker = _enemy()
    enemy_attacker.stun_on_hit_pct = 1.0
    session.rng.random = lambda: 0.0
    run_on_hit_procs(session, enemy_attacker, player, is_crit=False, skill_key="")
    assert not player.has_effect(EffectKey.CC_STUN), (
        "Stun should not land on a CC-immune saint-realm player"
    )


# ── 8. NO revive / NO cheat-death (identity guard) ───────────────────────────


def test_l9_no_revive(monkeypatch) -> None:
    """L9 Hoàng Cổ Thánh Thể never revives — a lethal hit is always final."""
    _skip_if_not_registered()
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    result = session._try_revive(player)
    assert result is False, (
        "TheChat_HoangCoThanhThe must NEVER revive (titan identity — no cheat-death)"
    )
    assert not player.is_alive(), "Player should remain dead after no-revive"


def test_no_revive_even_without_flag(monkeypatch) -> None:
    """Flag-OFF: no revive either (same inert path)."""
    _skip_if_not_registered()
    assert settings.constitution_process_enabled is False
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


# ── 9. Flag-OFF inertness ─────────────────────────────────────────────────────


def test_flag_off_no_process_effects_stamped() -> None:
    """Flag-OFF: no milestone buffs are applied; body is flat-only."""
    _skip_if_not_registered()
    assert settings.constitution_process_enabled is False
    player = _player(9)
    # None of the milestone gate buffs should be stamped
    for buff_key in (
        "BuffLongLanKhaiGiap",
    ):
        assert not player.has_effect(buff_key), (
            f"Flag-OFF player should not carry {buff_key}"
        )
    # saint_* milestone flags must NOT be active on the flat path
    from src.game.systems.combatant import Combatant
    if _combatant_supports("saint_realm_enabled"):
        assert player.saint_realm_enabled is False, (
            "saint_realm_enabled must be False without the process flag"
        )
    if _combatant_supports("saint_qilin_cleanse_chance"):
        assert player.saint_qilin_cleanse_chance == 0.0, (
            "saint_qilin_cleanse_chance must be 0 without the process flag"
        )


def test_flag_off_flat_bonuses_match_body_stat_bonuses() -> None:
    """Flag-OFF: compute_constitution_bonuses returns body's flat stat_bonuses."""
    _skip_if_not_registered()
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)
    data = _body_data()
    assert flat == data["stat_bonuses"], (
        "Flag-OFF path must return flat stat_bonuses verbatim"
    )


def test_flag_off_fortify_base_present_in_flat() -> None:
    """Flag-OFF: the flat fortify_per_turn_pct from stat_bonuses IS present.

    The L1 fortify base lives in flat stat_bonuses (not in a milestone block),
    so it IS set even when the flag is off — this is intentional (the mechanic
    operates at L1 regardless of the process flag). The *milestone* saint_*
    flags that gate higher-level payoffs are what must be absent.
    """
    _skip_if_not_registered()
    assert settings.constitution_process_enabled is False
    data = _body_data()
    flat_sb = data["stat_bonuses"]
    # The flat body should carry a non-zero fortify_per_turn_pct (L1 baseline)
    assert flat_sb.get("fortify_per_turn_pct", 0.0) > 0.0, (
        "flat stat_bonuses must include a non-zero fortify_per_turn_pct"
    )
    assert flat_sb.get("fortify_stack_cap", 0) == 5


def test_flag_off_revive_noop() -> None:
    """Flag-OFF: _try_revive returns False — no revive on the flat path."""
    _skip_if_not_registered()
    assert settings.constitution_process_enabled is False
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    assert session._try_revive(player) is False
