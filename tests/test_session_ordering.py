"""Characterization tests for CombatSession turn ordering.

These pin the *sequence* of operations in the turn loop — specifically
that the SPD extra-turn, turn-steal, round-1 shield prefill, and
end-of-round periodic + cooldown ticks all fire at the documented
moment.

The upcoming Phase 4 of the refactor breaks ``_process_periodic`` into
many small modules; these tests guarantee callers of ``step()`` continue
to observe the same observable side effects.
"""
from __future__ import annotations

from tests.conftest import SeedRng, make_combatant, make_session


def test_spd_extra_turn_fires_a_second_take_turn():
    """Massive SPD gap + SeedRng(0.0) triggers exactly one bonus action.

    With turn_steal_pct=0 the actor should call ``_take_turn`` twice:
    the normal turn plus the SPD-driven extra. Counts confirm ordering.
    """
    player = make_combatant("p", spd=1_000)
    enemy = make_combatant("e", spd=10)
    session = make_session(player, enemy)
    session.rng = SeedRng(0.0)

    call_log: list[str] = []

    def fake_take_turn(actor, target):  # type: ignore[no-untyped-def]
        call_log.append(actor.key)

    session._take_turn = fake_take_turn  # type: ignore[assignment]

    session._actor_phase(player, enemy, actor_is_player=True)

    # Normal turn + 1 SPD extra. turn_steal_pct=0 so no third hit.
    assert call_log == ["p", "p"]


def test_turn_steal_fires_a_third_take_turn():
    """SPD extra-turn AND turn_steal_pct=1.0 → three actions in one phase."""
    player = make_combatant("p", spd=1_000, turn_steal_pct=1.0)
    enemy = make_combatant("e", spd=10)
    session = make_session(player, enemy)
    session.rng = SeedRng(0.0)

    call_log: list[str] = []

    def fake_take_turn(actor, target):  # type: ignore[no-untyped-def]
        call_log.append(actor.key)

    session._take_turn = fake_take_turn  # type: ignore[assignment]

    session._actor_phase(player, enemy, actor_is_player=True)

    # Normal + SPD extra + turn-steal extra.
    assert call_log == ["p", "p", "p"]


def test_shield_prefills_to_cap_on_turn_one():
    """Combatants with shield_max_base > 0 walk into the fight with full shield."""
    player = make_combatant("p", shield_max_base=400)
    enemy = make_combatant("e", shield_max_base=200)
    session = make_session(player, enemy)
    # No-op the actor phase so we observe only step()'s round-start side
    # effects, not damage-driven shield drain.
    session._take_turn = lambda actor, target: None  # type: ignore[assignment]

    assert player.shield == 0
    assert enemy.shield == 0

    session.step()

    assert player.shield == player.shield_cap() == 400
    assert enemy.shield == enemy.shield_cap() == 200


def test_periodic_phase_runs_after_actor_actions():
    """hp_regen_flat fires during _process_periodic — observable post-step()."""
    player = make_combatant("p", hp=5_000, hp_max=10_000, hp_regen_flat=100)
    enemy = make_combatant("e")
    session = make_session(player, enemy)
    session._take_turn = lambda actor, target: None  # type: ignore[assignment]

    session.step()

    # Regen lands during the periodic phase, which runs AFTER both actor
    # phases per the comment in step(). No damage was dealt (no-op _take_turn)
    # so the observed change is purely the regen.
    assert player.hp == 5_100


def test_cooldowns_tick_once_per_round():
    """tick_cooldowns runs after the full round, decrementing every cooldown by 1."""
    player = make_combatant("p")
    enemy = make_combatant("e")
    player.cooldowns = {"some_skill": 3, "other_skill": 1}
    session = make_session(player, enemy)
    session._take_turn = lambda actor, target: None  # type: ignore[assignment]

    session.step()

    assert player.cooldowns == {"some_skill": 2, "other_skill": 0}
