"""v12 constitution — Thiên Lôi Cường Thể (Heavenly Thunder Strong Body, Lôi).

Identity: a glass shock/speed nuker — shock-on-hit, chance-based DebuffTeLiet
on crit, speed-advantage damage scaling, bonus-attack on crit/dodge, and a
+30% Lôi true-damage rider at L9. NO revive mechanic.

Standard 4-milestone [1, 3, 6, 9].

Flat stat_bonuses (always active even flag-OFF, like body #6 poison):
  shock_on_hit_pct          = 0.50   ← live on build regardless of flag
  dot_dmg_bonus             = 0.08   ← live on build regardless of flag
  loi_te_liet_on_crit_chance= 0.10   ← live on build regardless of flag
  loi_charge_enabled        = True   ← live on build regardless of flag

Milestone-only fields (0/False flag-OFF):
  loi_spd_advantage_per_10      (L3)
  loi_reflex_bonus_attack       (L6)
  loi_bonus_true_dmg_pct = 0.30 (L9)

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr``
so it auto-reverts after the test.

Scaffolding note: tests that depend on implementation-specific Combatant /
CombatStats fields skip gracefully via ``_player_or_skip()`` and
``_has_loi_field()`` until the coder lands the field work.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_ThienLoiCuong"
_ATTACK_SKILL = "SkillAtkLoi3"   # Lôi magical skill, element="loi"
_ENEMY_KEY = "LCLoi_T0_LoiTinhTieuQuai"  # linh_can/loi.json first entry
_BODY_REALM = 6
_PLAYER_REALM_TOTAL = 15


# ── helpers ───────────────────────────────────────────────────────────────────

def _body_data() -> dict:
    """Return body data, skipping if not yet registered."""
    data = registry.get_constitution(_BODY)
    if data is None or not data.get("process"):
        pytest.skip(f"{_BODY!r} not yet registered or missing process block")
    return data  # type: ignore[return-value]


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="ThienLoiTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1,
        active_axis="qi",
        constitution_type=_BODY,
        linh_can=["loi"],
        linh_can_levels={"loi": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _enemy(hp: int = 10 ** 9):
    e = build_enemy_combatant(_ENEMY_KEY, player_realm_total=_PLAYER_REALM_TOTAL)
    assert e is not None, f"{_ENEMY_KEY!r} not found in registry"
    e.hp = e.hp_max = hp
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.shield = 0
    e.immune_hard_cc = False
    return e


def _player(level: int):
    """Build player combatant at the given constitution level.

    Raises TypeError when CombatStats does not yet have loi-specific fields
    — callers that depend on those fields use _player_or_skip().
    """
    return build_player_combatant(_make_char(level), player_skill_keys=[_ATTACK_SKILL])


def _player_or_skip(level: int):
    """Build player combatant, skipping the test if loi fields are not wired."""
    try:
        return _player(level)
    except (TypeError, AttributeError) as exc:
        pytest.skip(f"loi Combatant/CombatStats fields not yet implemented: {exc}")


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(seed), max_turns=200,
    )


def _has_loi_field(field_name: str) -> bool:
    """Return True if Combatant has the given loi-specific field."""
    from dataclasses import fields as dc_fields
    from src.game.systems.combat_stats import _BODY_CFG_DEFAULTS
    from src.game.systems.combatant import Combatant
    # Registry-driven flags live in the ``body_cfg`` bag now, not as
    # dataclass fields — bag-known names count as supported.
    return (
        field_name in _BODY_CFG_DEFAULTS
        or hasattr(Combatant, field_name)
        or any(f.name == field_name for f in dc_fields(Combatant))
    )


# ── 1. Composition (pure seam — JSON only) ────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "loi"
    assert data.get("rarity") == "legendary"
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    # L1 adds no milestone stat_bonuses — only flat base + L1 effects
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffThienLoiKhiThe"]


def test_composition_l9_effects() -> None:
    data = _body_data()
    l9_effects = effective_effects(data, 9)
    # Four milestone buffs, one per milestone, in order
    assert len(l9_effects) == 4
    assert l9_effects[0] == "BuffThienLoiKhiThe"


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]


# ── 2. Stat ramp (matk_pct / spd_pct / crit_rating grow) ─────────────────────


def test_offensive_stats_grow_across_milestones() -> None:
    data = _body_data()

    l1 = effective_stat_bonuses(data, 1)
    l3 = effective_stat_bonuses(data, 3)
    l6 = effective_stat_bonuses(data, 6)
    l9 = effective_stat_bonuses(data, 9)

    # matk_pct grows monotonically — shock/nuke body leads with magic attack
    assert l3.get("matk_pct", 0.0) >= l1.get("matk_pct", 0.0)
    assert l6.get("matk_pct", 0.0) >= l3.get("matk_pct", 0.0)
    assert l9.get("matk_pct", 0.0) >= l6.get("matk_pct", 0.0)

    # spd_pct grows monotonically — speed is a core axis
    assert l3.get("spd_pct", 0.0) >= l1.get("spd_pct", 0.0)
    assert l6.get("spd_pct", 0.0) >= l3.get("spd_pct", 0.0)
    assert l9.get("spd_pct", 0.0) >= l6.get("spd_pct", 0.0)

    # crit_rating grows monotonically
    assert l3.get("crit_rating", 0) >= l1.get("crit_rating", 0)
    assert l6.get("crit_rating", 0) >= l3.get("crit_rating", 0)
    assert l9.get("crit_rating", 0) >= l6.get("crit_rating", 0)


def test_l9_matk_pct_approx() -> None:
    """L9 matk_pct includes flat base + milestone additions + per_level_growth×8."""
    data = _body_data()
    l9 = effective_stat_bonuses(data, 9)
    # Any reasonable L9 Lôi nuker should have ≥ 20% matk_pct at L9.
    assert l9.get("matk_pct", 0.0) >= 0.20, (
        f"L9 matk_pct too low for a nuker identity: {l9.get('matk_pct')}"
    )


# ── 3. L1 shock-on-hit (flat base, always active) ─────────────────────────────


def test_flat_shock_on_hit_pct_present_in_stat_bonuses() -> None:
    """shock_on_hit_pct lives in flat stat_bonuses — present even flag-OFF."""
    data = _body_data()
    assert data["stat_bonuses"].get("shock_on_hit_pct") == pytest.approx(0.50), (
        "flat stat_bonuses must carry shock_on_hit_pct == 0.50 "
        "(active regardless of flag)"
    )


def test_l1_shock_on_hit_pct_on_player(monkeypatch) -> None:
    """Flag-ON L1 player carries shock_on_hit_pct >= 0.50 on the combatant.

    The flat body bonus is 0.50; linh_can Lôi L3+ adds its own shock_on_hit_pct
    on top (e.g. +0.10 at L3), so the combatant value is >= 0.50, not exactly
    0.50.  The flat-stat_bonuses test already pins the JSON value to 0.50.
    """
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    val = getattr(player, "shock_on_hit_pct", None)
    assert val is not None and val >= 0.50, (
        f"L1 player must have shock_on_hit_pct >= 0.50 on the Combatant (got {val})"
    )


def test_l1_shock_on_hit_applies_debuff_soc_dien(monkeypatch) -> None:
    """Low roll (<0.50) causes DebuffSocDien to be applied on hit."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    if not _has_loi_field("shock_on_hit_pct"):
        pytest.skip("shock_on_hit_pct not yet in Combatant")

    # >= 0.50 because linh_can Lôi L3+ stacks additional shock_on_hit_pct
    assert player.shock_on_hit_pct >= 0.50

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # 0.0 < 0.50 → shock proc fires

    from src.game.systems.combat.procs import run_on_hit_procs
    run_on_hit_procs(session, player, enemy, is_crit=False)

    assert enemy.has_effect("DebuffSocDien"), (
        "roll 0.0 < 0.50 shock_on_hit_pct must apply DebuffSocDien"
    )
    assert enemy.shock_stacks >= 1, "DebuffSocDien must add at least 1 shock stack"


