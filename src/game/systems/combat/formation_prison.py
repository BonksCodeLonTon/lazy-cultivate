"""Formation-prison cast hooks — Vạn Kiếp Lôi Ngục Trận milestone + capstone.

The prison formation's signature skill auto-fires every round via
``fire_formation_skills`` and lands a Lôi Kiếp Ấn stack via the standard
``effects: ["DebuffLoiKiepAn"]`` path. This module runs **after** that stamp
lands and consumes the new stack count for two side effects:

1. **Milestone bolt** — every time the new stack count just crossed a
   multiple of ``milestone_n`` (5/10/15/…), the formation tax fires an extra
   Lôi Kiếp Phán bolt. Damage scales off the owner's ``loi_kiep_an``
   tunables and respects ``dmg_taken_bonus_loi`` / ``res_loi`` because the
   per-stack amp is already live on the target (the stamp landed first).

2. **Capstone — Vạn Kiếp Phán** — when the new count is ≥
   ``consume_threshold`` (10), fire the capstone strike with damage that
   scales linearly with the consumed stack count, then **clear all stacks**
   (drop the DebuffLoiKiepAn entry entirely) so the cycle restarts.
   ``capstone_refund_mp_pct`` (10-gem tier) optionally refunds a % of MP
   to the owner on capstone.

Both bolt + capstone route their damage through ``apply_elemental`` so
they pick up the engine's standard element clamp + amp lanes — attacker
``element_dmg_bonus`` / ``dmg_bonus_loi`` / ``element_pen[loi]`` AND
defender ``res_loi`` (base + per-stack shred from the prison itself) +
``dmg_taken_bonus_loi`` (per-stack amp from the prison itself). They
skip crit / evasion / ATK / ``cooldown`` / ``mp_cost`` / chain triggers
because they're deterministic side effects of a single formation tick,
not fresh skill casts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.effects import EffectKey
from src.game.engine.damage.color import colorize_damage
from src.game.engine.damage.elemental import apply_elemental
from src.game.engine.effects import EFFECTS, EffectKind, get_combat_modifiers

if TYPE_CHECKING:
    from src.game.systems.combat.session import CombatSession
    from src.game.systems.combatant import Combatant


_PRISON_ELEMENT = "loi"


def _compute_prison_dmg(
    actor: "Combatant", target: "Combatant", base: int, matk_part: int,
) -> int:
    """Apply the canonical element pipeline to a flat prison-tick Lôi hit.

    Routes through ``apply_elemental`` to get the ``MAX_ELEMENTAL_RES`` clamp
    for free, and folds in **both amp lanes** so the prison-tax design works
    end-to-end:

      * Attacker side — ``element_dmg_bonus[loi]`` (formation/equipment) plus
        active-effect ``dmg_bonus_loi`` (e.g. Tâm Lôi Cộng) plus the actor's
        ``element_pen[loi]``. Mirrors Vạn Kiếm summon-swing precedent so the
        bolt benefits from the holder's full Lôi build.

      * Defender side — ``res_loi`` (from base resistances + the prison's
        own per-stack -res_loi shred) plus ``dmg_taken_bonus_loi`` (the
        prison's per-stack +dmg_taken_bonus_loi from DebuffLoiKiepAn's
        scaling_rules). Both keys are read from ``get_combat_modifiers``
        so any other Lôi-amp source layers in too.

    Crit / evade / ATK scaling are intentionally NOT applied — the bolt is
    a deterministic side effect of a stack landing, not a fresh skill cast.
    """
    raw = max(1, int(base + matk_part))
    actor_mods = get_combat_modifiers(actor)
    target_mods = get_combat_modifiers(target)
    attacker_amp = float(actor_mods.get(f"dmg_bonus_{_PRISON_ELEMENT}", 0.0)) + float(
        actor.element_dmg_bonus.get(_PRISON_ELEMENT, 0.0)
    )
    defender_res = {
        _PRISON_ELEMENT: float(target.resistances.get(_PRISON_ELEMENT, 0.0))
        + float(target_mods.get(f"res_{_PRISON_ELEMENT}", 0.0)),
    }
    defender_amp = float(target_mods.get(f"dmg_taken_bonus_{_PRISON_ELEMENT}", 0.0))
    pen = float(actor.element_pen.get(_PRISON_ELEMENT, 0.0))
    return apply_elemental(
        raw, _PRISON_ELEMENT, defender_res, pen_pct=pen,
        damage_taken_by_element={_PRISON_ELEMENT: defender_amp} if defender_amp else None,
        attacker_element_amp={_PRISON_ELEMENT: attacker_amp} if attacker_amp else None,
    )


def _fire_te_liet(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    chance: float, duration: int,
) -> None:
    """Roll Tê Liệt; on success, route through inflict_debuff for parity."""
    if chance <= 0 or duration <= 0 or not target.is_alive():
        return
    if chance < 1.0 and session.rng.random() >= chance:
        return
    meta = EFFECTS.get(EffectKey.DEBUFF_TE_LIET)
    if meta is None:
        return
    # Lazy import: casting → formation_prison cycle would otherwise loop.
    from src.game.systems.combat.casting import inflict_debuff
    inflict_debuff(
        session, EffectKey.DEBUFF_TE_LIET, meta, target, actor=actor,
        overrides={"duration": duration},
    )


def _emit_bolt(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict, milestone: int,
) -> None:
    """Fire one Lôi Kiếp Phán bolt at the just-crossed milestone."""
    if not target.is_alive():
        return
    base = max(0, int(cfg.get("bolt_base_dmg", 0)))
    scale = max(0.0, float(cfg.get("bolt_matk_scale", 0.0)))
    matk_part = int(scale * actor.matk)
    dmg = _compute_prison_dmg(actor, target, base, matk_part)
    target.take_damage(dmg)
    tag = colorize_damage(f"-{dmg:,} HP", _PRISON_ELEMENT)
    session.log.append(
        f"    ⚡ **{actor.name}** **Lôi Kiếp Phán** (mốc ×{milestone}) "
        f"→ **{target.name}** {tag}"
    )
    _fire_te_liet(
        session, actor, target,
        chance=float(cfg.get("bolt_te_liet_chance", 0.0)),
        duration=1,
    )


def _emit_capstone(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict, stacks: int,
) -> None:
    """Fire Vạn Kiếp Phán — consume all Lôi Kiếp Ấn, then clear the debuff.

    Damage scales per consumed stack via ``capstone_base_per_stack`` +
    ``capstone_matk_per_stack``. After the strike, the debuff entry is
    fully removed (not just decremented) so the next formation tick lands
    a fresh stack-1 prison.
    """
    if not target.is_alive():
        return
    base_floor = max(0, int(cfg.get("capstone_base_floor", 0)))
    base_per = max(0, int(cfg.get("capstone_base_per_stack", 0)))
    matk_floor = max(0.0, float(cfg.get("capstone_matk_floor", 0.0)))
    matk_per = max(0.0, float(cfg.get("capstone_matk_per_stack", 0.0)))
    base = base_floor + base_per * stacks
    matk_part = int((matk_floor + matk_per * stacks) * actor.matk)
    dmg = _compute_prison_dmg(actor, target, base, matk_part)
    target.take_damage(dmg)
    tag = colorize_damage(f"-{dmg:,} HP", _PRISON_ELEMENT)
    session.log.append(
        f"  ⚡🔱 **{actor.name}** **Vạn Kiếp Phán** "
        f"(thiêu {stacks} dấu) → **{target.name}** {tag}"
    )
    _fire_te_liet(
        session, actor, target,
        chance=float(cfg.get("capstone_te_liet_chance", 0.0)),
        duration=2,
    )
    # MP refund for the 10-gem tier — read from the actor's own dict
    # (gem tier already merged the value via _merge_bonus_dict).
    refund_pct = max(0.0, min(1.0, float(cfg.get("capstone_refund_mp_pct", 0.0))))
    if refund_pct > 0:
        refund = max(1, int(actor.mp_max * refund_pct))
        actor.mp = min(actor.mp_max, actor.mp + refund)
        session.log.append(f"    💙 **{actor.name}** Vạn Kiếp hoàn +{refund:,} MP")
    # Clear the debuff entry + zero the counter via the canonical helper so
    # the next prison tick starts a fresh cycle. The .pop() handles the
    # effects dict + per-stack scaling override (Combatant.consume_stacks
    # zeroes the counter but doesn't touch the effects/overrides maps).
    target.effects.pop(EffectKey.DEBUFF_LOI_KIEP_AN.value, None)
    target.effect_overrides.pop(EffectKey.DEBUFF_LOI_KIEP_AN.value, None)
    target.consume_stacks("loi_kiep_an")


def process_loi_kiep_an_tick(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    spec: dict, prev_stacks: int,
) -> None:
    """Resolve milestone bolts + capstone Vạn Kiếp Phán for a freshly-landed tick.

    Called from the ``debuff_only`` branch of ``cast_skill`` after
    ``apply_skill_effects`` finishes (which is what landed the new
    DebuffLoiKiepAn stack via the registered stamper).

    Capstone wins over the bolt: if the new count crosses the
    ``consume_threshold`` it fires the capstone and clears stacks, so the
    milestone bolt at that same threshold doesn't double-dip.
    """
    if not target.is_alive() or not isinstance(spec, dict):
        return
    new_stacks = int(target.loi_kiep_an_stacks)
    if new_stacks <= prev_stacks:
        return
    milestone_n = max(1, int(spec.get("milestone_n", 5)))
    consume_threshold = max(0, int(spec.get("consume_threshold", 0)))
    cfg = (getattr(actor, "loi_kiep_an", None) or {})
    # Capstone first — at threshold, all stacks vaporize into the burst, so
    # the per-milestone bolts that *would* have crossed during this same tick
    # are folded into the capstone's per-stack scaling instead.
    if consume_threshold > 0 and new_stacks >= consume_threshold:
        _emit_capstone(session, actor, target, cfg, new_stacks)
        return
    # Milestone — at most one per tick today (per_cast caps at ~3 stacks),
    # but loop guards against future tunables that bump per_cast higher.
    prev_milestone = prev_stacks // milestone_n
    new_milestone = new_stacks // milestone_n
    for crossed in range(prev_milestone + 1, new_milestone + 1):
        if not target.is_alive():
            break
        _emit_bolt(session, actor, target, cfg, milestone=crossed * milestone_n)


# ─── Thiên Lôi Tru Tà Trận ──────────────────────────────────────────────
# Same shape as the Vạn Kiếp Lôi Ngục pipeline: per-turn formation tick lands
# a refreshable target debuff (DebuffThienLoiAn) whose magnitude scales with
# the target's current "corruption" (distinct debuff count), then fires
# either a Thiên Lôi Phán strike (regular) or a Thiên Lôi Đại Phán capstone
# (when the corruption threshold is crossed AND the capstone cooldown is
# clear). Capstone also strips target buffs.

# Capstone gating cooldown lives on actor.cooldowns under this key so the
# standard ``tick_cooldowns`` end-of-round decrement handles it for free
# without us needing a custom counter field on Combatant.
_DAI_PHAN_CD_KEY = "ThienLoiDaiPhanCD"


def _count_target_debuffs(target: "Combatant", exclude: str) -> int:
    """Count distinct ``EffectKind.DEBUFF`` entries on ``target``.

    ``EffectKind.CC`` deliberately excluded so the formation tracks
    "corruption / sin" (debuffs proper), not transient stuns/freezes.
    ``exclude`` skips the formation's own DebuffThienLoiAn so it can't
    bootstrap itself by stamping → counting itself → re-stamping bigger.
    """
    return sum(
        1 for k in target.effects
        if k != exclude and (m := EFFECTS.get(k)) is not None
        and m.kind is EffectKind.DEBUFF
    )


def _strip_one_buff(
    session: "CombatSession", target: "Combatant",
) -> str | None:
    """Rip one random ``EffectKind.BUFF`` off the target. Returns the key or None."""
    buff_keys = [
        k for k in list(target.effects)
        if (m := EFFECTS.get(k)) is not None and m.kind is EffectKind.BUFF
    ]
    if not buff_keys:
        return None
    chosen = session.rng.choice(buff_keys)
    target.effects.pop(chosen, None)
    target.effect_overrides.pop(chosen, None)
    return chosen


def _refresh_thien_loi_an(
    target: "Combatant", n: int, per_debuff_amp: float, refresh_duration: int,
) -> float:
    """Stamp DebuffThienLoiAn with a dynamic dmg_taken_bonus_loi magnitude.

    The override carries ``stat_bonus[dmg_taken_bonus_loi] = n × per_debuff_amp``
    so every subsequent Lôi hit on the target (the formation's own strike
    AND every other Lôi source the holder commands) reads the amp via the
    standard ``get_combat_modifiers`` aggregation. Duration is short so
    the aura naturally fades if the formation stops ticking.
    """
    amp = max(0.0, n * per_debuff_amp)
    target.apply_effect(
        EffectKey.DEBUFF_THIEN_LOI_AN,
        refresh_duration,
        overrides={"stat_bonus": {"dmg_taken_bonus_loi": amp}},
    )
    return amp


def _emit_thien_loi_phan(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict, n: int,
) -> None:
    """Fire the regular Thiên Lôi Phán strike — debuff-count-scaling Lôi hit."""
    if not target.is_alive():
        return
    base = int(cfg.get("bolt_base", 0)) + int(cfg.get("bolt_base_per_debuff", 0)) * n
    matk_scale = float(cfg.get("bolt_matk_scale", 0.0)) \
        + float(cfg.get("bolt_matk_per_debuff", 0.0)) * n
    matk_part = int(matk_scale * actor.matk)
    dmg = _compute_prison_dmg(actor, target, base, matk_part)
    dmg = _apply_low_hp_amp(target, cfg, dmg)
    target.take_damage(dmg)
    tag = colorize_damage(f"-{dmg:,} HP", _PRISON_ELEMENT)
    session.log.append(
        f"    ⚡ **{actor.name}** **Thiên Lôi Phán** (vận tà ×{n}) "
        f"→ **{target.name}** {tag}"
    )
    chance = float(cfg.get("bolt_te_liet_chance_floor", 0.0)) \
        + float(cfg.get("bolt_te_liet_chance_per_debuff", 0.0)) * n
    _fire_te_liet(session, actor, target, chance=min(1.0, chance), duration=1)


def _emit_thien_loi_dai_phan(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict, n: int,
) -> None:
    """Fire Thiên Lôi Đại Phán capstone — heavy nuke + buff strip + Tê Liệt 2t."""
    if not target.is_alive():
        return
    base = int(cfg.get("capstone_base", 0)) + int(cfg.get("capstone_base_per_debuff", 0)) * n
    matk_scale = float(cfg.get("capstone_matk_scale", 0.0)) \
        + float(cfg.get("capstone_matk_per_debuff", 0.0)) * n
    matk_part = int(matk_scale * actor.matk)
    dmg = _compute_prison_dmg(actor, target, base, matk_part)
    dmg = _apply_low_hp_amp(target, cfg, dmg)
    target.take_damage(dmg)
    tag = colorize_damage(f"-{dmg:,} HP", _PRISON_ELEMENT)
    session.log.append(
        f"  ⚡⚖️ **{actor.name}** **Thiên Lôi Đại Phán** "
        f"(vận tà ×{n}) → **{target.name}** {tag}"
    )
    # Strip buffs — number scales with gem tier.
    strip_count = max(0, int(cfg.get("dai_phan_buff_strip", 0)))
    for _ in range(strip_count):
        if not target.is_alive():
            break
        stripped = _strip_one_buff(session, target)
        if stripped is None:
            break
        meta = EFFECTS.get(stripped)
        label = meta.vi if meta else stripped
        emoji = meta.emoji if meta else "✨"
        session.log.append(
            f"    🩶 Thiên Lôi tước **{emoji}{label}** khỏi **{target.name}**"
        )
    _fire_te_liet(
        session, actor, target,
        chance=float(cfg.get("capstone_te_liet_chance", 0.0)),
        duration=max(1, int(cfg.get("capstone_te_liet_duration", 2))),
    )


def _apply_low_hp_amp(target: "Combatant", cfg: dict, dmg: int) -> int:
    """Tuyệt Đỉnh tier: +X% damage when target HP% drops below threshold."""
    threshold = float(cfg.get("low_hp_threshold_pct", 0.0))
    amp = float(cfg.get("low_hp_amp_pct", 0.0))
    if threshold <= 0 or amp <= 0 or target.hp_max <= 0:
        return dmg
    hp_pct = float(target.hp) / float(target.hp_max)
    if hp_pct >= threshold:
        return dmg
    return max(1, int(dmg * (1.0 + amp)))


def process_thien_loi_tru_ta_tick(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    spec: dict,
) -> None:
    """Resolve one Thiên Lôi Tru Tà Trận formation tick.

    Sequence: count target's distinct debuffs (excluding the formation's own
    Thiên Lôi Ấn to avoid self-feedback), refresh Thiên Lôi Ấn on target
    with a magnitude proportional to that count, then fire either the
    regular Thiên Lôi Phán or the Thiên Lôi Đại Phán capstone based on
    threshold + cooldown. Đại Phán additionally strips buffs.
    """
    if not target.is_alive() or not isinstance(spec, dict):
        return
    cfg = getattr(actor, "thien_loi_tru_ta", None) or {}
    cap = max(0, int(cfg.get("debuff_count_cap", 8)))
    raw_count = _count_target_debuffs(
        target, exclude=EffectKey.DEBUFF_THIEN_LOI_AN.value,
    )
    n = min(cap, raw_count) if cap > 0 else raw_count

    # Refresh the Thiên Lôi Ấn aura so its dmg_taken_bonus_loi magnitude
    # reflects the current corruption count. Always runs even when n == 0
    # so the previous tick's stale magnitude gets cleared to zero.
    refresh_duration = max(1, int(spec.get("refresh_duration", 2)))
    per_debuff_amp = float(cfg.get("per_debuff_amp", 0.0))
    amp = _refresh_thien_loi_an(target, n, per_debuff_amp, refresh_duration)
    if amp > 0:
        session.log.append(
            f"    ⚖️ **{target.name}** Thiên Lôi Ấn — vận tà ×{n}, "
            f"+{amp * 100:.0f}% ST Lôi phải nhận"
        )

    # Capstone first when threshold met and cooldown clear. Otherwise fire
    # the regular Phán strike. Either way, Phán fires when capstone is
    # gated by cooldown so the formation never sits idle.
    threshold = max(1, int(cfg.get("capstone_threshold", spec.get("capstone_threshold", 5))))
    cd_remaining = int(actor.cooldowns.get(_DAI_PHAN_CD_KEY, 0))
    if n >= threshold and cd_remaining <= 0:
        _emit_thien_loi_dai_phan(session, actor, target, cfg, n)
        actor.cooldowns[_DAI_PHAN_CD_KEY] = max(1, int(cfg.get("dai_phan_cd", 7)))
    else:
        _emit_thien_loi_phan(session, actor, target, cfg, n)


# ─── Thái Cực Âm Dương Lôi Đại Trận ────────────────────────────────────
# Capstone Lôi formation. Two-pole rotation: each tick fires either the
# Dương (attack) or Âm (utility) phase, accumulating its pole's Khí (cap
# 5). When BOTH poles hit cap the next tick fires Thái Cực Lưỡng Nghi Lôi
# — heavy nuke + uncleansable Tê Liệt + consumes target Sốc Điện stacks
# as bonus damage, then resets both Khí. The 10-gem ``dual_phase_fire``
# tier collapses the rotation so both poles emit every tick.

_THAI_CUC_FUSION_CD_KEY = "ThaiCucLuongNghiCD"

# Owner-side Lôi-flavored buffs the Âm phase can extend by +1t. Filtered
# at apply time so a buff with duration already at cap (or above the cap,
# like the 999-duration BuffTuLoiQuyet aura) is left untouched — mirrors
# the ``_run_extend_self`` 0<current<cap gate from evade_reactive.
_THAI_CUC_AM_EXTENDABLE_BUFFS: tuple[str, ...] = (
    "BuffNguLoiCharged",
    "BuffCuuThienNguLoi",
    "BuffTamLoiCong",
    "BuffTuLoiHoThan",
    "BuffCuuThienLoiGiap",
    "BuffLoiThanKhai",
    "BuffLoiQuangDienAnh",
    "BuffBonLoiThuat",
)


def _emit_thai_cuc_duong(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict,
) -> None:
    """Dương Lôi Trảm — attack-pole Lôi nuke with crit-style flat amp."""
    if not target.is_alive():
        return
    base = int(cfg.get("duong_base", 0))
    matk_part = int(float(cfg.get("duong_matk_scale", 0.0)) * actor.matk)
    dmg = _compute_prison_dmg(actor, target, base, matk_part)
    amp = max(0.0, float(cfg.get("duong_dmg_amp_pct", 0.0)))
    if amp:
        dmg = max(1, int(dmg * (1.0 + amp)))
    target.take_damage(dmg)
    tag = colorize_damage(f"-{dmg:,} HP", _PRISON_ELEMENT)
    session.log.append(
        f"    ☀️ **{actor.name}** **Dương Lôi Trảm** → **{target.name}** {tag}"
    )
    # Sốc Điện stamp(s) on target — route through inflict_debuff so the
    # stack stamper logs + propagates correctly.
    shock_meta = EFFECTS.get(EffectKey.DEBUFF_SOC_DIEN)
    if shock_meta is not None:
        from src.game.systems.combat.casting import inflict_debuff
        for _ in range(int(cfg.get("duong_shock_stacks", 0))):
            if not target.is_alive():
                break
            inflict_debuff(
                session, EffectKey.DEBUFF_SOC_DIEN, shock_meta, target, actor=actor,
            )
    _fire_te_liet(
        session, actor, target,
        chance=float(cfg.get("duong_te_liet_chance", 0.0)),
        duration=1,
    )


def _emit_thai_cuc_am(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict,
) -> None:
    """Âm Lôi Phù — utility-pole: Sốc Điện stamps + Tê Nguyên Lực debuff +
    self HP/MP restore + extend owner's Lôi buffs by +1t (cap)."""
    # Sốc Điện stamps (multi-stack to ramp the chain trigger faster).
    shock_meta = EFFECTS.get(EffectKey.DEBUFF_SOC_DIEN)
    if shock_meta is not None and target.is_alive():
        from src.game.systems.combat.casting import inflict_debuff
        for _ in range(int(cfg.get("am_shock_stacks", 0))):
            if not target.is_alive():
                break
            inflict_debuff(
                session, EffectKey.DEBUFF_SOC_DIEN, shock_meta, target, actor=actor,
            )
    # Tê Nguyên Lực — MP regen suppression flavor.
    if bool(cfg.get("am_apply_te_nguyen_luc", False)) and target.is_alive():
        tnl_meta = EFFECTS.get(EffectKey.DEBUFF_TE_NGUYEN_LUC)
        if tnl_meta is not None:
            from src.game.systems.combat.casting import inflict_debuff
            inflict_debuff(
                session, EffectKey.DEBUFF_TE_NGUYEN_LUC, tnl_meta, target, actor=actor,
            )
    # Self HP / MP restore.
    hp_pct = float(cfg.get("am_hp_restore_pct", 0.0))
    mp_pct = float(cfg.get("am_mp_restore_pct", 0.0))
    healed = 0
    mp_gain = 0
    if hp_pct > 0 and actor.hp > 0:
        want = max(1, int(actor.hp_max * hp_pct))
        healed = session._apply_heal(actor, want)
    if mp_pct > 0:
        want_mp = max(1, int(actor.mp_max * mp_pct))
        before = actor.mp
        actor.mp = min(actor.mp_max, actor.mp + want_mp)
        mp_gain = actor.mp - before
    if healed > 0 or mp_gain > 0:
        parts: list[str] = []
        if healed > 0:
            parts.append(f"❤️ +{healed:,} HP")
        if mp_gain > 0:
            parts.append(f"💙 +{mp_gain:,} MP")
        session.log.append(
            f"    🌑 **{actor.name}** Âm Lôi Phù — {' '.join(parts)}"
        )
    # Extend owner's Lôi-flavored buffs by +1t up to the cap.
    cap = max(0, int(cfg.get("am_buff_extend_cap", 6)))
    if cap > 0:
        extended: list[str] = []
        for buff_key in _THAI_CUC_AM_EXTENDABLE_BUFFS:
            current = actor.effects.get(buff_key, 0)
            if 0 < current < cap:
                actor.effects[buff_key] = current + 1
                extended.append(buff_key)
        if extended:
            labels = ", ".join(
                (EFFECTS.get(k).vi if EFFECTS.get(k) else k) for k in extended
            )
            session.log.append(
                f"    🌑 **{actor.name}** Âm Lôi nuôi dưỡng — {labels} +1t"
            )


