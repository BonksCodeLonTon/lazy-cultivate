"""Tests for Hóa Hình — the 9/9 one-bloodline beast transformation."""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.models.character import Character
from src.game.systems import body_parts as bp
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.hoa_hinh import try_hoa_hinh


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _full_body(essence: str) -> dict[str, dict]:
    return {p.key: {"essence": essence, "fed": 0} for p in bp.BODY_PARTS}


def _armed_player(essence: str = "manh_ho"):
    char = Character(player_id=1, discord_id=1, name="t",
                     body_realm=8, body_level=9, qi_realm=8, qi_level=9,
                     active_axis="body", constitution_type="",
                     body_part_infusions=_full_body(essence))
    return build_player_combatant(char, ["SkillAtkKim3"])


def _session(player_c):
    enemy_c = build_enemy_combatant("CommonHoaThu", 30)
    return CombatSession(player=player_c, enemy=enemy_c,
                         player_skill_keys=["SkillAtkKim3"])


# ── Form detection ────────────────────────────────────────────────────────────


def test_form_requires_nine_same_essence_parts():
    full = _full_body("manh_ho")
    assert bp.hoa_hinh_form(full, "body", 8) == ("BuffHoaHinhCuongLuc", "Tinh Huyết Mãnh Hổ")

    mixed = _full_body("manh_ho")
    mixed["than_hon"] = {"essence": "doc_xa", "fed": 0}
    assert bp.hoa_hinh_form(mixed, "body", 8) is None

    partial = {k: v for k, v in _full_body("manh_ho").items() if k != "than_hon"}
    assert bp.hoa_hinh_form(partial, "body", 8) is None


def test_form_requires_body_axis_and_realm_eight():
    full = _full_body("manh_ho")
    assert bp.hoa_hinh_form(full, "qi", 8) is None
    assert bp.hoa_hinh_form(full, "body", 7) is None
    assert bp.hoa_hinh_form(None, "body", 8) is None


def test_every_archetype_maps_to_a_real_buff():
    from src.game.engine.effects import EFFECTS
    assert set(bp.HOA_HINH_BUFF_BY_ARCHETYPE) == set(bp.ARCHETYPE_VI)
    for buff_key in bp.HOA_HINH_BUFF_BY_ARCHETYPE.values():
        meta = EFFECTS.get(buff_key)
        assert meta is not None, f"{buff_key} missing from effects registry"
        assert meta.stat_bonus, f"{buff_key} has no payload"


def test_form_follows_essence_archetype():
    # huyen_vu is cuong_the → the mountain-guard form with the per-hit cap.
    form = bp.hoa_hinh_form(_full_body("huyen_vu"), "body", 8)
    assert form is not None and form[0] == "BuffHoaHinhCuongThe"


# ── Builder arming ────────────────────────────────────────────────────────────


def test_builder_arms_the_transformation():
    player_c = _armed_player("phuong_hoang")     # thanh_linh archetype
    assert player_c.hoa_hinh_buff_key == "BuffHoaHinhThanhLinh"
    assert player_c.hoa_hinh_beast_vi == "Tinh Huyết Phượng Hoàng"
    assert player_c.hoa_hinh_used is False


def test_builder_leaves_partial_bodies_unarmed():
    char = Character(player_id=1, discord_id=1, name="t",
                     body_realm=8, active_axis="body", constitution_type="",
                     body_part_infusions={"huyet_dich": {"essence": "manh_ho", "fed": 0}})
    player_c = build_player_combatant(char, ["SkillAtkKim3"])
    assert player_c.hoa_hinh_buff_key == ""


# ── Trigger behavior ──────────────────────────────────────────────────────────


def test_transform_fires_below_threshold_once():
    player_c = _armed_player()
    player_c.heal_can_crit = False
    session = _session(player_c)

    player_c.hp = int(player_c.hp_max * 0.30)    # bloodied
    before = player_c.hp
    try_hoa_hinh(session, player_c)
    assert player_c.hoa_hinh_used is True
    assert player_c.has_effect("BuffHoaHinhCuongLuc")
    assert player_c.hp == before + int(player_c.hp_max * bp.HOA_HINH_HEAL_PCT)
    assert any("HÓA HÌNH" in line for line in session.log)

    # Latch: a second bloodied dip never re-triggers.
    player_c.effects.pop("BuffHoaHinhCuongLuc", None)
    player_c.hp = 1
    try_hoa_hinh(session, player_c)
    assert not player_c.has_effect("BuffHoaHinhCuongLuc")


def test_transform_holds_above_threshold():
    player_c = _armed_player()
    session = _session(player_c)
    player_c.hp = int(player_c.hp_max * 0.80)    # comfortably above the 60% trigger
    try_hoa_hinh(session, player_c)
    assert player_c.hoa_hinh_used is False
    assert not player_c.has_effect("BuffHoaHinhCuongLuc")


def test_unarmed_combatants_are_inert():
    player_c = _armed_player()
    session = _session(player_c)
    enemy = session.enemy
    enemy.hp = 1                                  # bloodied but no form
    try_hoa_hinh(session, enemy)
    assert enemy.hoa_hinh_used is False


def test_transform_payload_applies_to_modifiers():
    from src.game.engine.effects import get_combat_modifiers
    player_c = _armed_player("huyen_vu")          # cuong_the form
    session = _session(player_c)
    player_c.hp = int(player_c.hp_max * 0.30)
    try_hoa_hinh(session, player_c)
    mods = get_combat_modifiers(player_c)
    assert float(mods.get("max_dmg_per_hit_pct_hp_max", 0.0)) == pytest.approx(0.15)
    assert float(mods.get("final_dmg_reduce", 0.0)) >= 0.10


def test_transform_fires_inside_a_real_combat_round():
    """End-to-end: a bloodied armed player transforms during session.step()."""
    player_c = _armed_player()
    session = _session(player_c)
    # Inflate the dummy's HP so the round completes (the hook runs at
    # round end — a one-shot enemy would end the fight before it).
    session.enemy.hp = session.enemy.hp_max = 5_000_000
    player_c.hp = max(1, int(player_c.hp_max * 0.20))
    for _ in range(20):                           # cover at least one full round
        _, result = session.step()
        if player_c.hoa_hinh_used or result is not None:
            break
    assert player_c.hoa_hinh_used is True
    assert any("HÓA HÌNH" in line for line in session.log)
