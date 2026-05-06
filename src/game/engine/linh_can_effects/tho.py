"""Thổ Linh Căn — Hộ Thể proc + shield buff toolkit.

The legacy Hộ Thể was a one-shot HP-percentage shield that ignored the rest
of the Energy Shield economy (cap, regen, dmg-from-shield). Now Thổ players
who actually invest in shield gear get a proportional payoff: every dial the
build exposes — flat, pct, regen, dmg — feeds the same proc and the same
helpers.

Public surface:
  * ``check_shield(combatant, log)``
        Periodic Hộ Thể proc. Once per combat when HP ≤ 35%, grants a
        cap-aware shield + ignites an immediate regen pulse so the recharge
        pump runs from this turn instead of after the recharge_delay window.

  * ``grant_shield(combatant, *, flat, hp_pct, cap_pct, ...)``
        Compose a shield grant from any combination of flat / %-of-hp_max /
        %-of-shield_cap inputs. Skills, scrolls, and constitutions call this
        instead of touching ``combatant.shield`` directly so the cap and
        ``shield_max_pct`` amplification stay consistent.

  * ``tick_shield_regen(combatant, log, label="")``
        One regen pulse (cap × ``shield_regen_pct`` + ``shield_regen_flat``).
        Bypasses the recharge pause — used by burst-regen mechanics.

  * ``shield_dmg_bonus(combatant)``
        Read-only convenience: current shield × ``damage_bonus_from_shield_pct``.
        Mirrors the inline math in ``engine/damage/combat_hit.py`` so callers
        outside the damage pipeline (e.g. UI, previews) can show the same
        number without duplicating constants.

Phase: periodic (called at end of each turn).
"""
from __future__ import annotations

from src.game.systems.combatant import Combatant

# ── Hộ Thể tunables ─────────────────────────────────────────────────────────
_HP_THRESHOLD = 0.35
_BASE_SHIELD_PCT = 0.20
_SHIELD_PER_LEVEL = 0.02
# Per-level boost to the shield_max_pct amplifier applied to the proc's
# raw amount. Lets a Lv9 Thổ player turn a +20% shield_max_pct passive into
# something meaningfully larger on the reactive shield.
_PCT_AMP_PER_LEVEL = 0.05


# ── Internal helpers ────────────────────────────────────────────────────────

def _level_of(combatant: Combatant) -> int:
    """Return the Thổ level on the combatant (defaults to 1)."""
    levels = combatant.linh_can_levels or {}
    return max(1, int(levels.get("tho", 1)))


def _amplify(amount: int, combatant: Combatant, *, level: int) -> int:
    """Scale ``amount`` by the holder's ``shield_max_pct`` plus a Thổ
    per-level bump. Keeps the scaling predictable: a fresh Lv1 with no
    shield gear gets exactly ``amount``; a maxed Lv9 with +50% shield_max_pct
    gets ``amount × (1 + 0.50 + 8 × _PCT_AMP_PER_LEVEL)``.
    """
    bump = combatant.shield_max_pct + max(0, level - 1) * _PCT_AMP_PER_LEVEL
    if bump <= 0:
        return amount
    return int(amount * (1.0 + bump))


# ── Periodic proc — Hộ Thể ──────────────────────────────────────────────────

