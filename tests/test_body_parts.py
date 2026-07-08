"""Tests for the Bách Thể Chú Linh body-part infusion system (Thể Tu rework)."""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.systems import body_parts as bp
from src.game.systems.the_chat import max_slots


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


# ── Slot rework ───────────────────────────────────────────────────────────────


def test_max_slots_is_one_for_every_path_and_realm():
    for axis in ("body", "qi", "formation", None):
        for realm in range(9):
            assert max_slots(axis, realm) == 1


# ── Part definitions & unlock ─────────────────────────────────────────────────


def test_nine_parts_one_per_body_realm():
    assert len(bp.BODY_PARTS) == 9
    assert [p.tier for p in bp.BODY_PARTS] == list(range(9))
    assert len({p.key for p in bp.BODY_PARTS}) == 9


def test_parts_unlock_at_matching_realm():
    assert len(bp.unlocked_parts(0)) == 1
    assert len(bp.unlocked_parts(4)) == 5
    assert len(bp.unlocked_parts(8)) == 9
    part = bp.BODY_PARTS[5]
    assert not bp.is_part_unlocked(part, 4)
    assert bp.is_part_unlocked(part, 5)


def test_part_power_mult_grows_linearly():
    assert bp.part_power_mult(0) == 1.0
    assert bp.part_power_mult(8) == pytest.approx(3.0)


# ── Infusion cost ─────────────────────────────────────────────────────────────


def test_infusion_cost_scales_with_tier_within_each_rarity():
    for rarity in bp.ESSENCE_TIERS:
        costs = [bp.infusion_cost(p, {"essence_tier": rarity}) for p in bp.BODY_PARTS]
        assert costs == sorted(costs), f"{rarity} cost curve not monotonic"
        assert costs[0] >= 1
    normal_costs = [bp.infusion_cost(p, {"essence_tier": "normal"}) for p in bp.BODY_PARTS]
    assert normal_costs[0] == 3 and normal_costs[-1] == 11


def test_infusion_cost_falls_as_rarity_climbs():
    """Rarer blood needs fewer copies — scarcity lives in the drop rate."""
    ladder = ("normal", "magic", "rare", "legendary")
    for part in bp.BODY_PARTS:
        costs = [bp.infusion_cost(part, {"essence_tier": r}) for r in ladder]
        assert all(a >= b for a, b in zip(costs, costs[1:])), (
            f"cost ladder not non-increasing at tier {part.tier}: {costs}"
        )


def test_unknown_rarity_prices_like_normal():
    part = bp.BODY_PARTS[4]
    assert bp.infusion_cost(part, {"essence_tier": "typo"}) == bp.infusion_cost(
        part, {"essence_tier": "normal"}
    )


# ── Storage codec ─────────────────────────────────────────────────────────────


def test_parse_encode_roundtrip():
    infusions = {
        "huyet_dich": {"essence": "manh_ho", "fed": 12},
        "cot_cach": {"essence": "chan_long", "fed": 3},
    }
    assert bp.parse_infusions(bp.encode_infusions(infusions)) == infusions


def test_parse_accepts_legacy_string_values():
    legacy = '{"huyet_dich": "manh_ho"}'
    assert bp.parse_infusions(legacy) == {
        "huyet_dich": {"essence": "manh_ho", "fed": 0}
    }


def test_parse_is_defensive_against_garbage():
    assert bp.parse_infusions(None) == {}
    assert bp.parse_infusions("") == {}
    assert bp.parse_infusions("not json") == {}
    assert bp.parse_infusions("[1,2]") == {}
    # Unknown part keys are dropped at read time.
    assert bp.parse_infusions('{"no_such_part": {"essence": "manh_ho", "fed": 5}}') == {}
    # Negative / garbage fed values clamp to 0.
    parsed = bp.parse_infusions('{"huyet_dich": {"essence": "manh_ho", "fed": -3}}')
    assert parsed["huyet_dich"]["fed"] == 0


# ── Awakening (Giác Tỉnh) ────────────────────────────────────────────────────


def test_awaken_threshold_grows_with_part_tier():
    item = bp.essence_item("manh_ho")
    t0 = bp.awaken_threshold(bp.BODY_PARTS[0], item)
    t8 = bp.awaken_threshold(bp.BODY_PARTS[8], item)
    assert t0 == 30 and t8 == 30 + 16


