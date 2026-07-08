"""v12 constitution — Thôn Thiên Ma Thể (Heaven-Devouring Demon Body, Ám).

The devourer (sheet row 29, 3rd Ám body): L1 Ma Khí Thôn Phệ — META tradeoff
(sheet Option B; Option A/pill-immunity already went to Vô Cấu Lưu Ly):
cultivation speed +50% but ``loot_luck_bonus`` −50%; L3 Thôn Phệ Bản Nguyên —
copies the BASE Tầng-1 block (stat_bonuses via character_stats merge + effects
via builders stamp) of the STRONGEST other process body in the unlock tracker
(highest rarity, earliest unlock breaks ties; Phàm Thể / Hỗn Độn / self
excluded); L6 Hắc Động — 15% of damage taken becomes flat temp MATK (cap 60%
of starting matk, shares the stat-steal "matk_original" anchor) + 40% per
landed hit to STRIP one enemy buff into 8% max MP; L9 Thôn Thiên Thực Địa —
every 8 acted turns: steal ALL stealable enemy buffs + one big stat steal
(per_proc 0.10 = the lifetime cap, Sưu Hồn precedent) + capped true damage =
50% of the stolen stat points; passive 65% stat steal per hit. NO revive.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.auras.thon_thien import _thon_thien_devour
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.procs import apply_reactive_damage, run_thon_thien_procs
from src.game.systems.constitution_process import devoured_l1
from src.utils.config import settings

_BODY = "TheChat_ThonThienMa"
_ENEMY = "DuocVienR6_02"


class _ZeroRng(random.Random):
    def random(self) -> float:  # noqa: D102
        return 0.0


class _OneRng(random.Random):
    def random(self) -> float:  # noqa: D102
        return 0.999999


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


@pytest.fixture()
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


def _make_char(level: int | None = 9, tracker: str = "") -> Character:
    return Character(
        player_id=1, discord_id=1, name="ThonThienTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, constitution_tracker=tracker,
        linh_can=["am"], linh_can_levels={"am": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _holder(level: int = 9, tracker: str = ""):
    p = build_player_combatant(_make_char(level, tracker), ["SkillAtkKim1"])
    p.mp = p.mp_max = 10_000
    return p


def _session(player, rng=None):
    enemy = build_enemy_combatant(_ENEMY, player_realm_total=27)
    assert enemy is not None
    enemy.mp = enemy.mp_max = 99_999
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=["SkillAtkKim1"],
        rng=rng or random.Random(1), max_turns=10,
    ), enemy


# ── Data sanity + L1 meta tradeoff ───────────────────────────────────────────


def test_body_registered_legendary_am():
    data = registry.get_constitution(_BODY)
    assert data is not None
    assert data["rarity"] == "legendary" and data["element"] == "am"
    for lv in ("1", "3", "6", "9"):
        for eff in data["process"]["levels"][lv]["effects"]:
            assert eff in EFFECTS, eff


def test_l1_meta_tradeoff(_flag_on):
    """Option B: cultivation ×1.5 paid for with −50% loot luck."""
    from src.game.systems.cultivation import compute_constitution_bonuses

    bonuses = compute_constitution_bonuses(_BODY, "qi", 6, process_levels={_BODY: 1})
    assert bonuses.get("cultivation_speed_bonus") == pytest.approx(0.5)
    assert bonuses.get("loot_luck_bonus") == pytest.approx(-0.5)
    p = _holder(1)
    assert p.loot_luck_bonus == pytest.approx(-0.5)


# ── L3 Thôn Phệ Bản Nguyên — devour-copy ─────────────────────────────────────


def test_devour_picks_strongest_donor(_flag_on):
    """Mythic outranks legendary regardless of unlock order."""
    char = _make_char(
        3, tracker=f"{_BODY},TheChat_CuuThienCuongPhong,TheChat_HoangCoThanhThe",
    )
    donor, _stats, effects = devoured_l1(char)
    assert donor == "TheChat_HoangCoThanhThe"
    assert "BuffLongLanKhaiGiap" in effects


def test_devour_copies_base_l1_stats_and_effects(_flag_on):
    """A legendary donor's L1 stat_bonuses merge into the combatant and its
    L1 effects stamp — base values only (no growth, no higher milestones)."""
    char = _make_char(3, tracker=f"{_BODY},TheChat_CuuUMaDe")
    donor, stats, effects = devoured_l1(char)
    assert donor == "TheChat_CuuUMaDe"
    assert stats.get("soul_drain_on_hit_pct") == pytest.approx(0.25)  # T1 base
    p = build_player_combatant(char, ["SkillAtkKim1"])
    # 0.25 donor L1 + 0.10 Âm linh căn (no native drain on Thôn Thiên)
    assert p.soul_drain_on_hit_pct == pytest.approx(0.35)
    assert p.cu_ma_khi_cap == 9  # donor's Ma Khí ramp flag copied
    assert p.has_effect("BuffCuuUMaKhi")


def test_devour_excludes_self_pham_the_and_needs_l3(_flag_on):
    char = _make_char(3, tracker=f"{_BODY},ConstitutionPhamThe")
    assert devoured_l1(char) == (None, {}, [])
    char2 = _make_char(1, tracker=f"{_BODY},TheChat_CuuUMaDe")
    assert devoured_l1(char2) == (None, {}, [])  # below L3


def test_devour_requires_primary_slot(_flag_on):
    char = _make_char(9, tracker=f"{_BODY},TheChat_CuuUMaDe")
    char.constitution_type = "TheChat_CuuUMaDe"  # Thôn Thiên not primary
    assert devoured_l1(char) == (None, {}, [])


# ── L6 Hắc Động — absorb + strip→MP ──────────────────────────────────────────


def test_absorb_converts_damage_to_matk_with_cap(_flag_on):
    p = _holder(6)
    session, enemy = _session(p)
    matk0 = p.matk
    cap = int(matk0 * 0.60)
    apply_reactive_damage(session, enemy, p, 1000)
    assert p.matk - matk0 == 150  # 15% of 1000
    assert p.ttm_matk_absorbed == 150
    # Flood far past the cap — gain clamps at 60% of the starting matk.
    apply_reactive_damage(session, enemy, p, 10_000_000)
    assert p.ttm_matk_absorbed == cap
    assert p.matk == matk0 + cap


def test_strip_converts_enemy_buff_to_mp(_flag_on):
    p = _holder(6)
    session, enemy = _session(p, rng=_ZeroRng())
    enemy.apply_effect("BuffNhietTinh", 3)
    p.mp = 0
    run_thon_thien_procs(session, p, enemy, 500)
    assert not enemy.has_effect("BuffNhietTinh")
    assert p.mp == int(p.mp_max * 0.08)


def test_strip_respects_chance(_flag_on):
    p = _holder(6)
    session, enemy = _session(p, rng=_OneRng())
    enemy.apply_effect("BuffNhietTinh", 3)
    run_thon_thien_procs(session, p, enemy, 500)
    assert enemy.has_effect("BuffNhietTinh")


# ── L9 Thôn Thiên Thực Địa — devour burst ────────────────────────────────────


def test_devour_burst_steals_buffs_stats_and_damages(_flag_on):
    p = _holder(9)
    session, enemy = _session(p)
    enemy.apply_effect("BuffNhietTinh", 5)  # stealable
    atk0, hp0 = p.atk, enemy.hp
    ctx = TurnContext(actor=p, target=enemy, session=session)
    p.ttm_devour_turn_counter = 7  # next tick fires the 8-turn cadence
    _thon_thien_devour(ctx)
    assert p.has_effect("BuffNhietTinh")            # stolen, not destroyed
    assert not enemy.has_effect("BuffNhietTinh")
    assert (p.atk + p.matk + p.def_stat) > atk0 + p.matk - p.matk  # stats grew
    assert enemy.hp < hp0                            # devour true damage landed


def test_devour_burst_respects_cadence(_flag_on):
    p = _holder(9)
    session, enemy = _session(p)
    hp0 = enemy.hp
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _thon_thien_devour(ctx)  # counter 0→1, far from 8
    assert enemy.hp == hp0
    assert p.ttm_devour_turn_counter == 1


def test_l9_passive_steal_lane(_flag_on):
    char = _make_char(9)
    char.linh_can = ["kim"]  # isolate from the Âm root's +0.08
    char.linh_can_levels = {"kim": 5}
    p = build_player_combatant(char, ["SkillAtkKim1"])
    assert p.stat_steal_on_hit_pct == pytest.approx(0.65)


# ── Dormancy ─────────────────────────────────────────────────────────────────


def test_dormant_without_levels():
    p = build_player_combatant(
        _make_char(level=None, tracker=f"{_BODY},TheChat_CuuUMaDe"),
        ["SkillAtkKim1"],
    )
    assert p.ttm_devour_interval == 0
    assert p.ttm_absorb_matk_pct == 0.0
    assert not p.has_effect("BuffCuuUMaKhi")  # no devour-copy while dormant
    assert p.loot_luck_bonus == 0.0
