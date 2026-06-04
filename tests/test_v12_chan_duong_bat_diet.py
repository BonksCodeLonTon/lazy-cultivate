"""v12 constitution — Chân Dương Bất Diệt Thể (True Yang Inextinguishable Body, Hỏa).

The first Hỏa body on the Constitution-Process engine: a tanky fire-mage
phoenix. These tests pin every mechanic on the flag-ON path and the flag-OFF
inert seam. Phase-0 byte-identity is guarded separately by
``test_constitution_process_guard.py``.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import get_combat_modifiers
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.procs import run_on_hit_procs
from src.game.systems.constitution_process import (
    effective_effects,
    effective_stat_bonuses,
)
from src.utils.config import settings

_BODY = "TheChat_ChanDuongBatDiet"
_ATTACK_SKILL = "SkillAtkHoa1"  # resolved in fixture; fall back below
_ENEMY_KEY = "TinhKimTho"
_BODY_REALM = 5
_PLAYER_REALM_TOTAL = 15


def _attack_skill() -> str:
    """A registered single-hit Hỏa attack skill (so per-cast roll counting is
    unambiguous). Falls back to the 2-hit feeder if the single-hit key moves."""
    for key in ("SkillAtkHoa3", "SkillAtkHoa1", "SkillAtkHoaThieuDot2"):
        if registry.get_skill(key):
            return key
    raise AssertionError("no Hỏa attack skill registered")


def _make_char(level: int | None = None) -> Character:
    return Character(
        player_id=1,
        discord_id=1,
        name="ChanDuongTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=5, qi_level=1,
        formation_realm=5, formation_level=1,
        active_axis="qi",
        constitution_type=_BODY,
        linh_can=["hoa"],
        linh_can_levels={"hoa": 5},
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


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=list(player.skill_keys),
        rng=random.Random(seed), max_turns=50,
    )


def _build(level: int):
    skill = _attack_skill()
    return build_player_combatant(_make_char(level), player_skill_keys=[skill]), skill


# ── 1. Composition (pure seam) ──────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "hoa"
    assert data["rarity"] == "legendary"
    assert data["roll_weight"] == 0
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    assert data["process"]["levels"]["1"]["stat_bonuses"] == {}


def test_composition_l1_is_flat_identity() -> None:
    data = _body_data()
    assert effective_stat_bonuses(data, 1) == data["stat_bonuses"]
    assert effective_effects(data, 1) == ["BuffHoaKhiTuongSinh"]


def test_composition_milestone_and_growth_math() -> None:
    data = _body_data()

    l3 = effective_stat_bonuses(data, 3)
    assert l3["hp_pct"] == pytest.approx(0.08 + 0.04 + 0.005 * 2)
    assert l3["hp_regen_pct"] == pytest.approx(0.02 + 0.01 + 0.001 * 2)
    assert l3["matk_pct"] == pytest.approx(0.06 + 0.004 * 2)
    assert "BuffChanDuongNietBan" in effective_effects(data, 3)

    l6 = effective_stat_bonuses(data, 6)
    assert l6["hoa_burning_amp_chance"] == 0.50
    assert l6["hoa_burning_amp_pct"] == 0.30
    assert l6["element_dmg_bonus"]["hoa"] == 0.20

    l9 = effective_stat_bonuses(data, 9)
    assert l9["matk_pct"] == pytest.approx(0.06 + (0.05 + 0.07) + 0.004 * 8)
    assert l9["hp_pct"] == pytest.approx(0.08 + (0.04 + 0.04) + 0.005 * 8)
    assert l9["hoa_revive_upgraded"] is True
    assert l9["hoa_revive_charges"] == 3
    assert l9["hoa_revive_hp_pct_l9"] == 0.30
    assert effective_effects(data, 9) == [
        "BuffHoaKhiTuongSinh", "BuffChanDuongNietBan",
        "BuffChanHoaPhanThien", "BuffPhuongHoangTrongSinh",
    ]


# ── 2. L1 burn proc + burning crit-ramp ─────────────────────────────────────


def test_l1_burn_proc_applies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, skill = _build(1)
    # The body's flat burn_on_hit (0.60) plus any linh_can base — strongly > 0.
    assert player.burn_on_hit_pct >= 0.60
    assert player.has_effect("BuffHoaKhiTuongSinh")

    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # force the burn roll to land
    run_on_hit_procs(session, player, enemy, is_crit=False, skill_key=skill)
    assert enemy.has_effect("DebuffThieuDot")
    assert enemy.burn_stacks >= 1


def test_l1_burning_crit_ramps_and_resets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, _ = _build(1)
    enemy = _enemy()
    enemy.burn_stacks = 2  # target is burning
    session = _session(player, enemy)

    seen = []
    for _ in range(12):
        session._process_periodic(player)
        seen.append((player.burning_crit_stacks,
                     get_combat_modifiers(player).get("crit_rating", 0)))
    # +1 stack / +20 crit per round, capped at 10 stacks / +200 crit.
    assert seen[0] == (1, 20)
    assert seen[9] == (10, 200)
    assert seen[10] == (10, 200)  # held at cap

    # Burn drops → the ramp dissipates to 0 next tick.
    enemy.burn_stacks = 0
    session._process_periodic(player)
    assert player.burning_crit_stacks == 0
    assert get_combat_modifiers(player).get("crit_rating", 0) == 0


# ── 3. L3 Chân Dương Niết Bàn — single 20% revive ──────────────────────────


def test_l3_revives_once_then_dies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, _ = _build(3)
    assert player.has_effect("BuffChanDuongNietBan")
    enemy = _enemy()
    session = _session(player, enemy)

    # First lethal → revived at 20% HP, buff consumed.
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.hp == int(player.hp_max * 0.20)
    assert not player.has_effect("BuffChanDuongNietBan")

    # Second lethal → no revive left, death is final.
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


# ── 4. L6 Chân Hỏa Phần Thiên — chance-gated burning amp + Hỏa amp ──────────


def test_l6_burning_amp_fires_vs_burning_denies_vs_clean(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, skill = _build(6)
    assert player.hoa_burning_amp_chance == pytest.approx(0.50)
    assert player.hoa_burning_amp_pct == pytest.approx(0.30)

    skill_data = registry.get_skill(skill)

    # vs BURNING + a forced roll below the chance → amp fires.
    p_fire = build_player_combatant(_make_char(6), player_skill_keys=[skill])
    e_fire = _enemy()
    e_fire.burn_stacks = 3
    s_fire = _session(p_fire, e_fire)
    s_fire.rng.random = lambda: 0.0  # < 0.50 → fire (also forces no crit/etc.)
    before = len(s_fire.log)
    cast_skill(s_fire, p_fire, e_fire, skill, skill_data, 0)
    assert any("Chân Hỏa Phần Thiên" in line for line in s_fire.log[before:])

    # vs BURNING + a forced roll above the chance → amp denied.
    p_deny = build_player_combatant(_make_char(6), player_skill_keys=[skill])
    e_deny = _enemy()
    e_deny.burn_stacks = 3
    s_deny = _session(p_deny, e_deny)
    s_deny.rng.random = lambda: 0.99  # >= 0.50 → no fire
    before = len(s_deny.log)
    cast_skill(s_deny, p_deny, e_deny, skill, skill_data, 0)
    assert not any("Chân Hỏa Phần Thiên" in line for line in s_deny.log[before:])

    # vs CLEAN target (burn_stacks 0, burn proc disabled) → amp never fires
    # regardless of the roll.
    p_clean = build_player_combatant(_make_char(6), player_skill_keys=[skill])
    p_clean.burn_on_hit_pct = 0.0  # prevent self-applied burn mid-cast
    e_clean = _enemy()
    e_clean.burn_stacks = 0
    s_clean = _session(p_clean, e_clean)
    s_clean.rng.random = lambda: 0.0
    before = len(s_clean.log)
    cast_skill(s_clean, p_clean, e_clean, skill, skill_data, 0)
    assert not any("Chân Hỏa Phần Thiên" in line for line in s_clean.log[before:])


def test_l6_hoa_element_dmg_bonus(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    l3, _ = _build(3)
    l6, _ = _build(6)
    # The L6 milestone adds +0.20 Hỏa element_dmg_bonus over L3.
    assert l6.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(0.20)
    assert l3.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(0.0)


# ── 5. L9 Phượng Hoàng Trọng Sinh — 3-charge revive + burst ─────────────────


def test_l9_revives_three_times_at_30_then_dies(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, _ = _build(9)
    assert player.hoa_revive_upgraded is True
    assert player.hoa_revive_charges == 3
    enemy = _enemy()
    session = _session(player, enemy)

    for i in range(3):
        player.hp = 0
        assert session._try_revive(player) is True, f"revive {i + 1}"
        assert player.hp == int(player.hp_max * 0.30)
        assert player.hoa_revive_charges == 3 - (i + 1)
        # +40% post-revive window stamped, 2 turns.
        assert player.has_effect("BuffChanDuongDuHoa")
        assert player.effects["BuffChanDuongDuHoa"] == 2

    # 4th lethal → charges exhausted, the L3 buff was stripped on first rebirth,
    # so the death is final (no generic seam fires).
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()


def test_l9_revive_fires_phoenix_burst_at_enemy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, _ = _build(9)
    # Pin matk/atk so the burst magnitude is predictable.
    player.atk = 200
    player.matk = 300
    enemy = _enemy()
    session = _session(player, enemy)

    hp_before = enemy.hp
    player.hp = 0
    assert session._try_revive(player) is True
    dealt = hp_before - enemy.hp

    assert any("Phượng Hoàng Lửa" in line for line in session.log)
    # Burst is 300% atk + 300% matk (= 3×200 + 3×300 = 1500 base), routed through
    # the full pipeline (±15% variance, crit-able, +40% DuHoa, Hỏa-res-able), so
    # assert it lands in a generous band rather than an exact value.
    expected_base = 3 * player.atk + 3 * player.matk
    assert dealt >= int(expected_base * 0.85 * 0.5), (dealt, expected_base)


def test_l9_priority_claims_death_before_l3_buff(monkeypatch) -> None:
    """At L9 the upgraded hook claims the death first; the L3
    ``BuffChanDuongNietBan`` is stripped so it never grants a 4th revive."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, _ = _build(9)
    # Both effects co-exist at L9 (effects accumulate across milestones).
    assert player.has_effect("BuffChanDuongNietBan")
    assert player.has_effect("BuffPhuongHoangTrongSinh")
    enemy = _enemy()
    session = _session(player, enemy)

    player.hp = 0
    session._try_revive(player)
    # 30% (the L9 rate), not 20% (the L3 rate) → the priority-5 hook claimed it.
    assert player.hp == int(player.hp_max * 0.30)
    # And the L3 buff is gone so it can't add a bonus revive later.
    assert not player.has_effect("BuffChanDuongNietBan")