def test_mythic_awakens_sooner_than_normal():
    part = bp.BODY_PARTS[0]
    assert bp.awaken_threshold(part, bp.essence_item("chan_long")) < bp.awaken_threshold(
        part, bp.essence_item("manh_ho")
    )


def test_is_awakened_flips_at_threshold():
    part = bp.BODY_PARTS[0]
    threshold = bp.awaken_threshold(part, bp.essence_item("manh_ho"))
    assert not bp.is_awakened(part, {"essence": "manh_ho", "fed": threshold - 1})
    assert bp.is_awakened(part, {"essence": "manh_ho", "fed": threshold})
    assert not bp.is_awakened(part, None)
    assert not bp.is_awakened(part, {"essence": "no_such", "fed": 999})


def test_awakened_bonuses_add_special_effect_kit():
    base = bp.scaled_essence_bonuses("manh_ho", 0)
    awakened = bp.scaled_essence_bonuses("manh_ho", 0, awakened=True)
    item = bp.essence_item("manh_ho")
    awaken_kit = item["awakening"]["stat_bonuses"]
    # Awakening kit merges on top (atk_pct appears in both kits → sums).
    assert awakened["crit_dmg_rating"] == awaken_kit["crit_dmg_rating"]
    assert awakened["atk_pct"] == pytest.approx(
        base["atk_pct"] + awaken_kit["atk_pct"]
    )


def test_awakening_kit_scales_with_part_tier_except_no_scale():
    aw0 = bp.scaled_essence_bonuses("manh_ho", 0, awakened=True)
    aw8 = bp.scaled_essence_bonuses("manh_ho", 8, awakened=True)
    assert aw8["crit_dmg_rating"] == round(aw0["crit_dmg_rating"] * 3.0)
    # chan_long's awakening final_dmg_bonus is NO_SCALE — identical at any tier.
    cl0 = bp.scaled_essence_bonuses("chan_long", 0, awakened=True)
    cl8 = bp.scaled_essence_bonuses("chan_long", 8, awakened=True)
    assert cl0["final_dmg_bonus"] == cl8["final_dmg_bonus"]


def test_compute_bonuses_include_awakening_only_past_threshold():
    part = bp.BODY_PARTS[0]
    threshold = bp.awaken_threshold(part, bp.essence_item("manh_ho"))
    cold = bp.compute_body_part_bonuses(
        {"huyet_dich": {"essence": "manh_ho", "fed": threshold - 1}}, "body", 8,
    )
    hot = bp.compute_body_part_bonuses(
        {"huyet_dich": {"essence": "manh_ho", "fed": threshold}}, "body", 8,
    )
    assert "crit_dmg_rating" not in cold
    assert hot["crit_dmg_rating"] > 0


# ── Granted skills ────────────────────────────────────────────────────────────


def _awakened_entry(essence_key: str, part: bp.BodyPart) -> dict:
    item = bp.essence_item(essence_key)
    return {"essence": essence_key, "fed": bp.awaken_threshold(part, item)}


def test_granted_skills_require_awakening_and_body_axis():
    part = bp.BODY_PARTS[0]
    hot = {"huyet_dich": _awakened_entry("chan_long", part)}
    cold = {"huyet_dich": {"essence": "chan_long", "fed": 0}}
    assert bp.granted_awakening_skills(hot, "body", 8) == ["VitalSkill_LongTucPhanThien"]
    assert bp.granted_awakening_skills(cold, "body", 8) == []
    assert bp.granted_awakening_skills(hot, "qi", 8) == []
    assert bp.granted_awakening_skills(hot, "body", 8) != bp.granted_awakening_skills(None, "body", 8)


def test_normal_essences_grant_no_skill():
    part = bp.BODY_PARTS[0]
    hot = {"huyet_dich": _awakened_entry("manh_ho", part)}
    assert bp.granted_awakening_skills(hot, "body", 8) == []


def test_same_essence_in_two_parts_grants_skill_once():
    infusions = {
        "huyet_dich": _awakened_entry("chan_long", bp.BODY_PARTS[0]),
        "bi_phu": _awakened_entry("chan_long", bp.BODY_PARTS[1]),
    }
    assert bp.granted_awakening_skills(infusions, "body", 8) == ["VitalSkill_LongTucPhanThien"]


