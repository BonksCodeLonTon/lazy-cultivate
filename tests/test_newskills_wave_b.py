"""Characterization tests for the 4 Wave-B skills.

Each skill's config block lives on the holder's ``effect_overrides`` (stamped
by a real cast in-game). These tests drive the mechanic at the most
deterministic level available:

  * #1 Nghịch Lưu Phản Phệ — call the post-cast consumer ``_cooldown_reservoir``
    directly with the skill's JSON spec, after seeding the buff + cooldowns.
  * #2 Đồng Quy Vu Tận — stamp the seal override (replicating the apply-time
    stamper) then run the full PERIODIC chain so the priority-80 expiry hook
    invokes ``emit_dong_quy_detonate`` on NATURAL expiry.
  * #4 Mộc Linh Cộng Sinh — ``capture_overheal`` to bank, then run the PERIODIC
    chain so the priority-48 ``overheal_release`` hook pulses the strike.
  * #7 Âm Hồn Khế Ấn — brand the holder then call ``Combatant.take_damage``;
    assert the echo lands as extra true damage.

Spec values are pinned against the source JSON / module constants confirmed at
authoring time:
  - cooldown_reservoir: per=260, cap=6000, wipe_to=1, fdb_per=0.015, fdb_cap=0.25
  - dong_quy: per_pct_diff=0.30, cap_pct=0.45
  - moc_linh: capture=1.0, release=0.5, cap_scale=3.0×matk
  - am_hon: echo_pct=0.18, cap=2500, min_source_dmg=200
"""
from __future__ import annotations

from src.game.systems.combat.cast_consumers import _cooldown_reservoir
from src.game.systems.combat.overheal_reservoir import capture_overheal

from tests.conftest import make_combatant, make_session

# ── JSON spec snapshots (mirror the live skill data) ──────────────────────
_RESERVOIR_SPEC = {
    "shield_per_cd_turn": 260,
    "shield_cap": 6000,
    "wipe_to": 1,
    "buff_fdb_per_cd_turn": 0.015,
    "buff_fdb_cap": 0.25,
}
_DONG_QUY_CFG = {
    "per_pct_diff_dmg_pct": 0.30,
    "cap_pct_hp_max": 0.45,
    "element": "general",
}
_MOC_LINH_CFG = {
    "capture_pct": 1.0,
    "release_pct": 0.5,
    "reservoir_cap_matk_scale": 3.0,
    "element": "moc",
}
_AM_HON_CFG = {
    "echo_pct": 0.18,
    "echo_cap_per_hit": 2500,
    "element": "am",
    "min_source_dmg": 200,
}


# ═══════════════════════════════════════════════════════════════════════════
# #1 — Nghịch Lưu Phản Phệ (cooldown_reservoir consumer)
# ═══════════════════════════════════════════════════════════════════════════
def _seed_reservoir_actor(cooldowns: dict[str, int]):
    """Actor primed for the consumer: live BuffNghichLuu + a big shield cap."""
    actor = make_combatant("a", shield_max_base=20_000)
    enemy = make_combatant("e")
    actor.cooldowns = dict(cooldowns)
    actor.apply_effect("BuffNghichLuu", 3)  # buff is live before the consumer
    session = make_session(actor, enemy)
    return actor, enemy, session


def test_reservoir_converts_cooldowns_to_shield_and_fdb_and_wipes():
    """R = sum(other CDs) = 12 → 3120 shield, 0.18 fdb, A/B/C→1, own CD intact."""
    actor, _enemy, session = _seed_reservoir_actor(
        {"A": 5, "B": 4, "C": 3, "SkillNghichLuuPhanPhe": 8},
    )
    skill_data = {"key": "SkillNghichLuuPhanPhe"}

    _cooldown_reservoir(_RESERVOIR_SPEC, skill_data, actor, actor, session)

    # R = 5 + 4 + 3 = 12 (own key excluded). shield = min(6000, 12×260) = 3120.
    assert actor.shield == 3120
    # fdb = min(0.25, 12×0.015) = 0.18 stamped onto the live buff override.
    fdb = actor.effect_overrides["BuffNghichLuu"]["stat_bonus"]["final_dmg_bonus"]
    assert fdb == 0.18
    # Every OTHER cooldown wiped to wipe_to=1.
    assert actor.cooldowns["A"] == 1
    assert actor.cooldowns["B"] == 1
    assert actor.cooldowns["C"] == 1
    # The casting skill's own freshly-set cooldown is NOT wiped by the consumer.
    assert actor.cooldowns["SkillNghichLuuPhanPhe"] == 8


def test_reservoir_clamps_shield_and_fdb_on_huge_r():
    """Cooldowns summing to 30 → shield clamps 6000, fdb clamps 0.25."""
    actor, _enemy, session = _seed_reservoir_actor(
        {"A": 10, "B": 10, "C": 10, "SkillNghichLuuPhanPhe": 8},
    )
    skill_data = {"key": "SkillNghichLuuPhanPhe"}

    _cooldown_reservoir(_RESERVOIR_SPEC, skill_data, actor, actor, session)

    # R = 30 → 30×260 = 7800 > cap → clamps to 6000.
    assert actor.shield == 6000
    # 30×0.015 = 0.45 > cap → clamps to 0.25.
    fdb = actor.effect_overrides["BuffNghichLuu"]["stat_bonus"]["final_dmg_bonus"]
    assert fdb == 0.25


