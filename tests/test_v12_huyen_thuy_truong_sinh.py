"""v12 constitution — Huyền Thủy Trường Sinh Thể (Dark Water Longevity Body, Thủy).

The first Thủy body on the Constitution-Process engine: a tidal counter-puncher
that banks damage taken into an isolated Tide reservoir, freezes attackers,
shatters frozen enemies for burst, and floods the opponent on a cadence that
scales with fight length. These tests pin every mechanic on the flag-ON path and
the flag-OFF inert seam, plus the reservoir-isolation invariant. Phase-0
byte-identity is guarded separately by ``test_constitution_process_guard.py``.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
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
from src.game.systems.combat.procs import apply_reactive_damage, run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_HuyenThuyTruongSinh"
_ATTACK_SKILL = "SkillAtkThuy1"
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 5
_PLAYER_REALM_TOTAL = 15


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="HuyenThuyTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=5, qi_level=1,
        formation_realm=5, formation_level=1,
        active_axis="qi",
        constitution_type=_BODY,
        linh_can=["thuy"],
        linh_can_levels={"thuy": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None, f"{_BODY!r} must be registered"
    assert data.get("process"), f"{_BODY!r} must carry a process block"
    return data


def _enemy(hp: int = 10**9) -> "object":
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


# ── 1. Composition (pure seam) ──────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "thuy"
    assert data["rarity"] == "legendary"
    assert data["roll_weight"] == 0
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffNapTrieuKho"]


def test_composition_milestone_and_growth_math() -> None:
    data = _body_data()

    l3 = effective_stat_bonuses(data, 3)
    assert l3["hp_pct"] == pytest.approx(0.10 + 0.05 + 0.006 * 2)
    assert l3["thuy_retaliate_freeze_chance"] == 0.45

    l6 = effective_stat_bonuses(data, 6)
    assert l6["thuy_shatter_tide_pct"] == 0.30

    l9 = effective_stat_bonuses(data, 9)
    assert l9["hp_pct"] == pytest.approx(0.10 + (0.05 + 0.05 + 0.06) + 0.006 * 8)
    assert l9["shield_regen_pct"] == pytest.approx(0.05 + 0.02 + 0.002 * 8)
    assert l9["shield_max_pct"] == pytest.approx(0.12 + 0.004 * 8)
    assert l9["thuy_tide_intake_pct"] == 0.25
    assert l9["thuy_reservoir_cap_matk_scale"] == 6.0
    assert l9["thuy_tidal_flood_enabled"] is True
    assert l9["thuy_tidal_flood_interval"] == 4
    assert l9["thuy_tidal_release_pct"] == 0.70
    assert l9["thuy_tidal_depth_mult_cap"] == 3.0
    assert l9["thuy_tidal_refill_pct"] == 0.30
    assert effective_effects(data, 9) == [
        "BuffNapTrieuKho", "BuffHanThuyDongBang",
        "BuffBangToaiQuyet", "BuffHoiTrieuNoHai",
    ]


# ── 2. L1 Nạp Triều Khố — intake on damage taken, capped at 6×matk ──────────


def test_l1_intake_banks_25pct_capped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    assert player.has_effect("BuffNapTrieuKho")
    assert player.thuy_tide_intake_pct == pytest.approx(0.25)
    enemy = _enemy()
    session = _session(player, enemy)

    # apply_reactive_damage(session, ATTACKER, DEFENDER, dmg): the holder is the
    # defender taking the hit.
    apply_reactive_damage(session, enemy, player, 1000)
    assert player.thuy_intake_reservoir == 250  # 25% of 1000
    apply_reactive_damage(session, enemy, player, 2000)
    assert player.thuy_intake_reservoir == 250 + 500

    # Cap at 6 × matk.
    cap = 6 * player.matk
    for _ in range(50):
        apply_reactive_damage(session, enemy, player, 1_000_000)
    assert player.thuy_intake_reservoir == cap


def test_l1_intake_inert_without_buff() -> None:
    # No flag ON → no BuffNapTrieuKho → reservoir never fills.
    assert settings.constitution_process_enabled is False
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    apply_reactive_damage(session, enemy, player, 5000)
    assert player.thuy_intake_reservoir == 0


# ── 3. L3 Hàn Thủy Đóng Băng — retaliate-freeze the attacker ────────────────


def test_l3_freezes_attacker_on_forced_roll(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    assert player.thuy_retaliate_freeze_chance == pytest.approx(0.45)
    enemy = _enemy()
    session = _session(player, enemy)

    session.rng.random = lambda: 0.0  # < 0.45 → freeze lands
    apply_reactive_damage(session, enemy, player, 500)
    assert enemy.has_effect("DebuffDongBang")


def test_l3_freeze_denied_on_high_roll(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # >= 0.45 → no freeze
    apply_reactive_damage(session, enemy, player, 500)
    assert not enemy.has_effect("DebuffDongBang")


def test_l3_freeze_respects_hard_cc_immunity(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    enemy.immune_hard_cc = True
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would freeze, but immunity shrugs it
    apply_reactive_damage(session, enemy, player, 500)
    assert not enemy.has_effect("DebuffDongBang")


# ── 4. L6 Băng Toái Quyết — shatter a frozen target ─────────────────────────


def test_l6_shatter_frozen_target_drains_reservoir_and_breaks_ice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    assert player.thuy_shatter_tide_pct == pytest.approx(0.30)
    player.thuy_intake_reservoir = 10_000
    enemy = _enemy()
    enemy.apply_effect("DebuffDongBang", 2)
    session = _session(player, enemy)

    hp_before = enemy.hp
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    dealt = hp_before - enemy.hp

    # 30% of 10_000 = 3_000 released → reservoir drops to 7_000; the released
    # base is amped by (1 + final_dmg_bonus) like the existing tide/overheal
    # discharges, so the DEALT damage is at least the released base.
    assert player.thuy_intake_reservoir == 7_000
    assert dealt >= 3_000
    # The shatter breaks the ice.
    assert not enemy.has_effect("DebuffDongBang")


def test_l6_no_shatter_vs_non_frozen(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.thuy_intake_reservoir = 10_000
    enemy = _enemy()  # not frozen
    session = _session(player, enemy)
    hp_before = enemy.hp
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.hp == hp_before
    assert player.thuy_intake_reservoir == 10_000  # untouched


def test_l6_no_shatter_with_empty_reservoir(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.thuy_intake_reservoir = 0
    enemy = _enemy()
    enemy.apply_effect("DebuffDongBang", 2)
    session = _session(player, enemy)
    hp_before = enemy.hp
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.hp == hp_before
    # Empty reservoir → the freeze is NOT consumed (no shatter happened).
    assert enemy.has_effect("DebuffDongBang")


# ── 5. L9 Hồi Triều Nộ Hải — periodic tidal flood, depth-scaled + refill ────


def test_l9_tidal_flood_fires_on_cadence_with_depth_and_refill(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    assert player.thuy_tidal_flood_enabled is True
    enemy = _enemy()
    session = _session(player, enemy)

    # Off-cadence turn → no flood.
    player.thuy_intake_reservoir = 10_000
    session.turn = 3
    hp_before = enemy.hp
    session._process_periodic(player)
    assert enemy.hp == hp_before
    assert player.thuy_intake_reservoir == 10_000

    # Turn 4 (cadence) → flood; depth = 1 + 4×0.05 = 1.2; released base =
    # 10_000 × 0.70 × 1.2 = 8_400; reservoir refills to 30% (= 3_000).
    session.turn = 4
    hp_before = enemy.hp
    session._process_periodic(player)
    dealt = hp_before - enemy.hp
    assert dealt >= 8_400  # at least the released base (amped by final_dmg_bonus)
    assert player.thuy_intake_reservoir == 3_000  # 30% refill, not a full reset


def test_l9_tidal_depth_cap_binds_at_high_turn(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.final_dmg_bonus = 0.0  # isolate the depth math from the dmg amp
    enemy = _enemy()
    session = _session(player, enemy)

    # Turn 100 → raw depth would be 1 + 100×0.05 = 6.0, capped at 3.0.
    player.thuy_intake_reservoir = 10_000
    session.turn = 100  # divisible by interval 4? 100 % 4 == 0 → fires
    hp_before = enemy.hp
    session._process_periodic(player)
    dealt = hp_before - enemy.hp
    # released = 10_000 × 0.70 × 3.0 (capped) = 21_000; with final_dmg_bonus 0
    # and 0 resist, dealt == 21_000 exactly.
    assert dealt == 21_000


def test_l9_flood_inert_with_empty_reservoir(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.thuy_intake_reservoir = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.turn = 4
    hp_before = enemy.hp
    session._process_periodic(player)
    assert enemy.hp == hp_before


# ── 6. Reservoir isolation — body pool ≠ skill pool ─────────────────────────


def test_reservoir_isolated_from_skill_thuy_tide(monkeypatch) -> None:
    """The body's ``thuy_intake_reservoir`` and the skill
    ``SkillAtkThuyTrieuTichLang``'s ``thuy_tide`` are distinct fields that never
    cross-feed: filling one leaves the other untouched."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)

    assert player.thuy_intake_reservoir == 0
    assert player.thuy_tide == 0

    # Bank into the BODY reservoir via damage taken.
    apply_reactive_damage(session, enemy, player, 4000)
    assert player.thuy_intake_reservoir == 1000  # 25% of 4000
    # The skill's pool is untouched.
    assert player.thuy_tide == 0

    # And the reverse: poking the skill pool doesn't move the body reservoir.
    player.thuy_tide = 7777
    assert player.thuy_intake_reservoir == 1000