def test_granted_skill_lands_on_player_combatant():
    from src.game.models.character import Character
    from src.game.systems.combat import build_player_combatant

    part = bp.BODY_PARTS[0]
    char = Character(
        player_id=1, discord_id=1, name="t", body_realm=8,
        active_axis="body", constitution_type="",
        body_part_infusions={"huyet_dich": _awakened_entry("phuong_hoang", part)},
    )
    combatant = build_player_combatant(char, ["SkillAtkKim3"])
    assert "VitalSkill_PhuongVuCuuThien" in combatant.skill_keys


def test_compute_bonuses_flow_through_combat_stats():
    """Infusions must land in compute_combat_stats for a Thể Tu character."""
    from src.game.models.character import Character
    from src.game.systems.character_stats import compute_combat_stats

    base = Character(player_id=1, discord_id=1, name="t", body_realm=3,
                     active_axis="body", constitution_type="")
    infused = Character(player_id=1, discord_id=1, name="t", body_realm=3,
                        active_axis="body", constitution_type="",
                        body_part_infusions={"huyet_dich": {"essence": "thiet_quy", "fed": 0}})
    cs_base = compute_combat_stats(base)
    cs_inf = compute_combat_stats(infused)
    assert cs_inf.hp_max > cs_base.hp_max
    assert cs_inf.def_stat > cs_base.def_stat


def test_compute_bonuses_requires_body_axis():
    infusions = {"huyet_dich": {"essence": "manh_ho", "fed": 0}}
    assert bp.compute_body_part_bonuses(infusions, "qi", 8) == {}
    assert bp.compute_body_part_bonuses(infusions, "formation", 8) == {}
    assert bp.compute_body_part_bonuses(infusions, None, 8) == {}
    assert bp.compute_body_part_bonuses(infusions, "body", 8) != {}


def test_compute_bonuses_skips_locked_parts():
    infusions = {"than_hon": {"essence": "manh_ho", "fed": 0}}   # tier 8 part
    assert bp.compute_body_part_bonuses(infusions, "body", 3) == {}
    assert bp.compute_body_part_bonuses(infusions, "body", 8) != {}


def test_compute_bonuses_tolerates_legacy_string_entries():
    legacy = {"huyet_dich": "manh_ho"}
    assert bp.compute_body_part_bonuses(legacy, "body", 8) != {}


def test_compute_bonuses_merges_across_parts():
    infusions = {
        "huyet_dich": {"essence": "manh_ho", "fed": 0},
        "bi_phu": {"essence": "manh_ho", "fed": 0},
    }
    merged = bp.compute_body_part_bonuses(infusions, "body", 8)
    t0 = bp.scaled_essence_bonuses("manh_ho", 0)
    t1 = bp.scaled_essence_bonuses("manh_ho", 1)
    assert merged["atk_pct"] == pytest.approx(t0["atk_pct"] + t1["atk_pct"])
    assert merged["crit_rating"] == t0["crit_rating"] + t1["crit_rating"]


# ── Base scaling (unchanged rules) ────────────────────────────────────────────


def test_scaled_bonuses_apply_part_multiplier():
    tier0 = bp.scaled_essence_bonuses("manh_ho", 0)
    tier8 = bp.scaled_essence_bonuses("manh_ho", 8)
    assert tier8["atk_pct"] == pytest.approx(tier0["atk_pct"] * 3.0)
    assert tier8["crit_rating"] == round(tier0["crit_rating"] * 3.0)


def test_no_scale_stats_stay_at_base():
    tier8 = bp.scaled_essence_bonuses("phuong_hoang", 8)
    item = bp.essence_item("phuong_hoang")
    assert tier8["phoenix_revive_pct"] == item["stat_bonuses"]["phoenix_revive_pct"]
    long8 = bp.scaled_essence_bonuses("chan_long", 8)
    long_item = bp.essence_item("chan_long")
    assert long8["true_dmg_pct"] == long_item["stat_bonuses"]["true_dmg_pct"]
    assert long8["final_dmg_bonus"] == long_item["stat_bonuses"]["final_dmg_bonus"]


