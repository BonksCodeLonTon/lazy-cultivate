"""Characterization tests for the 5 Wave-C grade-3/4 mechanics.

Mirrors the wave_a / wave_b structure: each mechanic is driven at the most
deterministic level available — the riders/consumers are called directly with
the live JSON spec where the contract is pure state math, and the full
``cast_skill`` path is used only for the facts that live inside the cast loop
(combo increment, tide store + counter, fresh-stack reapply, cooldowns).

Skills covered (implementer's exact hook semantics):
  * B1 Truy Kích Liên Vũ (``SkillAtkPhongTruyKichLuyenVu``) — phong self-combo
    counter (``phong_combo``) + the companion base/fdb riders.
  * B2 Trào Tịch Tích Lãng (``SkillAtkThuyTrieuTichLang``) — tide reservoir
    store + ``tide_charge`` discharge every 3rd cast.
  * B3 Huyết Tế Chú (``SkillAtkAmHuyetTeChu``) — ``blood_offering`` HP-sac
    amp + lifesteal; non-double-trigger of the Địa Sát berserker path.
  * B4 Quá Tải Lôi Bạo (``SkillAtkLoiQuaTaiCong``) — shock-stack consume amp
    (phase-1) + self Lôi lockout (phase-2).
  * B5 Cộng Sinh Luân Hồi (``SkillPasMocCongSinhLuanHoi``) — Mộc-DoT siphon
    passive (``dot_siphon``) HP/MP gain + distinct-DoT amp.
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS
from src.game.systems.combat import casting
from src.game.systems.combat.cast_consumers import (
    _overload_recoil_lockout, _tide_charge_discharge,
)
from src.game.systems.combat.dmg_riders import (
    _combo_counter_base_rider, _combo_counter_fdb_rider,
)
from src.game.systems.combat.periodic.dots import _dot_siphon_config
from src.game.systems.combat.two_phase_consumers import (
    CastContext, _blood_offering_consumer, _overload_recoil_consumer,
    _self_hp_cost_berserker,
)

from tests.conftest import SeedRng, make_combatant, make_session

_B1_KEY = "SkillAtkPhongTruyKichLuyenVu"
_B2_KEY = "SkillAtkThuyTrieuTichLang"
_B3_KEY = "SkillAtkAmHuyetTeChu"
_B4_KEY = "SkillAtkLoiQuaTaiCong"
_B5_KEY = "SkillPasMocCongSinhLuanHoi"


def _cast(session, actor, target, skill_key):
    """Drive one real top-level cast via ``cast_skill``."""
    data = registry.get_skill(skill_key)
    assert data is not None, f"skill {skill_key} missing from registry"
    casting.cast_skill(
        session, actor, target, skill_key, data, mp_cost=data.get("mp_cost", 0),
    )


# ═══════════════════════════════════════════════════════════════════════════
# B1 — Truy Kích Liên Vũ (phong self-combo counter)
# ═══════════════════════════════════════════════════════════════════════════
def _b1_spec() -> dict:
    return registry.get_skill(_B1_KEY)["combo_counter_scaling"]


def test_b1_combo_increments_per_consecutive_cast_and_caps_at_six():
    """Each consecutive cast of the combo skill bumps phong_combo, capped at 6."""
    actor = make_combatant("a", mp=100_000, mp_max=100_000,
                           skill_keys=[_B1_KEY])
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)  # skip crit / proc rolls for clean casts

    for expected in range(1, 9):
        _cast(session, actor, target, _B1_KEY)
        assert actor.phong_combo == min(expected, 6)
    # Stayed clamped at the cap, never overflowed.
    assert actor.phong_combo == 6


def test_b1_riders_scale_base_and_fdb_by_combo_count():
    """phong_combo=4 → base +220×4, final_dmg_bonus +0.04×4 (companion riders)."""
    actor = make_combatant("a")
    target = make_combatant("e")
    actor.phong_combo = 4
    skill_data = {"combo_counter_scaling": _b1_spec()}

    base_res = _combo_counter_base_rider(skill_data, actor, target, 160, {})
    assert base_res is not None
    base_bonus, _log = base_res
    assert base_bonus == 220 * 4  # 880

    fdb_res = _combo_counter_fdb_rider(_b1_spec(), actor, target)
    assert fdb_res is not None
    fdb_bonus, _log2 = fdb_res
    assert abs(fdb_bonus - 0.04 * 4) < 1e-9  # 0.16


def test_b1_riders_respect_the_six_combo_cap():
    """An over-cap counter is clamped to combo_cap=6 by the riders too."""
    actor = make_combatant("a")
    target = make_combatant("e")
    actor.phong_combo = 99
    base_res = _combo_counter_base_rider(
        {"combo_counter_scaling": _b1_spec()}, actor, target, 160, {},
    )
    fdb_res = _combo_counter_fdb_rider(_b1_spec(), actor, target)
    assert base_res[0] == 220 * 6  # 1320
    assert abs(fdb_res[0] - 0.04 * 6) < 1e-9  # 0.24


def test_b1_combo_resets_on_cc_taken():
    """A hard CC (skips_turn) routed through inflict_debuff snaps the chain."""
    actor = make_combatant("a", mp=100_000, mp_max=100_000,
                           skill_keys=[_B1_KEY])
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)

    _cast(session, actor, target, _B1_KEY)
    _cast(session, actor, target, _B1_KEY)
    assert actor.phong_combo == 2

    # CC the COMBO OWNER (actor): inflict a stun on it → chain breaks to 0.
    stun_meta = EFFECTS.get(EffectKey.CC_STUN.value)
    assert stun_meta is not None and stun_meta.skips_turn
    casting.inflict_debuff(
        session, EffectKey.CC_STUN.value, stun_meta, actor, actor=target,
    )
    assert actor.phong_combo == 0


def test_b1_combo_resets_on_cross_element_cast():
    """A different-element top-level cast snaps the Liên Vũ chain to 0."""
    actor = make_combatant("a", mp=100_000, mp_max=100_000,
                           skill_keys=[_B1_KEY, "SkillAtkAm1"])
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)

    _cast(session, actor, target, _B1_KEY)
    _cast(session, actor, target, _B1_KEY)
    assert actor.phong_combo == 2

    # Cross-element cast (Âm) breaks the chain.
    _cast(session, actor, target, "SkillAtkAm1")
    assert actor.last_cast_element == "am"
    assert actor.phong_combo == 0


def test_b1_plain_phong_cast_does_not_increment_combo():
    """A same-element NON-combo Phong cast leaves phong_combo untouched."""
    actor = make_combatant("a", mp=100_000, mp_max=100_000,
                           skill_keys=[_B1_KEY, "SkillAtkPhong1"])
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.99)

    _cast(session, actor, target, _B1_KEY)
    assert actor.phong_combo == 1

    # A plain Phong cut (SkillAtkPhong1) carries no combo block → counter held.
    _cast(session, actor, target, "SkillAtkPhong1")
    assert actor.phong_combo == 1  # unchanged (not bumped, not reset)


def test_b1_inert_for_enemy_actor():
    """An enemy never builds phong_combo → both riders return None."""
    enemy = make_combatant("boss")  # phong_combo defaults to 0
    target = make_combatant("p")
    assert enemy.phong_combo == 0
    assert _combo_counter_base_rider(
        {"combo_counter_scaling": _b1_spec()}, enemy, target, 160, {},
    ) is None
    assert _combo_counter_fdb_rider(_b1_spec(), enemy, target) is None


# ═══════════════════════════════════════════════════════════════════════════
# B2 — Trào Tịch Tích Lãng (tide reservoir)
# ═══════════════════════════════════════════════════════════════════════════
def _b2_tide_cfg() -> dict:
    return registry.get_skill(_B2_KEY)["tide_charge"]


def test_b2_store_banks_30pct_clamped_and_advances_counter():
    """Each cast banks 30% of dealt dmg into thuy_tide (cap 4×matk) + counter++."""
    actor = make_combatant("a", matk=200, mp=100_000, mp_max=100_000)
    target = make_combatant("e", hp=100_000_000, hp_max=100_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.5)  # neutral variance, skip crit roll

    _cast(session, actor, target, _B2_KEY)
    cap = int(4.0 * actor.matk)  # 800
    assert actor.thuy_tide_casts == 1
    assert 0 < actor.thuy_tide <= cap  # banked something, under the cap


def test_b2_reservoir_clamps_to_four_times_matk():
    """A small matk forces the cap to bite — reservoir never exceeds 4×matk.

    Two casts of a high-damage cut bank far more than 4×matk would allow, so
    the clamp must hold. Stop at two casts (the 3rd would discharge + reset).
    """
    actor = make_combatant("a", matk=10, mp=100_000, mp_max=100_000)
    target = make_combatant("e", hp=100_000_000, hp_max=100_000_000)
    session = make_session(actor, target)
    session.rng = SeedRng(0.5)

    cap = int(4.0 * actor.matk)  # 40
    _cast(session, actor, target, _B2_KEY)
    _cast(session, actor, target, _B2_KEY)
    assert actor.thuy_tide_casts == 2  # not yet at the discharge boundary
    assert actor.thuy_tide <= cap


def test_b2_discharge_fires_on_third_cast_and_resets():
    """The tide_charge consumer fires on every 3rd cast: strikes + resets."""
    actor = make_combatant("a", matk=200)
    target = make_combatant("e", hp=100_000, hp_max=100_000)
    session = make_session(actor, target)
    cfg = _b2_tide_cfg()

    # Prime the reservoir + counter at the discharge boundary.
    actor.thuy_tide = 1_500
    actor.thuy_tide_casts = 3  # 3 % 3 == 0 → discharge
    hp_before = target.hp

    skill_data = {"key": _B2_KEY, "tide_charge": cfg}
    _tide_charge_discharge(cfg, skill_data, actor, target, session)

    # release_pct = 1.0 → released 1500; target res 0, fdb 0 → strike 1500.
    assert hp_before - target.hp == 1_500
    # Reservoir + counter reset after discharge.
    assert actor.thuy_tide == 0
    assert actor.thuy_tide_casts == 0


def test_b2_no_discharge_off_cadence():
    """On a non-3rd cast (counter not divisible by 3) nothing discharges."""
    actor = make_combatant("a", matk=200)
    target = make_combatant("e", hp=100_000, hp_max=100_000)
    session = make_session(actor, target)
    cfg = _b2_tide_cfg()

    actor.thuy_tide = 1_500
    actor.thuy_tide_casts = 2  # 2 % 3 != 0
    hp_before = target.hp

    _tide_charge_discharge(cfg, {"key": _B2_KEY, "tide_charge": cfg},
                           actor, target, session)

    assert target.hp == hp_before          # no strike
    assert actor.thuy_tide == 1_500         # reservoir untouched
    assert actor.thuy_tide_casts == 2       # counter untouched


def test_b2_inert_for_enemy():
    """An enemy never casts the skill → counter stays 0 → discharge is inert."""
    enemy = make_combatant("boss", matk=200)
    target = make_combatant("p", hp=100_000, hp_max=100_000)
    session = make_session(enemy, target)
    cfg = _b2_tide_cfg()
    assert enemy.thuy_tide_casts == 0
    hp_before = target.hp

    _tide_charge_discharge(cfg, {"key": _B2_KEY, "tide_charge": cfg},
                           enemy, target, session)

    assert target.hp == hp_before  # nothing fired


# ═══════════════════════════════════════════════════════════════════════════
# B3 — Huyết Tế Chú (blood offering)
# ═══════════════════════════════════════════════════════════════════════════
def _b3_skill_data() -> dict:
    """Live skill_data copy carrying self_hp_cost_pct + blood_offering."""
    return dict(registry.get_skill(_B3_KEY))


def _b3_ctx(actor, target, session, *, base_dmg: int = 180) -> CastContext:
    return CastContext(
        actor=actor, target=target, session=session,
        skill_element="am", skill_data=_b3_skill_data(),
        actor_mods={}, base_dmg=base_dmg,
    )


def test_b3_hp_cost_is_eight_pct_current_and_fdb_capped():
    """Pay 8% CURRENT hp; fdb = 0.4 × pct-spent capped at +0.5."""
    actor = make_combatant("a", hp=10_000, hp_max=10_000, matk=200)
    target = make_combatant("e", hp=1_000_000, hp_max=1_000_000)
    session = make_session(actor, target)
    ctx = _b3_ctx(actor, target, session)
    fdb_before = actor.final_dmg_bonus

    _blood_offering_consumer(ctx.skill_data["blood_offering"], ctx)

    # cost = 8% of 10000 current = 800 → HP drops to 9200 (no shield).
    assert actor.hp == 9_200
    # spent_pct = 800/10000 = 8%; fdb = 0.08×100×0.4 = 3.2 → capped at 0.5.
    assert abs(actor.final_dmg_bonus - (fdb_before + 0.5)) < 1e-9


def test_b3_shield_absorbs_cost_first():
    """Shield eats the HP cost before HP does (mirrors Địa Sát)."""
    actor = make_combatant("a", hp=10_000, hp_max=10_000, matk=200,
                           shield_max_base=50_000)
    actor.shield = 5_000
    target = make_combatant("e", hp=1_000_000, hp_max=1_000_000)
    session = make_session(actor, target)
    ctx = _b3_ctx(actor, target, session)

    _blood_offering_consumer(ctx.skill_data["blood_offering"], ctx)

    # cost = 800; shield (5000) absorbs all of it → HP untouched.
    assert actor.shield == 4_200
    assert actor.hp == 10_000


def test_b3_never_drops_below_one_hp():
    """A 1-HP berserker survives the offering — cost can never self-KO."""
    actor = make_combatant("a", hp=1, hp_max=10_000, matk=200)
    target = make_combatant("e", hp=1_000_000, hp_max=1_000_000)
    session = make_session(actor, target)
    ctx = _b3_ctx(actor, target, session)

    _blood_offering_consumer(ctx.skill_data["blood_offering"], ctx)

    assert actor.hp >= 1


def test_b3_lifesteal_heals_quarter_of_damage():
    """Post-hit after-callback heals 25% of the damage that landed."""
    actor = make_combatant("a", hp=5_000, hp_max=10_000, matk=200)
    target = make_combatant("e", hp=1_000_000, hp_max=1_000_000)
    session = make_session(actor, target)
    ctx = _b3_ctx(actor, target, session)

    after = _blood_offering_consumer(ctx.skill_data["blood_offering"], ctx)
    assert after is not None  # lifesteal closure was returned
    hp_after_cost = actor.hp

    after(ctx, 4_000)  # 4000 damage landed
    # lifesteal = 25% of 4000 = 1000 → healed (not at hp_max so it sticks).
    assert actor.hp == hp_after_cost + 1_000


def test_b3_does_not_double_trigger_berserker_path():
    """The Địa Sát self_hp_cost berserker path is inert for B3 (no per-hp field)."""
    actor = make_combatant("a", hp=10_000, hp_max=10_000, matk=200)
    target = make_combatant("e", hp=1_000_000, hp_max=1_000_000)
    session = make_session(actor, target)
    ctx = _b3_ctx(actor, target, session)
    hp_before = actor.hp
    base_before = ctx.base_dmg

    # B3 leaves ``bonus_base_dmg_per_hp_spent`` unset → berserker early-returns.
    result = _self_hp_cost_berserker(ctx.skill_data["self_hp_cost_pct"], ctx)

    assert result is None
    assert actor.hp == hp_before        # no second HP charge
    assert ctx.base_dmg == base_before  # no berserker base bonus


# ═══════════════════════════════════════════════════════════════════════════
# B4 — Quá Tải Lôi Bạo (shock-stack overload)
# ═══════════════════════════════════════════════════════════════════════════
def _b4_overload_spec() -> dict:
    return registry.get_skill(_B4_KEY)["overload_recoil"]


def _b4_ctx(actor, target, session, *, base_dmg: int = 420) -> CastContext:
    return CastContext(
        actor=actor, target=target, session=session,
        skill_element="loi",
        skill_data={"element": "loi", "base_dmg": base_dmg,
                    "overload_recoil": _b4_overload_spec()},
        actor_mods={}, base_dmg=base_dmg,
    )


def test_b4_phase1_amps_by_full_pre_consume_shock_count():
    """N=4 shock stacks → fdb +0.05×4, Lôi pen +0.04×4; stacks consumed after."""
    actor = make_combatant("a")
    target = make_combatant("e")
    target.shock_stacks = 4
    session = make_session(actor, target)
    ctx = _b4_ctx(actor, target, session)
    fdb_before = actor.final_dmg_bonus

    after = _overload_recoil_consumer(_b4_overload_spec(), ctx)

    # Amp reads the FULL pre-consume count (4), not the post state.
    assert abs(actor.final_dmg_bonus - (fdb_before + 0.05 * 4)) < 1e-9  # +0.20
    assert abs(actor.element_pen.get("loi", 0.0) - 0.04 * 4) < 1e-9      # +0.16
    # The after-callback consumes the snapshotted stacks.
    assert after is not None
    assert target.shock_stacks == 4  # not yet consumed (callback deferred)
    after(ctx, 9_999)
    assert target.shock_stacks == 0


def test_b4_phase1_inert_without_shock_stacks():
    """No shock stacks → consumer returns None, no amp."""
    actor = make_combatant("a")
    target = make_combatant("e")  # shock_stacks defaults to 0
    session = make_session(actor, target)
    ctx = _b4_ctx(actor, target, session)
    fdb_before = actor.final_dmg_bonus

    after = _overload_recoil_consumer(_b4_overload_spec(), ctx)

    assert after is None
    assert actor.final_dmg_bonus == fdb_before
    assert actor.element_pen.get("loi", 0.0) == 0.0


def test_b4_phase2_locks_other_loi_skills_not_the_caster():
    """+2 cd on the actor's OTHER Lôi skills; the casting skill is untouched."""
    actor = make_combatant(
        "a", skill_keys=[_B4_KEY, "SkillAtkLoi1", "SkillAtkAm1"],
    )
    target = make_combatant("e")
    session = make_session(actor, target)
    actor.cooldowns[_B4_KEY] = 4  # the casting skill already on its own cd

    skill_data = {"key": _B4_KEY, "overload_recoil": _b4_overload_spec()}
    _overload_recoil_lockout(_b4_overload_spec(), skill_data, actor, target, session)

    # Other Lôi skill locked +2; the casting skill's own cd is NOT extended.
    assert actor.cooldowns["SkillAtkLoi1"] == 2  # 0 + 2 lockout
    assert actor.cooldowns[_B4_KEY] == 4         # untouched
    # Non-Lôi skill (Âm) is out of scope.
    assert actor.cooldowns.get("SkillAtkAm1", 0) == 0


