"""v12 constitution — Tiêu Dao Thần Thể (Free-Wandering God Body, Phong).

The 2nd Phong body: a MOVEMENT-DANCER / DUAL-FORM TRANSFORMER (vs Phi Thiên's
dodge-counter bruiser) — Trang Tử's wanderer: resonate with Phù Dao Trực
Thượng, arm devastating strikes after every bộ pháp, shrug off control, and
transform into Bằng (roc, offense) or Côn (leviathan, soak-and-release).

  L1 Ngự Phong Nhi Hành — +18% spd +280 evasion; casting Phù Dao Trực Thượng
                          stamps BuffNguPhongCongHuong (+20% spd +200 eva, 3t);
                          each movement cast banks +1 Tiêu Dao Cảnh (cap 6).
  L3 Phù Du Bộ Pháp     — after any movement cast: next attack unevadable +
                          force-crit + pierce 50% def; movement CD −30%.
  L6 Tiêu Dao Vô Cực    — 65% chance to ACT through any turn-skip CC (the
                          shrug lives in check_cc_skip_turn — the single CC
                          chokepoint, so no apply-path bypasses it; reframed
                          from the sheet's complete immunity).
  L9 Hóa Bằng · Hóa Côn — every 8 acted turns, 3-turn form (+1 per 3 Cảnh,
                          max +2): HP<45% → Côn (+60% DR, bank HP lost,
                          expiry releases ×1.5 as capped true dmg + heal 20%);
                          else Bằng (spd +100%, force-crit, +1 hit, unevadable).

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr``
so it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, check_cc_skip_turn, get_combat_modifiers
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_TieuDaoThan"
_MOV = "SkillMovPhuDao_R8"          # Phù Dao Trực Thượng — the resonance skill
_MOV2 = "SkillMovPhongThanThoi_R8"  # a second movement skill (no resonance)
_ATTACK = "SkillAtkLoi3"
_ENEMY = "TinhKimTho"


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _body_data():
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.shield = 0
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.spd = 0
    return e


def _player(level: int):
    char = Character(
        player_id=1, discord_id=1, name="TieuDaoTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["phong"], linh_can_levels={"phong": 5},
        constitution_levels={_BODY: level}, stats=CharacterStats(),
    )
    return build_player_combatant(char, player_skill_keys=[_ATTACK, _MOV, _MOV2])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[_ATTACK],
        rng=random.Random(seed), max_turns=200,
    )


def _cast(session, actor, target, key, mp: int = 0) -> None:
    skill = registry.get_skill(key)
    cast_skill(session, actor, target, key, dict(skill), mp)


def _tick_pre_turn(session, combatant, turns: int) -> None:
    other = session.enemy if combatant is session.player else session.player
    for _ in range(turns):
        ctx = TurnContext(actor=combatant, target=other, session=session)
        run_phase(TurnPhase.PRE_TURN, ctx)


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "phong"
    assert data["rarity"] == "legendary"
    assert data["cost"]["merit"] == 60000
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    flat = data["stat_bonuses"]
    for cfg in ("td_resonance_enabled", "td_cc_shrug_pct", "td_form_interval"):
        assert cfg not in flat
    assert flat["spd_pct"] == 0.07


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffNguPhongNhiHanh"]
    assert effective_effects(data, 9) == [
        "BuffNguPhongNhiHanh", "BuffPhuDuBoPhap",
        "BuffTieuDaoVoCuc", "BuffHoaBangHoaCon",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffNguPhongNhiHanh", "BuffNguPhongCongHuong", "BuffPhuDuBoPhap",
                "BuffTieuDaoVoCuc", "BuffHoaBangHoaCon", "BuffHoaBang", "BuffHoaCon"):
        assert EFFECTS.get(key) is not None
    assert EFFECTS["BuffHoaCon"].stat_bonus["final_dmg_reduce"] == pytest.approx(0.60)
    assert EFFECTS["BuffHoaBang"].stat_bonus["spd_pct"] == pytest.approx(1.0)


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    assert p1.td_resonance_enabled is True
    assert p1.td_canh_cap == 6
    assert p1.td_canh_per_turn == 3
    p3 = _player(3)
    assert p3.td_post_mov_arm is True
    assert p3.td_pierce_def_pct == pytest.approx(0.50)
    assert p3.td_mov_cd_reduce_pct == pytest.approx(0.30)
    p6 = _player(6)
    assert p6.td_cc_shrug_pct == pytest.approx(0.65)
    p9 = _player(9)
    assert p9.td_form_interval == 8
    assert p9.td_form_duration == 3
    assert p9.td_con_hp_gate == pytest.approx(0.45)
    assert p9.td_con_release_mult == pytest.approx(1.5)
    assert p9.td_con_heal_pct == pytest.approx(0.20)
    assert p9.td_bang_extra_hits == 1


# ── 2. L1 — resonance + Tiêu Dao Cảnh ───────────────────────────────────────


def test_l1_resonance_on_phu_dao_cast(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    _cast(session, player, enemy, _MOV)
    assert player.has_effect("BuffNguPhongCongHuong")
    mods = get_combat_modifiers(player)
    assert mods.get("spd_pct", 0.0) >= 0.20
    assert mods.get("evasion_rating", 0) >= 200


def test_l1_no_resonance_on_other_movement(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    _cast(session, player, enemy, _MOV2)
    assert not player.has_effect("BuffNguPhongCongHuong")
    assert player.td_canh_stacks == 1      # Cảnh still banks on ANY movement


def test_canh_accrues_and_caps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    for _ in range(8):
        _cast(session, player, enemy, _MOV)
    assert player.td_canh_stacks == 6      # capped
    _cast(session, player, enemy, _ATTACK)
    assert player.td_canh_stacks == 6      # attacks don't bank


# ── 3. L3 — armed strike + movement CDR ─────────────────────────────────────


def test_l3_movement_arms_next_strike(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.crit_rating = 0
    enemy = _enemy()
    enemy.evasion_rating = 10**6           # capped dodge chance — bypass beats it
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0       # every dodge roll would succeed
    _cast(session, player, enemy, _MOV)
    assert player.td_strike_armed is True
    before = enemy.hp
    _cast(session, player, enemy, _ATTACK)
    assert player.td_strike_armed is False           # consumed
    assert enemy.hp < before                          # landed through max evasion
    assert any("BẠO KÍCH" in l for l in session.log)  # forced crit


def test_l3_armed_strike_pierces_def(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.crit_rating = 0
    enemy = _enemy()
    enemy.def_stat = 10_000
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    # Unarmed baseline vs armed strike on identical enemies.
    before = enemy.hp
    _cast(session, player, enemy, _ATTACK)
    loss_unarmed = before - enemy.hp
    player.td_strike_armed = True
    before = enemy.hp
    _cast(session, player, enemy, _ATTACK)
    loss_armed = before - enemy.hp
    # Armed hit pierces 50% of 10k def AND force-crits — strictly bigger.
    assert loss_armed > loss_unarmed


def test_l3_movement_cooldown_reduced(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    _cast(session, player, enemy, _MOV)          # base cd 7 → round(7×0.7)=5
    assert player.cooldowns.get(_MOV) == 5
    _cast(session, player, enemy, _ATTACK)       # attack cds untouched
    atk_cd = registry.get_skill(_ATTACK).get("cooldown", 1)
    assert player.cooldowns.get(_ATTACK) == atk_cd


# ── 4. L6 — CC shrug at the single chokepoint ───────────────────────────────


def test_l6_shrug_acts_through_stun(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.apply_effect("CCStun", 2)
    rng = random.Random(0)
    rng.random = lambda: 0.0                     # shrug roll always lands
    assert check_cc_skip_turn(player, rng) is None
    assert player.td_cc_just_shrugged == "CCStun"


def test_l6_shrug_covers_direct_apply_freeze(monkeypatch) -> None:
    """The shrug lives in check_cc_skip_turn → even direct-apply_effect CC
    (Bắc Minh freeze class) is covered — no bypass path exists."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.apply_effect("DebuffDongBang", 2)     # direct stamp, no inflict_debuff
    rng = random.Random(0)
    rng.random = lambda: 0.0
    assert check_cc_skip_turn(player, rng) is None


