"""DoT damage + companion drains.

One PERIODIC hook (priority 20) runs three logically-distinct effects
that all key off the actor's active debuffs:

  1. **DoT damage loop** — ``get_periodic_damage`` iterates every active
     DoT meta on the actor; each hit:
     * deals damage via ``take_damage(is_dot=True)``
     * may drain shield (``dot_shield_drain_pct``) and MP
       (``dot_mp_drain_pct``) — Lục Hồn Chú-style curse extras
     * may leech HP+MP back to the opponent's Mộc DoT applier
       (``opponent.dot_leech_pct``)
     * logs with element-colored damage tag and crit marker

  2. **U Minh Quỷ Hỏa** — pure MP-burn DoT. ``DebuffUMinh`` doesn't
     enter ``get_periodic_damage`` (no dot_pct / stack_kind) so it has
     its own MP-drain block here.

  3. **Bất Diệt Hỏa Chủng** — delayed fire-element retaliation. The
     heavy-hit passive set ``bat_diet_retaliate_pending = 2`` when it
     fired; this hook decrements each periodic phase. When it crosses
     1 → 0 the retaliation fires: fire damage to the opponent + stun.
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.constants.effects import EffectKey
from src.game.engine.damage.color import colorize_damage
from src.game.engine.effects import (
    EFFECTS, count_elemental_dots, effective_stack_cap, get_periodic_damage,
)

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


def _dot_siphon_config(holder) -> dict | None:
    """Return the ``dot_siphon`` config of the holder's passive, if equipped.

    Scans the holder's ``skill_keys`` for the first equipped skill carrying a
    ``dot_siphon`` block (Cộng Sinh Luân Hồi, Mộc B5) and returns it. Returns
    ``None`` for enemies / anyone without the passive — the standard
    "passive owner identified at tick time" pattern used by Bất Diệt Hỏa
    Chủng. Cached look-up is cheap (skill_keys is short).
    """
    for sk in holder.skill_keys:
        d = registry.get_skill(sk)
        cfg = (d or {}).get("dot_siphon")
        if isinstance(cfg, dict):
            return cfg
    return None


@register_hook(phase=TurnPhase.PERIODIC, name="dots", priority=20)
def _process_dots(ctx: TurnContext) -> None:
    combatant = ctx.actor
    opponent = ctx.target
    session = ctx.session

    # DoT damage from all active debuffs (poison, burn, bleed, etc.)
    # Pass is_dot=True so the Fortify Aura post-hit brace ignores DoT
    # ticks — only direct skill/aura/reflect hits arm the brace.
    # Cộng Sinh Luân Hồi (Mộc B5) — resolve the applier's siphon passive once
    # per phase. ``opponent`` is the DoT APPLIER in this loop; when it owns the
    # passive, each of its element-matched DoT ticks (a) amps by the distinct
    # same-element DoT count on the target and (b) siphons HP+MP back to the
    # applier. ``None`` (no passive / enemy) makes the whole block inert.
    siphon_cfg = _dot_siphon_config(opponent) if opponent else None
    siphon_elems = set(siphon_cfg.get("elements", [])) if siphon_cfg else set()

    for effect_key, dot_dmg, is_crit in get_periodic_damage(combatant, ctx.rng):
        combatant.take_damage(dot_dmg, is_dot=True)

        # Applier-heal DoTs (Thực Hồn-class) — heal the STRONGEST applier of
        # this DoT for ``dot_applier_heal_pct × tick``. ``dot_dmg`` is the
        # post-boss-cap, post-crit tick, so the boss cap also bounds the heal.
        # The applier is read from the holder's ``dot_bonus_sources`` (the same
        # map the caster-scaling tick uses) — strongest by recorded matk, the
        # one whose stats produced the tick. Default 0.0 → no-op (no heal, no
        # extra RNG) for every other DoT.
        _heal_meta = EFFECTS.get(effect_key)
        if _heal_meta is not None and _heal_meta.dot_applier_heal_pct > 0:
            _applier_key = max(
                combatant.dot_bonus_sources,
                key=lambda k: combatant.dot_bonus_sources[k].get("caster_matk", 0),
                default=None,
            )
            _applier = None
            if _applier_key == session.player.key:
                _applier = session.player
            elif _applier_key == session.enemy.key:
                _applier = session.enemy
            if _applier is not None and _applier is not combatant and _applier.is_alive():
                _heal = max(1, int(dot_dmg * _heal_meta.dot_applier_heal_pct))
                _healed = session._apply_heal(_applier, _heal)
                if _healed > 0:
                    ctx.log.append(
                        f"    🩻 **{_applier.name}** Thực Hồn hấp thu "
                        f"+{_healed:,} HP ({int(_heal_meta.dot_applier_heal_pct * 100)}% ST hồn phách)"
                    )

        # Cộng Sinh Luân Hồi (Mộc B5) — same-element DoT amp + HP/MP siphon.
        # Only the applier's passive-matched elements qualify. The amp
        # (``bonus_dot_pct_per_distinct_dot × distinct same-element DoTs``,
        # capped) is dealt as extra DoT damage on top of the base tick so the
        # base tick stays byte-identical for non-passive cases; the applier
        # then siphons HP+MP from the FULL (base + bonus) tick. Skipped when
        # the applier is dead.
        if (
            siphon_cfg
            and opponent
            and opponent.is_alive()
            and (_b5_meta := EFFECTS.get(effect_key)) is not None
            and _b5_meta.dot_element in siphon_elems
        ):
            _elem = _b5_meta.dot_element
            _distinct = count_elemental_dots(combatant, _elem)
            _per = float(siphon_cfg.get("bonus_dot_pct_per_distinct_dot", 0.0))
            _max = float(siphon_cfg.get("max_bonus_dot_pct", 0.0))
            _amp = _per * _distinct
            if _max > 0:
                _amp = min(_amp, _max)
            _bonus = int(dot_dmg * _amp) if _amp > 0 else 0
            if _bonus > 0:
                combatant.take_damage(_bonus, is_dot=True)
                _btag = colorize_damage(f"-{_bonus:,} HP", _elem)
                ctx.log.append(
                    f"    🌿 **{opponent.name}** Cộng Sinh Luân Hồi khuếch đại "
                    f"({_distinct} loại {_elem.upper()} DoT, +{int(_amp * 100)}%) "
                    f"→ **{combatant.name}** {_btag}"
                )
            _full = dot_dmg + _bonus
            _heal_pct = float(siphon_cfg.get("heal_pct_of_dot_dmg", 0.0))
            _mp_pct = float(siphon_cfg.get("mp_pct_of_dot_dmg", 0.0))
            if _heal_pct > 0:
                _healed = session._apply_heal(opponent, max(1, int(_full * _heal_pct)))
            else:
                _healed = 0
            _mp_gain = 0
            if _mp_pct > 0:
                _mp_gain = min(
                    opponent.mp_max - opponent.mp, max(1, int(_full * _mp_pct))
                )
                if _mp_gain > 0:
                    opponent.mp += _mp_gain
            if _healed > 0 or _mp_gain > 0:
                ctx.log.append(
                    f"    🌿 **{opponent.name}** Cộng Sinh Luân Hồi hấp thu "
                    f"+{_healed:,} HP / +{_mp_gain:,} MP ({_elem.upper()} DoT)"
                )

        # Curse-class extras: a DoT meta may also drain shield and MP each
        # tick (Lục Hồn Chú-style). Skipped when the combatant has no shield
        # or MP to drain so the log stays clean.
        tick_meta = EFFECTS.get(effect_key)
        if tick_meta is not None:
            if tick_meta.dot_shield_drain_pct > 0 and combatant.shield > 0:
                shield_loss = min(
                    combatant.shield,
                    max(1, int(dot_dmg * tick_meta.dot_shield_drain_pct)),
                )
                combatant.shield -= shield_loss
                ctx.log.append(
                    f"    🛡️ **{combatant.name}** mất {shield_loss:,} khiên do {tick_meta.vi}"
                )
            if tick_meta.dot_mp_drain_pct > 0 and combatant.mp > 0:
                mp_loss = min(
                    combatant.mp,
                    max(1, int(combatant.mp_max * tick_meta.dot_mp_drain_pct)),
                )
                combatant.mp -= mp_loss
                ctx.log.append(
                    f"    💙 **{combatant.name}** -{mp_loss:,} MP do {tick_meta.vi}"
                )

        # Moc build: leech a fraction of DoT damage as HP + MP to the
        # applier. Healing applies silently — the per-tick log line was
        # removed so the combat feed isn't confused with the direct-hit
        # ``life_steal_pct`` mechanic (which intentionally ignores DoT).
        if opponent and opponent.dot_leech_pct > 0 and opponent.is_alive():
            leech = max(1, int(dot_dmg * opponent.dot_leech_pct))
            session._apply_heal(opponent, leech)
            mp_gain = min(opponent.mp_max - opponent.mp, leech // 2)
            if mp_gain > 0:
                opponent.mp += mp_gain

        meta = EFFECTS.get(effect_key)
        emoji = meta.emoji if meta else "💢"
        name = meta.vi if meta else effect_key
        crit_tag = " 💥BẠO!" if is_crit else ""
        stack_tag = (
            f" [×{combatant.burn_stacks}]"
            if effect_key == EffectKey.DEBUFF_THIEU_DOT and combatant.burn_stacks > 0
            else ""
        )
        dot_tag = colorize_damage(
            f"-{dot_dmg:,} HP", meta.dot_element if meta else None,
        )
        ctx.log.append(
            f"  {emoji} **{combatant.name}** bị {name}{stack_tag} {dot_tag}{crit_tag}"
        )

    # U Minh Quỷ Hỏa — pure MP-burn DoT. While DebuffUMinh is active,
    # drain ``u_minh_stacks × u_minh_per_stack_mp_pct × mp_max`` MP each
    # turn. No HP damage; runs alongside the DoT loop because the meta
    # doesn't enter ``get_periodic_damage`` (no dot_pct / stack_kind).
    if (
        combatant.has_effect(EffectKey.DEBUFF_U_MINH)
        and combatant.u_minh_stacks > 0
        and combatant.mp > 0
    ):
        u_meta = EFFECTS.get(EffectKey.DEBUFF_U_MINH)
        drain_pct = combatant.u_minh_stacks * combatant.u_minh_per_stack_mp_pct
        mp_loss = min(combatant.mp, max(1, int(combatant.mp_max * drain_pct)))
        combatant.mp -= mp_loss
        ctx.log.append(
            f"    👻 **{combatant.name}** -{mp_loss:,} MP do "
            f"**{u_meta.vi if u_meta else 'U Minh'}** "
            f"[×{combatant.u_minh_stacks}/"
            f"{effective_stack_cap(combatant, EffectKey.DEBUFF_U_MINH.value)}]"
        )

    # Bất Diệt Hỏa Chủng — delayed retaliation. The heavy-hit passive set
    # ``bat_diet_retaliate_pending = 2`` when it fired; each periodic phase
    # decrements by 1. When it transitions from 1 → 0 the retaliation fires:
    # deal ``retaliate_dmg_pct_max_hp × hp_max`` fire damage to the opponent
    # and stun them for ``retaliate_stun_turns``. Magnitudes are read from
    # the same passive's skill data on the holder so JSON drives the tuning.
    if combatant.bat_diet_retaliate_pending > 0:
        combatant.bat_diet_retaliate_pending -= 1
        if combatant.bat_diet_retaliate_pending == 0 and opponent and opponent.is_alive():
            passive_data = None
            for sk in combatant.skill_keys:
                d = registry.get_skill(sk)
                if d and float(d.get("proc_on_heavy_hit_pct") or 0.0) > 0:
                    passive_data = d
                    break
            if passive_data is not None:
                dmg_pct = float(passive_data.get("retaliate_dmg_pct_max_hp", 0.15))
                stun_turns = int(passive_data.get("retaliate_stun_turns", 1))
                burst = max(1, int(combatant.hp_max * dmg_pct))
                tag = colorize_damage(f"-{burst:,} HP", "hoa")
                opponent.take_damage(burst)
                ctx.log.append(
                    f"  🔥 **{combatant.name}** bùng nổ **Bất Diệt Hỏa Chủng** "
                    f"→ **{opponent.name}** {tag} ({int(dmg_pct * 100)}% HP tối đa)"
                )
                # Stun the opponent — routed through inflict_debuff so
                # immune_hard_cc / effect_resist gates apply normally.
                # Duration is ``stun_turns + 1``: the opponent's own
                # periodic phase runs RIGHT AFTER this hook (same round),
                # ticking the just-applied stun once. The +1 ensures the
                # opponent still skips ``stun_turns`` actual turns.
                if stun_turns > 0 and opponent.is_alive():
                    stun_meta = EFFECTS.get(EffectKey.CC_STUN.value)
                    if stun_meta is not None:
                        from ..casting import inflict_debuff
                        inflict_debuff(
                            session, EffectKey.CC_STUN.value, stun_meta,
                            opponent, actor=combatant,
                            overrides={"duration": stun_turns + 1},
                        )