def test_b4_full_cast_lands_te_nguyen_luc_and_reapplies_one_shock():
    """Through the real cast: DebuffTeNguyenLuc dur 3, and a fresh shock == 1."""
    actor = make_combatant("a", mp=100_000, mp_max=100_000,
                           skill_keys=[_B4_KEY, "SkillAtkLoi1"])
    target = make_combatant("e", hp=10_000_000, hp_max=10_000_000)
    target.shock_stacks = 3  # pre-existing stacks to be consumed by phase-1
    session = make_session(actor, target)
    session.rng = SeedRng(0.0)  # all effect_chances (1.0) land

    _cast(session, actor, target, _B4_KEY)

    # Pre-existing 3 consumed, then the skill's own DebuffSocDien (chance 1.0)
    # re-applies exactly 1 fresh stack → post-cast shock == 1.
    assert target.shock_stacks == 1
    assert target.has_effect("DebuffTeNguyenLuc")
    assert target.effects["DebuffTeNguyenLuc"] == 3


def test_b4_inert_for_enemy_actor():
    """An enemy with no Lôi skill set → lockout finds nothing to lock."""
    enemy = make_combatant("boss", skill_keys=[])
    target = make_combatant("p")
    session = make_session(enemy, target)
    skill_data = {"key": _B4_KEY, "overload_recoil": _b4_overload_spec()}

    _overload_recoil_lockout(_b4_overload_spec(), skill_data, enemy, target, session)

    assert enemy.cooldowns == {}  # nothing locked


