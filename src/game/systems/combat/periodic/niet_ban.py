"""Niết Bàn Bất Diệt — Nghiệp Hỏa accumulation (PERIODIC).

One PERIODIC hook (priority 27, just after lietdiem@26) driving the Nghiệp Hỏa
self-refinement: each of the holder's periodic ticks, count the fire DoTs the
holder has burning on the opponent (``count_elemental_dots(target, "hoa")``) and
bank them into ``nb_nghiep_progress``. Every 10 micro-stacks rolls one
``nb_nghiep_tier`` (cap 3).

Each new tier monotonically bumps the holder's OUTGOING fire amps —
``element_dmg_bonus["hoa"]`` (read live by ``combat_hit`` for hoa skills) and
``dot_dmg_bonus`` (read live by ``dot.py`` for every DoT tick). The bump is
applied ONCE per tier-up, never recomputed: tiers only accumulate (no decay), so
``base + per_tier × tiers`` stays correct. The +5%-revive-HP-per-tier side is
read separately at revive time in ``revives.py`` (``nb_nghiep_tier`` ×
``nb_nghiep_revive_pct_per_tier``).

Scaling_rules can't drive these two amps: ``dot_dmg_bonus`` is read off the
Combatant FIELD (not the get_combat_modifiers dict), so a rule output there would
be invisible — hence the direct field mutation here.

Inert for every other build: the gate ``nb_nghiep_accumulate`` is False on every
non-Niết-Bàn combatant.
"""
from __future__ import annotations

from src.game.engine.effects import count_elemental_dots

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook

_NGHIEP_TIER_CAP = 3
_NGHIEP_PER_TIER = 10  # micro-stacks (vi tầng) per Nghiệp tier
_NGHIEP_HOA_PER_TIER = 0.08  # +8% hoa dmg / tier → element_dmg_bonus["hoa"]
_NGHIEP_DOT_PER_TIER = 0.12  # +12% DoT dmg / tier → dot_dmg_bonus


@register_hook(phase=TurnPhase.PERIODIC, name="niet_ban", priority=27)
def _niet_ban(ctx: TurnContext) -> None:
    actor = ctx.actor
    if not actor.nb_nghiep_accumulate or not actor.is_alive():
        return
    if actor.nb_nghiep_tier >= _NGHIEP_TIER_CAP:
        return

    ticks = count_elemental_dots(ctx.target, "hoa")
    if ticks <= 0:
        return

    actor.nb_nghiep_progress += ticks
    leveled = False
    while (
        actor.nb_nghiep_progress >= _NGHIEP_PER_TIER
        and actor.nb_nghiep_tier < _NGHIEP_TIER_CAP
    ):
        actor.nb_nghiep_progress -= _NGHIEP_PER_TIER
        actor.nb_nghiep_tier += 1
        # Monotonic stat bump — both fields are read LIVE downstream.
        actor.element_dmg_bonus["hoa"] = (
            actor.element_dmg_bonus.get("hoa", 0.0) + _NGHIEP_HOA_PER_TIER
        )
        actor.dot_dmg_bonus += _NGHIEP_DOT_PER_TIER
        leveled = True

    if actor.nb_nghiep_tier >= _NGHIEP_TIER_CAP:
        actor.nb_nghiep_progress = 0  # capped — stop banking micro-stacks

    if leveled:
        ctx.log.append(
            f"  🜂 **{actor.name}** Nghiệp Hỏa Tích Lũy "
            f"[×{actor.nb_nghiep_tier}/{_NGHIEP_TIER_CAP}] — "
            f"+{int(actor.nb_nghiep_tier * _NGHIEP_HOA_PER_TIER * 100)}% ST Hỏa, "
            f"+{int(actor.nb_nghiep_tier * _NGHIEP_DOT_PER_TIER * 100)}% ST Thiêu Đốt"
        )
