"""Thiên Địa Nhân Hòa — Hòa Khí accumulation + backlash + fusion-cleanse (PERIODIC).

One PERIODIC hook (priority 43, between dai_dia@42 and solar@45) that drives the
whole Hòa Khí spine each turn:

  1. **Accumulate** — Hòa Khí climbs +``harmony_stack_per_turn`` up to
     ``harmony_stack_cap`` and NEVER decays. The L1 ramp (+2% all stats/stack)
     and the L3/L9 thresholds read ``harmony_stacks`` via scaling_rules, so the
     stat side needs no code here — only the counter.
  2. **L6 Hòa Khí Chiến Trường** — once Hòa Khí reaches
     ``harmony_backlash_min_stacks``, radiate universal backlash damage to the
     opponent = ``harmony_stacks × harmony_backlash_pct_per_stack × (atk+matk)``.
     Deterministic (no crit/evasion roll), mirroring dai_dia. Universal element
     ⇒ no elemental resistance applies.
  3. **L9 Tam Tài Hợp Nhất** — at max Hòa Khí, cleanse up to
     ``harmony_l9_cleanse`` cleansable debuffs from self each turn (the Nhân
     talent's purity), reusing the EffectMeta.cleansable filter.

Inert for every other build: the gates are the per-body config flags, all 0 on
every non-harmony combatant.
"""
from __future__ import annotations

from src.game.engine.damage.color import colorize_damage
from src.game.engine.effects import EFFECTS

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="nhan_hoa", priority=43)
def _nhan_hoa(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.is_alive() or actor.harmony_stack_per_turn <= 0:
        return

    # 1. Accumulate Hòa Khí (capped, no decay).
    cap = actor.harmony_stack_cap or 0
    if cap > 0 and actor.harmony_stacks < cap:
        actor.harmony_stacks = min(cap, actor.harmony_stacks + actor.harmony_stack_per_turn)
        ctx.log.append(
            f"  ☯️ **{actor.name}** Hòa Khí [×{actor.harmony_stacks}/{cap}]"
        )

    # 2. L6 Hòa Khí Chiến Trường — backlash aura, scales with the current count.
    opponent = ctx.target
    if (
        actor.harmony_backlash_pct_per_stack > 0
        and actor.harmony_backlash_min_stacks > 0
        and actor.harmony_stacks >= actor.harmony_backlash_min_stacks
        and opponent is not None
        and opponent.is_alive()
    ):
        base = int(
            actor.harmony_stacks
            * actor.harmony_backlash_pct_per_stack
            * (actor.atk + actor.matk)
        )
        if base > 0:
            aura_dmg = max(1, int(base * (1.0 + actor.final_dmg_bonus)))
            opponent.take_damage(aura_dmg)
            ctx.log.append(
                f"  ☯️ **{actor.name}** Hòa Khí Chiến Trường → "
                f"**{opponent.name}** {colorize_damage(f'-{aura_dmg:,} HP', None)}"
            )

    # 3. L9 Tam Tài Hợp Nhất — Nhân talent self-cleanse at max Hòa Khí.
    if (
        actor.harmony_l9_cleanse > 0
        and cap > 0
        and actor.harmony_stacks >= cap
    ):
        cleansable = [
            k for k in list(actor.effects)
            if (m := EFFECTS.get(k)) is not None and m.cleansable
        ]
        removed = 0
        for k in cleansable[: actor.harmony_l9_cleanse]:
            del actor.effects[k]
            actor.effect_overrides.pop(k, None)
            removed += 1
        if removed:
            ctx.log.append(
                f"  ☯️ **{actor.name}** Tam Tài Hợp Nhất — thanh tẩy {removed} "
                f"hiệu ứng bất lợi"
            )