# ═══════════════════════════════════════════════════════════════════════════
# B5 — Cộng Sinh Luân Hồi (Mộc-DoT siphon passive)
# ═══════════════════════════════════════════════════════════════════════════
def _b5_cfg() -> dict:
    return registry.get_skill(_B5_KEY)["dot_siphon"]


def _seed_moc_dot(holder, *, dot_pct: float = 0.01):
    """Put one deterministic Mộc DoT (DebuffDocTo) on ``holder``.

    Uses the ``dot_scales_hp_pct`` model so the tick is exactly
    ``int(hp_max × per_stack_pct × stacks)`` regardless of applier power —
    keeps the siphon math reproducible through the real periodic path.
    """
    holder.poison_stacks = 1
    holder.poison_per_stack_pct = dot_pct
    holder.dot_scales_hp_pct = True
    holder.apply_effect("DebuffDocTo", 3)


def test_b5_dot_siphon_config_found_only_for_owner():
    """The helper resolves the passive on its owner and is None otherwise."""
    owner = make_combatant("owner", skill_keys=[_B5_KEY])
    non_owner = make_combatant("plain", skill_keys=["SkillAtkMoc1"])
    enemy = make_combatant("boss", skill_keys=[])

    cfg = _dot_siphon_config(owner)
    assert cfg is not None
    assert "moc" in cfg["elements"]
    assert cfg["heal_pct_of_dot_dmg"] == 0.4
    assert cfg["mp_pct_of_dot_dmg"] == 0.15

    assert _dot_siphon_config(non_owner) is None
    assert _dot_siphon_config(enemy) is None