def test_soul_drain_and_stat_steal_never_part_scale():
    """2026-07-08 nerf: the permanent-mutation proc chances (Đào Ngột's
    soul drain / stat steal) merge at authored value — the full tier-9
    awakened+affinity+resonance stack previously ballooned 0.10 → 0.68/1.02."""
    full = bp.scaled_essence_bonuses(
        "dao_ngot", 9, True, affinity=True, resonance=6, breadth=1.45,
    )
    item = bp.essence_item("dao_ngot")
    assert full["soul_drain_on_hit_pct"] == item["stat_bonuses"]["soul_drain_on_hit_pct"]
    assert full["stat_steal_on_hit_pct"] == (
        item["awakening"]["stat_bonuses"]["stat_steal_on_hit_pct"]
    )
    assert full["soul_drain_on_hit_pct"] == pytest.approx(0.10)
    assert full["stat_steal_on_hit_pct"] == pytest.approx(0.10)


def test_nested_dict_bonuses_scale_per_subkey():
    tier4 = bp.scaled_essence_bonuses("doc_xa", 4)
    base = bp.essence_item("doc_xa")["stat_bonuses"]["dot_dmg_bonus_by_kind"]["poison"]
    assert tier4["dot_dmg_bonus_by_kind"]["poison"] == pytest.approx(base * 2.0)


def test_unknown_essence_yields_empty():
    assert bp.scaled_essence_bonuses("no_such_essence", 3) == {}


# ── Essence catalogue integrity ───────────────────────────────────────────────


def test_essence_catalogue_shape():
    """Distinct beast types per rarity: 5+5+5+5 farmable + 4 mythic."""
    items = bp.all_essence_items()
    assert len(items) == 24
    keys = {i["essence_key"] for i in items}
    assert len(keys) == 24
    tiers = [i.get("essence_tier") for i in items]
    for rarity in ("normal", "magic", "rare", "legendary"):
        assert tiers.count(rarity) == 5, f"expected 5 {rarity} beast types"
    assert tiers.count("mythic") == 4
    for item in items:
        assert item.get("essence_tier") in bp.ESSENCE_TIERS
        assert item.get("stat_bonuses"), f"{item['key']} missing stat_bonuses"
        assert item.get("vi") and item.get("description_vi")


def test_every_essence_has_an_awakening():
    for item in bp.all_essence_items():
        awakening = item.get("awakening")
        assert awakening, f"{item['key']} missing awakening block"
        assert int(awakening.get("threshold", 0)) > 0
        assert awakening.get("stat_bonuses"), f"{item['key']} awakening has no effects"
        assert awakening.get("desc_vi"), f"{item['key']} awakening has no description"


# ── Part affinity (Bộ Vị Tương Thích) ────────────────────────────────────────


def test_every_essence_has_a_valid_archetype():
    for item in bp.all_essence_items():
        assert item.get("archetype") in bp.ARCHETYPE_VI, (
            f"{item['key']} has bad archetype {item.get('archetype')!r}"
        )


def test_every_part_has_a_valid_favored_archetype():
    assert set(bp.PART_AFFINITY) == {p.key for p in bp.BODY_PARTS}
    assert set(bp.PART_AFFINITY.values()) <= set(bp.ARCHETYPE_VI)


def test_affinity_multiplies_scalable_stats_only():
    base = bp.scaled_essence_bonuses("manh_ho", 0)
    boosted = bp.scaled_essence_bonuses("manh_ho", 0, affinity=True)
    assert boosted["atk_pct"] == pytest.approx(base["atk_pct"] * bp.AFFINITY_POTENCY_MULT)
    # NO_SCALE stats ignore affinity too.
    cl = bp.scaled_essence_bonuses("chan_long", 0)
    cl_aff = bp.scaled_essence_bonuses("chan_long", 0, affinity=True)
    assert cl_aff["true_dmg_pct"] == cl["true_dmg_pct"]


def test_compute_bonuses_apply_affinity_on_matching_part():
    # doc_xa is am_doc; huyet_dich favors am_doc → match. bi_phu (cuong_the) → no match.
    matched = bp.compute_body_part_bonuses(
        {"huyet_dich": {"essence": "doc_xa", "fed": 0}}, "body", 8,
    )
    plain_kit = bp.scaled_essence_bonuses("doc_xa", 0)
    assert matched["poison_on_hit_pct"] == pytest.approx(
        plain_kit["poison_on_hit_pct"] * bp.AFFINITY_POTENCY_MULT
    )
    unmatched = bp.compute_body_part_bonuses(
        {"bi_phu": {"essence": "doc_xa", "fed": 0}}, "body", 8,
    )
    kit_t1 = bp.scaled_essence_bonuses("doc_xa", 1)
    assert unmatched["poison_on_hit_pct"] == pytest.approx(kit_t1["poison_on_hit_pct"])


