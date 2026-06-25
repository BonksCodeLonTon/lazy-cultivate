"""v12 constitution — Niết Bàn Bất Diệt Thể (Nirvana Inextinguishable Body, Hỏa).

The 3rd Hỏa body: a fire-LIFESTEAL nirvana berserker (vs Chân Dương's offensive
multi-revive phoenix, Liệt Diễm's escalating nuker). Its identity is "bathe in
fire, die ONCE, come back as an all-stat berserker":

  L1 Dục Hỏa Sinh Cơ   — immune to every Hỏa-DoT debuff; lifts the Hỏa res cap to
                         0.90; fire res STACKED past 0.90 spills 1:1 into Hỏa
                         damage (overcap→offense, NOT a 0-damage gate); seeds
                         Nghiệp Hỏa accumulation.
  L3 Phượng Hoàng Chân Hỏa — +200 crit_dmg, +25% Hỏa skill dmg, +40% all DoT.
  L6 Niết Bàn Trọng Sinh   — one-use revive @50% HP (+5%/Nghiệp tier) + clear all
                             debuffs.
  L9 Cửu Chuyển Niết Bàn   — after the rebirth, +50% all stats / 4 turns + Hỏa.

Nghiệp Hỏa: each fire-DoT tick on the foe banks a vi-tầng; every 10 → 1 tier
(cap 3); per tier +8% Hỏa dmg, +12% DoT dmg, +5% revive HP — applied monotonically
(no decay) by the niet_ban PERIODIC hook.

Reuses: the existing ``hoa_max_resist_bonus`` cap-lift lane, ``element_dmg_bonus`` /
``dot_dmg_bonus`` amp fields, the inflict-interceptor registry, and the ON_REVIVE
hook seam (a dedicated priority-5 hook like Chân Dương's).

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so it
auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, count_elemental_dots
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import inflict_debuff
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_NietBanBatDiet"
_PHYS = "SkillKimTripleStrike"   # physical, kim — no hoa interference
_ENEMY = "TinhKimTho"


def _make_char(
    level: int | None = None,
    *,
    res_hoa: float = 0.0,
    linh_can: list[str] | None = None,
) -> Character:
    lc = linh_can if linh_can is not None else ["hoa"]
    lc_levels = {e: 5 for e in lc}
    return Character(
        player_id=1, discord_id=1, name="NietBanTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=lc, linh_can_levels=lc_levels,
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(res_hoa=res_hoa),
    )


def _body_data() -> dict:
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9, mp: int = 100_000):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.mp = e.mp_max = mp
    e.shield = 0
    e.resistances = {}
    e.evasion_rating = 0
    e.spd = 0
    return e


def _player(level: int, *, res_hoa: float = 0.0, linh_can: list[str] | None = None):
    return build_player_combatant(
        _make_char(level, res_hoa=res_hoa, linh_can=linh_can),
        player_skill_keys=[_PHYS],
    )


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[player.skill_keys[0]],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "hoa"
    assert data["rarity"] == "legendary"
    assert data["cost"]["merit"] == 60000
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    # Flat is pure stats; every mechanic config lives in the milestones.
    flat = data["stat_bonuses"]
    for cfg in (
        "hoa_overcap_to_dmg", "immune_hoa_skill_debuffs", "nb_nghiep_accumulate",
        "niet_ban_revive_enabled", "niet_ban_post_revive_boost",
    ):
        assert cfg not in flat
    assert flat["hp_pct"] == 0.10


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffDucHoaSinhCo", "BuffNghiepHoa"]
    assert effective_effects(data, 3) == [
        "BuffDucHoaSinhCo", "BuffNghiepHoa", "BuffNietBanPhuongHoangChanHoa",
    ]
    assert effective_effects(data, 9) == [
        "BuffDucHoaSinhCo", "BuffNghiepHoa", "BuffNietBanPhuongHoangChanHoa",
        "BuffNietBanTrongSinh",
    ]


def test_new_effects_registered() -> None:
    for key in (
        "BuffDucHoaSinhCo", "BuffNghiepHoa", "BuffNietBanPhuongHoangChanHoa",
        "BuffNietBanTrongSinh", "BuffCuuChuyenNietBan",
    ):
        assert EFFECTS.get(key) is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    assert p1.hoa_overcap_to_dmg is True
    assert p1.immune_hoa_skill_debuffs is True
    assert p1.nb_nghiep_accumulate is True
    p6 = _player(6)
    assert p6.niet_ban_revive_enabled is True
    assert p6.niet_ban_revive_pct == pytest.approx(0.50)
    assert p6.niet_ban_revive_clear_debuffs is True
    assert p6.nb_nghiep_revive_pct_per_tier == pytest.approx(0.05)
    assert _player(9).niet_ban_post_revive_boost is True


# ── 2. L1 Dục Hỏa Sinh Cơ — cap-lift + overcap→dmg + Hỏa-debuff immunity ─────


def test_l1_lifts_hoa_res_cap_to_90(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # High innate fire res — without the lift it would cap at 0.75.
    player = _player(9, res_hoa=0.80, linh_can=["kim"])
    assert player.element_max_resist_bonus.get("hoa", 0.0) == pytest.approx(0.15)
    assert player.resistances["hoa"] == pytest.approx(0.90)  # lifted cap, clamped


def test_l1_overcap_converts_to_hoa_dmg_1to1(monkeypatch) -> None:
    """Fire res past the 0.90 cap spills 1:1 into element_dmg_bonus.hoa.

    Two builds differ ONLY in innate fire res (both pushed well past the cap);
    the difference in their Hỏa-damage amp must equal the difference in res —
    proving the 1:1 conversion without depending on the absolute raw total.
    """
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p_lo = _player(9, res_hoa=0.50, linh_can=["kim"])
    p_hi = _player(9, res_hoa=0.70, linh_can=["kim"])
    # Resistance itself stays capped — NOT a 0-damage gate.
    assert p_lo.resistances["hoa"] == pytest.approx(0.90)
    assert p_hi.resistances["hoa"] == pytest.approx(0.90)
    # +0.20 more res → +0.20 more Hỏa damage (1:1 overcap spill).
    delta = p_hi.element_dmg_bonus["hoa"] - p_lo.element_dmg_bonus["hoa"]
    assert delta == pytest.approx(0.20)


def test_l1_no_overcap_when_under_cap(monkeypatch) -> None:
    """With fire res below the 0.90 cap there is no spill — only L3's flat amp."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # L1 only (no L3 amp), no innate fire res, neutral linh căn → raw well under cap.
    player = _player(1, res_hoa=0.0, linh_can=["kim"])
    assert player.resistances["hoa"] < 0.90
    assert player.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(0.0)