def test_b5_owner_siphons_hp_and_mp_from_moc_dot_tick():
    """Owner of the passive heals 40% HP + 15% MP of the full Mộc-DoT tick."""
    # holder = the DoT victim; owner = the passive holder who applied the DoT.
    holder = make_combatant("victim", hp=100_000, hp_max=100_000)
    owner = make_combatant(
        "owner", hp=50_000, hp_max=100_000, mp=0, mp_max=10_000,
        skill_keys=[_B5_KEY],
    )
    _seed_moc_dot(holder, dot_pct=0.01)  # tick = int(100000 × 0.01) = 1000

    # Owner must be the periodic opponent of the holder.
    session = make_session(owner, holder)
    owner_hp_before = owner.hp
    owner_mp_before = owner.mp

    session._process_periodic(holder)

    # tick = 1000; distinct moc DoTs = 1 → amp 0.06 → bonus int(1000×0.06)=60.
    # full = 1060. heal = int(1060×0.4) = 424, mp = int(1060×0.15) = 159.
    assert owner.hp == owner_hp_before + 424
    assert owner.mp == owner_mp_before + 159


def test_b5_inert_for_non_owner_applier():
    """A plain (non-passive) applier siphons nothing — owner gains 0 HP/MP."""
    holder = make_combatant("victim", hp=100_000, hp_max=100_000)
    plain = make_combatant(
        "plain", hp=50_000, hp_max=100_000, mp=0, mp_max=10_000,
        skill_keys=["SkillAtkMoc1"],  # no dot_siphon passive
    )
    _seed_moc_dot(holder, dot_pct=0.01)

    session = make_session(plain, holder)
    hp_before, mp_before = plain.hp, plain.mp

    session._process_periodic(holder)

    assert plain.hp == hp_before  # no siphon heal
    assert plain.mp == mp_before  # no siphon mp


