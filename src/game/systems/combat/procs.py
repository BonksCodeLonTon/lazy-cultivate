"""Post-damage proc machinery.

Everything here fires AFTER a successful damaging hit:
  - chance-based on-hit debuff/stack procs (_ON_HIT_PROCS table)
  - reactive damage (Thủy reflect, Thổ thorn)
  - Âm soul-drain + stat-steal (with lazy ``_original`` snapshots)

Each function takes ``session`` explicitly so it can append to the log and
read the RNG without holding a class reference.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import (
    SOUL_DRAIN_CAP_PCT, SOUL_DRAIN_PER_PROC_PCT, SOUL_DRAIN_SELF_GAIN_PCT,
    STAT_STEAL_CAP_PCT, STAT_STEAL_PER_PROC_PCT,
)
from src.game.constants.effects import EffectKey
from src.game.engine.damage import colorize_damage
from src.game.engine.effects import (
    EFFECTS, EffectKind, default_duration, effective_stack_cap, get_combat_modifiers,
)
from src.game.systems.combatant import Combatant

from .helpers import _ON_HIT_PROCS, _propagate_dot_bonuses, _propagate_stack_build

# Mirror multiplier on freeze_on_skill_chance — reflected freeze fires at
# 1.67× the cast chance (≈ 25% when cast = 15%) so a defensive freeze build
# punishes attackers harder via the mirror.
_FREEZE_MIRROR_BOOST = 1.67

if TYPE_CHECKING:
    from .session import CombatSession


def snapshot_original(combatant: Combatant, field: str, live_value: int) -> int:
    """Lazily record the combatant's starting value for a stat.

    Soul-drain and stat-steal cap math references pre-mutation snapshots;
    we capture the first-seen value instead of baking it into every call
    site of build_*_combatant. Returns the snapshot (live value if unset).
    """
    current = getattr(combatant, field, 0)
    if current:
        return current
    setattr(combatant, field, live_value)
    return live_value


# ── Bắc Minh Băng Phách Thể (Thủy ice/freeze/MP-drain disruptor) ────────────
_BM_SLOW = "DebuffLamCham"
_BM_HIT_SHAVE = "DebuffReduceHitCount"
_BM_FREEZE = EffectKey.DEBUFF_DONG_BANG.value
_BM_HEAL_LOCK = "DebuffCucHan"


def _bm_apply_cold_aura(
    session: "CombatSession", holder: Combatant, victim: Combatant,
) -> None:
    """L1 Cửu Âm Hàn Khí — slow + hit-count shave on ``victim`` (immunity-gated)."""
    if not victim.is_alive():
        return
    from .casting import inflict_debuff
    for key in (_BM_SLOW, _BM_HIT_SHAVE):
        meta = EFFECTS.get(key)
        if meta is not None:
            inflict_debuff(session, key, meta, victim, actor=holder)


def _bm_mp_drain(
    session: "CombatSession", drainer: Combatant, victim: Combatant,
) -> None:
    """L6 Bắc Minh Thôn Thực — drain ``victim`` MP → heal ``drainer``, bank Hàn Khí.

    Self-gates on ``drainer.bm_mp_drain_pct`` so it's inert for every build that
    isn't the body; ``han_khi_mp_drained_total`` accumulates for the L9 burst.
    """
    if drainer.bm_mp_drain_pct <= 0 or not victim.is_alive():
        return
    drained = int(victim.mp * drainer.bm_mp_drain_pct)
    if drained <= 0:
        return
    victim.mp = max(0, victim.mp - drained)
    drainer.han_khi_mp_drained_total += drained
    heal = int(drained * drainer.bm_mp_drain_heal_pct)
    if heal > 0:
        session._apply_heal(drainer, heal)
    if drainer.bm_han_khi_cap > 0 and drainer.han_khi_stacks < drainer.bm_han_khi_cap:
        drainer.han_khi_stacks += 1
    session.log.append(
        f"  🌀 **{drainer.name}** Bắc Minh Thôn Thực → hút {drained:,} MP "
        f"của **{victim.name}** (Hàn Khí ×{drainer.han_khi_stacks})"
    )


def _bm_try_freeze(
    session: "CombatSession", target: Combatant, turns: int,
) -> bool:
    """Freeze ``target`` for ``turns`` unless hard-CC immune. Returns True on freeze."""
    if not target.is_alive():
        return False
    if target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc"):
        session.log.append(f"    🛡️ **{target.name}** miễn nhiễm đóng băng!")
        return False
    target.apply_effect(_BM_FREEZE, turns)
    return True


def run_bac_minh_procs(
    session: "CombatSession", actor: Combatant, target: Combatant,
) -> None:
    """Bắc Minh Băng Phách on-hit logic, once per landed hit (``actor`` hits ``target``).

    When ``actor`` holds the body it runs the full attack kit (cold aura, freeze,
    MP drain, heal-reduce, Hàn Khí burst); when ``target`` holds it the
    bidirectional half (cold aura + reverse MP drain) fires back at the attacker.
    Every branch self-gates on the per-body config flags, so it's a no-op for
    every other build.
    """
    # ── Attacker side: the holder is striking ────────────────────────────────
    if actor.bm_cold_aura_enabled:
        _bm_apply_cold_aura(session, actor, target)
    _bm_mp_drain(session, actor, target)  # L6
    if (
        actor.bm_freeze_on_attack_chance > 0
        and target.is_alive()
        and session.rng.random() < actor.bm_freeze_on_attack_chance
        and _bm_try_freeze(session, target, default_duration(EffectKey.DEBUFF_DONG_BANG))
    ):
        session.log.append(
            f"    ❄️ **{actor.name}** Băng Phách → **{target.name}** đóng băng!"
        )
    if actor.bm_heal_reduce_chance > 0 and target.is_alive():
        chance = actor.bm_heal_reduce_chance
        if target.has_effect(_BM_FREEZE) and actor.bm_heal_reduce_vs_frozen_chance > chance:
            chance = actor.bm_heal_reduce_vs_frozen_chance
        if session.rng.random() < chance:
            meta = EFFECTS.get(_BM_HEAL_LOCK)
            if meta is not None:
                from .casting import inflict_debuff
                inflict_debuff(session, _BM_HEAL_LOCK, meta, target, actor=actor)
    if (
        actor.bm_burst_freeze_turns > 0
        and actor.bm_han_khi_cap > 0
        and actor.han_khi_stacks >= actor.bm_han_khi_cap
        and target.is_alive()
    ):
        _bm_try_freeze(session, target, actor.bm_burst_freeze_turns)
        burst = int(actor.han_khi_mp_drained_total * actor.bm_burst_drain_pct)
        if burst > 0:
            target.take_damage(burst)
            session.log.append(
                f"  🔱 **{actor.name}** Cực Hàn Bùng Nổ → **{target.name}** "
                f"{colorize_damage(f'-{burst:,} HP', None)}"
            )
        actor.han_khi_stacks = 0

    # ── Defender side: the holder is being struck (bidirectional L1 + L6) ─────
    if target.bm_cold_aura_enabled and actor.is_alive():
        _bm_apply_cold_aura(session, target, actor)
    _bm_mp_drain(session, target, actor)  # L6 reverse drain


# ── Huyền Minh Nhược Thể (Thủy anti-physical attrition disruptor) ────────────
_HM_SLOW = "DebuffLamCham"


def _hm_drain(
    session: "CombatSession", drainer: Combatant, victim: Combatant,
) -> None:
    """L3 Nhược Thủy Thôn Khí — drain ``victim`` MP → heal ``drainer``; on MP-dry,
    siphon HP instead and bank an Uyên stack.

    Self-gates on ``drainer.hm_mp_drain_pct`` so it's inert for every build that
    isn't the body. ``hm_mp_drained_total`` accumulates for the L9 drown burst.
    """
    if drainer.hm_mp_drain_pct <= 0 or not victim.is_alive():
        return
    if victim.mp > 0:
        drained = int(victim.mp * drainer.hm_mp_drain_pct)
        if drained <= 0:
            return
        victim.mp = max(0, victim.mp - drained)
        drainer.hm_mp_drained_total += drained
        heal = int(drained * drainer.hm_mp_drain_heal_pct)
        if heal > 0:
            session._apply_heal(drainer, heal)
        session.log.append(
            f"  🩸 **{drainer.name}** Nhược Thủy Thôn Khí → hút {drained:,} MP "
            f"của **{victim.name}**"
        )
        return
    # MP-dry: thôn thực sinh khí — siphon HP, heal, bank Uyên (gated on L3 siphon).
    if drainer.hm_hp_siphon_pct <= 0:
        return
    siphon = max(1, int(victim.hp_max * drainer.hm_hp_siphon_pct))
    victim.take_damage(siphon)
    heal = int(siphon * drainer.hm_mp_drain_heal_pct)
    if heal > 0:
        session._apply_heal(drainer, heal)
    if drainer.hm_uyen_cap > 0 and drainer.hm_uyen_stacks < drainer.hm_uyen_cap:
        drainer.hm_uyen_stacks += 1
    session.log.append(
        f"  🌊 **{drainer.name}** Thôn Thực Sinh Khí → hút {siphon:,} HP "
        f"của **{victim.name}** (Uyên ×{drainer.hm_uyen_stacks})"
    )


def run_huyen_minh_procs(
    session: "CombatSession", actor: Combatant, target: Combatant,
) -> None:
    """Huyền Minh Nhược on-hit logic, once per landed hit (``actor`` hits ``target``).

    Attacker side: L3 drain (MP→HP siphon) + L9 drown burst once Uyên is full.
    Defender side: the holder being struck drains the attacker back (bidirectional
    L3). Every branch self-gates on the per-body config flags, so it's a no-op for
    every other build. The L9 corrosion (guaranteed poison+bleed) is per-CAST and
    lives in cast_skill, not here.
    """
    # ── Attacker side: the holder is striking ────────────────────────────────
    _hm_drain(session, actor, target)  # L3
    if (
        actor.hm_drown_burst_drain_pct > 0
        and actor.hm_uyen_cap > 0
        and actor.hm_uyen_stacks >= actor.hm_uyen_cap
        and target.is_alive()
    ):
        burst = int(actor.hm_mp_drained_total * actor.hm_drown_burst_drain_pct)
        if burst > 0:
            target.take_damage(burst)
            session.log.append(
                f"  🌑 **{actor.name}** Hắc Thủy Nhấn Chìm → **{target.name}** "
                f"{colorize_damage(f'-{burst:,} HP', 'thuy')}"
            )
        meta = EFFECTS.get(_HM_SLOW)
        if meta is not None:
            from .casting import inflict_debuff
            inflict_debuff(session, _HM_SLOW, meta, target, actor=actor)
        actor.hm_uyen_stacks = 0

    # ── Defender side: the holder is being struck (bidirectional L3 drain) ─────
    _hm_drain(session, target, actor)


def run_on_hit_procs(
    session: "CombatSession", actor: Combatant, target: Combatant, is_crit: bool,
    skill_key: str = "",
) -> None:
    """Roll each chance-based on-hit proc from _ON_HIT_PROCS plus special cases.

    Special cases not driven by the table:
      - stun_on_hit_pct respects target's hard-CC immunity
      - paralysis_on_crit fires only on crit, always lands
      - freeze_on_skill_chance gates the freeze proc; mirror version
        scales by ``_FREEZE_MIRROR_BOOST``

    ``skill_key`` is the key of the skill that landed this hit (``""`` when a
    caller doesn't thread it — e.g. legacy test harnesses). The Kim Sát Khí
    stamp reads it to skip re-stamping on the auto-fired Kiếm Lãng splash.
    """
    # On-hit proc chances may be amped by active modifiers (buffs / scaling_rules)
    # — e.g. Tịnh Quang's Thánh Quang stacks raise blind_on_hit_pct via BuffHoPhap.
    # Additive: zero contribution (the default for every existing build) leaves the
    # raw-field behavior byte-identical.
    _actor_mods = get_combat_modifiers(actor)
    for spec in _ON_HIT_PROCS:
        chance = getattr(actor, spec["chance_attr"], 0.0) + float(
            _actor_mods.get(spec["chance_attr"], 0.0)
        )
        if chance <= 0 or session.rng.random() >= chance:
            continue
        effect_key = spec["effect_key"]
        dur = default_duration(effect_key)
        target.apply_effect(effect_key, dur)
        # Tịnh Quang Hộ Pháp — Thánh Quang: a landed blind banks a stack (cap 5),
        # which BuffHoPhap's scaling_rules convert into +blind chance + DR.
        if (
            effect_key == EffectKey.DEBUFF_LOA_MAT
            and actor.quang_blind_stack and actor.thanh_quang_stacks < 5
        ):
            actor.thanh_quang_stacks += 1
            session.log.append(
                f"    ☀️ Thánh Quang ngưng tụ [×{actor.thanh_quang_stacks}/5]"
            )
        stack_kind = spec.get("stack_kind")
        if stack_kind:
            _propagate_stack_build(actor, target, stack_kind)
            target.add_stack(stack_kind, 1)
            session.log.append(spec["log_fmt"].format(
                stacks=getattr(target, spec["stacks_attr"]),
                cap=effective_stack_cap(target, str(effect_key)),
            ))
        else:
            session.log.append(spec["log_fmt"])

    # Mộc build: Trường Xuân Bất Tử (L9) — guaranteed poison on EVERY attack, on
    # top of the 55% L1 proc above. Inert unless the L9 flag is set; respects
    # poison immunity (mirrors the manual gate in the kinh_hoa aura).
    if actor.moc_guaranteed_poison_on_attack and actor.moc_guaranteed_poison_stacks > 0:
        if target.poison_immunity:
            session.log.append(f"    🛡️ **{target.name}** miễn nhiễm Trúng Độc!")
        else:
            _poison_dur = default_duration(EffectKey.DEBUFF_DOC_TO)
            target.apply_effect(EffectKey.DEBUFF_DOC_TO, _poison_dur)
            _propagate_stack_build(actor, target, "poison")
            for _ in range(actor.moc_guaranteed_poison_stacks):
                target.add_stack("poison", 1)
            session.log.append(
                "    🍃 Linh Mộc Chi Độc (bất biến) [×{stacks}/{cap}]".format(
                    stacks=target.poison_stacks,
                    cap=effective_stack_cap(target, str(EffectKey.DEBUFF_DOC_TO)),
                )
            )

    # Hoàng Cổ Thánh Thể L5 — Lân Tủy Thánh Hòa: per-hit self-cleanse.
    # Reuses the same cleansable-flag filter that quang.try_cleanse uses.
    # Inert when chance 0.0 (default for all non-saint builds).
    if actor.saint_qilin_cleanse_chance > 0 and session.rng.random() < actor.saint_qilin_cleanse_chance:
        from src.game.engine.effects import EFFECTS as _EFFECTS_CLEANSE
        _cleansable = [
            k for k in list(actor.effects)
            if (m := _EFFECTS_CLEANSE.get(k)) is not None and m.cleansable
        ]
        if _cleansable:
            _removed = session.rng.choice(_cleansable)
            del actor.effects[_removed]
            actor.effect_overrides.pop(_removed, None)
            session.log.append(
                f"  🦌 **{actor.name}** Lân Tủy Thánh Hòa — thanh tẩy *{_removed}*"
            )

    # Hoàng Cổ Thánh Thể L8 — Đạo Văn Quy Nhất: restore MP per landed hit.
    # One-liner; inert when field is 0.0 (all non-saint builds).
    if actor.saint_mp_on_hit_pct > 0:
        actor.mp = min(actor.mp_max, actor.mp + int(actor.mp_max * actor.saint_mp_on_hit_pct))

    # Thổ build: stun_on_hit — flat chance, respects hard-CC immunity
    if actor.stun_on_hit_pct > 0 and session.rng.random() < actor.stun_on_hit_pct:
        if target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(f"    🛡️ **{target.name}** miễn dịch Choáng!")
        else:
            target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
            session.log.append(f"    💫 Choáng kích hoạt!")
    if is_crit and actor.paralysis_on_crit:
        if target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(
                f"    🛡️ **{target.name}** miễn dịch Tê Liệt khi Bạo Kích!"
            )
        else:
            target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
            session.log.append(f"    ⚡ Tê Liệt khi Bạo Kích kích hoạt!")
    # Quang build: silence_on_crit — crit-gated CCMuted application.
    # Respects the same hard-CC immunity used by stun_on_hit.
    if is_crit and actor.silence_on_crit_pct > 0 and session.rng.random() < actor.silence_on_crit_pct:
        if target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(f"    🛡️ **{target.name}** miễn dịch Câm Lặng!")
        else:
            target.apply_effect(EffectKey.CC_MUTED, default_duration(EffectKey.CC_MUTED))
            session.log.append(f"    ✨ Thánh Quang Chế Ngự — câm lặng kích hoạt!")
    # ── Thiên Lôi Cường Thể (Lôi shock/speed nuker) ──────────────────────────
    # L1 Tê Liệt on crit — chance-gated paralysis. Respects hard-CC immunity.
    # Inert unless the actor carries the L1 chance (non-Lôi builds never do).
    if (
        is_crit and actor.loi_te_liet_on_crit_chance > 0
        and session.rng.random() < actor.loi_te_liet_on_crit_chance
    ):
        if target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(f"    🛡️ **{target.name}** miễn dịch Tê Liệt!")
        else:
            from .casting import inflict_debuff
            _tl_key = EffectKey.DEBUFF_TE_LIET.value
            _tl_meta = EFFECTS.get(_tl_key)
            if _tl_meta is not None:
                inflict_debuff(session, _tl_key, _tl_meta, target, actor=actor)
                session.log.append(f"    ⚡ Tê Liệt kích hoạt!")
    # L6 Điện Quang Phản Ứng — bonus shock attack on CRIT (the dodge trigger
    # lives in casting.py's on-evade block). RECURSION-GUARDED: the bonus shock
    # routes back through run_on_hit_procs with skill_key == "SkillLoiBonusShock"
    # — skipping it here is what stops the reflex from re-firing without bound.
    if (
        is_crit and actor.loi_reflex_bonus_attack
        and skill_key != "SkillLoiBonusShock" and target.is_alive()
    ):
        from src.game.systems.combat.casting import cast_skill as _loi_cast
        from src.data.registry import registry as _loi_reg
        _bonus = _loi_reg.get_skill("SkillLoiBonusShock")
        if _bonus is not None:
            session.log.append(
                f"  ⚡ **{actor.name}** Điện Quang Phản Ứng — Sốc Điện phụ!"
            )
            _loi_cast(
                session, actor, target,
                "SkillLoiBonusShock", _bonus, 0, _suppress_extras=True,
            )
    # Tịnh Quang Hộ Pháp L9 — Thiên Quang Thẩm Phán: hitting a BUFFED enemy,
    # chance to strip one of its buffs + apply Phá Giáp (reuses the strip logic
    # + body #1's DebuffPhaGiap). Inert unless the L9 flag is set.
    if actor.quang_judgment_strip_chance > 0 and target.is_alive():
        _judg_buffs = [
            k for k in list(target.effects)
            if (m := EFFECTS.get(k)) is not None and m.kind is EffectKind.BUFF
        ]
        if _judg_buffs and session.rng.random() < actor.quang_judgment_strip_chance:
            _pick = session.rng.choice(_judg_buffs)
            target.effects.pop(_pick, None)
            target.effect_overrides.pop(_pick, None)
            _pm = EFFECTS.get(_pick)
            session.log.append(
                f"    ⚖️ **{actor.name}** Thiên Quang Thẩm Phán — tước "
                f"{_pm.vi if _pm else _pick}"
            )
            if actor.quang_judgment_applies_pha_giap:
                from .casting import inflict_debuff
                _pg = EFFECTS.get("DebuffPhaGiap")
                if _pg is not None:
                    inflict_debuff(session, "DebuffPhaGiap", _pg, target, actor=actor)
            # L9 buff — each strip giáng a 200% atk + 200% matk Quang burst AND
            # ramps the guardian's own final_dmg by +5% (cap +50%, read in
            # combat_hit). The pure-defense guardian's window of offense.
            _judg_burst = max(1, int(2.0 * actor.atk + 2.0 * actor.matk))
            if target.is_alive():
                target.take_damage(_judg_burst)
                _jtag = colorize_damage(f"-{_judg_burst:,} HP", "quang")
                session.log.append(
                    f"    ⚖️💥 **{actor.name}** Thiên Quang Thẩm Phán giáng phạt "
                    f"→ **{target.name}** {_jtag}"
                )
            actor.quang_judgment_dmg_bonus = min(
                0.50, actor.quang_judgment_dmg_bonus + 0.05
            )
            session.log.append(
                f"    ☀️ Thánh Uy tăng tiến — +{int(actor.quang_judgment_dmg_bonus * 100)}% ST cuối"
            )
    # ── Kim killing-body sweep (Thiên Cương Phá Sát Thể) ─────────────────────
    # All three blocks are no-ops unless the actor carries the Kim body's
    # effects/flags (enemies, flag-off builds, and non-Kim players never own
    # BuffSatKhi or non-zero kim_* flags), so this whole region stays inert
    # outside the intended build. Ordering is load-bearing per the design:
    # (a) stamp Sát Khí → (b) L3 Phá Giáp reads the post-stamp stack count →
    # (c) L9 payoff fires only at exactly 5 stacks.
    #
    # (a) Sát Khí stamp — one stack per non-Kiếm-Lãng hit. Skipping the splash's
    # own cast is what prevents a re-stamp / recursion loop (the splash routes
    # back through run_on_hit_procs with skill_key == "SkillKimKiemLang").
    if actor.has_effect("BuffSatKhi") and skill_key != "SkillKimKiemLang":
        actor.add_stack("sat_khi", 1)
    # (b) L3 Kim Phá Ngọc Toái — chance to apply Phá Giáp; the higher chance
    # applies once Sát Khí is at 3+ stacks. Routed via inflict_debuff so target
    # immunity / reflect handling stays consistent with every other debuff.
    if actor.kim_pha_giap_on_hit_chance > 0:
        pha_giap_chance = (
            actor.kim_pha_giap_high_sat_khi_chance
            if actor.sat_khi_stacks >= 3
            else actor.kim_pha_giap_on_hit_chance
        )
        if session.rng.random() < pha_giap_chance:
            from src.game.engine.effects import EFFECTS as _EFFECTS
            _pg_meta = _EFFECTS.get("DebuffPhaGiap")
            if _pg_meta is not None:
                from .casting import inflict_debuff
                inflict_debuff(session, "DebuffPhaGiap", _pg_meta, target, actor=actor)
    # (c) L9 Sát Khí Đại Thành — at exactly 5 stacks while the marker buff is
    # held: suppress the enemy's atk/matk (1-turn, refreshed each max-stack hit)
    # and roll the Kiếm Lãng splash. Splash chance scales off the actor's own
    # effective crit chance, capped.
    #
    # The ``skill_key != "SkillKimKiemLang"`` guard is RECURSION-CRITICAL: the
    # splash routes its own hit back through run_on_hit_procs, and the actor is
    # still at 5 stacks with the marker buff — without this guard the splash's
    # own sweep would re-fire the payoff (suppression + another splash) and
    # recurse without bound. The same guard already blocks the Sát Khí re-stamp
    # in block (a).
    if (
        skill_key != "SkillKimKiemLang"
        and actor.kim_sword_splash_at_max_sat_khi
        and actor.sat_khi_stacks == 5
        and actor.has_effect("BuffSatKhiDaiThanh")
    ):
        from src.game.engine.effects import EFFECTS as _EFFECTS
        from src.game.engine.rating import crit_chance
        from src.game.systems.combat.casting import cast_skill, inflict_debuff
        from src.data.registry import registry as _registry
        _kl_meta = _EFFECTS.get("DebuffKiemLangApChe")
        if _kl_meta is not None:
            inflict_debuff(session, "DebuffKiemLangApChe", _kl_meta, target, actor=actor)
        eff_crit = crit_chance(
            actor.crit_rating + int(get_combat_modifiers(actor).get("crit_rating", 0)), 0
        )
        splash_chance = min(
            actor.kim_sword_splash_chance_cap,
            actor.kim_sword_splash_base_chance
            + actor.kim_sword_splash_crit_coeff * eff_crit,
        )
        if splash_chance > 0 and session.rng.random() < splash_chance and target.is_alive():
            _kl_skill = _registry.get_skill("SkillKimKiemLang")
            if _kl_skill is not None:
                session.log.append(
                    f"  🗡️ **{actor.name}** Sát Khí Đại Thành — **Kiếm Lãng** bùng nổ!"
                )
                # ``_suppress_extras=True`` keeps the splash from chaining /
                # summoning, and the ``skill_key == "SkillKimKiemLang"`` guard in
                # block (a) keeps its own on-hit sweep from re-stamping Sát Khí.
                cast_skill(
                    session, actor, target,
                    "SkillKimKiemLang", _kl_skill, 0,
                    _suppress_extras=True,
                )

    # ── Ám shadow-mage sweep (Huyền Âm Thiên Ma Thể) ─────────────────────────
    # Inert unless the actor carries the body's flag/buff (enemies, flag-off,
    # non-Ám players never set ``shadow_stack_on_hit`` or own BuffMaKhi).
    #
    # (a) L1 Hắc Ám Ngưng Tụ — +1 Ma Khí per hit; at the 6-stack cap, drain the
    # target's soul (Hồn Phệ) + apply the Thực Hồn soul-eating DoT, then reset.
    if actor.shadow_stack_on_hit and actor.has_effect("BuffMaKhi"):
        actor.add_stack("shadow", 1)
        if actor.shadow_stacks >= effective_stack_cap(actor, "BuffMaKhi"):
            apply_soul_drain(session, actor, target)
            from src.game.engine.effects import EFFECTS as _EFFECTS
            _th_meta = _EFFECTS.get("DebuffThucHon")
            if _th_meta is not None:
                from .casting import inflict_debuff
                # Seed the applier's stats into ``target.dot_bonus_sources`` so
                # the caster-scaled (``dot_caster_matk_scale``) Thực Hồn tick
                # reads ``actor.matk``. inflict_debuff only auto-propagates for
                # ``dot_pct > 0`` DoTs, so a caster-scaled DoT must be seeded
                # here (same helper the DoT tick + tests rely on).
                _propagate_dot_bonuses(actor, target)
                inflict_debuff(session, "DebuffThucHon", _th_meta, target, actor=actor)
            actor.consume_stacks("shadow")
    # (b) L6 Thiên Ma Đồng Hóa — while in the Nhập Ma trance, every hit also
    # steals atk/matk/def from the target (Đạo Pháp Thôn Phệ). The +final_dmg
    # half lives in combat_hit.py; this is the stat-steal half.
    if actor.nhap_ma_dmg_bonus > 0 and actor.has_effect("BuffNhapMa"):
        apply_stat_steal(session, actor, target)

    # ── Huyền Thủy Trường Sinh — L6 Băng Toái Quyết (Glacial Shatter) ────────
    # Hitting a FROZEN target releases ``thuy_shatter_tide_pct`` of the body's
    # reservoir as a Thủy strike and breaks the ice. Inert unless the actor
    # holds the L6 buff with a non-empty reservoir and the target is frozen.
    if (
        actor.thuy_shatter_tide_pct > 0
        and actor.thuy_intake_reservoir > 0
        and actor.has_effect("BuffBangToaiQuyet")
        and target.has_effect(EffectKey.DEBUFF_DONG_BANG)
    ):
        from .thuy_tide import tide_strike
        shatter = int(actor.thuy_intake_reservoir * actor.thuy_shatter_tide_pct)
        dealt = tide_strike(actor, target, shatter)
        if dealt > 0:
            actor.thuy_intake_reservoir -= shatter
            # Break the ice — the shatter consumes the freeze.
            target.effects.pop(EffectKey.DEBUFF_DONG_BANG.value, None)
            target.effect_overrides.pop(EffectKey.DEBUFF_DONG_BANG.value, None)
            _tag = colorize_damage(f"-{dealt:,} HP", "thuy")
            session.log.append(
                f"    🧊 **{actor.name}** Băng Toái Quyết — dội Triều Khố "
                f"({shatter:,}) phá băng → **{target.name}** {_tag}"
            )

    # Freeze proc — chance comes from ``freeze_on_skill_chance``. Mirror
    # version (in apply_reflect) scales by ``_FREEZE_MIRROR_BOOST`` so the
    # reflected freeze fires harder than the cast.
    freeze_chance = actor.freeze_on_skill_chance
    if freeze_chance > 0 and session.rng.random() < freeze_chance:
        if target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(
                f"    🛡️ **{target.name}** miễn dịch Đóng Băng!"
            )
        else:
            dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
            target.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
            session.log.append(f"    🧊 Đông Băng kích hoạt!")
    # Âm build: Hồn Phệ — permanently drain target.hp_max, transfer half
    # to actor. Capped to avoid trivializing bosses.
    if actor.soul_drain_on_hit_pct > 0 and session.rng.random() < actor.soul_drain_on_hit_pct:
        apply_soul_drain(session, actor, target)
    # Âm build: Đạo Pháp Thôn Phệ — steal atk/matk/def from target.
    if actor.stat_steal_on_hit_pct > 0 and session.rng.random() < actor.stat_steal_on_hit_pct:
        apply_stat_steal(session, actor, target)
    # Aura-on-hit from active buffs — e.g. BuffHanKhi spreads Làm Chậm to
    # anyone the holder strikes. Iterate the actor's effects once and
    # fire any aura hooks their EffectMeta declares.
    for effect_key in list(actor.effects.keys()):
        aura_meta = EFFECTS.get(effect_key)
        if aura_meta is None or aura_meta.aura_on_hit is None:
            continue
        # ``aura_on_hit`` may be a 2-tuple (effect, chance) or a 3-tuple
        # (effect, chance, duration). Star-unpack the tail so a future 4th
        # field can land here without rewriting the call site.
        aura_effect, chance, *aura_rest = aura_meta.aura_on_hit
        if session.rng.random() >= chance:
            continue
        aura_dst_meta = EFFECTS.get(aura_effect)
        if aura_dst_meta is None:
            continue
        # Respect hard-CC immunity for stun/silence-style auras.
        if (target.immune_hard_cc or target.has_effect("BuffHoangCoThanhVuc")) and (
            aura_dst_meta.skips_turn or aura_dst_meta.prevents_skills
        ):
            continue
        # Fold buff-derived debuff_immune_pct into the target's base value so
        # passives like Chân Ma Chi Tâm can shrug off aura-on-hit spreads.
        # Aura procs don't get the actor's apply-bonus — that belongs to
        # explicit cast paths only.
        immune_mod = float(get_combat_modifiers(target).get("debuff_immune_pct", 0.0))
        immune = max(0.0, min(1.0, target.debuff_immune_pct + immune_mod))
        effective = 1.0 - immune
        if effective < 1.0 and session.rng.random() >= effective:
            continue
        aura_duration = int(aura_rest[0]) if aura_rest else default_duration(aura_effect)
        target.apply_effect(aura_effect, aura_duration)
        session.log.append(
            f"    {aura_meta.emoji} Hào quang **{aura_meta.vi}** "
            f"→ {aura_dst_meta.emoji} {aura_dst_meta.vi}"
        )


def apply_life_steal(
    session: "CombatSession", actor: Combatant, dmg: int
) -> int:
    """Heal ``actor`` for ``dmg × actor.life_steal_pct``.

    Routed through ``session._apply_heal`` so bleed-heal-reduction and
    ``heal_can_crit`` (Mộc/Quang) both apply uniformly. No-ops when the
    actor has no life-steal, when the hit dealt 0 damage, or when the
    actor is already dead. Returns the actual HP restored.
    """
    if actor.life_steal_pct <= 0 or dmg <= 0 or not actor.is_alive():
        return 0
    raw = max(1, int(dmg * actor.life_steal_pct))
    healed = session._apply_heal(actor, raw)
    if healed > 0:
        session.log.append(
            f"    🩸 **{actor.name}** Hút Máu → +{healed:,} HP "
            f"({actor.life_steal_pct * 100:.0f}% sát thương)"
        )
    return healed


def apply_soul_drain(
    session: "CombatSession", actor: Combatant, target: Combatant
) -> None:
    """Permanently shrink target.hp_max; transfer part to actor.

    No-op once the cap (SOUL_DRAIN_CAP_PCT of target's starting hp_max)
    is reached. The drained amount also removes current HP in parallel
    so the immediate fight impact matches the long-term one.

    World bosses are immune: mutating ``hp_max`` on a shared-pool entity
    would double-credit the attacker via the local-sim damage calc.
    Targets flagged ``immune_stat_mutation`` (chi-tôn bosses such as Chung
    Yên) are also immune by design — their stat sheet is canon.
    """
    if target.is_world_boss or target.immune_stat_mutation:
        session.log.append(
            f"    🌑 **Hồn Phệ** vô hiệu — **{target.name}** miễn nhiễm "
            f"xói mòn HP Max."
        )
        return
    start = snapshot_original(target, "hp_max_original", target.hp_max)
    cap = int(start * SOUL_DRAIN_CAP_PCT)
    remaining = cap - target.hp_max_drained
    if remaining <= 0:
        return
    drain = min(remaining, max(1, int(start * SOUL_DRAIN_PER_PROC_PCT)))
    target.hp_max_drained += drain
    target.hp_max = max(1, target.hp_max - drain)
    target.hp = min(target.hp, target.hp_max)
    gain = max(1, int(drain * SOUL_DRAIN_SELF_GAIN_PCT))
    actor.hp_max += gain
    actor.hp = min(actor.hp_max, actor.hp + gain)
    session.log.append(
        f"    🌑 **Hồn Phệ** — **{target.name}** -{drain:,} HP Max "
        f"(tổng {target.hp_max_drained:,}/{cap:,}) → **{actor.name}** +{gain:,} HP Max"
    )


def apply_stat_steal(
    session: "CombatSession", actor: Combatant, target: Combatant,
    per_proc_pct: float | None = None,
) -> None:
    """Transfer a slice of target's atk/matk/def into the actor.

    Cap and per-proc magnitude are both anchored to the **actor's own**
    starting stats (snapshotted at first proc), not the target's. This
    makes Âm growth bounded by the player's investment — a 5,000-ATK boss
    feeds the actor at the same rate as a 500-ATK trash mob, and the
    lifetime ceiling is always ``actor_start × STAT_STEAL_CAP_PCT``.

    The target loses exactly what the actor gains, clamped by the target's
    remaining live stat so they can't be drained below 0.

    ``per_proc_pct`` (optional) overrides the global per-proc magnitude
    (``STAT_STEAL_PER_PROC_PCT``). Used by signature steal skills like Sưu
    Hồn Đoạt Phách that take a big chunk in one cast instead of chaining
    several small procs.

    World bosses and ``immune_stat_mutation`` targets (Chung Yên and other
    chi-tôn bosses) are immune — same rationale as soul-drain: their stat
    sheet is authoritative and a player wearing them down across multiple
    casts would invalidate the encounter's tuning.
    """
    if target.is_world_boss or target.immune_stat_mutation:
        session.log.append(
            f"    🩶 **Đạo Pháp Thôn Phệ** vô hiệu — **{target.name}** "
            f"miễn nhiễm cướp chỉ số."
        )
        return
    proc_pct = STAT_STEAL_PER_PROC_PCT if per_proc_pct is None else per_proc_pct
    labels = [("atk", "stolen_atk", "atk_original", "Công"),
              ("matk", "stolen_matk", "matk_original", "Pháp"),
              ("def_stat", "stolen_def", "def_stat_original", "Phòng")]
    stolen_parts: list[str] = []
    for live_attr, stolen_attr, snap_attr, label in labels:
        # Anchor cap + per-proc magnitude to the actor's starting stat —
        # snapshot lazily so the very first proc captures the pre-steal
        # value, and subsequent procs read it back without compounding.
        actor_start = snapshot_original(actor, snap_attr, getattr(actor, live_attr))
        if actor_start <= 0:
            continue
        cap = int(actor_start * STAT_STEAL_CAP_PCT)
        already_gained = getattr(actor, stolen_attr)
        remaining = cap - already_gained
        if remaining <= 0:
            continue
        target_live = getattr(target, live_attr)
        if target_live <= 0:
            continue  # nothing to drain
        # Snapshot target's start too so Quang's cleanse counter (reads
        # ``target.<snap_attr>``) still has a reference point if the
        # target later cleanses what was stolen from them.
        snapshot_original(target, snap_attr, target_live)
        steal = min(
            remaining,
            max(1, int(actor_start * proc_pct)),
            target_live,
        )
        # Apply: actor grows, target shrinks by the same amount.
        setattr(actor, stolen_attr, already_gained + steal)
        setattr(actor, live_attr, getattr(actor, live_attr) + steal)
        setattr(target, stolen_attr, getattr(target, stolen_attr) + steal)
        setattr(target, live_attr, target_live - steal)
        stolen_parts.append(f"+{steal} {label}")
    if stolen_parts:
        session.log.append(
            f"    🩶 **Đạo Pháp Thôn Phệ** — **{actor.name}** cướp "
            f"{' / '.join(stolen_parts)} từ **{target.name}**"
        )


def apply_buff_steal(
    session: "CombatSession", actor: Combatant, target: Combatant,
) -> None:
    """Rip one stealable buff off the target and stamp it on the actor.

    Picks a random ``EffectMeta.stealable`` entry from the target's active
    effects, removes it (along with any ``effect_overrides`` stamp), and
    re-applies it on the actor with the same remaining duration + override
    magnitudes. Re-applying via ``apply_effect`` means refreshing-vs-existing
    semantics still work: if the actor already carries the same buff, the
    stronger of the two stat-bonus values wins per stat.

    Random pick (rather than first-in-iteration) so application order on the
    target can't be gamed to shield the most valuable buff. No-op when the
    target has no stealable buff.
    """
    stealable_keys = [
        k for k in list(target.effects)
        if (m := EFFECTS.get(k)) is not None and m.stealable
    ]
    if not stealable_keys:
        return
    key = session.rng.choice(stealable_keys)
    dur = target.effects.pop(key)
    override = target.effect_overrides.pop(key, None)
    actor.apply_effect(key, dur, overrides=override)
    meta = EFFECTS.get(key)
    label = meta.vi if meta else key
    emoji = meta.emoji if meta else "✨"
    session.log.append(
        f"    🩶 **Đoạt Pháp** — **{actor.name}** cướp {emoji}{label} ({dur}t) "
        f"từ **{target.name}**"
    )


def apply_reactive_damage(
    session: "CombatSession", actor: Combatant, target: Combatant, dmg: int,
    skill_element: str | None = None,
) -> None:
    """Thủy reflect + Thổ thorn — defender-triggered retaliation.

    Reflect reads the defender's permanent ``reflect_pct`` plus any active-
    effect contribution (Kim Chung Tráo style buffs) so temp reflect auras
    fire alongside permanent gear-driven reflect.

    ``skill_element`` (the incoming hit's element, when elemental) drives the
    adaptive-elemental-resist aegis hook (Tri Hành Hợp Nhất) at the tail.
    """
    from src.game.engine.effects import get_combat_modifiers

    # ── Huyền Thủy Trường Sinh — defender-side tidal counter-punch ───────────
    # ``target`` here is the DEFENDER (the holder taking ``dmg``); ``actor`` is
    # the attacker. Both blocks are inert unless the defender carries the body's
    # buffs / non-zero flags (enemies, flag-off, non-Thủy bodies).
    #
    # (5a) L1 Nạp Triều Khố — bank ``thuy_tide_intake_pct`` of the damage taken
    # into the body's OWN reservoir (``thuy_intake_reservoir`` — isolated from
    # the skill's ``thuy_tide``), clamped to ``cap_scale × matk``. Mirrors
    # ``capture_overheal``.
    if (
        dmg > 0
        and target.thuy_tide_intake_pct > 0
        and target.has_effect("BuffNapTrieuKho")
    ):
        cap = int(target.thuy_reservoir_cap_matk_scale * target.matk)
        if cap > 0:
            gained = int(dmg * target.thuy_tide_intake_pct)
            target.thuy_intake_reservoir = min(
                cap, target.thuy_intake_reservoir + gained
            )
    # (5b) L3 Hàn Thủy Đóng Băng — chance to freeze the attacker on a hit taken.
    # Mirrors the freeze-mirror pattern (respects hard-CC immunity).
    if (
        dmg > 0
        and actor.is_alive()
        and target.thuy_retaliate_freeze_chance > 0
        and target.has_effect("BuffHanThuyDongBang")
        and session.rng.random() < target.thuy_retaliate_freeze_chance
    ):
        if actor.immune_hard_cc or actor.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(
                f"    🛡️ **{actor.name}** miễn dịch Hàn Thủy Đóng Băng!"
            )
        else:
            dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
            actor.apply_effect(EffectKey.DEBUFF_DONG_BANG.value, dur)
            session.log.append(
                f"    ❄️ **{target.name}** Hàn Thủy Đóng Băng "
                f"→ **{actor.name}** bị đông cứng!"
            )

    # ── Thiên Thủy Thánh Thể — defender-side damage→heal + reflect ───────────
    # Nhu Thủy Hóa Kình (L3): the holder converts a fraction of the damage it
    # just took into healing (routed through ``_apply_heal`` so the L6 cleanse +
    # Tịnh Hóa MP-on-heal fire), then reflects a fraction of the REMAINDER back
    # at the attacker. Self-gates on the per-body flag (inert for everyone else).
    if dmg > 0 and target.tt_dmg_convert_heal_pct > 0:
        healed = session._apply_heal(target, int(dmg * target.tt_dmg_convert_heal_pct))
        if healed > 0:
            session.log.append(
                f"    🌊 **{target.name}** Nhu Thủy Hóa Kình → hồi {healed:,} HP"
            )
        if target.tt_reflect_remainder_pct > 0 and actor.is_alive():
            remainder = int(dmg * (1.0 - target.tt_dmg_convert_heal_pct))
            reflected = int(remainder * target.tt_reflect_remainder_pct)
            if reflected > 0:
                actor.take_damage(reflected)
                session.log.append(
                    f"    🪞 **{target.name}** phản chấn → **{actor.name}** "
                    f"{colorize_damage(f'-{reflected:,} HP', 'thuy')}"
                )

    reflect_total = target.reflect_pct + float(
        get_combat_modifiers(target).get("reflect_pct", 0.0)
    )
    if reflect_total > 0:
        apply_reflect(session, actor, target, dmg, reflect_pct=reflect_total)
    if target.thorn_pct > 0 and actor.is_alive():
        apply_thorn(session, actor, target, dmg)

    # Hộ Thể Kiếm Cương — every damaging hit on a holder of the buff spawns
    # the swords declared by the buff's ``summon_on_hit_taken`` override.
    # Routed through ``maybe_spawn_summon`` so the existing summon limit /
    # element-amp / consume-spec plumbing all apply unchanged.
    if dmg > 0 and target.has_effect(EffectKey.BUFF_HO_THE_KIEM_CUONG):
        buff_ovr = target.effect_overrides.get(
            EffectKey.BUFF_HO_THE_KIEM_CUONG.value
        ) or {}
        summon_spec = buff_ovr.get("summon_on_hit_taken")
        if summon_spec:
            from .skill_extras import maybe_spawn_summon
            maybe_spawn_summon(session, target, {"summon_spec": summon_spec})
    # Phượng Hoàng Chân Hỏa — defensive aura. When the defender owns the
    # buff and an attacker lands a damaging hit, stamp Phượng Hỏa stack(s)
    # on the attacker. Tunables (duration, stack_cap, per_stack_pct,
    # stacks_per_hit, stat_bonus) live in the skill JSON under
    # ``effect_overrides.DebuffPhuongHoa`` and were stashed onto the buff's
    # own override dict at cast time (``_phuong_hoa_emit``). Routed through
    # ``inflict_debuff`` so hard-CC immunity / debuff_immune_pct still gate
    # the proc consistently with other applied debuffs.
    if (
        dmg > 0
        and actor.is_alive()
        and target.has_effect(EffectKey.BUFF_PHUONG_HOANG_CHAN_HOA)
    ):
        ph_meta = EFFECTS.get(EffectKey.DEBUFF_PHUONG_HOA)
        if ph_meta is not None:
            buff_ovr = target.effect_overrides.get(
                EffectKey.BUFF_PHUONG_HOANG_CHAN_HOA.value
            ) or {}
            emit_ovr = dict(buff_ovr.get("_phuong_hoa_emit") or {})
            stacks_per_hit = max(1, int(emit_ovr.pop("stacks_per_hit", 1)))
            inflict_ovr = emit_ovr or None
            from .casting import inflict_debuff
            for _ in range(stacks_per_hit):
                if not actor.is_alive():
                    break
                inflict_debuff(
                    session, EffectKey.DEBUFF_PHUONG_HOA.value, ph_meta,
                    actor, actor=target, overrides=inflict_ovr,
                )

    # Defense-aegis on-hit inflict hook — generic dispatch for any active
    # aegis buff on the target whose ``_aegis.on_hit_inflict`` block declares
    # a debuff key + chance. Covers Tử Lôi Hộ Thân (Sốc Điện 40%),
    # Cửu Thiên Lôi Giáp (Sốc Điện 100%), and any future entrant. Reflect /
    # damage routing stay on the existing apply_reactive_damage path above.
    from .defense_aegis import apply_on_hit_inflicts
    apply_on_hit_inflicts(session, actor, target, dmg)

    # Defense-aegis adaptive elemental resist (Tri Hành Hợp Nhất) — hardens
    # the defender against the element currently striking it, resetting when a
    # different element lands. Needs the hit's element, which the on-hit path
    # carries (take_damage does not), so it lives here rather than in
    # ``Combatant.take_damage``.
    from .defense_aegis import adapt_elem_res
    adapt_elem_res(target, skill_element, dmg)

    # Bắc Minh Băng Phách — bidirectional ice/freeze/MP-drain kit. Self-gates on
    # the per-body config flags (inert for every other build).
    run_bac_minh_procs(session, actor, target)
    run_huyen_minh_procs(session, actor, target)


def apply_reflect(
    session: "CombatSession", attacker: Combatant, defender: Combatant, dmg: int,
    reflect_pct: float | None = None,
) -> None:
    """Reflect a portion of damage dealt to ``defender`` back at ``attacker``.

    When ``defender.reflect_applies_effects`` is True, the defender's own
    on-hit proc flags (freeze_on_skill_chance, slow_on_hit_pct, burn_on_hit_pct,
    bleed_on_hit_pct) also roll against the attacker — the mirror throws
    the defender's build flavor back at them.

    ``reflect_pct`` (optional) overrides ``defender.reflect_pct`` for cases
    where active-effect contributions need to fold into the rate (caller
    supplies the summed total). Falls back to the field when omitted.
    """
    if not attacker.is_alive() or dmg <= 0:
        return
    effective_pct = defender.reflect_pct if reflect_pct is None else reflect_pct
    reflected = max(1, int(dmg * effective_pct))
    attacker.take_damage(reflected)
    # Reflect paints in the defender's element (mirror = their flavor).
    reflect_tag = colorize_damage(f"-{reflected:,} HP", defender.element)
    session.log.append(
        f"    🪞 **{defender.name}** phản đòn → **{attacker.name}** {reflect_tag}"
    )

    # Generic debuff reflect — any effect on the defender whose meta carries
    # ``reflectable=True`` mirrors back to the attacker. Data-driven counterpart
    # to the legacy ``reflect_applies_effects`` block (which mirrors the
    # defender's hardcoded on-hit FLAGS — see below). Fires unconditionally
    # whenever damage reflect lands; any reflectable debuff currently on the
    # defender bounces. Note: ``apply_reactive_damage`` runs BEFORE
    # ``apply_skill_effects`` in the cast pipeline, so debuffs the attacker is
    # about to stamp from THIS skill aren't on the defender yet — they'll
    # bounce on the NEXT damaging hit. Pre-existing reflectable debuffs
    # bounce immediately on every damaging hit until they expire.
    from src.game.engine.effects import EFFECTS
    from src.game.systems.combat.casting import inflict_debuff
    for refl_key in list(defender.effects.keys()):
        refl_meta = EFFECTS.get(refl_key)
        if refl_meta is None or not refl_meta.reflectable:
            continue
        refl_ovr = defender.effect_overrides.get(refl_key)
        session.log.append(
            f"    🪞 **{defender.name}** Phản Hồi **{refl_meta.vi}** → **{attacker.name}**"
        )
        inflict_debuff(
            session, refl_key, refl_meta, attacker,
            actor=defender, overrides=refl_ovr,
        )

    if not defender.reflect_applies_effects:
        return

    # Mirror the defender's on-hit effect flags back at the attacker.
    # Mirror freeze fires harder than the cast — defenders with freeze get a
    # ``_FREEZE_MIRROR_BOOST`` × multiplier on their reflected proc rate.
    freeze_chance = min(1.0, defender.freeze_on_skill_chance * _FREEZE_MIRROR_BOOST)
    if freeze_chance > 0 and session.rng.random() < freeze_chance:
        if attacker.immune_hard_cc or attacker.has_effect("BuffHoangCoThanhVuc"):
            session.log.append(
                f"    🛡️ **{attacker.name}** miễn dịch Mirror Đóng Băng!"
            )
        else:
            dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
            attacker.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
            session.log.append(f"    🧊 Phản Đóng Băng — **{attacker.name}** bị đông cứng!")
    if defender.slow_on_hit_pct > 0 and session.rng.random() < defender.slow_on_hit_pct:
        dur = default_duration(EffectKey.DEBUFF_LAM_CHAM)
        attacker.apply_effect(EffectKey.DEBUFF_LAM_CHAM, dur)
        session.log.append(f"    🐢 Phản Làm Chậm — **{attacker.name}**")
    if defender.burn_on_hit_pct > 0 and session.rng.random() < defender.burn_on_hit_pct:
        dur = default_duration(EffectKey.DEBUFF_THIEU_DOT)
        attacker.apply_effect(EffectKey.DEBUFF_THIEU_DOT, dur)
        _propagate_stack_build(defender, attacker, "burn")
        attacker.add_stack("burn", 1)
        session.log.append(f"    🔥 Phản Thiêu Đốt — **{attacker.name}**")
    if defender.bleed_on_hit_pct > 0 and session.rng.random() < defender.bleed_on_hit_pct:
        dur = default_duration(EffectKey.DEBUFF_CHAY_MAU)
        attacker.apply_effect(EffectKey.DEBUFF_CHAY_MAU, dur)
        _propagate_stack_build(defender, attacker, "bleed")
        attacker.add_stack("bleed", 1)
        session.log.append(f"    🩸 Phản Chảy Máu — **{attacker.name}**")


def apply_thorn(
    session: "CombatSession", attacker: Combatant, defender: Combatant, dmg: int
) -> None:
    """Thorn reflects raw physical damage back; never misses.

    When ``defender.thorn_from_shield`` is True, the reflected amount is
    also paid out of the defender's shield pool first (using it as an
    offense budget). Attackers cannot die from thorn (floor at 1 HP) so
    they can still retaliate — the dedicated thorn build win condition is
    damage-over-time accumulation, not one-shot kills.
    """
    if dmg <= 0 or not attacker.is_alive():
        return
    thorn = max(1, int(dmg * defender.thorn_pct))
    # Optional: deduct thorn from shield as an offense cost
    if defender.thorn_from_shield and defender.shield > 0:
        spent = min(defender.shield, thorn // 2)
        if spent > 0:
            defender.shield -= spent
    # Apply physical reduction from attacker def
    from src.game.engine.damage.physical import apply_physical_defense
    reduced = apply_physical_defense(thorn, "physical", attacker.def_stat)
    final_dmg = max(1, reduced)
    attacker.hp = max(1, attacker.hp - final_dmg)
    thorn_tag = colorize_damage(f"-{final_dmg:,} HP", None)  # physical thorn
    session.log.append(
        f"    🌵 Gai Phản Đòn → **{attacker.name}** {thorn_tag}"
    )
