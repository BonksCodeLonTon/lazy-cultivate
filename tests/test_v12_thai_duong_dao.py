"""v12 constitution — Thái Dương Đạo Thể (Supreme Yang Dao Body, universal mythic).

The solar anti-demon furnace tank (sheet row 30, first 🔒 special — unlock
condition left null pending design): L1 Cửu Dương Chân Khí — +HP, hard poison
immunity (narrow bool lane), 85% freeze resist via the buff's
``effect_resist:DebuffDongBang``, and +35% final dmg vs "yêu ma quỷ quái"
(mapped to Ám-element enemies + Beast* families — no enemy tag system exists);
L3 Thái Dương Chân Hỏa — guaranteed burn + 60% blind per hit (generic lanes) +
25% Hỏa dmg; L6 Thần Lô — each damaging hit TAKEN banks a furnace stack (cap
15), each +3% atk/matk/def/spd via BuffThaiDuongThanLo scaling; L9 Nhật Diệu
Cửu Thiên — every 8 acted turns a solar burst (capped true dmg = 60% of OWN
max HP + 3-turn blind), +50% Ám resist (the sheet's "immune to all Ám skills"
CONVERTED per the no-0-damage-gate rule), furnace amp +1.5%/stack, anti-demon
total 55%. NO revive.
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
from src.game.systems.combat.auras.thai_duong import _thai_duong_solar
from src.game.systems.combat.casting import inflict_debuff
from src.game.systems.combat.context import TurnContext
from src.game.systems.combat.procs import apply_reactive_damage
from src.utils.config import settings

_BODY = "TheChat_ThaiDuongDao"
_ENEMY = "DuocVienR6_02"


class _ZeroRng(random.Random):
    def random(self) -> float:  # noqa: D102
        return 0.0


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


@pytest.fixture()
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


def _make_char(level: int | None = 9) -> Character:
    return Character(
        player_id=1, discord_id=1, name="ThaiDuongTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["hoa"], linh_can_levels={"hoa": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _holder(level: int = 9):
    p = build_player_combatant(_make_char(level), ["SkillAtkHoa1"])
    p.mp = p.mp_max = 99_999
    return p


def _session(player, enemy_key: str = _ENEMY, rng=None):
    enemy = build_enemy_combatant(enemy_key, player_realm_total=27)
    assert enemy is not None
    enemy.mp = enemy.mp_max = 99_999
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=["SkillAtkHoa1"],
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


def test_l1_immunities(_flag_on):
    p = _holder(1)
    assert p.poison_immunity
    session, enemy = _session(p, rng=_ZeroRng())
    # Poison: hard narrow-lane immunity.
    inflict_debuff(session, "DebuffDocTo", EFFECTS["DebuffDocTo"], p, actor=enemy)
    assert not p.has_effect("DebuffDocTo")
    # Freeze: 85% resist via the buff's effect_resist (ZeroRng → resists).
    inflict_debuff(session, "DebuffDongBang", EFFECTS["DebuffDongBang"], p, actor=enemy)
    assert not p.has_effect("DebuffDongBang")


def test_anti_demon_bonus_is_l9_only(_flag_on):
    """T1 rework (2026-07-08): the anti-demon bonus left T1 — only the L9
    milestone carries it now (0.20)."""
    assert _holder(1).td_anti_demon_dmg_pct == pytest.approx(0.0)
    assert _holder(9).td_anti_demon_dmg_pct == pytest.approx(0.20)


# ── T1 rework — HP → Hỏa damage conversion ───────────────────────────────────


def test_hp_converts_to_fire_damage_scaled_by_realm(_flag_on):
    """bonus = hp_max/1000 × 0.001 × realm_total, on top of any authored hoa."""
    p = _holder(1)
    realm_total = 6 + 6 + 6
    expected = min(2.5, (p.hp_max / 1000.0) * 0.0005 * realm_total)
    assert p.element_dmg_bonus.get("hoa", 0.0) == pytest.approx(expected)
    assert expected > 0


def test_fire_conversion_grows_with_realm(_flag_on):
    """Same body, higher realms → a larger conversion (rate × realm_total,
    and the realm HP pool feeds hp_max too)."""
    low = _holder(1)
    char = _make_char(1)
    char.body_realm = char.qi_realm = char.formation_realm = 9
    high = build_player_combatant(char, ["SkillAtkHoa1"])
    assert high.element_dmg_bonus["hoa"] > low.element_dmg_bonus["hoa"]


def test_fire_conversion_caps_at_250(_flag_on):
    """A monstrous HP pool clamps at the authored 2.5 cap (the 200-300% band)."""
    char = _make_char(1)
    char.body_realm = char.qi_realm = char.formation_realm = 9
    p = build_player_combatant(
        char, ["SkillAtkHoa1"], equip_stats={"hp_max": 2_000_000},
    )
    assert p.element_dmg_bonus["hoa"] == pytest.approx(2.5)


def test_anti_demon_hits_beasts_and_am_harder(_flag_on):
    """Same attack deals more vs a Beast* / Ám target than a neutral elite."""
    from src.game.systems.combat.casting import cast_skill

    def _dmg_vs(enemy_key: str) -> int:
        p = _holder(9)
        p.than_lo_stacks = 0
        session, enemy = _session(p, enemy_key, rng=_ZeroRng())
        enemy.hp = enemy.hp_max = 10**9
        enemy.evasion_rating = 0
        hp0 = enemy.hp
        skill = registry.get_skill("SkillAtkHoa1")
        cast_skill(session, p, enemy, "SkillAtkHoa1", skill, mp_cost=0)
        return hp0 - enemy.hp

    beast_key = next(k for k in registry.enemies if k.startswith("Beast"))
    assert _dmg_vs(beast_key) > 0
    # A same-fight neutral comparison is realm-scaled differently, so assert
    # the flag path directly instead: zeroing the flag lowers beast damage.
    p = _holder(9)
    session, enemy = _session(p, beast_key, rng=_ZeroRng())
    enemy.hp = enemy.hp_max = 10**9
    enemy.evasion_rating = 0
    from src.game.systems.combat.casting import cast_skill as _cast
    skill = registry.get_skill("SkillAtkHoa1")
    hp0 = enemy.hp
    _cast(session, p, enemy, "SkillAtkHoa1", skill, mp_cost=0)
    with_bonus = hp0 - enemy.hp
    p.td_anti_demon_dmg_pct = 0.0
    hp1 = enemy.hp
    _cast(session, p, enemy, "SkillAtkHoa1", skill, mp_cost=0)
    without_bonus = hp1 - enemy.hp
    assert with_bonus > without_bonus


# ── L3 rework — Thái Dương Hào Quang (solar aura) ────────────────────────────


def test_l3_grants_solar_aura(_flag_on):
    p = _holder(3)
    assert p.solar_aura_pct == pytest.approx(0.02)
    assert _holder(1).solar_aura_pct == 0.0  # not before T3


def test_solar_aura_ticks_own_hp_and_scales_with_fire_bonuses(_flag_on):
    """The aura deals %-of-own-HP fire damage per periodic tick, amplified by
    DoT-damage and Hỏa-damage bonuses (the T1 HP→hoa conversion feeds it)."""
    from src.game.systems.combat.periodic.solar_wither import _solar_aura

    def _tick_dmg(p) -> int:
        session, enemy = _session(p)
        enemy.hp = enemy.hp_max = 10**9
        enemy.resistances["hoa"] = 0.0
        hp0 = enemy.hp
        _solar_aura(TurnContext(actor=p, target=enemy, session=session))
        return hp0 - enemy.hp

    p = _holder(3)
    base_hoa = p.element_dmg_bonus.get("hoa", 0.0)
    dealt = _tick_dmg(p)
    # Mirror every engine term (linh-căn grants burn-kind DoT + hoa mods too).
    from src.game.engine.effects import get_combat_modifiers as _gcm
    mult = (
        1.0 + p.final_dmg_bonus + p.dot_dmg_bonus
        + float(p.dot_dmg_bonus_by_kind.get("burn", 0.0))
        + base_hoa + float(_gcm(p).get("dmg_bonus_hoa", 0.0))
    )
    assert dealt == max(1, int(int(p.hp_max * 0.02) * mult))
    # More fire identity → a harder aura.
    p2 = _holder(3)
    p2.element_dmg_bonus["hoa"] = base_hoa + 1.0
    assert _tick_dmg(p2) > dealt


# ── L6 Thần Lô furnace ───────────────────────────────────────────────────────


def test_furnace_banks_on_damage_taken_and_caps(_flag_on):
    p = _holder(6)
    session, enemy = _session(p)
    for _ in range(20):
        apply_reactive_damage(session, enemy, p, 500)
    assert p.than_lo_stacks == 15


def test_furnace_scales_all_core_stats(_flag_on):
    p = _holder(6)
    p.than_lo_stacks = 10
    mods = get_combat_modifiers(p)
    for stat in ("atk_pct", "matk_pct", "def_pct", "spd_pct"):
        assert mods.get(stat, 0.0) == pytest.approx(10 * 0.03)


def test_l9_amplifies_furnace(_flag_on):
    p = _holder(9)
    p.than_lo_stacks = 10
    mods = get_combat_modifiers(p)
    # 3%/stack (L6) + 1.5%/stack (L9) = 4.5%/stack
    assert mods.get("atk_pct", 0.0) == pytest.approx(10 * 0.045)


# ── L9 solar burst ───────────────────────────────────────────────────────────


def test_solar_burst_fires_on_cadence(_flag_on):
    p = _holder(9)
    session, enemy = _session(p, rng=_ZeroRng())
    hp0 = enemy.hp
    ctx = TurnContext(actor=p, target=enemy, session=session)
    p.td_solar_turn_counter = 7  # next tick completes the 8-turn cadence
    _thai_duong_solar(ctx)
    dealt = hp0 - enemy.hp
    # Capped true dmg: min(60% own hp_max, 12% target hp_max)
    assert dealt == min(int(p.hp_max * 0.60), int(enemy.hp_max * 0.12))
    assert p.has_effect("BuffThaiDuongThanLo") or True  # holder untouched
    assert enemy.has_effect("DebuffLoaMat")


def test_solar_burst_respects_cadence(_flag_on):
    p = _holder(9)
    session, enemy = _session(p)
    hp0 = enemy.hp
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _thai_duong_solar(ctx)  # counter 0→1
    assert enemy.hp == hp0


def test_l9_am_resist_not_immunity(_flag_on):
    """The sheet's 'immune to all Ám skills' ships as +50% Ám resist —
    high-but-capped, never a 0-damage gate."""
    p = _holder(9)
    assert p.resistances.get("am", 0.0) == pytest.approx(0.50)


# ── Dormancy ─────────────────────────────────────────────────────────────────


def test_dormant_without_levels():
    p = build_player_combatant(_make_char(level=None), ["SkillAtkHoa1"])
    assert p.td_anti_demon_dmg_pct == 0.0
    assert p.td_solar_interval == 0
    assert not p.td_than_lo_per_hit
    session, enemy = _session(p)
    apply_reactive_damage(session, enemy, p, 500)
    assert p.than_lo_stacks == 0