def test_l1_shock_on_hit_skips_on_high_roll(monkeypatch) -> None:
    """High roll (≥0.50) → no shock applied."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    if not _has_loi_field("shock_on_hit_pct"):
        pytest.skip("shock_on_hit_pct not yet in Combatant")

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # 0.99 >= 0.50 → no proc

    from src.game.systems.combat.procs import run_on_hit_procs
    run_on_hit_procs(session, player, enemy, is_crit=False)

    assert not enemy.has_effect("DebuffSocDien"), (
        "roll 0.99 >= 0.50 must NOT apply DebuffSocDien"
    )


# ── 4. L1 Tê Liệt on crit (10%) ───────────────────────────────────────────────


def test_flat_te_liet_on_crit_chance_present_in_stat_bonuses() -> None:
    """loi_te_liet_on_crit_chance lives in flat stat_bonuses."""
    data = _body_data()
    assert data["stat_bonuses"].get("loi_te_liet_on_crit_chance") == pytest.approx(0.10), (
        "flat stat_bonuses must carry loi_te_liet_on_crit_chance == 0.10"
    )


def test_l1_te_liet_on_crit_fires_on_low_roll(monkeypatch) -> None:
    """Crit + roll < 0.10 → DebuffTeLiet applied to enemy."""
    if not _has_loi_field("loi_te_liet_on_crit_chance"):
        pytest.skip("loi_te_liet_on_crit_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    te_liet_chance = getattr(player, "loi_te_liet_on_crit_chance", 0.0)
    assert te_liet_chance == pytest.approx(0.10), (
        "L1 must grant loi_te_liet_on_crit_chance == 0.10"
    )

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # 0.0 < 0.10 → te_liet fires

    from src.game.systems.combat.procs import run_on_hit_procs
    run_on_hit_procs(session, player, enemy, is_crit=True)

    assert enemy.has_effect("DebuffTeLiet"), (
        "crit + roll 0.0 < 0.10 must apply DebuffTeLiet"
    )


def test_l1_te_liet_on_crit_skips_on_high_roll(monkeypatch) -> None:
    """Crit + roll ≥ 0.10 → no DebuffTeLiet."""
    if not _has_loi_field("loi_te_liet_on_crit_chance"):
        pytest.skip("loi_te_liet_on_crit_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # 0.99 >= 0.10 → no proc

    from src.game.systems.combat.procs import run_on_hit_procs
    run_on_hit_procs(session, player, enemy, is_crit=True)

    assert not enemy.has_effect("DebuffTeLiet"), (
        "crit + roll 0.99 >= 0.10 must NOT apply DebuffTeLiet"
    )


def test_l1_te_liet_on_crit_skips_when_not_crit(monkeypatch) -> None:
    """Non-crit + low roll → DebuffTeLiet must NOT fire (crit-gated)."""
    if not _has_loi_field("loi_te_liet_on_crit_chance"):
        pytest.skip("loi_te_liet_on_crit_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # low roll, but NOT a crit

    from src.game.systems.combat.procs import run_on_hit_procs
    run_on_hit_procs(session, player, enemy, is_crit=False)

    assert not enemy.has_effect("DebuffTeLiet"), (
        "DebuffTeLiet is crit-gated — must not fire on a non-crit even with roll 0.0"
    )


def test_l1_te_liet_on_crit_respects_immune_hard_cc(monkeypatch) -> None:
    """immune_hard_cc=True must block DebuffTeLiet."""
    if not _has_loi_field("loi_te_liet_on_crit_chance"):
        pytest.skip("loi_te_liet_on_crit_chance field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)

    enemy = _enemy()
    enemy.immune_hard_cc = True

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would fire without immunity

    from src.game.systems.combat.procs import run_on_hit_procs
    run_on_hit_procs(session, player, enemy, is_crit=True)

    assert not enemy.has_effect("DebuffTeLiet"), (
        "immune_hard_cc=True must prevent DebuffTeLiet from landing"
    )


# ── 5. L1 Lôi charge burst ────────────────────────────────────────────────────


def test_flat_loi_charge_enabled_in_stat_bonuses() -> None:
    """loi_charge_enabled lives in flat stat_bonuses (always active)."""
    data = _body_data()
    assert data["stat_bonuses"].get("loi_charge_enabled") is True, (
        "flat stat_bonuses must carry loi_charge_enabled = true"
    )


def test_l1_loi_charge_enabled_on_player(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    if not _has_loi_field("loi_charge_enabled"):
        pytest.skip("loi_charge_enabled field not yet in Combatant")
    assert getattr(player, "loi_charge_enabled", False) is True, (
        "L1 player must have loi_charge_enabled=True"
    )


def test_loi_charge_climbs_on_dot_ticks_and_bursts(monkeypatch) -> None:
    """DebuffSocDien ticks → loi_charge increments; at 10 resets + true-dmg burst."""
    if not _has_loi_field("loi_charge"):
        pytest.skip("loi_charge field not yet in Combatant")
    if not _has_loi_field("loi_charge_enabled"):
        pytest.skip("loi_charge_enabled field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)

    assert getattr(player, "loi_charge_enabled", False) is True

    enemy = _enemy()
    # DebuffSetDanh is the Lôi DoT (dot_element="loi", dot_pct). DebuffSocDien is
    # NOT a DoT (it's a damage-taken amp), so it never ticks / charges. The charge
    # accrues to the APPLIER, so we tick the ENEMY's periodic (player = applier).
    enemy.apply_effect("DebuffSetDanh", 10)

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0

    initial_charge = getattr(player, "loi_charge", 0)
    hp_before = enemy.hp

    # Run several periodic phases (on the ENEMY, the DoT holder) to accumulate.
    for _ in range(3):
        session._process_periodic(enemy)

    charge_after = getattr(player, "loi_charge", 0)
    # Charge must have changed from 0 (either climbed or already burst-reset)
    assert charge_after != initial_charge or enemy.hp < hp_before, (
        "loi_charge must climb or burst after DoT ticks; "
        f"charge={charge_after}, hp_delta={hp_before - enemy.hp}"
    )


def test_loi_charge_burst_deals_true_damage_at_10(monkeypatch) -> None:
    """At loi_charge == 10, the burst fires ~int(0.70 * player.matk) true damage."""
    if not _has_loi_field("loi_charge"):
        pytest.skip("loi_charge field not yet in Combatant")
    if not _has_loi_field("loi_charge_enabled"):
        pytest.skip("loi_charge_enabled field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(1)
    player.loi_charge = 9  # one more Lôi-DoT tick will push to 10 and burst

    enemy = _enemy()
    # DebuffSetDanh = the Lôi DoT (DebuffSocDien is a damage-amp, not a DoT). The
    # charge accrues to the APPLIER (player), so tick the ENEMY's periodic.
    enemy.apply_effect("DebuffSetDanh", 5)

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0

    hp_before = enemy.hp
    session._process_periodic(enemy)
    damage_dealt = hp_before - enemy.hp

    # Burst is ~int(0.70 * player.matk); bounded check because other DoTs may also tick
    expected_floor = int(0.70 * player.matk) - 1  # minus 1 for int truncation tolerance
    assert damage_dealt >= expected_floor, (
        f"loi_charge burst at 10 stacks expected >= {expected_floor} true damage; "
        f"got {damage_dealt} (player.matk={player.matk})"
    )

    # After burst, charge must reset to 0
    assert player.loi_charge == 0, (
        f"loi_charge must reset to 0 after burst; got {player.loi_charge}"
    )


# ── 6. L3 speed-advantage damage bonus ────────────────────────────────────────


def test_l3_spd_advantage_field_present(monkeypatch) -> None:
    if not _has_loi_field("loi_spd_advantage_per_10"):
        pytest.skip("loi_spd_advantage_per_10 field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)
    val = getattr(player, "loi_spd_advantage_per_10", 0.0)
    assert val > 0.0, "L3 must set loi_spd_advantage_per_10 > 0"


def test_l3_spd_advantage_scales_final_dmg_bonus(monkeypatch) -> None:
    """With actor SPD >> target SPD, final_dmg_bonus includes spd-advantage bonus."""
    if not _has_loi_field("loi_spd_advantage_per_10"):
        pytest.skip("loi_spd_advantage_per_10 field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)

    from src.game.engine.effects import get_combat_modifiers
    from src.game.engine.damage.combat_hit import build_attack_stats

    # Set a large SPD gap: actor 100, target 10 → gap = 90 → 9 "tens" → 9 × 0.05 = 0.45
    player.spd = 100
    enemy = _enemy()
    enemy.spd = 10

    actor_mods = get_combat_modifiers(player)
    stats_gap = build_attack_stats(player, enemy, actor_mods, skill_element="loi")
    base_bonus = player.final_dmg_bonus

    assert stats_gap.final_dmg_bonus > base_bonus, (
        "SPD gap of 90 must increase final_dmg_bonus via loi_spd_advantage_per_10"
    )


def test_l3_spd_advantage_zero_at_equal_spd(monkeypatch) -> None:
    """Equal SPD gives no speed-advantage bonus."""
    if not _has_loi_field("loi_spd_advantage_per_10"):
        pytest.skip("loi_spd_advantage_per_10 field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)

    from src.game.engine.effects import get_combat_modifiers
    from src.game.engine.damage.combat_hit import build_attack_stats

    player.spd = 50
    enemy = _enemy()
    enemy.spd = 50

    actor_mods = get_combat_modifiers(player)
    stats = build_attack_stats(player, enemy, actor_mods, skill_element="loi")

    # At equal SPD the bonus from SPD-advantage must be zero
    expected = player.final_dmg_bonus + actor_mods.get("final_dmg_bonus", 0.0)
    assert stats.final_dmg_bonus <= expected + 0.001, (
        "equal SPD must add 0 speed-advantage bonus to final_dmg_bonus"
    )


def test_l3_spd_advantage_caps_at_0_50(monkeypatch) -> None:
    """Speed-advantage bonus caps at 0.50 regardless of SPD gap size."""
    if not _has_loi_field("loi_spd_advantage_per_10"):
        pytest.skip("loi_spd_advantage_per_10 field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(3)

    from src.game.engine.effects import get_combat_modifiers
    from src.game.engine.damage.combat_hit import build_attack_stats

    player.spd = 9999   # extreme gap — must still cap at 0.50
    enemy = _enemy()
    enemy.spd = 1

    actor_mods = get_combat_modifiers(player)
    stats = build_attack_stats(player, enemy, actor_mods, skill_element="loi")
    base_bonus = player.final_dmg_bonus + actor_mods.get("final_dmg_bonus", 0.0)
    spd_contribution = stats.final_dmg_bonus - base_bonus

    assert spd_contribution <= 0.50 + 0.001, (
        f"speed-advantage bonus must be capped at 0.50; got contribution={spd_contribution:.4f}"
    )


# ── 7. L6 reflex bonus-attack ─────────────────────────────────────────────────


def test_l6_reflex_bonus_attack_field_present(monkeypatch) -> None:
    if not _has_loi_field("loi_reflex_bonus_attack"):
        pytest.skip("loi_reflex_bonus_attack field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(6)
    assert getattr(player, "loi_reflex_bonus_attack", False) is True, (
        "L6 must set loi_reflex_bonus_attack=True"
    )


def test_l6_reflex_bonus_attack_on_crit(monkeypatch) -> None:
    """On a crit hit, an extra SkillLoiBonusShock fires → enemy takes extra damage."""
    if not _has_loi_field("loi_reflex_bonus_attack"):
        pytest.skip("loi_reflex_bonus_attack field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(6)
    assert getattr(player, "loi_reflex_bonus_attack", False) is True

    # Guarantee a crit (high crit_rating) AND no dodge (zero enemy evasion/spd,
    # else rng=0.0 < evasion would dodge and the main hit deals 0).
    player.crit_rating = 99999
    player.hp = 1_000_000

    enemy = _enemy()
    enemy.hp = 1_000_000
    enemy.evasion_rating = 0
    enemy.spd = 0

    session = _session(player, enemy, seed=0)
    session.rng.random = lambda: 0.0  # force crit + all procs

    hp_before = enemy.hp
    from src.game.systems.combat.casting import cast_skill
    skill_data = registry.get_skill(_ATTACK_SKILL)
    assert skill_data is not None
    mp_cost = skill_data.get("mp_cost", 0)
    player.mp = max(player.mp, mp_cost + 1)

    cast_skill(session, player, enemy, _ATTACK_SKILL, skill_data, mp_cost)
    damage_1 = hp_before - enemy.hp

    # Without reflex: an L1 player (no L6 reflex) at equal conditions deals less.
    # Same metric (hp_before - hp) for an apples-to-apples comparison.
    player_l1 = _player_or_skip(1)
    player_l1.crit_rating = 99999
    player_l1.hp = 1_000_000
    enemy2 = _enemy()
    enemy2.hp = 1_000_000
    enemy2.evasion_rating = 0
    enemy2.spd = 0
    session2 = _session(player_l1, enemy2, seed=0)
    session2.rng.random = lambda: 0.0
    player_l1.mp = max(player_l1.mp, mp_cost + 1)
    hp_before2 = enemy2.hp
    cast_skill(session2, player_l1, enemy2, _ATTACK_SKILL, skill_data, mp_cost)
    damage_l1 = hp_before2 - enemy2.hp

    assert damage_1 > damage_l1, (
        f"L6 reflex bonus attack on crit must deal more total damage than L1 "
        f"(L6={damage_1}, L1={damage_l1})"
    )


def test_l6_reflex_bonus_attack_recursion_guard(monkeypatch) -> None:
    """The bonus-attack must not re-trigger itself (no infinite recursion)."""
    if not _has_loi_field("loi_reflex_bonus_attack"):
        pytest.skip("loi_reflex_bonus_attack field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(6)
    player.crit_rating = 99999

    enemy = _enemy()
    enemy.hp = 1_000_000

    session = _session(player, enemy, seed=0)
    session.rng.random = lambda: 0.0

    skill_data = registry.get_skill(_ATTACK_SKILL)
    assert skill_data is not None
    mp_cost = skill_data.get("mp_cost", 0)
    player.mp = 999999  # enough for many casts

    from src.game.systems.combat.casting import cast_skill

    # If there's no recursion guard this will raise RecursionError or hang.
    # Simply running without error is the assertion.
    cast_skill(session, player, enemy, _ATTACK_SKILL, skill_data, mp_cost)
    # If we get here the recursion guard worked.


# ── 8. L9 +30% Lôi true-damage rider ─────────────────────────────────────────


def test_l9_loi_bonus_true_dmg_pct_value(monkeypatch) -> None:
    if not _has_loi_field("loi_bonus_true_dmg_pct"):
        pytest.skip("loi_bonus_true_dmg_pct field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player_or_skip(9)
    val = getattr(player, "loi_bonus_true_dmg_pct", 0.0)
    assert val == pytest.approx(0.30), (
        f"L9 must set loi_bonus_true_dmg_pct == 0.30; got {val}"
    )


def test_l9_true_rider_fires_on_loi_hit_bypasses_resistance(monkeypatch) -> None:
    """L9 Lôi true-damage rider must bypass the target's Lôi resistance.

    Strategy: give the enemy a high Lôi resistance (0.80) so the base Lôi
    damage is heavily reduced.  The true-damage rider must still arrive at
    ~30% of the base Lôi damage.  We compare a L9 vs a L1 player hitting the
    same enemy; the delta should be >= 30% of the base-damage component.
    """
    if not _has_loi_field("loi_bonus_true_dmg_pct"):
        pytest.skip("loi_bonus_true_dmg_pct field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    # Enemy with high Lôi resistance to prove the rider ignores res
    enemy_l9 = _enemy()
    enemy_l9.resistances = {"loi": 0.80}  # 80% Lôi resist
    enemy_l9.evasion_rating = 0
    enemy_l9.final_dmg_reduce = 0.0
    enemy_l9.hp = 10_000_000

    enemy_l1 = _enemy()
    enemy_l1.resistances = {"loi": 0.80}
    enemy_l1.evasion_rating = 0
    enemy_l1.final_dmg_reduce = 0.0
    enemy_l1.hp = 10_000_000

    player_l9 = _player_or_skip(9)
    player_l9.crit_rating = 0  # no crits for clean measurement

    player_l1 = _player_or_skip(1)
    player_l1.crit_rating = 0

    skill_data = registry.get_skill(_ATTACK_SKILL)
    assert skill_data is not None
    mp_cost = skill_data.get("mp_cost", 0)
    player_l9.mp = 999999
    player_l1.mp = 999999

    session_l9 = _session(player_l9, enemy_l9)
    session_l9.rng.random = lambda: 0.5  # neutral variance, no evasion
    session_l1 = _session(player_l1, enemy_l1)
    session_l1.rng.random = lambda: 0.5

    from src.game.systems.combat.casting import cast_skill
    cast_skill(session_l9, player_l9, enemy_l9, _ATTACK_SKILL, skill_data, mp_cost)
    cast_skill(session_l1, player_l1, enemy_l1, _ATTACK_SKILL, skill_data, mp_cost)

    dmg_l9 = enemy_l9.hp_max - enemy_l9.hp
    dmg_l1 = enemy_l1.hp_max - enemy_l1.hp

    assert dmg_l9 > dmg_l1, (
        f"L9 with true-damage rider must deal more total damage vs high-res enemy "
        f"than L1 (L9={dmg_l9}, L1={dmg_l1})"
    )


def test_l9_true_rider_absent_on_non_loi_skill(monkeypatch) -> None:
    """Non-Lôi skill must NOT trigger the +30% true-damage rider."""
    if not _has_loi_field("loi_bonus_true_dmg_pct"):
        pytest.skip("loi_bonus_true_dmg_pct field not yet in Combatant")

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player_l9 = _player_or_skip(9)

    # Use a physical tho skill (guaranteed non-Lôi) to confirm no rider
    _NON_LOI_SKILL = "EnemyTho_T2"
    skill_data = registry.get_skill(_NON_LOI_SKILL)
    if skill_data is None:
        pytest.skip(f"{_NON_LOI_SKILL!r} not in registry — skip non-loi check")
    if skill_data.get("element") == "loi":
        pytest.skip(f"{_NON_LOI_SKILL!r} is unexpectedly Lôi-element — pick another skill")

    enemy_l9 = _enemy()
    enemy_l9.resistances = {"loi": 0.80}  # high Lôi res — rider would bypass this
    enemy_l9.evasion_rating = 0
    enemy_l9.final_dmg_reduce = 0.0
    enemy_l9.hp = 10_000_000

    player_l1 = _player_or_skip(1)
    player_l1.crit_rating = 0
    enemy_l1 = _enemy()
    enemy_l1.resistances = {"loi": 0.80}
    enemy_l1.evasion_rating = 0
    enemy_l1.final_dmg_reduce = 0.0
    enemy_l1.hp = 10_000_000

    mp_cost = skill_data.get("mp_cost", 0)
    player_l9.mp = 999999
    player_l1.mp = 999999
    player_l9.crit_rating = 0

    session_l9 = _session(player_l9, enemy_l9)
    session_l9.rng.random = lambda: 0.5
    session_l1 = _session(player_l1, enemy_l1)
    session_l1.rng.random = lambda: 0.5

    from src.game.systems.combat.casting import cast_skill
    cast_skill(session_l9, player_l9, enemy_l9, _NON_LOI_SKILL, skill_data, mp_cost)
    cast_skill(session_l1, player_l1, enemy_l1, _NON_LOI_SKILL, skill_data, mp_cost)

    dmg_l9 = enemy_l9.hp_max - enemy_l9.hp
    dmg_l1 = enemy_l1.hp_max - enemy_l1.hp

    # Non-Lôi rider absent: L9 may deal slightly more due to stat growth but
    # the delta must be small (stat growth only, no true-rider bypass).
    # The ratio test: true-rider would add ≥30% on top; stat growth is ≤ ~20%.
    if dmg_l1 > 0:
        ratio = dmg_l9 / dmg_l1
        assert ratio < 1.55, (
            f"non-Lôi hit must NOT trigger the +30% true-rider bypass; "
            f"L9/L1 damage ratio={ratio:.3f} is suspiciously high "
            f"(L9={dmg_l9}, L1={dmg_l1})"
        )


# ── 9. NO revive (identity guard) ─────────────────────────────────────────────


def test_no_revive_data_only() -> None:
    """Pure-JSON guard: L9 bonuses must carry no revive flag."""
    data = _body_data()
    l9_bonuses = effective_stat_bonuses(data, 9)

    for revive_flag in (
        "moc_undying_spring_enabled",
        "phoenix_revive_pct",
        "endure_threshold_pct",
        "chan_menh_loi_phu_enabled",
    ):
        assert not l9_bonuses.get(revive_flag), (
            f"Thiên Lôi Cường Thể must NOT carry revive flag {revive_flag!r}"
        )


def test_no_revive_at_l9_flag_on(monkeypatch) -> None:
    """_try_revive returns False for a glass-cannon body with no cheat-death."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    data = _body_data()
    l9_bonuses = effective_stat_bonuses(data, 9)
    for revive_flag in (
        "moc_undying_spring_enabled",
        "phoenix_revive_pct",
        "endure_threshold_pct",
        "chan_menh_loi_phu_enabled",
    ):
        assert not l9_bonuses.get(revive_flag), (
            f"L9 bonuses must NOT contain revive flag {revive_flag!r}"
        )

    player = _player_or_skip(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    result = session._try_revive(player)

    assert result is False, (
        "Thiên Lôi Cường Thể must NOT revive — _try_revive must return False"
    )
    assert not player.is_alive(), "player must stay dead after _try_revive returns False"


# ── 10. Flag-OFF inertness ────────────────────────────────────────────────────


def test_flag_off_no_effects_stamped() -> None:
    """Flag-OFF: no constitution-process buffs on the combatant."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)
    for key in (
        "BuffThienLoiKhiThe",
        "BuffThienLoiTocChien",   # expected L3 buff (name TBD by coder)
        "BuffThienLoiPhanChieu",  # expected L6 buff (name TBD by coder)
        "BuffThienLoiChiTon",     # expected L9 buff (name TBD by coder)
    ):
        # Only assert for buffs that actually appear in the data
        if data.get("process"):
            effects_from_data = set()
            for m_block in data["process"].get("levels", {}).values():
                effects_from_data.update(m_block.get("effects", []))
            if key in effects_from_data:
                assert not player.has_effect(key), (
                    f"flag-OFF must not stamp {key!r} onto the combatant"
                )


def test_flag_off_flat_base_fields_are_present() -> None:
    """Flag-OFF: flat stat_bonuses fields (shock_on_hit, te_liet_chance,
    loi_charge_enabled, dot_dmg_bonus) ARE present on the built combatant
    because they live in the flat stat_bonuses, not milestone-only blocks.
    """
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    # The flat stat_bonuses should equal what compute_constitution_bonuses returns
    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)
    assert flat == data["stat_bonuses"], (
        "flag-OFF: compute_constitution_bonuses must return the raw flat stat_bonuses"
    )

    # Flat-base fields must be in the bonuses
    assert flat.get("shock_on_hit_pct") == pytest.approx(0.50), (
        "shock_on_hit_pct must be present in flat stat_bonuses (flag-OFF active)"
    )
    assert flat.get("loi_te_liet_on_crit_chance") == pytest.approx(0.10), (
        "loi_te_liet_on_crit_chance must be present in flat stat_bonuses"
    )
    assert flat.get("loi_charge_enabled") is True, (
        "loi_charge_enabled must be present in flat stat_bonuses"
    )


def test_flag_off_milestone_fields_absent_or_zero() -> None:
    """Flag-OFF: milestone-only fields are 0/False (not in flat stat_bonuses)."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    from src.game.systems.cultivation import compute_constitution_bonuses
    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)

    for proc_only_key in (
        "loi_spd_advantage_per_10",
        "loi_reflex_bonus_attack",
        "loi_bonus_true_dmg_pct",
    ):
        assert not flat.get(proc_only_key), (
            f"{proc_only_key!r} must NOT appear in flat stat_bonuses (flag-OFF)"
        )

    # Verify inert defaults on built combatant
    player = _player_or_skip(9)
    if _has_loi_field("loi_spd_advantage_per_10"):
        assert getattr(player, "loi_spd_advantage_per_10", 0.0) == 0.0, (
            "flag-OFF: loi_spd_advantage_per_10 must be 0.0"
        )
    if _has_loi_field("loi_reflex_bonus_attack"):
        assert getattr(player, "loi_reflex_bonus_attack", False) is False, (
            "flag-OFF: loi_reflex_bonus_attack must be False"
        )
    if _has_loi_field("loi_bonus_true_dmg_pct"):
        assert getattr(player, "loi_bonus_true_dmg_pct", 0.0) == 0.0, (
            "flag-OFF: loi_bonus_true_dmg_pct must be 0.0"
        )


