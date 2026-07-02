"""v12 constitution — Cửu Thiên Huyền Lôi Thể (Nine Heavens Mysterious Thunder Body, Lôi).

The 3rd Lôi body: a SIGNATURE-ART CHANNELER (vs Thiên Lôi Cường's shock nuker
and Thiên Kiếp's CC executioner) — the whole kit amplifies ONE named skill,
Cửu Thiên Ngự Lôi Chân Quyết (casting.py ``_CT_NAMED_SKILL``).

  L1 Tử Tiêu Lôi Khí     — 30% Tê Liệt per hit; the named skill +60% dmg and
                           banks +1 Năng Lượng per USE (cap 5, no reset), each
                           stack a further +12% named-skill dmg.
  L3 Thiểm Điện Thân Pháp — +12% spd, +250 evasion; a successful dodge stamps
                           BuffThiemDienPhanKich (+40% Lôi dmg, next turn).
  L6 Lôi Trì Ngự Khống   — every hit applies DebuffLoiXuyenThau (res_loi −22%,
                           4t); the named skill strikes +3 extra times.
  L9 Thần Lôi Diệt Thế   — every 7 acted turns → BuffThanLoiGiangThe for 2
                           turns (3 at 5 Năng Lượng): spd +100%, Lôi +50%,
                           force-crit, hits auto-apply Sốc Điện + Sét Đánh and
                           Tê Liệt at 50%.

Reuses: the on-hit proc seam (run_cuu_thien_procs, dmg>0-gated), the frozen
AttackStats replace precedent for the named-skill amp, the hit_count fold, the
is_evaded dodge seam, the tick_cadence PRE_TURN aura pattern (auras/cuu_thien),
and the force_crit OR-chain in combat_hit.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr``
so it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, get_combat_modifiers
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.hooks import TurnPhase, run_phase
from src.game.systems.combat.procs import run_cuu_thien_procs
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_CuuThienHuyenLoi"
_NAMED = "SkillLoiCuuThienNguLoiChanQuyet"
_OTHER_LOI = "SkillAtkLoi3"
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
        player_id=1, discord_id=1, name="CuuThienTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["loi"], linh_can_levels={"loi": 5},
        constitution_levels={_BODY: level}, stats=CharacterStats(),
    )
    return build_player_combatant(char, player_skill_keys=[_NAMED, _OTHER_LOI])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[player.skill_keys[0]],
        rng=random.Random(seed), max_turns=200,
    )


def _cast_named_loss(player, *, seed: int = 0) -> int:
    """Enemy HP lost from one top-level named-skill cast (rng pinned no-crit)."""
    enemy = _enemy()
    session = _session(player, enemy, seed=seed)
    session.rng.random = lambda: 0.99   # no crits, no rider procs
    skill = registry.get_skill(_NAMED)
    before = enemy.hp
    cast_skill(session, player, enemy, _NAMED, dict(skill), skill.get("mp_cost", 0))
    return before - enemy.hp


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "loi"
    assert data["rarity"] == "legendary"
    assert data["cost"]["merit"] == 60000
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    flat = data["stat_bonuses"]
    for cfg in ("te_liet_on_hit_pct", "ct_skill_dmg_amp",
                "ct_burst_interval", "ct_skill_extra_hits"):
        assert cfg not in flat
    assert flat["spd_pct"] == 0.06


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffTuTieuLoiKhi"]
    assert effective_effects(data, 9) == [
        "BuffTuTieuLoiKhi", "BuffThiemDienThanPhap",
        "BuffLoiTriNguKhong", "BuffThanLoiDietThe",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffTuTieuLoiKhi", "BuffThiemDienThanPhap", "BuffThiemDienPhanKich",
                "BuffLoiTriNguKhong", "BuffThanLoiDietThe", "BuffThanLoiGiangThe",
                "DebuffLoiXuyenThau"):
        assert EFFECTS.get(key) is not None
    assert EFFECTS["DebuffLoiXuyenThau"].stat_bonus["res_loi"] == pytest.approx(-0.22)


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    # Tê Liệt rides the GENERIC te_liet_on_hit_pct lane.
    assert p1.te_liet_on_hit_pct >= 0.30
    assert p1.ct_skill_dmg_amp == pytest.approx(0.60)
    assert p1.ct_nang_luong_cap == 5
    assert p1.ct_nang_luong_dmg_per_stack == pytest.approx(0.12)
    p3 = _player(3)
    assert p3.ct_dodge_loi_amp == pytest.approx(0.40)
    p6 = _player(6)
    assert p6.loi_shred_on_hit_pct >= 1.0    # generic shred lane
    assert p6.ct_skill_extra_hits == 3
    p9 = _player(9)
    assert p9.ct_burst_interval == 7
    assert p9.ct_burst_duration == 2
    assert p9.ct_burst_bonus_turn_gate == 5


# ── 2. L1 — named-skill amp + Năng Lượng ────────────────────────────────────


def test_l1_named_skill_amp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    loss_on = _cast_named_loss(player)
    player.ct_skill_dmg_amp = 0.0       # disable the amp → baseline
    player.ct_nang_luong_stacks = 0
    loss_off = _cast_named_loss(player)
    # +60% final_dmg_bonus → strictly more damage. The bonus is ADDITIVE with
    # the build's existing final-dmg pool, so the observed ratio is well under
    # ×1.6 — assert direction + a meaningful floor instead of the raw ratio.
    assert loss_on > loss_off
    assert loss_on >= int(loss_off * 1.10)


def test_l1_amp_does_not_touch_other_skills(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    skill = registry.get_skill(_OTHER_LOI)
    before = enemy.hp
    cast_skill(session, player, enemy, _OTHER_LOI, dict(skill), 0)
    loss_on = before - enemy.hp
    player.ct_skill_dmg_amp = 0.0
    enemy2 = _enemy()
    session2 = _session(player, enemy2)
    session2.rng.random = lambda: 0.99
    before2 = enemy2.hp
    cast_skill(session2, player, enemy2, _OTHER_LOI, dict(skill), 0)
    assert loss_on == before2 - enemy2.hp   # identical — amp is named-skill-only


def test_nang_luong_accrues_per_named_cast_and_caps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    skill = registry.get_skill(_NAMED)
    for _ in range(7):
        cast_skill(session, player, enemy, _NAMED, dict(skill), 0)
    assert player.ct_nang_luong_stacks == 5      # capped, never resets
    other = registry.get_skill(_OTHER_LOI)
    cast_skill(session, player, enemy, _OTHER_LOI, dict(other), 0)
    assert player.ct_nang_luong_stacks == 5      # other skills don't bank


def test_nang_luong_raises_named_amp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    player.ct_nang_luong_stacks = 5
    loss_5 = _cast_named_loss(player)
    player.ct_nang_luong_stacks = 0
    loss_0 = _cast_named_loss(player)
    assert loss_5 > loss_0                        # +0.60/+1.20 vs +0.60


# ── 3. L3 — dodge loads next turn's lightning ───────────────────────────────


def test_l3_dodge_stamps_riposte_buff(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.evasion_rating = 10**6                # dodge chance at the cap
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0             # roll under the capped chance → evade
    skill = registry.get_skill(_OTHER_LOI)
    cast_skill(session, enemy, player, _OTHER_LOI, dict(skill), 0)  # enemy attacks
    assert player.has_effect("BuffThiemDienPhanKich")
    mods = get_combat_modifiers(player)
    assert mods.get("dmg_bonus_loi", 0.0) == pytest.approx(0.40)


# ── 4. L6 — Lôi Xuyên Thấu shred + named-skill extra hits ───────────────────


def test_l6_shred_applied_on_hit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    player.crit_rating = 0
    session = _session(player, enemy)
    session.rng.random = lambda: 0.5             # shred lane at 1.0 still lands
    skill = registry.get_skill(_OTHER_LOI)
    cast_skill(session, player, enemy, _OTHER_LOI, dict(skill), 0)
    assert enemy.has_effect("DebuffLoiXuyenThau")
    mods = get_combat_modifiers(enemy)
    assert mods.get("res_loi", 0.0) == pytest.approx(-0.22)


def test_l6_named_skill_extra_hits(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    skill = registry.get_skill(_NAMED)
    cast_skill(session, player, enemy, _NAMED, dict(skill), 0)
    follow_ups = [l for l in session.log if "liên kích" in l]
    assert len(follow_ups) == 3                  # 1 + 3 strikes
    other = registry.get_skill(_OTHER_LOI)
    session.log.clear()
    cast_skill(session, player, enemy, _OTHER_LOI, dict(other), 0)
    assert not [l for l in session.log if "liên kích" in l]  # named-only


def test_l1_te_liet_rider_and_negated_hit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0             # lane roll lands
    skill = registry.get_skill(_OTHER_LOI)
    cast_skill(session, player, enemy, _OTHER_LOI, dict(skill), 0)
    assert enemy.has_effect("DebuffTeLiet")      # generic te_liet lane
    # The body proc itself stays dmg-gated (window Sét Đánh rider).
    player.apply_effect("BuffThanLoiGiangThe", 2)
    enemy2 = _enemy()
    run_cuu_thien_procs(session, player, enemy2, dmg=0)  # negated hit
    assert not enemy2.has_effect("DebuffSetDanh")


# ── 5. L9 — Thần Lôi Giáng Thế burst window ─────────────────────────────────


def _tick_pre_turn(session, combatant, turns: int) -> None:
    other = session.enemy if combatant is session.player else session.player
    for _ in range(turns):
        ctx = TurnContext(actor=combatant, target=other, session=session)
        run_phase(TurnPhase.PRE_TURN, ctx)


def test_l9_burst_cadence_fires_every_interval(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    _tick_pre_turn(session, player, 6)
    assert not player.has_effect("BuffThanLoiGiangThe")
    _tick_pre_turn(session, player, 1)           # 7th acted turn
    assert player.has_effect("BuffThanLoiGiangThe")
    assert player.effects["BuffThanLoiGiangThe"] == 2   # base duration


def test_l9_burst_bonus_turn_at_full_nang_luong(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.ct_nang_luong_stacks = 5
    enemy = _enemy()
    session = _session(player, enemy)
    _tick_pre_turn(session, player, 7)
    assert player.effects.get("BuffThanLoiGiangThe") == 3   # 2 + 1


def test_l9_window_stats_and_force_crit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.apply_effect("BuffThanLoiGiangThe", 2)
    mods = get_combat_modifiers(player)
    assert mods.get("spd_pct", 0.0) >= 1.0
    assert mods.get("dmg_bonus_loi", 0.0) >= 0.50
    from src.game.engine.damage.combat_hit import build_attack_stats
    enemy = _enemy()
    stats = build_attack_stats(player, enemy, mods, "loi")
    assert stats.force_crit is True


def test_l9_window_riders_auto_apply(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.apply_effect("BuffThanLoiGiangThe", 2)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.45            # < 0.50 boosted Tê Liệt, > 0.30 base
    skill = registry.get_skill(_OTHER_LOI)
    cast_skill(session, player, enemy, _OTHER_LOI, dict(skill), 0)
    assert enemy.has_effect("DebuffSocDien")     # shock lane at 1.0 in window
    assert enemy.has_effect("DebuffSetDanh")     # body proc, window-gated
    assert enemy.has_effect("DebuffTeLiet")      # 0.30 + 0.20 window boost


def test_l9_no_riders_outside_window(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    player.crit_rating = 0
    session = _session(player, enemy)
    session.rng.random = lambda: 0.45            # base 30% Tê Liệt roll fails
    skill = registry.get_skill(_OTHER_LOI)
    cast_skill(session, player, enemy, _OTHER_LOI, dict(skill), 0)
    assert not enemy.has_effect("DebuffSocDien")
    assert not enemy.has_effect("DebuffSetDanh")
    assert not enemy.has_effect("DebuffTeLiet")


# ── 6. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.ct_skill_dmg_amp == 0.0
    assert player.ct_nang_luong_cap == 0
    assert player.ct_burst_interval == 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_cuu_thien_procs(session, player, enemy, dmg=1_000)
    assert not enemy.has_effect("DebuffTeLiet")
    assert not enemy.has_effect("DebuffLoiXuyenThau")
    _tick_pre_turn(session, player, 10)
    assert not player.has_effect("BuffThanLoiGiangThe")
    skill = registry.get_skill(_NAMED)
    cast_skill(session, player, enemy, _NAMED, dict(skill), 0)
    assert player.ct_nang_luong_stacks == 0
