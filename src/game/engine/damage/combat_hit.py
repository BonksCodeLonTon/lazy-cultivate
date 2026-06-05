"""Combat-hit damage helpers.

Wraps the pure damage pipeline with effective-stat construction and
post-pipeline scaling that depend on live combatant state (stacks,
active buff/debuff modifiers, res shreds, HP/MP/shield/mana pools).

Keeping these here keeps combat.py focused on sequencing and logging
while all damage math lives under ``engine/damage``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from src.game.constants.balance import (
    MAX_ELEMENTAL_RES,
    MAX_FINAL_DMG_REDUCE,
    SPD_EVASION_BASELINE,
    SPD_EVASION_CAP,
    SPD_EVASION_PER_POINT,
)
from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS, EffectKind
from src.game.engine.stats import AttackStats, DefenseStats

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


# Element → ``<name>_dmg_taken`` stat_bonus key. Per-element "damage taken"
# amps live under English names so any source — debuffs, equipment,
# constitutions — can write to a generic, reusable key. Mirrors the 9-element
# system in ``src/game/constants/elements.py``.
ELEMENT_DMG_TAKEN_KEYS: dict[str, str] = {
    "kim":   "metal_dmg_taken",
    "moc":   "wood_dmg_taken",
    "thuy":  "water_dmg_taken",
    "hoa":   "fire_dmg_taken",
    "tho":   "earth_dmg_taken",
    "loi":   "lightning_dmg_taken",
    "phong": "wind_dmg_taken",
    "quang": "light_dmg_taken",
    "am":    "shadow_dmg_taken",
}


def _doan_tuyet_bonus(actor: "Combatant", target: "Combatant") -> float:
    """Đoạn Tuyệt: +``dmg_per_debuff_pct`` per active debuff on the target.

    Magnitude is supplied per-cast via ``effect_overrides[BuffDoanTuyet]
    [dmg_per_debuff_pct]`` and stamped onto the actor when the buff is
    applied. Counts only ``EffectKind.DEBUFF`` entries (CC and DoT
    debuffs both count if their meta kind is DEBUFF; pure CC kinds are
    ignored to keep the multiplier semantically about debuffs).
    """
    if not actor.has_effect(EffectKey.BUFF_DOAN_TUYET):
        return 0.0
    override = actor.effect_overrides.get(EffectKey.BUFF_DOAN_TUYET) or {}
    per_debuff = float(override.get("dmg_per_debuff_pct", 0.0))
    if per_debuff <= 0:
        return 0.0
    debuff_count = sum(
        1 for k in target.effects
        if (m := EFFECTS.get(k)) and m.kind is EffectKind.DEBUFF
    )
    return per_debuff * debuff_count


def spd_evasion_bonus(spd: int) -> int:
    """Flat evasion-rating bonus derived from SPD (capped).

    Single source of truth for SPD→evasion conversion — used both by
    build_defense_stats (defender's effective evasion) and by
    apply_damage_scaling (Phong damage_bonus_from_evasion_pct scaling).
    """
    raw = max(0, (spd - SPD_EVASION_BASELINE) * SPD_EVASION_PER_POINT)
    return min(SPD_EVASION_CAP, raw)


# Per-element penetration is read directly from ``actor.element_pen`` (the
# generic dict). Stacks additively with target-side res debuffs (XuyenThau
# per-element + XeRach res_all).


def build_attack_stats(
    actor: "Combatant", target: "Combatant", actor_mods: dict,
    skill_element: str | None = None,
) -> AttackStats:
    """Build effective AttackStats for one hit.

    Applies the actor's active buff mods plus target-state vulnerabilities:
      - ``bonus_dmg_vs_burn`` when the target has burn stacks
      - shock stacks on the target amplify final_dmg_bonus per stack, but only
        for Lôi-element hits (lightning payload resonates with the shock)
      - ``crit_amp_vs[bleed|marked|drained]`` adds rating/dmg vs targets in
        those debuff states (bleed stacks / Ấn Phong mark / soul-drain mark)
      - frozen target (DEBUFF_DONG_BANG): force_crit = True. The pipeline
        skips the crit roll and the freeze is consumed by the hit's caller.
      - ``dmg_bonus_<elem>`` from effects only counts when the outgoing skill
        matches that element (e.g. BuffHoaThan boosts Hỏa skills only).
    """
    final_dmg_bonus = actor.final_dmg_bonus + actor_mods.get("final_dmg_bonus", 0.0)
    # Hào Quang Củng Cố — each accumulated fortify stack also boosts outgoing
    # final damage (matches the symmetric DR contribution in
    # ``effective_damage_reduction``). Stacks tick up during periodic phase.
    if actor.fortify_per_turn_pct > 0 and actor.fortify_stacks > 0:
        final_dmg_bonus += actor.fortify_per_turn_pct * actor.fortify_stacks
    # Sát Khí Đại Thành — each enemy killed earlier this dungeon stacks a
    # permanent final_dmg_bonus on the killer. ``kill_streak_stacks`` is
    # incremented in CombatSession._victory(). Capped via kill_buff_cap.
    if actor.kill_buff_per_kill_pct > 0 and actor.kill_streak_stacks > 0:
        final_dmg_bonus += actor.kill_buff_per_kill_pct * actor.kill_streak_stacks
    if skill_element:
        final_dmg_bonus += actor_mods.get(f"dmg_bonus_{skill_element}", 0.0)
        # Permanent per-element dmg bonus from constitutions / equipment
        # (generic dict pickup — replaces per-element flat fields).
        final_dmg_bonus += float(actor.element_dmg_bonus.get(skill_element, 0.0))
        # Phi Thiên Lăng Vân — L1 evasion→Phong conversion. PHONG skills only:
        # +``(eva_total / 300) × rate`` to final_dmg_bonus, NO cap. ``rate`` is
        # the Combatant base PLUS any per-300 uplift the BuffPhongTheTieuDao
        # scaling_rule folds into actor_mods at 6 Phong Vân stacks. ``eva_total``
        # mirrors the defender's effective-evasion calc (base + mods + SPD-derived)
        # so SPD-stacking Phong builds get paid too.
        if skill_element == "phong" and actor.phong_eva_phong_dmg_per_300 > 0:
            _eff_spd = max(1, round(actor.spd * (1.0 + actor_mods.get("spd_pct", 0.0))))
            _eva_total = (
                actor.evasion_rating
                + int(actor_mods.get("evasion_rating", 0))
                + spd_evasion_bonus(_eff_spd)
            )
            _conv_rate = (
                actor.phong_eva_phong_dmg_per_300
                + float(actor_mods.get("phong_eva_conv_uplift", 0.0))
            )
            final_dmg_bonus += (_eva_total / 300.0) * _conv_rate
    # Thiên Ma Đồng Hóa (L6) — while in the Nhập Ma trance, every hit gains a
    # flat final-damage bonus. Gated on the L6+ magnitude AND the active trance,
    # so it's inert for every other build (default field 0.0 / no buff).
    if actor.nhap_ma_dmg_bonus > 0 and actor.has_effect("BuffNhapMa"):
        final_dmg_bonus += actor.nhap_ma_dmg_bonus
    # Vạn Kiếm Quy Tông Sword-Heart stacks — +5% kim damage per stack,
    # routed through ``element_dmg_amp`` (applied at the elemental step)
    # so it amps ONLY kim damage and never feeds the generic
    # ``final_dmg_bonus`` pool. Stacks cap at 10 (max +50% kim damage).
    element_dmg_amp: dict[str, float] = {}
    if actor.sword_heart_stacks > 0:
        element_dmg_amp["kim"] = element_dmg_amp.get("kim", 0.0) + 0.05 * actor.sword_heart_stacks
    if target.burn_stacks > 0 and actor.bonus_dmg_vs_burn > 0:
        final_dmg_bonus += actor.bonus_dmg_vs_burn
    # Mộc Vương Thống Lĩnh (L3) — flat final-dmg bonus while the target is Làm
    # Chậm (slowed). Mirror of the vs-burn block; 0.0 field → inert for everyone
    # who didn't unlock the Trường Xuân Linh Mộc L3 milestone.
    if actor.moc_vs_slowed_dmg_bonus > 0 and target.has_effect(EffectKey.DEBUFF_LAM_CHAM):
        final_dmg_bonus += actor.moc_vs_slowed_dmg_bonus
    if (
        skill_element == "loi"
        and target.shock_stacks > 0
        and target.shock_per_stack_pct > 0
    ):
        final_dmg_bonus += target.shock_stacks * target.shock_per_stack_pct
    final_dmg_bonus += _doan_tuyet_bonus(actor, target)

    crit_rating = actor.crit_rating + int(actor_mods.get("crit_rating", 0))
    crit_dmg_rating = actor.crit_dmg_rating + int(actor_mods.get("crit_dmg_rating", 0))
    # Conditional crit amps from ``actor.crit_amp_vs``: outer key = state,
    # inner = {"rating": int, "dmg": int}. Missing keys default to 0 so a
    # build that doesn't roll the corresponding affix simply contributes
    # nothing.
    # Thái Bạch Canh Kim — Huyết Lạp Thái Bạch (L9). When the actor armed the
    # bleed-hunt and the target is bleeding, amp this hit's crit CHANCE + crit
    # DMG MULT (threaded into ``apply_critical`` via AttackStats). Distinct from
    # the rating-based ``crit_amp_vs["bleed"]`` lane below: these are direct
    # chance / multiplier adds, not flat rating. Both 0.0 → inert for every
    # other build (default Combatant fields).
    bonus_crit_chance = 0.0
    bonus_crit_dmg_mult = 0.0
    if target.bleed_stacks > 0 and actor.bleed_hunter_crit_chance_bonus > 0:
        bonus_crit_chance = actor.bleed_hunter_crit_chance_bonus
        bonus_crit_dmg_mult = actor.bleed_hunter_crit_dmg_bonus
    if target.bleed_stacks > 0:
        _amp = actor.crit_amp_vs.get("bleed") or {}
        crit_rating += int(_amp.get("rating", 0))
        crit_dmg_rating += int(_amp.get("dmg", 0))
    if target.has_effect(EffectKey.DEBUFF_AN_PHONG):
        _amp = actor.crit_amp_vs.get("marked") or {}
        crit_rating += int(_amp.get("rating", 0))
        crit_dmg_rating += int(_amp.get("dmg", 0))
    if target.hp_max_drained > 0:
        _amp = actor.crit_amp_vs.get("drained") or {}
        crit_rating += int(_amp.get("rating", 0))
        crit_dmg_rating += int(_amp.get("dmg", 0))

    # Đông Băng auto-crit OR the Thái Bạch periodic guaranteed-crit arm OR the
    # saint crit arm (L6 cadence) OR the Hoàng Cổ Thánh Vực realm window (L9)
    # OR the Phi Thiên Lăng Vân L6 dodge-armed crit.
    # All arms (except the realm buff) are consumed on the first landed cast
    # (casting.py / hoang_co.py). Default False.
    force_crit = (
        target.has_effect(EffectKey.DEBUFF_DONG_BANG)
        or actor.bleed_hunter_crit_armed
        or actor.saint_crit_armed
        or actor.has_effect("BuffHoangCoThanhVuc")
        or actor.phong_crit_armed
    )

    # ATK / MATK scale with current Energy Shield: depleted shield = depleted
    # punch. Constitutions like Nguyên Linh Khiên Thể (mage) and Cương Khiên
    # Chiến Thể (physical) turn shield into a live offensive resource that
    # drains as it absorbs hits.
    effective_atk = actor.atk
    if actor.atk_from_shield_pct > 0 and actor.shield > 0:
        effective_atk += int(actor.shield * actor.atk_from_shield_pct)
    effective_matk = actor.matk
    if actor.matk_from_shield_pct > 0 and actor.shield > 0:
        effective_matk += int(actor.shield * actor.matk_from_shield_pct)
    # Địa Mạch Quy Chân — armor-to-power conversion. While stacks are
    # active on the actor (each stack representing one absorbed enemy
    # hit, capped at 5 by the bump hook), 10% of def_stat per stack folds
    # into BOTH atk and matk. Max conversion at 5 stacks = +50% of armor
    # to each offensive stat. The passive's ownership gates the increment
    # (see ``_bump_dia_mach_stack``), so a non-zero stack here always
    # implies the holder owns the skill.
    if actor.dia_mach_stacks > 0 and actor.def_stat > 0:
        _dm_bonus = int(actor.def_stat * 0.10 * actor.dia_mach_stacks)
        effective_atk += _dm_bonus
        effective_matk += _dm_bonus
    # Active-effect ATK/MATK percent modifiers (e.g. DebuffSuyKhi reducing atk,
    # DebuffPhapNhuoc reducing matk). Floor at 1 so a heavy stack can't
    # zero out the swing — also keeps ``effective_atk × dmg_scale`` honest.
    atk_pct_mod = actor_mods.get("atk_pct", 0.0)
    if atk_pct_mod:
        effective_atk = max(1, int(effective_atk * (1.0 + atk_pct_mod)))
    matk_pct_mod = actor_mods.get("matk_pct", 0.0)
    if matk_pct_mod:
        effective_matk = max(1, int(effective_matk * (1.0 + matk_pct_mod)))

    # ``accuracy_rating_pct`` mirrors ``evasion_rating_pct`` on the defender
    # side — multiplies the attacker's base ``accuracy_rating`` before flat
    # mods stack. A debuff like Già Thiên Mạn can stamp -0.30 to shave 30%
    # off the attacker's accuracy without touching the flat-rating bonuses
    # they earn from gear/passives.
    acc_pct_mod = float(actor_mods.get("accuracy_rating_pct", 0.0))
    acc_pct_bonus = int(actor.accuracy_rating * acc_pct_mod) if acc_pct_mod else 0
    accuracy_rating = max(
        0,
        actor.accuracy_rating + acc_pct_bonus + int(actor_mods.get("accuracy_rating", 0)),
    )

    return AttackStats(
        crit_rating=crit_rating,
        crit_dmg_rating=crit_dmg_rating,
        final_dmg_bonus=final_dmg_bonus,
        atk=effective_atk,
        matk=effective_matk,
        force_crit=force_crit,
        accuracy_rating=accuracy_rating,
        crit_dmg_rating_to_dmg_pct=actor.crit_dmg_rating_to_dmg_pct,
        element_dmg_amp=element_dmg_amp,
        bonus_crit_chance=bonus_crit_chance,
        bonus_crit_dmg_mult=bonus_crit_dmg_mult,
    )


def build_defense_stats(
    target: "Combatant", target_mods: dict, actor: "Combatant",
    spd_evasion_bonus: Callable[[int], int],
) -> DefenseStats:
    """Build effective DefenseStats for one hit.

    Combines the target's base resistances with ``res_all`` / per-element
    mods from active effects and the attacker's elemental shreds. SPD-derived
    evasion bonus (``spd_evasion_bonus``) stacks with the target's base
    evasion_rating and active modifiers.
    """
    res_all_mod = target_mods.get("res_all", 0.0)
    effective_res: dict[str, float] = {}
    from src.game.engine.effects import effective_res_cap
    for elem, res in target.resistances.items():
        per_elem_mod = target_mods.get(f"res_{elem}", 0.0)
        pen = float(actor.element_pen.get(elem, 0.0))
        cap = effective_res_cap(target, elem)
        effective_res[elem] = max(0.0, min(cap, res + res_all_mod + per_elem_mod - pen))

    effective_spd = max(1, round(target.spd * (1.0 + target_mods.get("spd_pct", 0.0))))
    # ``evasion_rating_pct`` is a multiplier on the holder's base
    # ``evasion_rating`` (mirrors how ``spd_pct`` multiplies spd). Stacks
    # additively across sources via the standard get_combat_modifiers
    # aggregation; applied to the holder's base rating only (not to flat
    # evasion_rating mods or spd-evasion conversion).
    eva_pct = float(target_mods.get("evasion_rating_pct", 0.0))
    evasion_pct_bonus = int(target.evasion_rating * eva_pct) if eva_pct else 0
    # Per-element damage-taken multipliers — any active effect contributes via
    # the canonical key (see ``ELEMENT_DMG_TAKEN_KEYS``, e.g. ``fire_dmg_taken``
    # for hoa). Aggregated by element so the pipeline's apply_elemental step
    # can amp the right hits.
    damage_taken_by_element: dict[str, float] = {}
    for elem, key in ELEMENT_DMG_TAKEN_KEYS.items():
        amp = float(target_mods.get(key, 0.0))
        if amp:
            damage_taken_by_element[elem] = amp
    # Hộ Pháp armor-extension lane — sums two surfaces: active-effect
    # aggregation via target_mods (e.g. BuffThoNguyenHoPhap) AND the permanent
    # actor field (formation gem ladder folds here). Pipeline applies via
    # ``apply_armor_to_elemental`` on non-physical hits.
    def_applies_to_elemental_pct = (
        float(target_mods.get("def_applies_to_elemental_pct", 0.0))
        + float(getattr(target, "def_applies_to_elemental_pct", 0.0))
    )
    # Active-effect ``def_pct`` lane — scales the defender's def_stat at hit
    # time. Permanent def_pct (formations / equipment / constitutions) is
    # already baked into ``target.def_stat`` at session start; this picks up
    # buff-driven temporary armor boosts so a future "Stone-Skin" buff with
    # ``def_pct: 0.30`` actually affects mitigation.
    effective_def_stat = int(target.def_stat * (1.0 + float(target_mods.get("def_pct", 0.0))))
    return DefenseStats(
        evasion_rating=(
            target.evasion_rating
            + int(target_mods.get("evasion_rating", 0))
            + spd_evasion_bonus(effective_spd)
            + evasion_pct_bonus
        ),
        crit_res_rating=target.crit_res_rating + int(target_mods.get("crit_res_rating", 0)),
        def_stat=effective_def_stat,
        resistances=effective_res,
        damage_taken_by_element=damage_taken_by_element,
        def_applies_to_elemental_pct=def_applies_to_elemental_pct,
    )


def effective_damage_reduction(target: "Combatant", target_mods: dict) -> float:
    """Final damage reduction for the target, capped at MAX_FINAL_DMG_REDUCE.

    BuffBatTu and similar debuff mods stack additively with the base reduce.
    Hào Quang Củng Cố adds two contributions on top: (a) accumulated fortify
    stacks scale by ``fortify_per_turn_pct``, and (b) the post-hit brace adds
    ``fortify_post_hit_dr_pct`` while ``fortify_braced_turns > 0``. Both are
    capped along with everything else at ``MAX_FINAL_DMG_REDUCE``.
    """
    reduce = target.final_dmg_reduce + target_mods.get("final_dmg_reduce", 0.0)
    if target.fortify_per_turn_pct > 0 and target.fortify_stacks > 0:
        reduce += target.fortify_per_turn_pct * target.fortify_stacks
    if target.fortify_braced_turns > 0 and target.fortify_post_hit_dr_pct > 0:
        reduce += target.fortify_post_hit_dr_pct
    # Hộ Thể Kiếm Cương passive — Kiếm Tâm stacks contribute flat DR per stack
    # (e.g. 3 % × 10 stacks = +30 % DR at max). Gated on both the passive being
    # equipped (sword_heart_per_stack_dr > 0) and at least one stack present.
    if target.sword_heart_per_stack_dr > 0 and target.sword_heart_stacks > 0:
        reduce += target.sword_heart_per_stack_dr * target.sword_heart_stacks
    return min(MAX_FINAL_DMG_REDUCE, max(0.0, reduce))


def apply_damage_scaling(dmg: int, actor: "Combatant", actor_mods: dict) -> int:
    """Add flat bonuses from the actor's HP/MP/evasion/shield pools and mana stacks.

    Ordering: flat bonuses add first, then mana_stack_dmg_bonus applies as a
    multiplier so late stacks compound the scaled damage, not just base.
    Runs AFTER target damage reduction so HP/MP-scaling builds still deliver
    their power payoff through heavy DR.
    """
    hp_bonus = int(actor.hp_max * actor.damage_bonus_from_hp_pct)
    if hp_bonus > 0:
        dmg += hp_bonus
    mp_bonus = int(actor.mp_max * actor.damage_bonus_from_mp_pct)
    if mp_bonus > 0:
        dmg += mp_bonus
    if actor.damage_bonus_from_evasion_pct > 0:
        # Mirror the defender's effective-evasion calc so SPD-derived evasion
        # also feeds the Phong damage scaler — fast-attack Phong builds get
        # paid for stacking SPD instead of only base evasion_rating.
        eff_spd = max(1, round(actor.spd * (1.0 + actor_mods.get("spd_pct", 0.0))))
        eva_total = (
            actor.evasion_rating
            + int(actor_mods.get("evasion_rating", 0))
            + spd_evasion_bonus(eff_spd)
        )
        eva_bonus = int(eva_total * actor.damage_bonus_from_evasion_pct)
        if eva_bonus > 0:
            dmg += eva_bonus
    if actor.shield > 0 and actor.damage_bonus_from_shield_pct > 0:
        shield_bonus = int(actor.shield * actor.damage_bonus_from_shield_pct)
        if shield_bonus > 0:
            dmg += shield_bonus
    if actor.mana_stacks > 0 and actor.mana_stack_dmg_bonus > 0:
        stack_mult = 1.0 + (actor.mana_stacks * actor.mana_stack_dmg_bonus)
        dmg = int(dmg * stack_mult)
    return dmg