def test_l6_shrug_can_fail(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.td_cc_just_shrugged = None
    player.apply_effect("CCStun", 2)
    rng = random.Random(0)
    rng.random = lambda: 0.99                    # roll above 0.65 → CC sticks
    assert check_cc_skip_turn(player, rng) == "CCStun"


# ── 5. L9 — Hóa Bằng / Hóa Côn ──────────────────────────────────────────────


def test_l9_bang_form_at_healthy_hp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    _tick_pre_turn(session, player, 8)
    assert player.has_effect("BuffHoaBang")
    assert not player.has_effect("BuffHoaCon")
    assert player.effects["BuffHoaBang"] == 3    # base duration, 0 stacks


def test_l9_con_form_at_low_hp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.hp = int(player.hp_max * 0.30)        # under the 45% gate
    enemy = _enemy()
    session = _session(player, enemy)
    _tick_pre_turn(session, player, 8)
    assert player.has_effect("BuffHoaCon")
    assert not player.has_effect("BuffHoaBang")


def test_l9_canh_stacks_extend_form(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.td_canh_stacks = 6                    # 6 // 3 = +2 turns
    enemy = _enemy()
    session = _session(player, enemy)
    _tick_pre_turn(session, player, 8)
    assert player.effects["BuffHoaBang"] == 5    # 3 + 2


def test_l9_bang_extra_hit_and_unevadable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.crit_rating = 0
    player.apply_effect("BuffHoaBang", 3)
    enemy = _enemy()
    enemy.evasion_rating = 10**6
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0             # dodge rolls would all succeed
    before = enemy.hp
    _cast(session, player, enemy, _ATTACK)
    assert enemy.hp < before                     # unevadable
    follow_ups = [l for l in session.log if "liên kích" in l]
    assert len(follow_ups) == 1                  # +1 extra hit


def test_l9_con_banks_and_releases_on_expiry(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.hp = player.hp_max                    # full → heal is pure overheal-clamp
    player.apply_effect("BuffHoaCon", 1)         # expires on the next periodic
    enemy = _enemy(hp=1_000_000)
    session = _session(player, enemy)
    # The form soaks a hit — the HP actually lost is banked.
    player.take_damage(10_000)
    assert player.td_con_bank == 10_000
    hp_after_hit = player.hp
    # Natural expiry → release ×1.5 (capped at 12% enemy max HP) + heal 20%.
    session._process_periodic(player)
    assert player.td_con_bank == 0
    expected = min(int(10_000 * 1.5), int(enemy.hp_max * 0.12))
    assert enemy.hp == 1_000_000 - expected
    assert player.hp > hp_after_hit              # healed


def test_l9_con_dr_reduces_damage_taken(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    mods = get_combat_modifiers(player)
    base_dr = mods.get("final_dmg_reduce", 0.0)
    player.apply_effect("BuffHoaCon", 3)
    mods = get_combat_modifiers(player)
    assert mods.get("final_dmg_reduce", 0.0) == pytest.approx(base_dr + 0.60)


# ── 6. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.td_resonance_enabled is False
    assert player.td_cc_shrug_pct == 0.0
    assert player.td_form_interval == 0
    enemy = _enemy()
    session = _session(player, enemy)
    _cast(session, player, enemy, _MOV)
    assert not player.has_effect("BuffNguPhongCongHuong")
    assert player.td_strike_armed is False
    assert player.td_canh_stacks == 0
    _tick_pre_turn(session, player, 10)
    assert not player.has_effect("BuffHoaBang")
    assert not player.has_effect("BuffHoaCon")
    player.apply_effect("CCStun", 2)
    rng = random.Random(0)
    rng.random = lambda: 0.0
    assert check_cc_skip_turn(player, rng) == "CCStun"   # no shrug