def test_l1_immune_to_hoa_dot_debuff(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    burn = EFFECTS.get("DebuffThieuDot")
    assert burn is not None and burn.dot_element == "hoa"
    inflict_debuff(session, "DebuffThieuDot", burn, player, actor=enemy)
    assert not player.has_effect("DebuffThieuDot")  # fire debuff blocked


def test_l1_non_hoa_debuff_still_lands(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    slow = EFFECTS.get("DebuffLamCham")
    assert slow is not None and slow.dot_element != "hoa"
    inflict_debuff(session, "DebuffLamCham", slow, player, actor=enemy)
    assert player.has_effect("DebuffLamCham")  # non-fire debuff unaffected


# ── 3. L3 Phượng Hoàng Chân Hỏa — crit + Hỏa + DoT amps ─────────────────────


def test_l3_amps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3, linh_can=["kim"])  # neutral linh căn → isolate L3 from overcap
    assert player.dot_dmg_bonus == pytest.approx(0.40)
    assert player.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(0.25)
    # +200 from L3 stacks on top of the flat 80 (+ growth).
    assert player.crit_dmg_rating >= 280


# ── 4. L6 Niết Bàn Trọng Sinh — one-use revive + clear debuffs ──────────────


def test_l6_revives_at_50pct_and_clears_debuffs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    # Pile on debuffs + a burn DoT, then take a lethal hit.
    player.apply_effect("DebuffLamCham", 3)
    player.burn_stacks = 4
    assert player.has_effect("DebuffLamCham")
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.hp == int(player.hp_max * 0.50)
    assert not player.has_effect("DebuffLamCham")   # debuffs wiped
    assert player.burn_stacks == 0                  # DoT carrier reset


def test_l6_revive_is_once_per_fight(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.niet_ban_revive_used is True
    player.hp = 0
    assert session._try_revive(player) is False     # charge spent
    assert not player.is_alive()


def test_l6_revive_hp_scales_with_nghiep_tier(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)
    player.nb_nghiep_tier = 3                        # +3×5% = +15%
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.hp == int(player.hp_max * (0.50 + 3 * 0.05))  # 65%


# ── 5. L9 Cửu Chuyển Niết Bàn — post-revive all-stat berserk ────────────────


def test_l9_post_revive_stamps_berserk(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    assert not player.has_effect("BuffCuuChuyenNietBan")
    player.hp = 0
    assert session._try_revive(player) is True
    assert player.has_effect("BuffCuuChuyenNietBan")  # 4-turn berserk armed


def test_l6_revive_without_l9_has_no_berserk(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(6)                              # L6 revive, no L9 arm
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is True
    assert not player.has_effect("BuffCuuChuyenNietBan")


# ── 6. Nghiệp Hỏa Tích Lũy — DoT-tick accumulation + per-tier amps ──────────


def test_nghiep_hoa_accrues_tier_and_bumps_amps(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1, linh_can=["kim"])
    enemy = _enemy()
    session = _session(player, enemy)
    # Burn the enemy so it carries a counted fire DoT.
    enemy.apply_effect("DebuffThieuDot", 99)
    enemy.burn_stacks = 5
    assert count_elemental_dots(enemy, "hoa") >= 1

    hoa0 = player.element_dmg_bonus.get("hoa", 0.0)
    dot0 = player.dot_dmg_bonus
    player.nb_nghiep_progress = 9                    # one tick from tier 1
    session._process_periodic(player)
    assert player.nb_nghiep_tier == 1
    assert player.element_dmg_bonus["hoa"] == pytest.approx(hoa0 + 0.08)
    assert player.dot_dmg_bonus == pytest.approx(dot0 + 0.12)


def test_nghiep_hoa_caps_at_3_tiers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1, linh_can=["kim"])
    enemy = _enemy()
    session = _session(player, enemy)
    enemy.apply_effect("DebuffThieuDot", 99)
    enemy.burn_stacks = 5
    # Far more ticks than 3 tiers' worth — must clamp at 3.
    for _ in range(60):
        session._process_periodic(player)
    assert player.nb_nghiep_tier == 3


# ── 7. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    # No monkeypatch → constitution_process_enabled is its dormant default (False).
    assert settings.constitution_process_enabled is False
    player = _player(9, res_hoa=0.80)
    assert player.hoa_overcap_to_dmg is False
    assert player.immune_hoa_skill_debuffs is False
    assert player.niet_ban_revive_enabled is False
    assert player.nb_nghiep_accumulate is False
    # No cap-lift and no overcap spill while dormant.
    assert player.element_max_resist_bonus.get("hoa", 0.0) == pytest.approx(0.0)
    assert player.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(0.0)


def test_flag_off_no_revive() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    enemy = _enemy()
    session = _session(player, enemy)
    player.hp = 0
    assert session._try_revive(player) is False     # dormant → death is final