def test_b5_inert_for_enemy_holder():
    """An enemy holding the passive key but not applying a matched DoT is inert.

    Here the opponent has no passive at all (enemy), so even though the holder
    carries a Mộc DoT, the siphon block never arms — no HP/MP changes on the
    DoT-less opponent beyond the tick on the holder.
    """
    holder = make_combatant("victim", hp=100_000, hp_max=100_000)
    enemy = make_combatant("boss", hp=50_000, hp_max=100_000, mp=0, mp_max=10_000,
                           skill_keys=[])
    _seed_moc_dot(holder, dot_pct=0.01)

    session = make_session(enemy, holder)
    hp_before, mp_before = enemy.hp, enemy.mp

    session._process_periodic(holder)

    assert enemy.hp == hp_before
    assert enemy.mp == mp_before


def test_b5_distinct_dot_amp_formula_caps_at_thirty_pct():
    """Pure-helper unit check of the amp: per×distinct, clamped to max_bonus.

    DebuffDocTo is currently the only Mộc DoT, so the live distinct-count maxes
    at 1 in practice; this pins the formula itself for the capped regime.
    """
    cfg = _b5_cfg()
    per = float(cfg["bonus_dot_pct_per_distinct_dot"])
    cap = float(cfg["max_bonus_dot_pct"])
    assert per == 0.06
    assert cap == 0.3

    def amp(distinct: int) -> float:
        return min(per * distinct, cap)

    assert abs(amp(1) - 0.06) < 1e-9   # one DoT (the live case)
    assert abs(amp(5) - 0.30) < 1e-9   # 0.30 (uncapped would be 0.30)
    assert abs(amp(9) - 0.30) < 1e-9   # clamped to cap
