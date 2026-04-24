"""Turn-based combat package.

Split out of the former ``combat.py`` monolith into focused modules:
  - session.py  — CombatSession, turn loop, periodic effects, resolution
  - casting.py  — skill cast pipeline, formation barrage, support skills
  - procs.py    — on-hit procs, reactive damage, soul drain / stat steal
  - bursts.py   — consume-X burst skill effects (shield / mana / burn)
  - builders.py — player/enemy/world-boss combatant factories
  - helpers.py  — stateless helpers shared by the above

External callers should continue to import from ``src.game.systems.combat``;
this module re-exports the original public surface verbatim.
"""
from .builders import (
    build_enemy_combatant,
    build_player_combatant,
    build_world_boss_combatant,
)
from .helpers import (
    _build_skill_obj,
    _ON_HIT_PROCS,
    _propagate_dot_bonuses,
    _propagate_stack_build,
    _STACK_BUILD_FIELDS,
    effective_spd,
    spd_extra_turn_pct,
)
from .session import (
    CombatAction,
    CombatEndReason,
    CombatResult,
    CombatSession,
)

__all__ = [
    "CombatAction",
    "CombatEndReason",
    "CombatResult",
    "CombatSession",
    "build_enemy_combatant",
    "build_player_combatant",
    "build_world_boss_combatant",
    "effective_spd",
    "spd_extra_turn_pct",
    "_propagate_stack_build",   # consumed by tests/test_combat_builds.py
]
