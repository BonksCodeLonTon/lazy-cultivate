"""Thôn Thiên Ma Thể — L9 Thôn Thiên Thực Địa devour burst (PRE_TURN).

Every ``ttm_devour_interval`` acted turns the black abyss opens wide:

  1. STEAL every stealable enemy buff (loops ``apply_buff_steal`` — the
     canonical Đoạt Pháp transfer, so already-held duplicates keep the
     stronger-value semantics and unstealable buffs are respected).
  2. One BIG stat steal — ``apply_stat_steal(per_proc_pct=0.10)``, the Sưu
     Hồn Đoạt Phách precedent: reaches the self-anchored 10%-lifetime cap
     in one pulse; world-boss / ``immune_stat_mutation`` immunities apply.
  3. True damage = 50% of the stat points actually stolen this burst,
     through the shared ``_deal_capped_true_dmg`` tail (12% target-max-HP
     clamp) — an already-drained target feeds no extra damage.

Mirrors the tick_cadence PRE_TURN pattern (acted turns only). Inert for
every other build (``ttm_devour_interval`` defaults 0).
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

# Fraction of the stolen stat total converted into the burst's true damage.
_DEVOUR_TRUE_DMG_PCT = 0.5
# The big-steal per-proc magnitude — one pulse = the full lifetime cap.
_DEVOUR_STEAL_PCT = 0.10


@register_hook(phase=TurnPhase.PRE_TURN, name="thon_thien_thuc_dia", priority=39)
def _thon_thien_devour(ctx: TurnContext) -> None:
    actor = ctx.actor
    target = ctx.target
    session = ctx.session
    if actor.ttm_devour_interval <= 0 or not target.is_alive():
        return
    if not actor.tick_cadence("ttm_devour_turn_counter", actor.ttm_devour_interval):
        return
    from ..casting import _deal_capped_true_dmg
    from ..procs import apply_buff_steal, apply_stat_steal

    ctx.log.append(f"  🕳️ **{actor.name}** THÔN THIÊN THỰC ĐỊA — hắc động mở toang!")
    # 1. Swallow every stealable buff (count first — apply_buff_steal pops one
    # per call, so the pre-count bounds the loop even if a steal re-grants).
    stealable = sum(
        1 for k in list(target.effects)
        if (m := EFFECTS.get(k)) is not None and m.stealable
    )
    for _ in range(stealable):
        apply_buff_steal(session, actor, target)
    # 2. The big steal — measure what was actually taken for the damage step.
    before = actor.atk + actor.matk + actor.def_stat
    apply_stat_steal(session, actor, target, per_proc_pct=_DEVOUR_STEAL_PCT)
    stolen = (actor.atk + actor.matk + actor.def_stat) - before
    # 3. Devour damage — capped true dmg from the stolen essence.
    if stolen > 0:
        _deal_capped_true_dmg(
            session, target, int(stolen * _DEVOUR_TRUE_DMG_PCT),
            "Thôn Thiên Thực Địa",
        )
