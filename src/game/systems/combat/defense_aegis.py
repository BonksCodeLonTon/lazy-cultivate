"""Data-driven hooks for defensive aegis buffs.

A "defense aegis" is a self-buff whose effect_override carries an ``_aegis``
block declaring one or more capability sub-blocks. The engine calls these
four hooks at fixed points; any buff that declares the matching capability
gets the behavior automatically — no per-buff hardcoded branches.

Schema (all sub-blocks optional):

    "_aegis": {
        "shield_grant":   {"flat": int, "matk_scale": float},
        "store_charge":   {"pct": float,
                           "cap_flat": int,
                           "cap_matk_scale": float},
        "adapt_elem_res": {"step": float,   # per-element resist gained per hit
                           "cap":  float},  # resist ceiling for the tracked element

        "on_hit_inflict": {"debuff": str,
                           "chance": float,
                           # any other key (e.g. ``duration``) forwards to
                           # ``inflict_debuff`` as a per-cast override
                           ...},
        "discharge":      {"element": str,
                           "base_dmg": int,
                           "matk_scale": float,
                           "stored_mult": float,    # multiplier on stored charge
                           "stored_label": str,     # log word for the banked value
                           "log_emoji": str,
                           "log_name":  str,
                           "cc": {"key": str,
                                  "chance": float,
                                  "duration": int}}
    }

Stored-charge accumulator lives at the buff override's TOP level under
``_stored_charge`` (config vs. state separation). The discharge hook reads
both ``_aegis.discharge`` (config) and ``_stored_charge`` (mutable value).

The ``adapt_elem_res`` capability tracks which element it is currently
hardening against at the override's TOP level under ``_adapt_element``, and
stores the live resist in the override's ``stat_bonus`` under ``res_<elem>``.
Taking a *different* element's hit resets it: the old ``res_<elem>`` is
dropped and the new element starts fresh at one step.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.game.constants.elements import ALL_ELEMENTS
from src.game.engine.damage.color import colorize_damage
from src.game.engine.effects import EFFECTS

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant
    from src.game.systems.combat.session import CombatSession

# Canonical lowercase element keys (kim/moc/thuy/hoa/tho/loi/phong/quang/am).
# Adaptive elemental resist only triggers on hits carrying one of these.
_ELEMENT_KEYS = frozenset(e.value for e in ALL_ELEMENTS)


def _aegis_block(holder: "Combatant", buff_key: str) -> Optional[dict]:
    """Return the ``_aegis`` config block for an active buff, or ``None``."""
    if holder.effects.get(buff_key, 0) <= 0:
        return None
    ovr = holder.effect_overrides.get(buff_key) or {}
    block = ovr.get("_aegis")
    return block if isinstance(block, dict) else None


def _iter_active_aegis(holder: "Combatant"):
    """Yield ``(buff_key, aegis_block)`` for every active aegis buff."""
    for key in list(holder.effects.keys()):
        block = _aegis_block(holder, key)
        if block is not None:
            yield key, block


def grant_shield_on_cast(
    session: "CombatSession", actor: "Combatant", buff_key: str
) -> None:
    """Read ``_aegis.shield_grant`` off the just-applied buff and add shield.

    Shield amount = ``flat + matk_scale × actor.matk``. Capped by the actor's
    own ``shield_cap()`` — characters without shield-capacity gear get 0.
    Logs only when at least one point of shield landed.
    """
    block = _aegis_block(actor, buff_key)
    if block is None:
        return
    grant_cfg = block.get("shield_grant")
    if not isinstance(grant_cfg, dict):
        return
    flat = int(grant_cfg.get("flat", 0))
    matk_scale = float(grant_cfg.get("matk_scale", 0.0))
    amount = max(0, flat + int(matk_scale * actor.matk))
    # Skill Mastery: scale the granted shield by the holder buff's stamped
    # ``_mastery_mult`` (early-out on 1.0 → absent / enemy / flag-off path).
    # TODO(mastery 3b-followup): scale ``store_charge`` caps.
    mm = float((actor.effect_overrides.get(buff_key) or {}).get("_mastery_mult", 1.0))
    if mm != 1.0:
        amount = max(0, int(amount * mm))
    if amount <= 0:
        return
    gained = actor.add_shield(amount)
    if gained > 0:
        meta = EFFECTS.get(buff_key)
        label = meta.vi if meta is not None else buff_key
        session.log.append(
            f"      🛡️ **{actor.name}** {label} — +{gained:,} Khiên"
        )


def accumulate_stored_charge(holder: "Combatant", pre_absorb_amount: int) -> None:
    """Bank a fraction of incoming damage into every active aegis's stored pool.

    Called from ``Combatant.take_damage`` after the pre-absorb snapshot. The
    ``store_charge`` capability declares ``pct`` (fraction banked) and a cap
    via ``cap_flat`` and/or ``cap_matk_scale`` (max of the two). Each aegis
    accumulates independently into its own buff override's ``_stored_charge``.
    """
    if pre_absorb_amount <= 0:
        return
    for buff_key, block in _iter_active_aegis(holder):
        cfg = block.get("store_charge")
        if not isinstance(cfg, dict):
            continue
        pct = float(cfg.get("pct", 0.0))
        if pct <= 0:
            continue
        cap_flat = int(cfg.get("cap_flat", 0))
        cap_scale = float(cfg.get("cap_matk_scale", 0.0))
        cap = max(cap_flat, int(cap_scale * holder.matk))
        if cap <= 0:
            continue
        ovr = holder.effect_overrides.setdefault(buff_key, {})
        current = int(ovr.get("_stored_charge", 0))
        ovr["_stored_charge"] = min(cap, current + int(pre_absorb_amount * pct))


def adapt_elem_res(
    holder: "Combatant", element: Optional[str], incoming: int
) -> None:
    """Adapt every active aegis's *elemental* resistance to the hit's element.

    The ``adapt_elem_res`` capability declares a ``step`` (resist gained per
    same-element hit) and a ``cap`` (the per-element ceiling). On each
    damaging *elemental* hit the buff override's
    ``stat_bonus["res_<element>"]`` ramps one step toward the cap — the holder
    "learns" to resist the element currently striking it (知行合一: the hit
    teaches, the body adapts). ``get_combat_modifiers`` → ``build_defense_stats``
    reads the grown value on the *next* hit, so the triggering hit lands at the
    current (pre-ramp) resist.

    Taking a hit of a *different* element resets the adaptation: the old
    element's accrued ``res_<elem>`` is dropped and the new element starts
    fresh at one step. The currently-tracked element lives at the override's
    TOP level under ``_adapt_element``. No-op on non-elemental hits or for
    buffs without the capability.

    Mutating ``stat_bonus`` / ``_adapt_element`` is safe: those live on the
    fresh per-combatant override dict that ``_merge_effect_overrides`` creates,
    never the shared registry ``_aegis`` block (read-only here).
    """
    if not element or element not in _ELEMENT_KEYS or incoming <= 0:
        return
    for buff_key, block in _iter_active_aegis(holder):
        cfg = block.get("adapt_elem_res")
        if not isinstance(cfg, dict):
            continue
        step = float(cfg.get("step", 0.0))
        cap = float(cfg.get("cap", 0.0))
        if step <= 0 or cap <= 0:
            continue
        ovr = holder.effect_overrides.setdefault(buff_key, {})
        stats = ovr.setdefault("stat_bonus", {})
        res_key = f"res_{element}"
        if ovr.get("_adapt_element") == element:
            # Same element — keep hardening toward the cap.
            current = float(stats.get(res_key, 0.0))
            if current < cap:
                stats[res_key] = min(cap, current + step)
        else:
            # New element — reset: drop the prior element's accrued resist and
            # begin learning the new one at a single step.
            prev = ovr.get("_adapt_element")
            if prev:
                stats.pop(f"res_{prev}", None)
            ovr["_adapt_element"] = element
            stats[res_key] = min(cap, step)


def apply_on_hit_inflicts(
    session: "CombatSession",
    attacker: "Combatant",
    defender: "Combatant",
    dmg: int,
) -> None:
    """Stamp configured debuffs on the attacker for every active aegis.

    ``on_hit_inflict`` carries ``debuff`` (target key in EFFECTS), ``chance``
    (per-hit roll), and arbitrary debuff overrides (e.g. ``duration``). The
    debuff is inflicted via ``inflict_debuff`` so immunity / resist gating
    applies normally. Damaging hits only — DoT / 0-dmg taps don't trigger.
    """
    if dmg <= 0 or not attacker.is_alive():
        return
    # Lazy import: casting → combatant cycle.
    from src.game.systems.combat.casting import inflict_debuff
    for _, block in _iter_active_aegis(defender):
        cfg = block.get("on_hit_inflict")
        if not isinstance(cfg, dict):
            continue
        debuff_key = cfg.get("debuff")
        if not debuff_key:
            continue
        meta = EFFECTS.get(debuff_key)
        if meta is None:
            continue
        chance = float(cfg.get("chance", 1.0))
        if chance <= 0:
            continue
        if chance < 1.0 and session.rng.random() >= chance:
            continue
        ovr = {k: v for k, v in cfg.items() if k not in ("debuff", "chance")}
        inflict_debuff(
            session, debuff_key, meta, attacker, actor=defender,
            overrides=ovr or None,
        )


def emit_discharge_on_expire(
    session: "CombatSession",
    holder: "Combatant",
    opponent: Optional["Combatant"],
    expired_key: str,
    overrides_snapshot: dict,
) -> None:
    """Fire a discharge volley when an aegis expires naturally.

    Reads ``_aegis.discharge`` and the banked ``_stored_charge`` from the
    pre-tick snapshot (because ``tick_effects`` already popped the live
    override). Damage = ``base + matk_scale·matk + stored × stored_mult``,
    rolled through ``take_damage`` so opponent shield / DR / evasion apply
    normally. An optional ``cc`` block stamps a debuff via ``inflict_debuff``.
    """
    if opponent is None or not opponent.is_alive():
        return
    snap = overrides_snapshot.get(expired_key) or {}
    block = snap.get("_aegis")
    if not isinstance(block, dict):
        return
    cfg = block.get("discharge")
    if not isinstance(cfg, dict):
        return
    element = str(cfg.get("element", "loi"))
    base = int(cfg.get("base_dmg", 0))
    matk_scale = float(cfg.get("matk_scale", 0.0))
    stored_mult = float(cfg.get("stored_mult", 0.0))
    stored = int(snap.get("_stored_charge", 0))
    burst = max(
        1,
        int(base + matk_scale * holder.matk + stored * stored_mult),
    )
    # Skill Mastery: scale the discharge volley by the holder buff's stamped
    # ``_mastery_mult`` (read from the pre-tick snapshot; early-out on 1.0).
    mm = float(snap.get("_mastery_mult", 1.0))
    if mm != 1.0:
        burst = max(1, int(burst * mm))
    opponent.take_damage(burst)
    tag = colorize_damage(f"-{burst:,} HP", element)
    emoji = cfg.get("log_emoji", "⚡")
    name = cfg.get("log_name", "Discharge")
    if stored > 0:
        stored_label = cfg.get("stored_label", "Lôi Điện Trữ")
        suffix = f" ({stored_label} {stored:,})"
    else:
        suffix = ""
    session.log.append(
        f"  {emoji} **{holder.name}** **{name}**{suffix} → "
        f"**{opponent.name}** {tag}"
    )
    cc_cfg = cfg.get("cc")
    if isinstance(cc_cfg, dict) and opponent.is_alive():
        cc_key = cc_cfg.get("key")
        cc_chance = float(cc_cfg.get("chance", 0.0))
        cc_duration = int(cc_cfg.get("duration", 0))
        if cc_key and cc_chance > 0 and cc_duration > 0:
            cc_meta = EFFECTS.get(cc_key)
            if cc_meta is not None and (
                cc_chance >= 1.0 or session.rng.random() < cc_chance
            ):
                from src.game.systems.combat.casting import inflict_debuff
                inflict_debuff(
                    session, cc_key, cc_meta, opponent, actor=holder,
                    overrides={"duration": cc_duration},
                )
