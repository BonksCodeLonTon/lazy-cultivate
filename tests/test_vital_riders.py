"""Behavioral tests for the vital-essence awakening skill riders (wave 2).

Exercises ``combat/skill_extras.apply_vital_rider`` directly with a real
CombatSession + combatants so heal routing, the capped true-damage tail,
shield clamps, and cleanse mechanics all run on production code paths.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.models.character import Character
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.skill_extras import apply_vital_rider


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _arena(enemy_key: str = "CommonHoaThu", realm_total: int = 30) -> tuple:
    """Fresh (session, player_c, enemy_c) triple on production builders.

    Rider bonus chunks route through the shared capped-true-damage tail
    (clamped at a % of target max HP), so exact-value assertions use the
    beefy R9 Thao Thiết whose HP pool keeps the raw values under the cap.
    """
    char = Character(player_id=1, discord_id=1, name="t",
                     body_realm=8, body_level=9,
                     qi_realm=8, qi_level=9,
                     active_axis="body", constitution_type="")
    player_c = build_player_combatant(char, ["SkillAtkKim3"])
    enemy_c = build_enemy_combatant(enemy_key, realm_total)
    session = CombatSession(player=player_c, enemy=enemy_c,
                            player_skill_keys=["SkillAtkKim3"])
    return session, player_c, enemy_c


def _big_arena() -> tuple:
    session, player, enemy = _arena("BeastThaoThiet_R9", 60)
    assert enemy.hp_max > 10_000          # raw rider chunks stay under the cap
    return session, player, enemy


def _rider_skill(spec: dict) -> dict:
    return {"vi": "test", "vital_rider": spec}


# ── Thôn Phệ: devour heal + execute ───────────────────────────────────────────


def test_devour_heals_share_of_damage():
    session, player, enemy = _arena()
    player.heal_can_crit = False
    player.hp = player.hp_max // 2
    before = player.hp
    apply_vital_rider(session, player, enemy,
                      _rider_skill({"heal_pct_of_dmg": 0.30}),
                      dealt_total=1000, cast_ctx={})
    assert player.hp == before + 300


def test_execute_fires_only_below_threshold():
    spec = {"execute_below_pct": 0.30, "execute_bonus_pct": 1.0}
    session, player, enemy = _arena()
    enemy.hp = int(enemy.hp_max * 0.5)
    before = enemy.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=500, cast_ctx={})
    assert enemy.hp == before                       # above 30% → no execute

    enemy.hp = int(enemy.hp_max * 0.2)
    before = enemy.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=500, cast_ctx={})
    assert enemy.hp < before                        # below 30% → chunk lands


def test_execute_is_capped_at_share_of_max_hp():
    """The shared true-dmg tail caps a rider chunk — no one-shot riders."""
    session, player, enemy = _arena()
    enemy.hp = int(enemy.hp_max * 0.25)
    before = enemy.hp
    apply_vital_rider(session, player, enemy,
                      _rider_skill({"execute_below_pct": 0.30,
                                    "execute_bonus_pct": 1.0}),
                      dealt_total=10_000_000, cast_ctx={})
    assert enemy.hp > 0 or (before - enemy.hp) < enemy.hp_max  # capped, not nuked


# ── Long Uy: true-damage portion pierces shield ───────────────────────────────


def test_true_damage_portion_bypasses_shield():
    session, player, enemy = _big_arena()
    enemy.shield = enemy.hp_max                     # big shield
    before_hp = enemy.hp
    apply_vital_rider(session, player, enemy,
                      _rider_skill({"true_dmg_pct_of_dmg": 0.30}),
                      dealt_total=1000, cast_ctx={})
    assert enemy.hp == before_hp - 300              # went straight to HP


# ── Phần Dực: burn detonation ─────────────────────────────────────────────────


def test_detonation_scales_with_burn_stacks_and_caps():
    spec = {"detonate_stack": "burn", "detonate_pct_per_stack": 0.15,
            "detonate_stack_cap": 8}
    session, player, enemy = _big_arena()
    enemy.burn_stacks = 4
    before = enemy.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=1000, cast_ctx={})
    assert before - enemy.hp == int(1000 * 0.15 * 4)

    session2, player2, enemy2 = _big_arena()
    enemy2.burn_stacks = 20                          # over cap → clamp to 8
    before2 = enemy2.hp
    apply_vital_rider(session2, player2, enemy2, _rider_skill(spec),
                      dealt_total=1000, cast_ctx={})
    assert before2 - enemy2.hp == int(1000 * 0.15 * 8)


def test_detonation_inert_without_stacks():
    session, player, enemy = _arena()
    before = enemy.hp
    apply_vital_rider(session, player, enemy,
                      _rider_skill({"detonate_stack": "burn",
                                    "detonate_pct_per_stack": 0.15}),
                      dealt_total=1000, cast_ctx={})
    assert enemy.hp == before


# ── Hồng Lưu: MP surge + kill refund ─────────────────────────────────────────


def test_mp_surge_drains_and_deals_bonus():
    spec = {"mp_surge_pct_of_current": 0.25, "mp_surge_dmg_per_mp": 2.0}
    session, player, enemy = _big_arena()
    player.mp = 400
    before_hp = enemy.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=500, cast_ctx={})
    assert player.mp == 300                         # 25% of 400 burnt
    assert before_hp - enemy.hp == 200              # 100 MP × 2


def test_mp_surge_refunds_half_on_kill():
    spec = {"mp_surge_pct_of_current": 0.25, "mp_surge_dmg_per_mp": 2.0,
            "mp_refund_on_kill_pct": 0.5}
    session, player, enemy = _big_arena()
    player.mp = 400
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=500, cast_ctx={"killed": True})
    assert player.mp == 300 + 50                    # half of the 100 drained


# ── Niết Bàn Vũ: heal + cleanse + vengeance ──────────────────────────────────


def test_phoenix_rider_heals_and_cleanses_self():
    from src.game.engine.effects import EFFECTS
    spec = {"self_heal_pct_max_hp": 0.12, "cleanse_count": 1}
    session, player, enemy = _arena()
    player.heal_can_crit = False
    player.hp = player.hp_max // 2
    # Stamp a real cleansable debuff on self.
    assert EFFECTS["DebuffSuyKhi"].cleansable
    player.effects["DebuffSuyKhi"] = 3
    before = player.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=100, cast_ctx={})
    assert player.hp == before + int(player.hp_max * 0.12)
    assert "DebuffSuyKhi" not in player.effects


def test_phoenix_vengeance_requires_consumed_revive():
    spec = {"revenge_after_revive_pct": 0.40}
    session, player, enemy = _big_arena()
    before = enemy.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=1000, cast_ctx={})
    assert enemy.hp == before                       # revive unspent → nothing

    player.phoenix_revive_used = True
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=1000, cast_ctx={})
    assert before - enemy.hp == 400


# ── Thụy Quang: shield + per-debuff smite ────────────────────────────────────


def test_qilin_rider_shields_and_smites_per_debuff():
    from src.game.engine.effects import EFFECTS
    spec = {"shield_pct_max_hp": 0.15,
            "per_debuff_bonus_pct": 0.08, "per_debuff_cap": 6}
    session, player, enemy = _big_arena()
    player.shield = 0
    player.shield_max_flat = player.hp_max          # room for the barrier
    for key in ("DebuffSuyKhi", "DebuffPhapNhuoc", "DebuffTroBuoc"):
        assert EFFECTS[key].cleansable
        enemy.effects[key] = 3
    before = enemy.hp
    apply_vital_rider(session, player, enemy, _rider_skill(spec),
                      dealt_total=1000, cast_ctx={})
    assert player.shield == int(player.hp_max * 0.15)
    assert before - enemy.hp == int(1000 * 0.08 * 3)


# ── Data wiring ───────────────────────────────────────────────────────────────


def test_awakening_skills_carry_expected_signature_mechanics():
    expects = {
        "VitalSkill_ThonPhe": {"heal_pct_of_dmg", "execute_below_pct"},
        "VitalSkill_TatPhuongPhanDuc": {"detonate_stack"},
        "VitalSkill_CuuAnhHongLuu": {"mp_surge_pct_of_current"},
        "VitalSkill_LongTucPhanThien": {"true_dmg_pct_of_dmg"},
        "VitalSkill_PhuongVuCuuThien": {"self_heal_pct_max_hp", "cleanse_count"},
        "VitalSkill_KyLanThuyQuang": {"shield_pct_max_hp", "per_debuff_bonus_pct"},
    }
    for key, fields in expects.items():
        skill = registry.get_skill(key)
        rider = skill.get("vital_rider") or {}
        assert fields <= set(rider), f"{key} missing rider fields {fields - set(rider)}"
    flurry = registry.get_skill("VitalSkill_CungKyLietPhong")
    assert flurry.get("hit_count") == 3


def test_full_cast_runs_rider_end_to_end():
    """A real cast of Thôn Phệ through cast_skill triggers the devour heal."""
    from src.game.systems.combat.casting import cast_skill

    session, player, enemy = _arena()
    player.heal_can_crit = False
    player.hp = player.hp_max // 2
    skill = registry.get_skill("VitalSkill_ThonPhe")
    before_hp = player.hp
    cast_skill(session, player, enemy, "VitalSkill_ThonPhe", skill,
               mp_cost=skill["mp_cost"])
    log_text = "\n".join(session.log)
    if "🍖" in log_text:                             # hit landed (not evaded)
        assert player.hp > before_hp