def test_nhap_ma_style_buff_baseline_dmg(monkeypatch) -> None:
    """``BuffChanDuongDuHoa`` grants +40% final_dmg_bonus while active."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player, _ = _build(9)
    before = get_combat_modifiers(player).get("final_dmg_bonus", 0.0)
    player.apply_effect("BuffChanDuongDuHoa", 2)
    after = get_combat_modifiers(player).get("final_dmg_bonus", 0.0)
    assert after == pytest.approx(before + 0.40)


# ── 6. Inertness — flag OFF leaves the body flat, no buffs, paths no-op ──────


def test_flag_off_no_effects_stamped() -> None:
    assert settings.constitution_process_enabled is False
    player, _ = _build(9)
    for key in (
        "BuffHoaKhiTuongSinh", "BuffChanDuongNietBan",
        "BuffChanHoaPhanThien", "BuffPhuongHoangTrongSinh",
    ):
        assert not player.has_effect(key)
    assert player.hoa_revive_upgraded is False
    assert player.hoa_revive_charges == 0
    assert player.hoa_burning_amp_chance == 0.0


def test_flag_off_resolves_to_flat_stat_bonuses() -> None:
    assert settings.constitution_process_enabled is False
    from src.game.systems.cultivation import compute_constitution_bonuses

    flat = compute_constitution_bonuses(_BODY, "qi", _BODY_REALM)
    data = _body_data()
    assert flat == data["stat_bonuses"]
    assert "hoa_revive_upgraded" not in flat
    assert "hoa_burning_amp_chance" not in flat


def test_flag_off_revive_and_periodic_paths_noop() -> None:
    assert settings.constitution_process_enabled is False
    player, _ = _build(9)
    enemy = _enemy()
    enemy.burn_stacks = 3
    session = _session(player, enemy)

    # No BuffHoaKhiTuongSinh → burning crit-ramp never grows.
    for _ in range(5):
        session._process_periodic(player)
    assert player.burning_crit_stacks == 0

    # No upgraded-revive config → the priority-5 hook is inert; with no revive
    # buffs stamped either, a death is final.
    player.hp = 0
    assert session._try_revive(player) is False
    assert not player.is_alive()