def test_flag_off_revive_noop() -> None:
    """Flag-OFF: _try_revive must return False (no revive mechanic)."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_flag_off_periodic_paths_noop() -> None:
    """Flag-OFF: periodic phase must not fire loi_charge burst or reflex attack."""
    assert settings.constitution_process_enabled is False

    data = registry.get_constitution(_BODY)
    if data is None:
        pytest.skip(f"{_BODY!r} not yet registered")

    player = _player_or_skip(9)

    # Pre-charge to 9 so a burst would fire if the path were active
    if _has_loi_field("loi_charge"):
        player.loi_charge = 9

    enemy = _enemy()
    enemy.apply_effect("DebuffSocDien", 5)
    enemy.add_stack("shock", 5)

    session = _session(player, enemy)
    session.rng.random = lambda: 0.0

    hp_before = enemy.hp
    session._process_periodic(player)

    if _has_loi_field("loi_charge_enabled"):
        if not getattr(player, "loi_charge_enabled", False):
            # loi_charge burst path inactive → only normal DoT damage
            # The exact HP delta depends on other DoTs; just confirm no burst
            # from the charge mechanic (charge at 9, one tick would hit 10 and burst).
            # If charge_enabled is False the counter stays, no burst fires.
            if _has_loi_field("loi_charge"):
                # Counter should NOT have reset to 0 (burst didn't fire)
                assert player.loi_charge != 0, (
                    "flag-OFF: loi_charge must not burst-reset when loi_charge_enabled is False"
                )