# ── 7. Inertness — flag OFF leaves the body flat, no buffs, paths no-op ──────


def test_flag_off_no_effects_stamped() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    # No process EFFECTS are stamped while dormant — the proc/hook gates all
    # require these buffs, so their absence is what keeps every path inert.
    for key in (
        "BuffNapTrieuKho", "BuffHanThuyDongBang",
        "BuffBangToaiQuyet", "BuffHoiTrieuNoHai",
    ):
        assert not player.has_effect(key)
    # Milestone-only config flags (L3/L6/L9) stay at their inert defaults — the
    # flat path ignores the level so no milestone bonuses land.
    assert player.thuy_retaliate_freeze_chance == 0.0
    assert player.thuy_shatter_tide_pct == 0.0
    assert player.thuy_tidal_flood_enabled is False
    # The L1 ``thuy_tide_intake_pct`` / cap-scale live in the body's FLAT
    # stat_bonuses (not a milestone), so they ARE set on the flat path — but
    # they're harmless without the L1 ``BuffNapTrieuKho`` gate the intake proc
    # also requires (asserted absent above). ``test_flag_off_all_paths_noop``
    # pins that behavioural inertness.
    assert player.thuy_tide_intake_pct == pytest.approx(0.25)


def test_flag_off_resolves_to_flat_stat_bonuses() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)
    data = _body_data()
    assert flat == data["stat_bonuses"]
    assert "thuy_retaliate_freeze_chance" not in flat
    assert "thuy_tidal_flood_enabled" not in flat


def test_flag_off_all_paths_noop() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # would force any roll that's reached

    # 5a/5b: damage taken → no intake, no freeze.
    apply_reactive_damage(session, enemy, player, 5000)
    assert player.thuy_intake_reservoir == 0
    assert not enemy.has_effect("DebuffDongBang")

    # 5c: hitting a frozen target → no shatter (no buff / zero flag).
    enemy.apply_effect("DebuffDongBang", 2)
    player.thuy_intake_reservoir = 10_000  # pretend a stale pool
    hp_before = enemy.hp
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=_ATTACK_SKILL)
    assert enemy.hp == hp_before
    assert enemy.has_effect("DebuffDongBang")

    # 5d: periodic flood → no-op.
    session.turn = 4
    hp_before = enemy.hp
    session._process_periodic(player)
    assert enemy.hp == hp_before
