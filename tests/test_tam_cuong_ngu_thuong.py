"""Characterization tests for the general formation ``TamCuongNguThuong``.

Pins the contract of:

  * ``TamCuongNguThuong`` (src/data/formations/general.json) — a general
    (element-neutral) formation whose 5-gem ladder (1/3/5/7/10) encodes the
    Ngũ Thường (Nhân/Nghĩa/Lễ/Trí/Tín) as a cumulative stat ladder, plus a
    passive ``base_stat_bonus``.
  * The embedded active ``SkillFrmTamCuong`` — registered into the skill
    table by ``_load_formations`` with the ``formation_key`` back-ref. It is
    a control formation that lands two debuffs on the target
    (``DebuffPhaGiap`` −10% dmg reduce / 3t, ``DebuffLamCham`` −25% SPD / 2t).
  * Casting the active through the real effect path stamps both bonds
    on the target with the documented magnitudes + durations.

Schema/registration pattern mirrors the two existing general formations
(``KiemTran`` / ``CuuCungBatQua``); the on-hit application mirrors how other
debuff-applying skills are exercised via ``apply_skill_effects``.
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, get_combat_modifiers
from src.game.systems.combat.casting import apply_skill_effects

from tests.conftest import make_combatant, make_session

_FORM_KEY = "TamCuongNguThuong"
_SKILL_KEY = "SkillFrmTamCuong"
_BONDS = ("DebuffPhaGiap", "DebuffLamCham")


# ── Formation schema + ladder ───────────────────────────────────────────


def test_formation_loads_with_base_bonus():
    form = registry.get_formation(_FORM_KEY)

    assert form is not None, f"{_FORM_KEY} missing from registry"
    assert form["element"] is None
    assert form["base_stat_bonus"] == {"hp_pct": 0.04, "mp_pct": 0.05}
    assert form["formation_skill_key"] == _SKILL_KEY


def test_ngu_thuong_ladder_is_cumulative_and_named():
    """Five tiers at gem counts 1/3/5/7/10, each tagged with its virtue, the
    stat set monotonically growing (cumulative totals)."""
    tiers = registry.get_formation(_FORM_KEY)["gem_threshold_bonuses"]

    assert set(tiers) == {"1", "3", "5", "7", "10"}
    for gem, virtue in (("1", "Nhân"), ("3", "Nghĩa"), ("5", "Lễ"),
                        ("7", "Trí"), ("10", "Tín")):
        assert tiers[gem]["note"].startswith(virtue), gem

    # Each successive tier carries at least the previous tier's stat keys
    # (cumulative ladder) and never lowers a shared stat.
    order = ["1", "3", "5", "7", "10"]
    for lo, hi in zip(order, order[1:]):
        lo_stats = {k: v for k, v in tiers[lo].items() if k != "note"}
        hi_stats = {k: v for k, v in tiers[hi].items() if k != "note"}
        assert set(lo_stats) <= set(hi_stats), f"{hi} dropped a stat from {lo}"
        for k, v in lo_stats.items():
            assert hi_stats[k] >= v, f"{hi}.{k} regressed below {lo}"

    # Capstone (Tín) unites all five dimensions.
    cap = tiers["10"]
    assert cap["hp_pct"] == 0.05
    assert cap["final_dmg_bonus"] == 0.5
    assert cap["res_all"] == 0.1
    assert cap["cooldown_reduce"] == 0.18
    assert cap["all_element_bonus"] == 0.08


# ── Embedded active skill ───────────────────────────────────────────────


def test_active_skill_registered_with_back_ref():
    skill = registry.get_skill(_SKILL_KEY)

    assert skill is not None, f"{_SKILL_KEY} not registered from the formation"
    assert skill["category"] == "formation"
    assert skill["formation_key"] == _FORM_KEY
    assert skill["scroll_grade"] == 3
    assert skill["element"] is None
    # Post damage reshape: base_dmg ÷10 (850→85), mp_cost ÷3 (130→43).
    assert skill["mp_cost"] == 43
    assert skill["cooldown"] == 4
    assert skill["base_dmg"] == 85
    assert list(skill["effects"]) == list(_BONDS)


def test_bond_override_magnitudes():
    ov = registry.get_skill(_SKILL_KEY)["effect_overrides"]

    assert ov["DebuffPhaGiap"]["stat_bonus"]["final_dmg_reduce"] == -0.1
    assert ov["DebuffPhaGiap"]["duration"] == 3
    assert ov["DebuffLamCham"]["stat_bonus"]["spd_pct"] == -0.25
    assert ov["DebuffLamCham"]["duration"] == 2


def test_bond_effects_exist_in_registry():
    for key in _BONDS:
        assert EFFECTS.get(key) is not None, f"{key} missing from EFFECTS"


# ── On-hit application of the three bonds ───────────────────────────────


def test_casting_lands_both_bonds_on_target():
    """Through the real effect path the active stamps both bonds on the
    target with the override magnitudes (apply_chance defaults to 1.0)."""
    actor = make_combatant("formationist")
    target = make_combatant("enemy")
    session = make_session(actor, target)
    skill_data = registry.get_skill(_SKILL_KEY)

    apply_skill_effects(session, skill_data, actor, target, hit=True)

    for key in _BONDS:
        assert target.has_effect(key), f"{key} did not land"
    assert target.effects["DebuffPhaGiap"] == 3
    assert target.effects["DebuffLamCham"] == 2

    # The stamped magnitudes flow through get_combat_modifiers on the target.
    mods = get_combat_modifiers(target)
    assert mods.get("final_dmg_reduce") == -0.1   # PhaGiap: takes more
    assert mods.get("spd_pct") == -0.25           # LamCham: slowed