# ── Huyết Mạch Cộng Hưởng (resonance) ────────────────────────────────────────


def _same_essence_infusions(essence: str, n: int) -> dict:
    return {p.key: {"essence": essence, "fed": 0} for p in bp.BODY_PARTS[:n]}


def test_resonance_count_only_counts_unlocked_matching_parts():
    infusions = _same_essence_infusions("manh_ho", 4)
    infusions["ngu_tang"] = {"essence": "doc_xa", "fed": 0}
    assert bp.resonance_count(infusions, 8, "manh_ho") == 4
    assert bp.resonance_count(infusions, 8, "doc_xa") == 1
    # body_realm 1 → only tiers 0-1 unlocked.
    assert bp.resonance_count(infusions, 1, "manh_ho") == 2


def test_resonance_tier1_boosts_everything_at_three_parts():
    base = bp.scaled_essence_bonuses("manh_ho", 0)
    reso = bp.scaled_essence_bonuses("manh_ho", 0, resonance=bp.RESONANCE_T1_COUNT)
    assert reso["atk_pct"] == pytest.approx(base["atk_pct"] * bp.RESONANCE_T1_MULT)
    below = bp.scaled_essence_bonuses("manh_ho", 0, resonance=bp.RESONANCE_T1_COUNT - 1)
    assert below == base


def test_resonance_tier2_amplifies_awakening_kit_only():
    t1 = bp.scaled_essence_bonuses("manh_ho", 0, awakened=True,
                                   resonance=bp.RESONANCE_T1_COUNT)
    t2 = bp.scaled_essence_bonuses("manh_ho", 0, awakened=True,
                                   resonance=bp.RESONANCE_T2_COUNT)
    # crit_rating lives only in the BASE kit → unchanged between T1 and T2.
    assert t2["crit_rating"] == t1["crit_rating"]
    # crit_dmg_rating lives only in the AWAKENING kit → ×1.5 at T2
    # (int rounding makes the ratio approximate).
    assert t2["crit_dmg_rating"] / t1["crit_dmg_rating"] == pytest.approx(
        bp.RESONANCE_T2_AWAKEN_MULT, rel=0.02
    )


def test_compute_bonuses_apply_resonance_automatically():
    solo = bp.compute_body_part_bonuses(
        {"bi_phu": {"essence": "manh_ho", "fed": 0}}, "body", 8,
    )
    trio = bp.compute_body_part_bonuses({
        "bi_phu": {"essence": "manh_ho", "fed": 0},
        "can_mach": {"essence": "manh_ho", "fed": 0},
        "ngu_tang": {"essence": "manh_ho", "fed": 0},
    }, "body", 8)
    solo_share = solo["crit_rating"]
    # The bi_phu share inside the trio must exceed its solo value by ×1.15.
    assert trio["crit_rating"] > solo_share * 3  # all three shares boosted


# ── Giác Tỉnh breadth (awakened-part compounding) ────────────────────────────


def test_awakened_breadth_mult_counts_awakened_unlocked_parts():
    part0, part1 = bp.BODY_PARTS[0], bp.BODY_PARTS[1]
    infusions = {
        part0.key: _awakened_entry("manh_ho", part0),
        part1.key: {"essence": "manh_ho", "fed": 0},          # not awakened
    }
    assert bp.awakened_breadth_mult(infusions, 8) == pytest.approx(
        1.0 + bp.AWAKENED_BREADTH_PER_PART
    )
    assert bp.awakened_breadth_mult(None, 8) == 1.0


def test_breadth_scales_all_contributions():
    part = bp.BODY_PARTS[0]
    one = bp.compute_body_part_bonuses(
        {part.key: _awakened_entry("manh_ho", part)}, "body", 8,
    )
    # Same part entry, but a second awakened part elsewhere raises breadth.
    part8 = bp.BODY_PARTS[8]
    two = bp.compute_body_part_bonuses({
        part.key: _awakened_entry("manh_ho", part),
        part8.key: _awakened_entry("thiet_quy", part8),
    }, "body", 8)
    # manh_ho's crit_rating share grew by the breadth step (1.05 → 1.10).
    ratio = 1.0 + 2 * bp.AWAKENED_BREADTH_PER_PART
    base_ratio = 1.0 + bp.AWAKENED_BREADTH_PER_PART
    assert two["crit_rating"] / one["crit_rating"] == pytest.approx(
        ratio / base_ratio, rel=0.02
    )