def _emit_thai_cuc_fusion(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    cfg: dict,
) -> None:
    """Thái Cực Lưỡng Nghi Lôi — capstone Lôi nuke when both poles cap.

    Damage = base + matk·scale + target_hp_pct·target.hp_max +
    bonus_per_stack·target.shock_stacks. Sốc Điện stacks are consumed
    (zeroed) after the bonus is computed so they can't double-dip on the
    same fusion. Uncleansable Tê Liệt for the configured duration.
    """
    if not target.is_alive():
        return
    base = int(cfg.get("fusion_base", 0))
    matk_part = int(float(cfg.get("fusion_matk_scale", 0.0)) * actor.matk)
    hp_pct = float(cfg.get("fusion_target_hp_pct", 0.0))
    hp_chunk = int(hp_pct * target.hp_max)
    shock_bonus_per = int(cfg.get("fusion_shock_bonus_per_stack", 0))
    shock_stacks = int(getattr(target, "shock_stacks", 0))
    shock_bonus = shock_bonus_per * shock_stacks
    raw_base = base + hp_chunk + shock_bonus
    dmg = _compute_prison_dmg(actor, target, raw_base, matk_part)
    target.take_damage(dmg)
    tag = colorize_damage(f"-{dmg:,} HP", _PRISON_ELEMENT)
    extras: list[str] = []
    if hp_chunk:
        extras.append(f"+{int(hp_pct * 100)}% HP địch")
    if shock_bonus:
        extras.append(f"+{shock_bonus:,} ({shock_stacks} Sốc Điện)")
    extra_suffix = f" ({'; '.join(extras)})" if extras else ""
    session.log.append(
        f"  ☯️ **{actor.name}** **Thái Cực Lưỡng Nghi Lôi**{extra_suffix} "
        f"→ **{target.name}** {tag}"
    )
    if shock_stacks > 0:
        target.consume_stacks("shock")
        target.effects.pop(EffectKey.DEBUFF_SOC_DIEN.value, None)
        target.effect_overrides.pop(EffectKey.DEBUFF_SOC_DIEN.value, None)
    # Uncleansable Tê Liệt: stamp via inflict_debuff with cleansable=False
    # override so dispel can't shake it. inflict_debuff merges the override
    # into the per-cast effect_overrides; the meta's cleansable field is
    # read elsewhere by the cleanse path.
    duration = max(1, int(cfg.get("fusion_te_liet_duration", 3)))
    meta = EFFECTS.get(EffectKey.DEBUFF_TE_LIET)
    if meta is not None and target.is_alive():
        from src.game.systems.combat.casting import inflict_debuff
        inflict_debuff(
            session, EffectKey.DEBUFF_TE_LIET, meta, target, actor=actor,
            overrides={"duration": duration, "cleansable": False},
        )


