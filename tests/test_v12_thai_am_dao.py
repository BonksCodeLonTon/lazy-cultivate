"""v12 constitution — Thái Âm Đạo Thể (Supreme Yin Dao Body, universal mythic).

The yin-moon evasion/freeze twin of Thái Dương (sheet row 31, 2nd 🔒 special —
unlock condition null pending design): L1 Cửu Âm Chân Khí — MP regen, +180
evasion (mythic evasion band deliberately raised 200→450 for this identity),
+25% Thủy, and the EXISTING ``damage_bonus_from_evasion_pct`` conversion lane
(0.35 × evasion as flat damage); L3 Thái Âm Chân Thủy — HP/shield regen, the
generic ``freeze_on_skill_chance`` lane (35%), +30% final dmg vs FROZEN
targets (stacks with the existing frozen→auto-crit); L6 Trảm Đạo Kiến Minh —
every 4 acted turns strip up to 2 enemy buffs + stamp DebuffTramDao (−10%
atk/matk/def, 3t); the sheet's linh-căn-passive nullify is DROPPED (no such
registry on enemies); L9 Hải Thượng Minh Nguyệt — per-turn 4% heal + 30%
freeze, and the Kính Hoa Thủy Nguyệt debuff-transfer becomes GUARANTEED while
its buff is up. NO revive.
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
from src.game.systems.combat.auras.kinh_hoa import _try_transfer_debuffs
from src.game.systems.combat.auras.thai_am import _thai_am_moon
from src.game.systems.combat.context import TurnContext
from src.utils.config import settings

_BODY = "TheChat_ThaiAmDao"
_ENEMY = "DuocVienR6_02"
_MIRROR = "SkillDefKinhHoaThuyNguyet_R7"


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


def _make_char(level: int | None = 9) -> Character:
    return Character(
        player_id=1, discord_id=1, name="ThaiAmTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["thuy"], linh_can_levels={"thuy": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _holder(level: int = 9):
    p = build_player_combatant(_make_char(level), ["SkillAtkKim1"])
    p.mp = p.mp_max = 99_999
    return p


def _session(player, rng=None):
    enemy = build_enemy_combatant(_ENEMY, player_realm_total=27)
    assert enemy is not None
    enemy.mp = enemy.mp_max = 99_999
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=["SkillAtkKim1"],
        rng=rng or random.Random(1), max_turns=10,
    ), enemy


# ── Data sanity + L1 ─────────────────────────────────────────────────────────


def test_body_registered_universal_mythic():
    data = registry.get_constitution(_BODY)
    assert data is not None
    assert data["rarity"] == "mythic" and data["element"] == "universal"
    for lv in ("1", "3", "6", "9"):
        for eff in data["process"]["levels"][lv]["effects"]:
            assert eff in EFFECTS, eff
    assert registry.get_skill(_MIRROR) is not None
    assert "DebuffTramDao" in EFFECTS


def test_l1_rides_generic_lanes(_flag_on):
    p = _holder(1)
    assert p.evasion_rating >= 80 + 180
    assert p.mp_regen_pct >= 0.08
    assert p.element_dmg_bonus.get("thuy", 0.0) >= 0.25
    assert p.damage_bonus_from_evasion_pct == pytest.approx(0.35)


# ── L3 — freeze lane + vs-frozen bonus ───────────────────────────────────────


def test_l3_freeze_lane_and_vs_frozen_flag(_flag_on):
    p = _holder(3)
    assert p.freeze_on_skill_chance == pytest.approx(0.35)
    assert p.ta_dmg_vs_frozen_pct == pytest.approx(0.30)


def test_vs_frozen_bonus_raises_damage(_flag_on):
    from src.game.systems.combat.casting import cast_skill

    p = _holder(3)
    p.freeze_on_skill_chance = 0.0  # isolate: no fresh freezes mid-test
    session, enemy = _session(p, rng=_OneRng())
    enemy.hp = enemy.hp_max = 10**9
    enemy.evasion_rating = 0
    enemy.resistances["hoa"] = 0.0
    skill = registry.get_skill("SkillAtkHoa1")
    assert skill is not None
    hp0 = enemy.hp
    cast_skill(session, p, enemy, "SkillAtkHoa1", skill, mp_cost=0)
    unfrozen = hp0 - enemy.hp
    enemy.apply_effect("DebuffDongBang", 3)
    hp1 = enemy.hp
    cast_skill(session, p, enemy, "SkillAtkHoa1", skill, mp_cost=0)
    frozen = hp1 - enemy.hp
    assert frozen > unfrozen  # +30% fdb (and the frozen auto-crit lane)


# ── L6 — Trảm Đạo cadence ────────────────────────────────────────────────────


def test_tram_dao_strips_and_seals_on_cadence(_flag_on):
    p = _holder(6)
    session, enemy = _session(p, rng=_ZeroRng())
    enemy.apply_effect("BuffNhietTinh", 5)
    ctx = TurnContext(actor=p, target=enemy, session=session)
    p.ta_tram_dao_turn_counter = 3  # next tick completes the 4-turn cadence
    _thai_am_moon(ctx)
    assert not enemy.has_effect("BuffNhietTinh")   # stripped (destroyed)
    assert enemy.has_effect("DebuffTramDao")       # stat seal stamped


def test_tram_dao_respects_cadence(_flag_on):
    p = _holder(6)
    session, enemy = _session(p)
    enemy.apply_effect("BuffNhietTinh", 5)
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _thai_am_moon(ctx)  # counter 0→1, far from 4
    assert enemy.has_effect("BuffNhietTinh")
    assert not enemy.has_effect("DebuffTramDao")


# ── L9 — moonlight + Kính Hoa resonance ──────────────────────────────────────


def test_moonlight_heals_and_freezes(_flag_on):
    p = _holder(9)
    session, enemy = _session(p, rng=_ZeroRng())
    p.hp = int(p.hp_max * 0.5)
    hp0 = p.hp
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _thai_am_moon(ctx)
    assert p.hp - hp0 >= int(p.hp_max * 0.04) - 1   # 4% moonlight heal
    assert enemy.has_effect("DebuffDongBang")        # 30% roll fired at 0.0


def test_resonance_guarantees_mirror_transfer(_flag_on):
    """With the L9 flag + the mirror buff up, a roll that would FAIL the
    skill's native 50% still transfers the debuff back."""
    p = _holder(9)
    session, enemy = _session(p, rng=_OneRng())
    p.apply_effect("BuffKinhHoaThuyNguyet", 3)
    p.apply_effect("DebuffLamCham", 3)  # cleansable, non-stacking → transferable
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _try_transfer_debuffs(ctx)
    assert not p.has_effect("DebuffLamCham")
    assert enemy.has_effect("DebuffLamCham")


def test_no_resonance_without_l9(_flag_on):
    p = _holder(6)
    session, enemy = _session(p, rng=_OneRng())
    p.apply_effect("BuffKinhHoaThuyNguyet", 3)
    p.apply_effect("DebuffLamCham", 3)
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _try_transfer_debuffs(ctx)
    assert p.has_effect("DebuffLamCham")  # native 50% roll failed at 0.999


# ── Dormancy ─────────────────────────────────────────────────────────────────


def test_dormant_without_levels():
    p = build_player_combatant(_make_char(level=None), ["SkillAtkKim1"])
    assert p.ta_tram_dao_interval == 0
    assert p.ta_moonlight_heal_pct == 0.0
    assert not p.ta_kinh_hoa_resonance
    assert p.freeze_on_skill_chance == 0.0