def check_shield(combatant: Combatant, log: list[str]) -> None:
    """Activate Hộ Thể if HP ≤ 35% and the proc hasn't fired this combat.

    Pipeline:
      1. Compute base shield = ``hp_max × (0.20 + (level-1) × 0.02)``.
      2. Floor: when the holder has invested in flat shield capacity
         (``shield_max_base + shield_max_flat``), the proc never lands less
         than 30% of that flat baseline. Pure shield builds with low hp_max
         still get a meaningful reactive.
      3. Amplify by ``shield_max_pct`` and a Thổ per-level bump so equip
         multipliers compose with the linh_can scaling.
      4. Apply via ``add_shield`` (cap-aware — never overflows shield_cap).
      5. Ignite the regen pump: immediately drop the recharge pause so the
         next periodic phase regen ticks even if the holder was just hit.
    """
    if "tho" not in combatant.linh_can:
        return
    if combatant.ho_the_used or not combatant.is_alive():
        return
    if combatant.hp > int(combatant.hp_max * _HP_THRESHOLD):
        return

    level = _level_of(combatant)
    shield_pct = _BASE_SHIELD_PCT + max(0, level - 1) * _SHIELD_PER_LEVEL
    raw = int(combatant.hp_max * shield_pct)
    flat_floor = int((combatant.shield_max_base + combatant.shield_max_flat) * 0.30)
    raw = max(raw, flat_floor)
    amount = _amplify(raw, combatant, level=level)

    gained = combatant.add_shield(amount)
    combatant.ho_the_used = True
    # Ignite the regen pump so the holder doesn't wait recharge_delay turns
    # for the first tick — Hộ Thể should feel like the build "switching on".
    combatant.shield_recharge_pause = 0

    if gained > 0:
        cap = combatant.shield_cap()
        log.append(
            f"  🪨 **{combatant.name}** [Thổ Lv{level}] Hộ Thể kích hoạt! "
            f"+{gained:,} khiên ({combatant.shield:,}/{cap:,})"
        )
    else:
        # Cap was already saturated — still consume the proc so it doesn't
        # repeatedly try to fire each periodic phase.
        log.append(
            f"  🪨 **{combatant.name}** [Thổ Lv{level}] Hộ Thể đã đầy khiên tối đa."
        )


# ── Public shield-buff helpers ──────────────────────────────────────────────

def grant_shield(
    combatant: Combatant,
    *,
    flat: int = 0,
    hp_pct: float = 0.0,
    cap_pct: float = 0.0,
    amplify: bool = True,
    log: list[str] | None = None,
    label: str = "Hộ Thuẫn",
    emoji: str = "🛡️",
) -> int:
    """Grant a composed shield amount in one call.

    Args:
        combatant: target of the buff (usually the caster of the skill).
        flat:    raw flat shield to add.
        hp_pct:  fraction of ``hp_max`` to add (e.g. 0.15 = 15% HP).
        cap_pct: fraction of current ``shield_cap()`` to add.
        amplify: when True (default) the composed amount is multiplied by
                 ``(1 + shield_max_pct + Thổ per-level bump)`` so a shield
                 build with high pct multipliers gets bigger grants.
        log:     optional log buffer; an entry is appended when ``gained > 0``.
        label:   short Vietnamese tag used in the log line.
        emoji:   leading emoji for the log line.

    Returns:
        Actual shield gained after the cap clamp (``add_shield`` return).
    """
    if not combatant.is_alive():
        return 0

    raw = int(flat)
    if hp_pct > 0:
        raw += int(combatant.hp_max * hp_pct)
    if cap_pct > 0:
        raw += int(combatant.shield_cap() * cap_pct)
    if raw <= 0:
        return 0

    if amplify:
        raw = _amplify(raw, combatant, level=_level_of(combatant))

    gained = combatant.add_shield(raw)
    if gained > 0 and log is not None:
        log.append(
            f"  {emoji} **{combatant.name}** {label}: "
            f"+{gained:,} khiên ({combatant.shield:,}/{combatant.shield_cap():,})"
        )
    return gained


def tick_shield_regen(
    combatant: Combatant,
    log: list[str] | None = None,
    *,
    label: str = "Hộ Thuẫn hồi",
) -> int:
    """Apply one shield regen pulse, bypassing the recharge pause.

    Equivalent to one full periodic-phase regen tick computed as
    ``shield_cap × shield_regen_pct + shield_regen_flat``, but ignoring
    ``shield_recharge_pause``. Returns shield gained.
    """
    if not combatant.is_alive():
        return 0
    pct_part = int(combatant.shield_cap() * combatant.shield_regen_pct)
    amount = pct_part + max(0, combatant.shield_regen_flat)
    if amount <= 0:
        return 0
    gained = combatant.add_shield(amount)
    if gained > 0 and log is not None:
        log.append(
            f"  🪨 **{combatant.name}** {label} +{gained:,} khiên "
            f"({combatant.shield:,}/{combatant.shield_cap():,})"
        )
    return gained


def shield_dmg_bonus(combatant: Combatant) -> int:
    """Flat damage bonus contributed by current shield, mirroring the math
    in ``engine/damage/combat_hit.py``. Returns 0 when the dial isn't set.
    """
    if combatant.shield <= 0 or combatant.damage_bonus_from_shield_pct <= 0:
        return 0
    return int(combatant.shield * combatant.damage_bonus_from_shield_pct)