def process_thai_cuc_tick(
    session: "CombatSession", actor: "Combatant", target: "Combatant",
    spec: dict,
) -> None:
    """Resolve one Thái Cực Âm Dương Lôi Đại Trận formation tick.

    Sequence:
      1. If both Khí ≥ cap AND capstone CD clear → fire Thái Cực Lưỡng
         Nghi Lôi, reset both Khí to 0, set the fusion CD, return.
      2. Otherwise: read ``dual_phase_fire`` (10-gem). If set, emit BOTH
         poles this tick and bump BOTH Khí. Else emit the current
         ``taic_phase`` only and toggle.
      3. Each emit clamps its Khí counter to ``khi_cap``.
    """
    if not target.is_alive() or not isinstance(spec, dict):
        return
    cfg = getattr(actor, "thai_cuc_am_duong_loi", None) or {}
    cap = max(1, int(cfg.get("khi_cap", 5)))

    # Fusion check — both poles capped + CD clear.
    cd_remaining = int(actor.cooldowns.get(_THAI_CUC_FUSION_CD_KEY, 0))
    if (
        actor.taic_duong_khi >= cap
        and actor.taic_am_khi >= cap
        and cd_remaining <= 0
    ):
        _emit_thai_cuc_fusion(session, actor, target, cfg)
        actor.taic_duong_khi = 0
        actor.taic_am_khi = 0
        actor.cooldowns[_THAI_CUC_FUSION_CD_KEY] = max(
            1, int(cfg.get("fusion_cd", 8)),
        )
        return

    # Phase emit. 10-gem ``dual_phase_fire`` collapses the rotation so
    # both poles fire every tick and both Khí advance in lockstep.
    if bool(cfg.get("dual_phase_fire", False)):
        _emit_thai_cuc_duong(session, actor, target, cfg)
        actor.taic_duong_khi = min(cap, actor.taic_duong_khi + 1)
        if target.is_alive():
            _emit_thai_cuc_am(session, actor, target, cfg)
            actor.taic_am_khi = min(cap, actor.taic_am_khi + 1)
        return

    if actor.taic_phase == 0:
        _emit_thai_cuc_duong(session, actor, target, cfg)
        actor.taic_duong_khi = min(cap, actor.taic_duong_khi + 1)
        actor.taic_phase = 1
    else:
        _emit_thai_cuc_am(session, actor, target, cfg)
        actor.taic_am_khi = min(cap, actor.taic_am_khi + 1)
        actor.taic_phase = 0