def test_reservoir_no_op_when_r_is_zero():
    """No other cooldowns → R=0 → no shield, no fdb stamped."""
    actor, _enemy, session = _seed_reservoir_actor(
        {"SkillNghichLuuPhanPhe": 8},  # only own key on cd
    )
    skill_data = {"key": "SkillNghichLuuPhanPhe"}

    _cooldown_reservoir(_RESERVOIR_SPEC, skill_data, actor, actor, session)

    assert actor.shield == 0
    # Override may exist (from apply_effect) but must carry no fdb stamp.
    ovr = actor.effect_overrides.get("BuffNghichLuu", {})
    assert ovr.get("stat_bonus", {}).get("final_dmg_bonus") in (None, 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# #2 — Đồng Quy Vu Tận (HP-race detonate on natural expiry)
# ═══════════════════════════════════════════════════════════════════════════
def _stamp_dong_quy(
    holder, holder_hp_pct_at_apply: float, applier_hp_pct_at_apply: float,
):
    """Replicate the apply-time stamper: snapshot both sides' hp% + config."""
    holder.effects["DebuffDongQuyAn"] = 1  # 1 turn → expires THIS round (natural)
    holder.effect_overrides["DebuffDongQuyAn"] = {
        "_dq_holder_hp_pct": holder_hp_pct_at_apply,
        "_dq_applier_hp_pct": applier_hp_pct_at_apply,
        "expire_detonate_hp_race": dict(_DONG_QUY_CFG),
    }


def test_dong_quy_detonates_on_natural_expiry():
    """Holder bled 40%, applier bled 10% → 0.12 × holder.hp_max bypass-shield hit."""
    # holder = enemy carrying the seal; applier = player (the opponent at detonate).
    holder = make_combatant("enemy", hp=10_000, hp_max=10_000)
    applier = make_combatant("player", hp=10_000, hp_max=10_000)
    # Apply-time: both at full (1.0). Then holder drops to 50%, applier to 90%.
    _stamp_dong_quy(holder, holder_hp_pct_at_apply=1.0, applier_hp_pct_at_apply=1.0)
    holder.hp = 5_000   # enemy_lost = 1.00 - 0.50 = 0.40
    applier.hp = 9_000  # self_lost  = 1.00 - 0.90 = 0.10
    # Give the holder a shield to prove the detonation bypasses it.
    holder.shield_max_base = 5_000
    holder.shield = 5_000

    # holder is the enemy → opponent threaded by the periodic hook is the player.
    session = make_session(applier, holder)
    hp_before = holder.hp

    session._process_periodic(holder)

    # enemy_lost = holder_at_apply(1.0) - holder_now(0.50) = 0.50.
    # self_lost  = applier_at_apply(1.0) - applier_now(0.90) = 0.10.
    # diff = max(0, 0.50 - 0.10) = 0.40 → dmg_pct = min(0.45, 0.40×0.30) = 0.12.
    expected = int(0.12 * holder.hp_max)  # 1200
    lost = hp_before - holder.hp
    assert lost == expected
    # Shield untouched (bypass_shield=True routes straight to HP).
    assert holder.shield == 5_000


def test_dong_quy_no_detonate_on_cleanse():
    """Cleansed seal (popped before expiry) → no detonation damage."""
    holder = make_combatant("enemy", hp=5_000, hp_max=10_000)
    applier = make_combatant("player", hp=9_000, hp_max=10_000)
    _stamp_dong_quy(holder, holder_hp_pct_at_apply=1.0, applier_hp_pct_at_apply=1.0)
    # Cleanse: remove the effect + override before the periodic phase.
    holder.effects.pop("DebuffDongQuyAn", None)
    holder.effect_overrides.pop("DebuffDongQuyAn", None)

    session = make_session(applier, holder)
    hp_before = holder.hp

    session._process_periodic(holder)

    assert holder.hp == hp_before  # nothing detonated


def test_dong_quy_diff_floored_at_zero_when_applier_outbled_holder():
    """Applier lost MORE HP% than holder → diff floored at 0 → no detonation."""
    holder = make_combatant("enemy", hp=9_000, hp_max=10_000)   # lost 10%
    applier = make_combatant("player", hp=5_000, hp_max=10_000)  # lost 50%
    _stamp_dong_quy(holder, holder_hp_pct_at_apply=1.0, applier_hp_pct_at_apply=1.0)

    session = make_session(applier, holder)
    hp_before = holder.hp

    session._process_periodic(holder)

    # enemy_lost=0.10, self_lost=0.50 → diff = max(0, -0.40) = 0 → no blast.
    assert holder.hp == hp_before


# ═══════════════════════════════════════════════════════════════════════════
# #4 — Mộc Linh Cộng Sinh (overheal reservoir capture + release)
# ═══════════════════════════════════════════════════════════════════════════
def _install_moc_linh(holder):
    holder.effects["BuffMocLinhCongSinh"] = 5
    holder.effect_overrides["BuffMocLinhCongSinh"] = {
        "overheal_reservoir": dict(_MOC_LINH_CFG),
    }


def test_moc_linh_captures_under_cap_then_clamps():
    """Overheal banks ×1.0 into _sap; cap = 3.0 × matk = 6000."""
    holder = make_combatant("h", matk=2_000)  # cap = 3.0 × 2000 = 6000
    _install_moc_linh(holder)

    capture_overheal(holder, 5_000)
    sap = holder.effect_overrides["BuffMocLinhCongSinh"]["_sap"]
    assert sap == 5_000  # under the 6000 cap

    # A further 8000 overheal: 5000 + 8000 = 13000 → clamps to cap 6000.
    capture_overheal(holder, 8_000)
    sap = holder.effect_overrides["BuffMocLinhCongSinh"]["_sap"]
    assert sap == 6_000


def test_moc_linh_release_pulses_half_sap_and_decrements():
    """Priority-48 release pulses 50% of _sap as moc damage and decrements bank."""
    holder = make_combatant("h", matk=2_000)
    enemy = make_combatant("e", hp=10_000, hp_max=10_000)
    _install_moc_linh(holder)
    holder.effect_overrides["BuffMocLinhCongSinh"]["_sap"] = 6_000

    session = make_session(holder, enemy)
    hp_before = enemy.hp

    session._process_periodic(holder)

    # release_pct=0.5 → released = 3000. Enemy has 0 moc resistance and holder
    # 0 final_dmg_bonus, so the strike is exactly 3000 (mult=1.0, res=0.0).
    assert hp_before - enemy.hp == 3_000
    # Bank decremented by exactly what was released.
    assert holder.effect_overrides["BuffMocLinhCongSinh"]["_sap"] == 3_000


def test_moc_linh_no_capture_without_buff():
    """Overheal without the buff banks nothing (no override created)."""
    holder = make_combatant("h", matk=2_000)  # no BuffMocLinhCongSinh

    capture_overheal(holder, 5_000)

    assert "BuffMocLinhCongSinh" not in holder.effect_overrides


# ═══════════════════════════════════════════════════════════════════════════
# #7 — Âm Hồn Khế Ấn (damage echo)
# ═══════════════════════════════════════════════════════════════════════════
def _brand(holder):
    holder.effects["DebuffAmHonKheAn"] = 3
    holder.effect_overrides["DebuffAmHonKheAn"] = {"damage_echo": dict(_AM_HON_CFG)}


def test_am_hon_echo_adds_true_damage_under_cap():
    """10000 HP-loss hit → echo min(2500, 1800)=1800 extra true damage."""
    holder = make_combatant("h", hp=1_000_000, hp_max=1_000_000)
    _brand(holder)
    hp_before = holder.hp

    holder.take_damage(10_000)

    # Source hit = 10000, echo = min(2500, int(10000×0.18)) = 1800.
    assert hp_before - holder.hp == 10_000 + 1_800


def test_am_hon_echo_caps_per_hit():
    """20000 hit → echo caps at 2500."""
    holder = make_combatant("h", hp=1_000_000, hp_max=1_000_000)
    _brand(holder)
    hp_before = holder.hp

    holder.take_damage(20_000)

    # int(20000×0.18)=3600 → min(2500, 3600)=2500.
    assert hp_before - holder.hp == 20_000 + 2_500


def test_am_hon_no_echo_below_min_source_dmg():
    """A 100 HP-loss hit (< min_source_dmg 200) produces no echo."""
    holder = make_combatant("h", hp=1_000_000, hp_max=1_000_000)
    _brand(holder)
    hp_before = holder.hp

    holder.take_damage(100)

    assert hp_before - holder.hp == 100  # no echo added


def test_am_hon_echo_recursion_guard():
    """An is_echo=True hit deals exactly its amount with NO further echo."""
    holder = make_combatant("h", hp=1_000_000, hp_max=1_000_000)
    _brand(holder)
    hp_before = holder.hp

    holder.take_damage(10_000, is_echo=True)

    # Recursion guard: the echo block is skipped entirely for is_echo hits.
    assert hp_before - holder.hp == 10_000


def test_am_hon_no_echo_on_dot_tick():
    """DoT-tick damage (is_dot=True) does not trigger the echo."""
    holder = make_combatant("h", hp=1_000_000, hp_max=1_000_000)
    _brand(holder)
    hp_before = holder.hp

    holder.take_damage(10_000, is_dot=True)

    assert hp_before - holder.hp == 10_000  # DoT loss only, no echo


def test_am_hon_no_echo_when_hit_fully_shield_absorbed():
    """A hit fully absorbed by shield → 0 HP loss → no echo."""
    holder = make_combatant("h", hp=1_000_000, hp_max=1_000_000, shield_max_base=50_000)
    holder.shield = 50_000
    _brand(holder)
    hp_before = holder.hp

    holder.take_damage(10_000)

    # Shield ate the whole hit → no HP loss → echo gate (HP loss > 0) fails.
    assert holder.hp == hp_before
    assert holder.shield == 40_000
