"""Phase-0 regression anchor for the Skill Mastery feature.

Skill Mastery (Phase 3) introduces a per-skill ``power_mult`` that scales
skill damage. Before any of that lands, this module freezes the *current*
combat output of a fixed-seed fight so we can prove the new code is inert
when mastery is absent.

The contract this test guards:

    Once ``skill_mastery`` exists, running the IDENTICAL fixed-seed fight
    with mastery absent (``skill_mastery=None`` / empty, i.e. an implicit
    ``power_mult`` of 1.0) MUST reproduce the exact golden values asserted
    below. Any drift means the mastery code path is not truly inert when a
    combatant has no mastery — a regression.

The golden constants were captured on the clean ``feat/season-2`` tree by
running the fight twice and confirming byte-identical output (final HP of
both combatants, total turns, ``CombatResult`` reason, log length, and a
SHA-256 of the joined log). The fight is a several-turn matchup that ends
in a genuine kill, so the victory / damage-pipeline path — exactly what a
power multiplier would perturb — is what gets pinned.

IMPORTANT: this file references only symbols that exist today. It does NOT
import or mention ``power_mult``, ``skill_mastery``, or any mastery module
— it is purely a "freeze current combat output" snapshot.
"""
from __future__ import annotations

import hashlib
import random

from src.data.registry import registry
from src.game.systems.combat import CombatEndReason, CombatSession
from tests.conftest import make_combatant

# A real, registered low-cost attack skill so the fight exercises the full
# _take_turn → damage pipeline each round (mirrors test_combat_builds.py's
# ``test_run_terminates_with_result`` which uses the same key).
_ATTACK_SKILL = "EnemyKim_T1"

# ── Golden constants — captured on clean feat/season-2 ─────────────────────
# Player (10k HP "large" profile) vs a weaker enemy (1k HP "small" profile),
# both spamming _ATTACK_SKILL, seed=0, max_turns=50. The player wins with
# the enemy dead and these exact survivor values.
#
# Re-pinned to the ORIGINAL Phase-0 baseline. A mid-season rebalance briefly
# buffed EnemyKim_T1 base_dmg 10 -> 200 (which moved this golden to
# 9009/turns5/log16); a later balance pass reverted enemy damage to the
# original baseline, so base_dmg is 10 again and the fixed-seed fight returns
# to its first-captured deterministic state: player kills the 1k "small" enemy
# at turn 23, log length 70, surviving at 8961 HP. This golden guards against
# *unintended* combat drift; the drift here is the intended revert, so
# re-pinning to the original values is correct.
_GOLDEN_PLAYER_HP = 8_961
_GOLDEN_ENEMY_HP = 0
_GOLDEN_TURNS = 23
_GOLDEN_REASON = CombatEndReason.PLAYER_WIN
_GOLDEN_LOG_LEN = 70
_GOLDEN_LOG_SHA256 = (
    "eb8ad87169a2973eaffdbf03d72f8570be4a6f7faf5ff6f4940458e2d38c67f8"
)


def _build_fight() -> tuple[CombatSession, "object", "object"]:
    """Build the fixed-seed snapshot fight. Returns (session, player, enemy)."""
    # mp set high so neither side ever falls back to a basic attack (which
    # would make the snapshot depend on MP-regen tuning rather than skills).
    player = make_combatant(
        "player", profile="large", skill_keys=[_ATTACK_SKILL], mp=9_999, mp_max=9_999
    )
    enemy = make_combatant(
        "enemy", profile="small", skill_keys=[_ATTACK_SKILL], mp=9_999, mp_max=9_999
    )
    session = CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(0),
        max_turns=50,
    )
    return session, player, enemy


def test_fight_damage_byte_identical_without_mastery() -> None:
    """The fixed-seed fight reproduces its frozen golden output exactly.

    This is the Phase-3 anchor: with skill mastery absent, the combat
    pipeline must be byte-for-byte identical to today.
    """
    # Sanity: the skill we snapshot against must actually be registered, so a
    # silent registry change surfaces here rather than as a confusing HP drift.
    assert registry.get_skill(_ATTACK_SKILL) is not None

    session, player, enemy = _build_fight()
    result = session.run()

    log_sha256 = hashlib.sha256("\n".join(session.log).encode("utf-8")).hexdigest()

    assert player.hp == _GOLDEN_PLAYER_HP
    assert enemy.hp == _GOLDEN_ENEMY_HP
    assert result.turns == _GOLDEN_TURNS
    assert result.reason == _GOLDEN_REASON
    assert len(session.log) == _GOLDEN_LOG_LEN
    assert log_sha256 == _GOLDEN_LOG_SHA256
