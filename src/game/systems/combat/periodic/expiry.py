"""Effect-tick + on-expire hooks.

One PERIODIC hook (priority 80) that runs late in the chain so every
duration-bearing effect has already done its turn-of-life work. Three
on-expire branches fire per expired effect:

  1. ``expire_dmg_pct_hp_max`` — one-shot fall/crash damage
     (Cuốn Bay → Ngã Xuống). Direct HP subtraction, no pipeline.
  2. ``on_expire_apply`` — chain-apply a follow-up effect on the same
     holder. Powers self-loops like Thiên Ma Giải Thể
     (BuffThienMa ↔ DebuffThienMaPost).
  3. **Aegis discharge** — generic on-natural-expire emit for any aegis
     whose ``_aegis.discharge`` block declares damage tunables. Reads
     ``_stored_charge`` from the snapshot since ``tick_effects`` already
     popped the live override.

The override snapshot is taken BEFORE ``tick_effects`` runs so each
hook can still read tunables that were stashed at apply time (e.g.
``_discharge_emit`` for Cửu Thiên Lôi Giáp).

Cleansed effects never reach this branch — ``tick_effects`` only
returns keys that ticked their duration to 0.
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS, default_duration

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PERIODIC, name="expiry", priority=80)
def _expiry(ctx: TurnContext) -> None:
    combatant = ctx.actor
    opponent = ctx.target
    session = ctx.session

    # Snapshot per-instance overrides BEFORE tick_effects pops them, so
    # the on-expire hooks below can still read tunables that were stashed
    # at apply time (e.g. ``_discharge_emit`` for Cửu Thiên Lôi Giáp).
    overrides_snapshot = {
        k: dict(v or {}) for k, v in combatant.effect_overrides.items()
    }
    expired = combatant.tick_effects()
    for key in expired:
        meta = EFFECTS.get(key)
        if not meta:
            continue

        if meta.expire_dmg_pct_hp_max > 0 and combatant.hp_current > 0:
            fall_dmg = max(
                1, int(combatant.stats.hp_max * meta.expire_dmg_pct_hp_max)
            )
            element = meta.expire_dmg_element or "physical"
            combatant.hp_current = max(0, combatant.hp_current - fall_dmg)
            ctx.log.append(
                f"  💢 **{combatant.name}** rơi xuống đất — "
                f"−{fall_dmg:,} HP ({element})"
            )

        if meta.on_expire_apply:
            next_key, next_override = meta.on_expire_apply
            next_meta = EFFECTS.get(next_key)
            if next_meta:
                ov = next_override or {}
                dur = int(ov.get("duration", default_duration(next_key)))
                stamp = {k: v for k, v in ov.items() if k != "duration"} or None
                combatant.apply_effect(next_key, dur, overrides=stamp)
                ctx.log.append(
                    f"  {next_meta.emoji} **{combatant.name}** chuyển hóa "
                    f"**{meta.vi}** → **{next_meta.vi}** ({dur}t)"
                )

        # Defense-aegis discharge hook — generic on-natural-expire emit
        # for any aegis whose ``_aegis.discharge`` block declares damage
        # tunables (base / matk_scale / stored_mult / cc). Covers
        # Cửu Thiên Lôi Giáp (Lôi Hồi Quang) and Lôi Thần Khải (Lôi Thần
        # Phán) without per-buff branches.
        from ..defense_aegis import emit_discharge_on_expire
        emit_discharge_on_expire(
            session, combatant, opponent, key, overrides_snapshot,
        )

        # Đồng Quy Vu Tận — HP-race detonate on natural seal expiry. Driven
        # off the same pre-tick snapshot as the aegis discharge so the
        # apply-time hp% survives ``tick_effects`` popping the live override.
        # No-op for any key that isn't the seal / lacks the config block.
        from ..dong_quy import emit_dong_quy_detonate
        emit_dong_quy_detonate(
            session, combatant, opponent, key, overrides_snapshot,
        )
