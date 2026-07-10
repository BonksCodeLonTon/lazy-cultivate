"""v12 constitution — Hỗn Độn Ma Thần Thể (Chaotic Demon God Body, universal mythic).

The pure-physical chaos brute (sheet row 32, 3rd 🔒 special — unlock null
pending design): L1 Hỗn Độn Nhục Thân — the roster's biggest base-stat spine
(sheet's flat +40%-all TRIMMED into the atk/hp/def bands); L3 Phá Toái Hư
Không — magical ATTACK skills are FILTERED from the rotation at build
(defenses keep working; all-magical loadouts fall back to basic attacks), and
every landed cast adds capped TRUE damage = 8% of OWN max HP (the Hậu Thổ
rider pattern; sheet's 100% trimmed); L6 Vạn Pháp Quy Hỗn — +30% resist to
each ngũ hành element (sheet's 50% dmg-reduction trimmed to the capped res
lanes) + 10% res_all; L9 Khai Thiên Tích Địa — every 7 acted turns the 4-turn
Hỗn Độn Chân Thân form (+40% atk/def, +30% res_all — sheet's "immune to all
elemental damage" CONVERTED per the no-0-damage-gate rule) and the HP rider
×1.5 during the form. NO revive.

Bench note: this body benches with the kit_overrides physical twins
(SkillAtkTho1/SkillAtkPhong1) — the standard magical kit is illegal for it.
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
from src.game.systems.combat.auras.hon_don_ma import _hon_don_ma_form
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.context import TurnContext
from src.utils.config import settings

_BODY = "TheChat_HonDonMaThan"
_ENEMY = "DuocVienR6_02"
_PHYS = "SkillAtkTho1"       # physical attack (55/17/4)
_MAGIC = "SkillAtkHoa1"      # magical attack — must be locked out


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
        player_id=1, discord_id=1, name="MaThanTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["tho"], linh_can_levels={"tho": 5},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _holder(level: int = 9, skills: list[str] | None = None):
    p = build_player_combatant(_make_char(level), skills or [_PHYS, _MAGIC])
    p.mp = p.mp_max = 99_999
    return p


def _session(player, rng=None):
    enemy = build_enemy_combatant(_ENEMY, player_realm_total=27)
    assert enemy is not None
    enemy.mp = enemy.mp_max = 99_999
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=list(player.skill_keys),
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
    assert "BuffHonDonChanThan" in EFFECTS


# ── L3 — MATK lock + HP shockwave rider ──────────────────────────────────────


def test_magical_attacks_filtered_from_rotation(_flag_on):
    p = _holder(3)
    assert _MAGIC not in p.skill_keys   # magical attack locked out
    assert _PHYS in p.skill_keys        # physical attack kept


def test_defense_skills_survive_the_lock(_flag_on):
    p = _holder(3, skills=[_MAGIC, "SkillDefKinhHoaThuyNguyet_R7"])
    assert "SkillDefKinhHoaThuyNguyet_R7" in p.skill_keys  # magical DEFENSE ok
    assert _MAGIC not in p.skill_keys


def test_no_lock_below_l3(_flag_on):
    p = _holder(1)
    assert _MAGIC in p.skill_keys


def test_hp_shockwave_rider_fires_per_cast(_flag_on):
    p = _holder(3)
    session, enemy = _session(p, rng=_OneRng())
    enemy.hp = enemy.hp_max = 10**9
    enemy.evasion_rating = 0
    skill = registry.get_skill(_PHYS)
    cast_skill(session, p, enemy, _PHYS, skill, mp_cost=0)
    rider_lines = [l for l in session.log if "Phá Toái Hư Không" in l]
    assert len(rider_lines) == 1
    # amount = min(8% own hp_max, 12% target hp_max) — huge target → own-HP side
    assert f"{int(p.hp_max * 0.08):,}" in rider_lines[0]


def test_rider_multiplied_during_form(_flag_on):
    p = _holder(9)
    session, enemy = _session(p, rng=_OneRng())
    enemy.hp = enemy.hp_max = 10**9
    enemy.evasion_rating = 0
    p.apply_effect("BuffHonDonChanThan", 4)
    skill = registry.get_skill(_PHYS)
    cast_skill(session, p, enemy, _PHYS, skill, mp_cost=0)
    rider_lines = [l for l in session.log if "Phá Toái Hư Không" in l]
    assert f"{int(int(p.hp_max * 0.08) * 1.5):,}" in rider_lines[-1]


# ── L6 — ngũ hành resists ────────────────────────────────────────────────────


def test_wuxing_resists(_flag_on):
    p = _holder(6)
    for elem in ("kim", "moc", "thuy", "hoa", "tho"):
        # +0.30 per-element + 0.10 res_all fold into the resistances dict
        assert p.resistances.get(elem, 0.0) >= 0.30
    # Non-ngũ-hành elements only get the res_all share
    assert p.resistances.get("am", 0.0) < 0.30


# ── L9 — Hỗn Độn Chân Thân form cadence ──────────────────────────────────────


def test_form_fires_on_cadence(_flag_on):
    from src.game.engine.effects import get_combat_modifiers

    p = _holder(9)
    session, enemy = _session(p)
    ctx = TurnContext(actor=p, target=enemy, session=session)
    p.hdm_form_turn_counter = 6  # next tick completes the 7-turn cadence
    _hon_don_ma_form(ctx)
    assert p.has_effect("BuffHonDonChanThan")
    mods = get_combat_modifiers(p)
    assert mods.get("atk_pct", 0.0) >= 0.40
    assert mods.get("def_pct", 0.0) >= 0.40


def test_form_respects_cadence(_flag_on):
    p = _holder(9)
    session, enemy = _session(p)
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _hon_don_ma_form(ctx)  # counter 0→1
    assert not p.has_effect("BuffHonDonChanThan")


# ── Dormancy ─────────────────────────────────────────────────────────────────


def test_dormant_without_levels():
    p = build_player_combatant(_make_char(level=None), [_PHYS, _MAGIC])
    assert _MAGIC in p.skill_keys          # no lock while dormant
    assert p.hdm_hp_true_dmg_pct == 0.0
    assert p.hdm_form_interval == 0
