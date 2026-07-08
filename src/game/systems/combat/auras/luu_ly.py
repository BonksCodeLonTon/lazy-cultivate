"""Lưu Ly Tịnh Hỏa — per-fire-DoT cleanse aura.

Extracted from ``CombatSession._process_luu_ly_tinh_hoa``. For every
distinct fire-element DoT currently active on the opponent, roll the
buff's cleanse chance (default 30%). Each successful roll strips one
random cleansable effect off the actor and increments
``luu_ly_tinh_hoa_stacks`` (capped at the holder's stack cap). The
scaling rule on ``BuffLuuLyTinhHoa`` then folds stacks × per_unit into
``crit_res_rating`` via ``get_combat_modifiers``.

Cleansable picking mirrors the Quang ``try_cleanse`` path — uses the
data-driven ``EffectMeta.cleansable`` flag so oddly-keyed debuffs (e.g.
``EffectNgungDong``) are also eligible.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.engine.effects import EFFECTS, EffectKind, effective_stack_cap

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="luu_ly_tinh_hoa", priority=20)
def _process_luu_ly_tinh_hoa(ctx: TurnContext) -> None:
    actor = ctx.actor
    opponent = ctx.target
    if not actor.has_effect(EffectKey.BUFF_LUU_LY_TINH_HOA):
        return
    # Distinct fire DoT kinds on opponent — same filter as
    # ``count_elemental_dots`` but returns the list of keys so each
    # roll below can quote the originating fire DoT in the log.
    fire_dot_keys = [
        k for k in opponent.effects
        if (m := EFFECTS.get(k)) is not None
        and m.dot_element == "hoa"
        and (
            m.dot_pct > 0
            or m.stack_kind
            or m.dot_caster_hp_pct > 0
            or m.dot_caster_matk_scale > 0
        )
    ]
    if not fire_dot_keys:
        return
    # Cleanse chance lives in the buff's stat_bonus so per-skill
    # effect_overrides flow through naturally. Default 0.30.
    buff_ovr = actor.effect_overrides.get(EffectKey.BUFF_LUU_LY_TINH_HOA.value) or {}
    ovr_stats = buff_ovr.get("stat_bonus") or {}
    chance = float(ovr_stats.get(
        "luu_ly_cleanse_chance",
        EFFECTS[EffectKey.BUFF_LUU_LY_TINH_HOA].stat_bonus.get("luu_ly_cleanse_chance", 0.30),
    ))
    # Vô Cấu Lưu Ly L3 (Lưu Ly Cộng Hưởng) — the body resonates with its
    # namesake skill: every cleanse roll is guaranteed, and each successful
    # cleanse below also strips one enemy buff.
    if actor.vc_tinh_hoa_resonance:
        chance = 1.0
    cap = effective_stack_cap(actor, EffectKey.BUFF_LUU_LY_TINH_HOA.value)
    for fire_key in fire_dot_keys:
        if actor.luu_ly_tinh_hoa_stacks >= cap:
            break
        if ctx.rng.random() >= chance:
            continue
        cleansable_keys = [
            k for k in list(actor.effects)
            if (m := EFFECTS.get(k)) is not None and m.cleansable
        ]
        if not cleansable_keys:
            break  # nothing left to cleanse — bail rather than burn rolls
        removed = ctx.rng.choice(cleansable_keys)
        removed_meta = EFFECTS.get(removed)
        del actor.effects[removed]
        actor.effect_overrides.pop(removed, None)
        actor.luu_ly_tinh_hoa_stacks += 1
        fire_meta = EFFECTS.get(fire_key)
        ctx.log.append(
            f"  🔮 **{actor.name}** Lưu Ly Tịnh Hỏa "
            f"({fire_meta.vi if fire_meta else fire_key} → Thanh Tẩy "
            f"*{removed_meta.vi if removed_meta else removed}*) "
            f"[×{actor.luu_ly_tinh_hoa_stacks}/{cap}]"
        )
        # Resonance rider — strip one random enemy buff per cleanse.
        if actor.vc_tinh_hoa_resonance and opponent.is_alive():
            _enemy_buffs = [
                k for k in list(opponent.effects)
                if (bm := EFFECTS.get(k)) is not None
                and bm.kind is EffectKind.BUFF
            ]
            if _enemy_buffs:
                _stripped = ctx.rng.choice(_enemy_buffs)
                _stripped_meta = EFFECTS.get(_stripped)
                opponent.effects.pop(_stripped, None)
                opponent.effect_overrides.pop(_stripped, None)
                ctx.log.append(
                    f"    🔮 Cộng hưởng — tước "
                    f"*{_stripped_meta.vi if _stripped_meta else _stripped}* "
                    f"của **{opponent.name}**"
                )