# ── First-pass effect upgrades ────────────────────────────────────────────────


def test_quy_tuc_endure_does_not_scale_with_tier():
    aw0 = bp.scaled_essence_bonuses("thiet_quy", 0, awakened=True)
    aw8 = bp.scaled_essence_bonuses("thiet_quy", 8, awakened=True)
    assert aw0["endure_threshold_pct"] == aw8["endure_threshold_pct"] == 0.20
    assert aw0["endure_cooldown"] == aw8["endure_cooldown"] == 8


def test_loc_linh_overheal_conversion_kit():
    kit = bp.scaled_essence_bonuses("ngan_giac_loc", 8, awakened=True)
    assert kit["overheal_to_shield_pct"] == 0.50     # NO_SCALE conversion
    assert kit["shield_max_flat"] > 0                # room for the shield
    assert kit["heal_can_crit"] is True


def test_overheal_to_shield_flows_into_combat_stats():
    from src.game.models.character import Character
    from src.game.systems.character_stats import compute_combat_stats

    part = bp.BODY_PARTS[4]                          # ngu_tang — thanh_linh affinity
    item = bp.essence_item("ngan_giac_loc")
    char = Character(
        player_id=1, discord_id=1, name="t", body_realm=8,
        active_axis="body", constitution_type="",
        body_part_infusions={
            "ngu_tang": {"essence": "ngan_giac_loc",
                         "fed": bp.awaken_threshold(part, item)},
        },
    )
    cs = compute_combat_stats(char)
    assert cs.overheal_to_shield_pct == pytest.approx(0.50)
    assert cs.shield_max > 0


def test_overheal_converts_to_shield_in_apply_heal():
    from src.game.models.character import Character
    from src.game.systems.combat import (
        CombatSession, build_enemy_combatant, build_player_combatant,
    )

    part = bp.BODY_PARTS[4]
    item = bp.essence_item("ngan_giac_loc")
    char = Character(
        player_id=1, discord_id=1, name="t", body_realm=8,
        active_axis="body", constitution_type="",
        body_part_infusions={
            "ngu_tang": {"essence": "ngan_giac_loc",
                         "fed": bp.awaken_threshold(part, item)},
        },
    )
    player_c = build_player_combatant(char, ["SkillAtkKim3"])
    player_c.heal_can_crit = False                   # deterministic heal
    player_c.shield = 0
    enemy_c = build_enemy_combatant("CommonHoaThu", 10)
    session = CombatSession(player=player_c, enemy=enemy_c,
                            player_skill_keys=["SkillAtkKim3"])
    player_c.hp = player_c.hp_max - 10
    session._apply_heal(player_c, 200)               # 10 applied, 190 overheal
    assert player_c.hp == player_c.hp_max
    assert player_c.shield == min(95, player_c.shield_cap())
    assert player_c.shield > 0


def test_dao_ngot_and_huyen_vu_skills_carry_signature_debuffs():
    import json
    from pathlib import Path

    dao = registry.get_skill("VitalSkill_DaoNgotMaDiem")
    assert set(dao["effects"]) == {"DebuffSuyKhi", "DebuffPhapNhuoc"}
    hv = registry.get_skill("VitalSkill_HuyenVuTranHai")
    assert hv["effects"] == ["DebuffTroBuoc"]
    debuff_keys = {
        e["key"] for e in json.loads(
            Path("src/data/effects/debuffs.json").read_text(encoding="utf-8")
        )
    }
    for key in ("DebuffSuyKhi", "DebuffPhapNhuoc", "DebuffTroBuoc"):
        assert key in debuff_keys


def test_legendary_and_mythic_awakenings_grant_real_skills():
    for item in bp.all_essence_items():
        tier = item.get("essence_tier")
        skill_key = (item.get("awakening") or {}).get("granted_skill")
        if tier in ("legendary", "mythic"):
            assert skill_key, f"{item['key']} ({tier}) should grant a skill"
            skill = registry.get_skill(skill_key)
            assert skill is not None, f"{skill_key} missing from registry"
            assert skill.get("no_scroll"), f"{skill_key} must be no_scroll"
            # No shop/loot scroll may exist for an awakening-only skill.
            assert registry.get_item(f"Scroll_{skill_key}") is None
        else:
            assert not skill_key, f"{item['key']} ({tier}) should not grant a skill"
