"""Phase-Lock invulnerability mode (e.g. Chung Yên — Thập Nhật Chung Yên).

A boss carrying ``phase_lock_config`` enters an invulnerability phase the
first time its HP drops below the configured threshold:

  • heals to full HP
  • final_dmg_reduce → MAX_FINAL_DMG_REDUCE
  • every elemental resistance → MAX_ELEMENTAL_RES
  • shield = hp_max (shield_cap is widened so absorption isn't truncated)
  • boss skips its own turns while the phase is active

Each round, the bonuses linearly decay 1/duration of their starting delta —
turn 1 caps everything at max, turn ``duration`` returns to the original
profile. If the boss is still alive when the timer expires, the player dies.

Pure helpers — sequencing/logging belongs to ``CombatSession``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import MAX_ELEMENTAL_RES, MAX_FINAL_DMG_REDUCE

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


# Every element a player skill might roll against — phase fills any missing
# entry so a non-elemental skill never sneaks past untouched. Kept here
# instead of importing constants/elements to avoid a circular dependency.
_ALL_ELEMENTS: tuple[str, ...] = (
    "kim", "moc", "thuy", "hoa", "tho",
    "loi", "phong", "quang", "am",
)


def is_phase_lock_active(combatant: "Combatant") -> bool:
    """True while the combatant is inside its invulnerability phase.

    Used by the turn loop to skip the boss's action.
    """
    return bool(combatant.phase_lock_active)


def try_trigger(combatant: "Combatant", log: list[str]) -> bool:
    """Activate the phase if HP just crossed the threshold.

    No-op when the combatant has no ``phase_lock_config``, has already
    triggered once, is at/below 0 HP, or is still above the configured HP
    fraction. Returns True when the phase actually activated this call so
    the caller can decide whether to short-circuit follow-up logic.

    Lethal damage at 0 HP routes to the phoenix-revive path (or plain
    death) — the phase only fires while the boss is still drawing breath.
    """
    cfg = combatant.phase_lock_config
    if not cfg or combatant.phase_lock_triggered:
        return False

    if combatant.hp <= 0:
        return False

    threshold = float(cfg.get("trigger_hp_pct", 0.5))
    if combatant.hp > combatant.hp_max * threshold:
        return False

    duration = max(1, int(cfg.get("duration", 10)))

    combatant.phase_lock_triggered = True
    combatant.phase_lock_active = True
    combatant.phase_lock_remaining = duration

    # Snapshot the pre-phase profile so a ``restore`` after expiry / abort
    # rebuilds the fight's pre-trigger state instead of leaving the boss
    # permanently buffed.
    combatant.phase_lock_orig_dr = combatant.final_dmg_reduce
    combatant.phase_lock_orig_res = dict(combatant.resistances)
    combatant.phase_lock_orig_shield_base = combatant.shield_max_base

    # Heal to full and slap on the shield bumper.
    combatant.hp = combatant.hp_max
    combatant.shield_max_base = max(combatant.shield_max_base, combatant.hp_max)
    combatant.shield = combatant.hp_max

    # Bonuses ramp from MAX down to original over ``duration`` ticks.
    target_dr = MAX_FINAL_DMG_REDUCE
    target_res = MAX_ELEMENTAL_RES
    combatant.final_dmg_reduce = target_dr
    for elem in _ALL_ELEMENTS:
        combatant.resistances[elem] = target_res

    combatant.phase_lock_dr_step = max(
        0.0, (target_dr - combatant.phase_lock_orig_dr) / duration,
    )
    # Single per-element step — every elemental res starts at MAX and decays
    # toward ``MAX``-(``orig+step``)*N, which collapses to a uniform decay rate
    # since all elements were forced to the same MAX.
    combatant.phase_lock_res_step = max(0.0, target_res / duration)

    name = cfg.get("phase_name_vi", "Phase Lock")
    emoji = cfg.get("phase_emoji", "✨")
    log.append(
        f"\n{emoji} **{combatant.name}** kích hoạt **{name}** — "
        f"hồi phục toàn bộ HP, miễn nhiễm sát thương, khiên = {combatant.hp_max:,} "
        f"(người chơi có {duration} lượt để hạ gục, nếu không sẽ chết!)"
    )
    return True


def tick(combatant: "Combatant", opponent: "Combatant", log: list[str]) -> bool:
    """Advance one round of phase decay; return True if the phase just
    expired with the boss still alive (caller converts to player defeat).

    Decrements the timer, decays DR / res by their per-turn step (clamped at
    the original snapshot), and short-circuits with a False if the phase
    isn't active. When the timer hits zero, the boss's pre-phase profile is
    restored, the opponent (the player) is dropped to 0 HP so the existing
    death-check pipeline handles defeat resolution, and the function
    returns True.
    """
    if not combatant.phase_lock_active:
        return False

    combatant.phase_lock_remaining -= 1

    # Decay each tunable stat by one fixed step, clamped at the original.
    combatant.final_dmg_reduce = max(
        combatant.phase_lock_orig_dr,
        combatant.final_dmg_reduce - combatant.phase_lock_dr_step,
    )
    for elem in list(combatant.resistances.keys()):
        original = combatant.phase_lock_orig_res.get(elem, 0.0)
        combatant.resistances[elem] = max(
            original, combatant.resistances[elem] - combatant.phase_lock_res_step,
        )

    cfg = combatant.phase_lock_config or {}
    name = cfg.get("phase_name_vi", "Phase Lock")
    emoji = cfg.get("phase_emoji", "✨")

    if combatant.phase_lock_remaining > 0:
        log.append(
            f"  {emoji} **{combatant.name}** — **{name}** còn "
            f"{combatant.phase_lock_remaining} lượt "
            f"(Giảm ST: {combatant.final_dmg_reduce * 100:.0f}%, "
            f"Kháng: {max(combatant.resistances.values()) * 100:.0f}%)"
        )
        return False

    # Timer expired — restore profile and finish the player.
    _restore(combatant)
    log.append(
        f"\n💀 **{combatant.name}** — **{name}** hoàn thành: "
        f"người chơi không kịp hạ gục, **{opponent.name}** bị nuốt chửng!"
    )
    opponent.hp = 0
    return True


def _restore(combatant: "Combatant") -> None:
    """Reset phase state and roll back to the pre-trigger snapshot."""
    combatant.phase_lock_active = False
    combatant.phase_lock_remaining = 0
    combatant.final_dmg_reduce = combatant.phase_lock_orig_dr
    combatant.resistances = dict(combatant.phase_lock_orig_res)
    combatant.shield_max_base = combatant.phase_lock_orig_shield_base
    combatant.shield = min(combatant.shield, combatant.shield_cap())
