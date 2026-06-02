"""Once-per-fight start-of-combat auras.

Two effects fire on turn 1, *not* every turn — so they don't go through
the per-turn hook registry. They live here as plain module-level
functions called by ``CombatSession.step()`` at the round-1 branch.

  * :func:`apply_stat_drain_aura` — Thôn Thiên Ma Khí. Holder drains a
    flat percentage of the target's core stats and absorbs the same
    amount. Idempotent via the holder's ``stat_drain_aura_applied``
    flag so repeated harness calls don't compound.
  * :func:`apply_passive_auras` — walks the holder's skill list for
    ``aura: true`` and ``passive_effects: [...]`` entries and stamps the
    declared effects on the configured ``aura_targets``. Idempotent
    because ``Combatant.apply_effect`` already merges by max-magnitude
    per stat.

Adding a new once-per-fight aura is one new function here plus one new
call in ``step()``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, default_duration

if TYPE_CHECKING:
    from src.game.systems.combat.session import CombatSession
    from src.game.systems.combatant import Combatant


def apply_passive_auras(
    session: "CombatSession", holder: "Combatant", opponent: "Combatant",
) -> None:
    """Stamp passive auras onto their targets at combat start.

    Two skill-data shapes are honored:
      * ``aura: true`` — the entire skill is passive; its full ``effects``
        list applies to ``aura_targets`` (default ``["self"]``).
      * ``passive_effects: [...]`` — the skill is otherwise active
        (movement / attack / defense), but the listed effects are
        stamped onto self at fight start. Lets one JSON entry expose
        both an active cast AND a permanent passive aura.

    Each effect honors the per-cast ``effect_overrides[<key>]``
    magnitudes — same routing as a normal cast, just without MP cost
    or cooldown.
    """
    for skill_key in holder.skill_keys:
        data = registry.get_skill(skill_key)
        if not data:
            continue

        if data.get("aura"):
            aura_keys = list(data.get("effects") or [])
            targets_cfg = data.get("aura_targets") or ["self"]
        elif data.get("passive_effects"):
            # Hybrid skill — active cast lives in ``effects``, passive
            # aura lives in ``passive_effects``. Always self-targeted.
            aura_keys = list(data.get("passive_effects") or [])
            targets_cfg = ["self"]
        else:
            continue

        target_map = {"self": holder, "opponent": opponent}
        recipients = [
            target_map[t] for t in targets_cfg if t in target_map
        ]
        if not recipients or not aura_keys:
            continue
        overrides = data.get("effect_overrides") or {}
        for effect_key in aura_keys:
            meta = EFFECTS.get(effect_key)
            if not meta:
                continue
            ov = overrides.get(effect_key) or {}
            dur = int(ov.get("duration", default_duration(effect_key)))
            stamp = {k: v for k, v in ov.items() if k != "duration"} or None
            # ``hp_max_pct`` on a BUFF is a one-shot grow — mirror of the
            # one-shot shrink on debuffs handled in cast_skill. Mutate the
            # recipient's hp_max + hp before stamping so the buff's
            # bigger-pool intent applies immediately.
            merged_sb = dict(meta.stat_bonus)
            if stamp and "stat_bonus" in stamp:
                merged_sb.update(stamp["stat_bonus"])
            grow_pct = float(merged_sb.get("hp_max_pct", 0.0))
            for recipient in recipients:
                if grow_pct > 0 and not recipient.has_effect(effect_key):
                    delta = max(0, int(recipient.hp_max * grow_pct))
                    recipient.hp_max += delta
                    recipient.hp += delta
                recipient.apply_effect(effect_key, dur, overrides=stamp)
                session.log.append(
                    f"  {meta.emoji} **{recipient.name}** chịu hào quang "
                    f"**{meta.vi}** từ *{data.get('vi', skill_key)}*."
                )


def apply_stat_drain_aura(
    session: "CombatSession", holder: "Combatant", target: "Combatant",
) -> None:
    """Thôn Thiên Ma Khí — drain & absorb a fraction of target's core stats."""
    pct = holder.stat_drain_aura_pct
    if pct <= 0 or holder.stat_drain_aura_applied:
        return
    holder.stat_drain_aura_applied = True

    drained_atk = int(target.atk * pct)
    drained_matk = int(target.matk * pct)
    drained_def = int(target.def_stat * pct)
    drained_spd = int(target.spd * pct)

    target.atk = max(0, target.atk - drained_atk)
    target.matk = max(0, target.matk - drained_matk)
    target.def_stat = max(0, target.def_stat - drained_def)
    target.spd = max(1, target.spd - drained_spd)

    holder.atk += drained_atk
    holder.matk += drained_matk
    holder.def_stat += drained_def
    holder.spd += drained_spd

    if drained_atk + drained_matk + drained_def + drained_spd > 0:
        session.log.append(
            f"  🌑 **{holder.name}** Thôn Thiên Ma Khí — hấp thụ "
            f"{int(pct * 100)}% chỉ số đối phương "
            f"(+{drained_atk} ATK / +{drained_matk} MATK / "
            f"+{drained_def} DEF / +{drained_spd} SPD)!"
        )
