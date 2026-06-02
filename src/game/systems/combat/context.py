"""Shared turn-phase context.

A ``TurnContext`` is the single argument passed to every hook registered
in :mod:`src.game.systems.combat.hooks`. Hooks read ``actor`` / ``target``
and reach into ``session`` (log, rng, turn counter) without each one
re-deriving those handles.

This keeps the hook signature uniform so the registry's dispatcher can
walk hooks generically — no per-phase argument plumbing in
``session.py``.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.game.systems.combat.session import CombatSession
    from src.game.systems.combatant import Combatant


@dataclass
class TurnContext:
    """Per-hook execution context.

    ``actor`` is the combatant whose phase this is (the one acting / being
    ticked). ``target`` is their opponent. ``session`` is the orchestrator,
    used for log/rng/turn reads — hooks should not mutate session fields
    that aren't conceptually theirs (e.g. don't bump ``session.turn``).

    ``scratch`` is a free-form dict for hooks to leave breadcrumbs the
    dispatcher or later hooks in the same phase can read (e.g.
    ``ctx.scratch["revived"] = True``). It is reset to ``{}`` for each
    phase invocation by ``run_phase``.
    """
    actor: "Combatant"
    target: "Combatant"
    session: "CombatSession"
    scratch: dict[str, Any] = field(default_factory=dict)

    @property
    def log(self) -> list[str]:
        return self.session.log

    @property
    def rng(self) -> random.Random:
        return self.session.rng

    @property
    def turn(self) -> int:
        return self.session.turn
