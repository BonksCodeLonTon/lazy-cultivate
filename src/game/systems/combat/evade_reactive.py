"""Data-driven hooks for on-evade reactive buffs.

A buff is an "evade reactor" if it declares an ``evade_react`` block —
either as a top-level field on its ``EffectMeta`` (buff JSON) or as a
per-instance entry in the holder's ``effect_overrides[buff]._evade_react``.
The engine calls one dispatcher from the on-evade hook in ``cast_skill``;
any buff carrying the capability blocks runs its handlers in a fixed
order (counter → inflict → extend_self → proc_cast).

Capability schema (all sub-blocks optional, mix-and-match):

    "evade_react": {
        "counter":     {"element": str,
                        "base_dmg": int,
                        "matk_scale": float,
                        "log_emoji": str,
                        "log_name":  str},
        "inflict":     {"debuff": str,
                        "chance": float,
                        # any other key (e.g. ``duration``) forwards to
                        # ``inflict_debuff`` as a per-cast override
                        ...},
        "extend_self": {"cap": int},
        "proc_cast":   {"skill": str},
    }

Schema lookup order per buff: per-instance override (``_evade_react``)
wins over meta default (``EffectMeta.evade_react``) so granting skills
can customize tunables. Legacy ``proc_on_holder_evade_cast`` (meta field
or override key) is still honored for buffs not yet migrated.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.data.registry import registry
from src.game.engine.damage.color import colorize_damage
from src.game.engine.effects import EFFECTS
from src.game.constants.effects import EffectKey

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant
    from src.game.systems.combat.session import CombatSession


def _react_block(holder: "Combatant", buff_key: str) -> Optional[dict]:
    """Return the ``evade_react`` config for an active buff (override → meta)."""
    if holder.effects.get(buff_key, 0) <= 0:
        return None
    ovr = holder.effect_overrides.get(buff_key) or {}
    block = ovr.get("_evade_react")
    if isinstance(block, dict):
        return block
    meta = EFFECTS.get(buff_key)
    meta_block = getattr(meta, "evade_react", None) if meta else None
    return meta_block if isinstance(meta_block, dict) else None


def _legacy_proc_cast_key(holder: "Combatant", buff_key: str) -> Optional[str]:
    """Resolve the legacy ``proc_on_holder_evade_cast`` skill key for a buff.

    Override entry wins over meta field. Returns ``None`` when neither is
    set. Kept so unmigrated buffs (e.g. Lưu Quang Huyễn Ảnh) keep working
    without a JSON edit.
    """
    ovr = holder.effect_overrides.get(buff_key) or {}
    legacy = ovr.get("proc_on_holder_evade_cast")
    if legacy:
        return legacy
    meta = EFFECTS.get(buff_key)
    return meta.proc_on_holder_evade_cast if meta else None


def _run_counter(
    session: "CombatSession",
    attacker: "Combatant",
    defender: "Combatant",
    block: dict,
) -> None:
    cfg = block.get("counter")
    if not isinstance(cfg, dict) or not attacker.is_alive():
        return
    # Optional ``chance`` gate (Phi Thiên Lăng Vân Lăng Vân Phản Kích). At 6
    # Phong Vân stacks the counter is guaranteed (chance forced to 1.0).
    # Absent ``chance`` (legacy evade_react buffs) → always fires.
    chance = cfg.get("chance")
    if chance is not None:
        if defender.phong_van_stacks >= 6:
            chance = 1.0
        if float(chance) < 1.0 and session.rng.random() >= float(chance):
            return
    element = str(cfg.get("element", "loi"))
    base = int(cfg.get("base_dmg", 0))
    matk_scale = float(cfg.get("matk_scale", 0.0))
    burst = max(1, base + int(defender.matk * matk_scale))
    shield_before = attacker.shield
    attacker.take_damage(burst)
    absorbed = shield_before - attacker.shield
    tag = colorize_damage(f"-{burst:,} HP", element)
    emoji = cfg.get("log_emoji", "⚡")
    name = cfg.get("log_name", "Phản Kích")
    session.log.append(
        f"    {emoji} **{defender.name}** {name} → {tag}"
        + (f" 🛡️-{absorbed:,}" if absorbed > 0 else "")
    )


def _run_inflict(
    session: "CombatSession",
    attacker: "Combatant",
    defender: "Combatant",
    block: dict,
) -> None:
    cfg = block.get("inflict")
    if not isinstance(cfg, dict) or not attacker.is_alive():
        return
    debuff_key = cfg.get("debuff")
    if not debuff_key:
        return
    meta = EFFECTS.get(debuff_key)
    if meta is None:
        return
    chance = float(cfg.get("chance", 1.0))
    if chance <= 0:
        return
    # Apply attacker/defender debuff modifiers (matches Ma Long's behavior).
    # Local import avoids the ``casting → evade_reactive → casting`` cycle.
    from .casting import _effective_debuff_chance, inflict_debuff
    effective = _effective_debuff_chance(chance, defender, attacker)
    if effective < 1.0 and session.rng.random() >= effective:
        return
    ovr = {k: v for k, v in cfg.items() if k not in ("debuff", "chance")}
    inflict_debuff(
        session, debuff_key, meta, attacker, actor=defender,
        overrides=ovr or None,
    )


def _run_extend_self(
    session: "CombatSession",
    defender: "Combatant",
    buff_key: str,
    block: dict,
) -> None:
    cfg = block.get("extend_self")
    if not isinstance(cfg, dict):
        return
    cap = int(cfg.get("cap", 0))
    if cap <= 0:
        return
    current = defender.effects.get(buff_key, 0)
    if not (0 < current < cap):
        return
    defender.effects[buff_key] = current + 1
    meta = EFFECTS.get(buff_key)
    label = meta.vi if meta else buff_key
    emoji = meta.emoji if meta else "✨"
    session.log.append(
        f"    {emoji} **{defender.name}** {label} kéo dài +1t "
        f"[{current + 1}/{cap}]"
    )


def _run_proc_cast(
    session: "CombatSession",
    attacker: "Combatant",
    defender: "Combatant",
    buff_key: str,
    block: dict,
) -> None:
    # New schema first; legacy field as fallback.
    proc_key: Optional[str] = None
    cfg = block.get("proc_cast")
    if isinstance(cfg, dict):
        proc_key = cfg.get("skill")
    if not proc_key:
        proc_key = _legacy_proc_cast_key(defender, buff_key)
    if not proc_key:
        return
    if proc_key not in defender.skill_keys:
        return
    skill_data = registry.get_skill(proc_key)
    if skill_data is None:
        return
    meta = EFFECTS.get(buff_key)
    label = meta.vi if meta else buff_key
    emoji = meta.emoji if meta else "✨"
    session.log.append(
        f"    {emoji} **{defender.name}** {label} → phản chiêu "
        f"**{skill_data.get('vi', proc_key)}**"
    )
    from .casting import cast_skill
    cast_skill(
        session, defender, attacker,
        proc_key, skill_data, 0,
        _suppress_extras=True,
    )


def apply_evade_reactives(
    session: "CombatSession",
    attacker: "Combatant",
    defender: "Combatant",
) -> None:
    """Run every active aegis-style on-evade capability on the defender.

    Walked once per evade. For each active buff on the defender:
      1. Resolve the ``evade_react`` block (override → meta → legacy proc_cast).
      2. Run capabilities in fixed order: counter → inflict → extend_self →
         proc_cast. Each capability gates itself on its own config dict.
    Legacy ``proc_on_holder_evade_cast`` (meta or override field) still
    fires through the proc_cast capability even without a new schema block.
    """
    if not attacker.is_alive():
        return
    for buff_key in list(defender.effects):
        block = _react_block(defender, buff_key)
        # Always attempt proc_cast — legacy buffs may have no new-schema
        # block but a populated ``proc_on_holder_evade_cast`` field.
        if block is None and not _legacy_proc_cast_key(defender, buff_key):
            continue
        merged_block = block or {}
        _run_counter(session, attacker, defender, merged_block)
        _run_inflict(session, attacker, defender, merged_block)
        _run_extend_self(session, defender, buff_key, merged_block)
        _run_proc_cast(session, attacker, defender, buff_key, merged_block)
        if not attacker.is_alive():
            return
