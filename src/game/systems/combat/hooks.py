"""Turn-phase hook registry.

The orchestrator (``CombatSession``) used to in-line dozens of effect
checks at fixed points in the turn loop (pre-turn auras, on-hit procs,
periodic ticks, revives). Each new mechanic added another ``if
has_effect(...)`` branch, and the order of these branches was only
documented by comments — never enforced.

This module formalizes those interception points as **typed phases**
with a **priority-ordered hook registry**. Each phase is one entry of
:class:`TurnPhase`; each hook is a small dataclass that knows its
priority, an optional predicate, and the function to run.

Hooks are registered with :func:`register_hook` (decorator form) and
dispatched with :func:`run_phase`, which:

  1. Resets ``ctx.scratch`` so phase-local breadcrumbs do not leak.
  2. Iterates hooks for that phase in ascending priority order.
  3. Invokes ``predicate(ctx)``; skips the hook if it returns falsy.
  4. Invokes ``apply(ctx)``; collects any return value into the result
     list for callers that want to inspect what fired.

Phase 1 of the refactor ships this scaffolding with **zero hooks
registered** — behavior is unchanged. Later phases peel logic out of
``session.py`` and re-attach it here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Optional

from src.game.systems.combat.context import TurnContext


class TurnPhase(StrEnum):
    """Ordered interception points in the combat turn lifecycle.

    The values are stable identifiers usable in logs and tests; the
    enum members themselves are what hooks register against.
    """
    PRE_TURN = "pre_turn"       # before the actor picks/casts a skill
    POST_HIT = "post_hit"       # after a damaging skill resolves
    PERIODIC = "periodic"       # end-of-round DoTs, regen, expiries
    ON_EXPIRE = "on_expire"     # an effect just ended this tick
    ON_REVIVE = "on_revive"     # actor would have died this tick


HookFn = Callable[[TurnContext], Optional[Any]]
PredicateFn = Callable[[TurnContext], bool]


@dataclass(frozen=True)
class Hook:
    """One registered handler for a single :class:`TurnPhase`.

    ``priority`` orders hooks within a phase — lower runs first. Ties
    break by registration order (Python's ``list.sort`` is stable).

    ``predicate`` is an optional gate; when ``None`` the hook always
    runs. Use it to encode preconditions ("actor has BuffPhuDao", "ctx
    was not CC-skipped this turn") so the gate is data, not buried
    inside ``apply``.
    """
    name: str
    phase: TurnPhase
    priority: int
    apply: HookFn
    predicate: Optional[PredicateFn] = None


@dataclass
class HookRegistry:
    """Phase → priority-ordered hook list.

    Module-level singleton :data:`registry` is what callers should use;
    the class is exposed for tests that want an isolated registry.
    """
    _hooks: dict[TurnPhase, list[Hook]] = field(
        default_factory=lambda: {phase: [] for phase in TurnPhase}
    )

    def register(self, hook: Hook) -> None:
        bucket = self._hooks[hook.phase]
        bucket.append(hook)
        bucket.sort(key=lambda h: h.priority)

    def hooks_for(self, phase: TurnPhase) -> list[Hook]:
        return list(self._hooks[phase])

    def clear(self) -> None:
        for bucket in self._hooks.values():
            bucket.clear()


registry: HookRegistry = HookRegistry()


def register_hook(
    *,
    phase: TurnPhase,
    name: str,
    priority: int = 100,
    predicate: Optional[PredicateFn] = None,
) -> Callable[[HookFn], HookFn]:
    """Decorator — register ``fn`` as a hook on the module-level registry.

    Example::

        @register_hook(phase=TurnPhase.PRE_TURN, name="luu_tinh_refresh",
                       priority=30)
        def _refresh(ctx: TurnContext) -> None:
            ...
    """
    def deco(fn: HookFn) -> HookFn:
        registry.register(Hook(
            name=name,
            phase=phase,
            priority=priority,
            apply=fn,
            predicate=predicate,
        ))
        return fn
    return deco


def run_phase(phase: TurnPhase, ctx: TurnContext) -> list[tuple[str, Any]]:
    """Invoke every hook registered for ``phase`` in priority order.

    Returns a list of ``(hook_name, return_value)`` tuples for hooks
    whose predicate passed and that returned a non-``None`` value —
    useful for callers that want to inspect which hooks fired (e.g.
    revive phase needs to know if any revive triggered).

    Resets ``ctx.scratch`` before dispatching so prior phases' notes
    don't leak across phase boundaries.
    """
    ctx.scratch = {}
    fired: list[tuple[str, Any]] = []
    for hook in registry.hooks_for(phase):
        if hook.predicate is not None and not hook.predicate(ctx):
            continue
        result = hook.apply(ctx)
        if result is not None:
            fired.append((hook.name, result))
    return fired
