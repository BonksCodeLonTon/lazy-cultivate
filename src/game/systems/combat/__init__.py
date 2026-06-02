"""Turn-based combat package.

Module map:

  Orchestrator
    session.py   — CombatSession (turn loop, resolution, victory/defeat)

  Hook infrastructure (added by the Phase 1-4 refactor)
    hooks.py     — HookRegistry, TurnPhase enum, register_hook decorator,
                   run_phase dispatcher
    context.py   — TurnContext passed to every hook

  Per-phase hook packages
    auras/       — 6 PRE_TURN hooks (kinh_hoa, luu_ly, luu_tinh,
                   lieu_nhu, bo_bo, phu_dao)
    periodic/    — 8 PERIODIC hooks (endure, dots, summons,
                   solar_wither, regen, fortify, expiry, luc_duc)
    revives.py   — 3 ON_REVIVE hooks (phoenix, buff, chan_menh_loi_phu)

  Skill mechanics
    casting.py            — cast pipeline, formation barrage, supports
    procs.py              — on-hit procs, reactive damage
    bursts.py             — consume-X burst skill effects
    skill_extras.py       — multi-hit, charge, chain, auto-cast, summons
    dmg_riders.py         — pre-damage final_dmg_bonus rider registry
    cast_consumers.py     — post-cast effect consumers
    stack_stampers.py     — debuff stack-stamp dispatcher
    inflict_interceptors.py — pre-stamp guard chain
    defense_aegis.py      — defensive aegis buff hooks
    evade_reactive.py     — on-evade reactive buff hooks
    formation_prison.py   — Lôi prison formation tax

  Once-per-fight
    start_aura.py — passive auras + stat-drain aura

  Helpers
    builders.py  — player/enemy/world-boss combatant factories
    helpers.py   — stateless helpers shared by the above
    phase.py     — phase-lock invulnerability windows

Adding a new turn-phase hook: drop a new module in auras/ or
periodic/, decorate the handler with ``@register_hook(phase=..., priority=...)``.
No edits to session.py.

External callers should continue to import from ``src.game.systems.combat``;
this module re-exports the public surface.
"""
from .builders import (
    build_enemy_combatant,
    build_player_combatant,
    build_world_boss_combatant,
)
from .context import TurnContext
from .helpers import (
    _build_skill_obj,
    _ON_HIT_PROCS,
    _propagate_dot_bonuses,
    _propagate_stack_build,
    _STACK_BUILD_FIELDS,
    effective_spd,
    spd_extra_turn_pct,
)
from .hooks import (
    Hook,
    HookRegistry,
    TurnPhase,
    register_hook,
    registry as hook_registry,
    run_phase,
)
from .session import (
    CombatAction,
    CombatEndReason,
    CombatResult,
    CombatSession,
)
# Register pre-turn aura hooks and ON_REVIVE hooks. Imported AFTER
# session so the hook decorators can rely on the rest of the combat
# package being live.
from . import auras as _auras  # noqa: F401
from . import periodic as _periodic  # noqa: F401
from . import revives as _revives  # noqa: F401
# Registers the priority-48 ``overheal_release`` PERIODIC hook (Mộc Linh
# Cộng Sinh). Capture half is called from ``session._apply_heal``.
from . import overheal_reservoir as _overheal_reservoir  # noqa: F401

__all__ = [
    "CombatAction",
    "CombatEndReason",
    "CombatResult",
    "CombatSession",
    "Hook",
    "HookRegistry",
    "TurnContext",
    "TurnPhase",
    "build_enemy_combatant",
    "build_player_combatant",
    "build_world_boss_combatant",
    "effective_spd",
    "hook_registry",
    "register_hook",
    "run_phase",
    "spd_extra_turn_pct",
    "_propagate_stack_build",   # consumed by tests/test_combat_builds.py
]
