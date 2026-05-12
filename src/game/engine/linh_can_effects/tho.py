"""Thổ Linh Căn — Hộ Thể proc + shield buff toolkit.

Thổ players who invest in shield gear get a proportional payoff: every dial
the build exposes — flat, pct, regen, dmg — feeds the same proc and the same
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
# 50% HP trigger so the reactive engages before the holder is already dying.
_HP_THRESHOLD = 0.50
# Base + level scaling: Lv1 gets 30% HP shield, Lv9 gets 30% + 8×4% = 62%.
_BASE_SHIELD_PCT = 0.30
_SHIELD_PER_LEVEL = 0.04
# Floor share of flat shield investment (shield_max_base + shield_max_flat)
# the proc never undershoots. Set to 0.55 so a pure shield build with low
# hp_max still gets a meaningful reactive that scales with the investment,
# not with HP.
_FLAT_FLOOR_SHARE = 0.55
# Per-level boost to the shield_max_pct amplifier applied to the proc's
# raw amount. Lets a Lv9 Thổ player turn a +20% shield_max_pct passive
# into something meaningfully larger on the reactive shield.
_PCT_AMP_PER_LEVEL = 0.08


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
    """Activate Hộ Thể if HP ≤ 50% and the proc hasn't fired this combat.

    Pipeline:
      1. Compute base shield = ``hp_max × (0.30 + (level-1) × 0.04)``.
      2. Floor: when the holder has invested in flat shield capacity
         (``shield_max_base + shield_max_flat``), the proc never lands less
         than 55% of that flat baseline. Pure shield builds with low hp_max
         still get a meaningful reactive.
      3. Amplify by ``shield_max_pct`` and a Thổ per-level bump so equip
         multipliers compose with the linh_can scaling.
      4. **Overflow the cap.** Hộ Thể is the build's emergency response —
         it's allowed to push ``shield`` above ``shield_cap()`` for one
         burst. Subsequent regen ticks (which use ``add_shield``) cannot
         replenish above cap, so the overcap portion is single-use and
         drains naturally as the holder takes hits. A cap-aware path here
         would trivialise the proc for any shield-gear build (precisely
         the player it should reward).
      5. Chain an immediate regen pulse so the recharge pump engages this
         turn (no wait for the recharge_delay). The pulse uses the normal
         cap-aware path; if shield is already overcap it adds 0, else it
         tops off toward cap.
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
    flat_floor = int(
        (combatant.shield_max_base + combatant.shield_max_flat) * _FLAT_FLOOR_SHARE
    )
    raw = max(raw, flat_floor)
    amount = _amplify(raw, combatant, level=level)

    before = combatant.shield
    combatant.shield += amount
    gained = combatant.shield - before
    combatant.ho_the_used = True
    combatant.shield_recharge_pause = 0

    cap = combatant.shield_cap()
    overcap_tag = ""
    if combatant.shield > cap:
        overcap_tag = f" — quá tải +{combatant.shield - cap:,}"
    log.append(
        f"  🪨 **{combatant.name}** [Thổ Lv{level}] Hộ Thể bùng nổ! "
        f"+{gained:,} khiên ({combatant.shield:,}/{cap:,}){overcap_tag}"
    )
    
    if combatant.shield < cap:
        tick_shield_regen(combatant, log, label="Hộ Thể tiếp khí")


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
