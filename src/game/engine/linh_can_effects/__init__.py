"""Linh Căn combat effect modules.

Each element's combat effect lives in its own sibling module; this package
exposes orchestration helpers used by the combat engine so callers don't have
to juggle nine imports.

Phase conventions:
  pre_turn (on target)  → try_dodge    — Phong
  pre_turn (on actor)   → try_cleanse  — Quang
  pre_damage (on actor) → get_pen_pct  — Kim
  on_hit (on actor)     → on_hit       — Hoả / Thuỷ / Lôi / Âm
  periodic (on self)    → check_shield — Thổ

Drop a new module here and wire it through the appropriate orchestrator
function to add a new Linh Căn effect — no call-site changes elsewhere.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from . import am, hoa, kim, loi, phong, quang, tho, thuy

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant

__all__ = [
    "am", "hoa", "kim", "loi", "phong", "quang", "tho", "thuy",
    "try_dodge", "try_cleanse", "get_pen_pct", "on_hit", "check_shield",
]


def try_dodge(target: "Combatant", rng: random.Random, log: list[str]) -> bool:
    """Pre-turn: target may dodge the incoming attack entirely (Phong)."""
    return phong.try_dodge(target, rng, log)


def try_cleanse(actor: "Combatant", rng: random.Random, log: list[str]) -> None:
    """Pre-turn: actor may cleanse a debuff and restore MP (Quang)."""
    quang.try_cleanse(actor, rng, log)


def get_pen_pct(actor: "Combatant", rng: random.Random, log: list[str]) -> float:
    """Pre-damage: actor may gain elemental penetration this hit (Kim)."""
    return kim.get_pen_pct(actor, rng, log)


def on_hit(
    actor: "Combatant",
    target: "Combatant",
    dmg: int,
    is_crit: bool,
    rng: random.Random,
    log: list[str],
) -> None:
    """On-hit: run every Linh Căn's post-damage proc in a fixed order."""
    if dmg <= 0:
        return
    hoa.on_hit(actor, target, dmg, rng, log)
    thuy.on_hit(actor, target, dmg, rng, log)
    loi.on_hit(actor, target, dmg, is_crit, rng, log)
    am.on_hit(actor, target, dmg, rng, log)


def check_shield(combatant: "Combatant", log: list[str]) -> None:
    """Periodic: Thổ may activate a one-time HP shield when low."""
    tho.check_shield(combatant, log)
