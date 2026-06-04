"""Phase-0 regression anchor for the Constitution Process feature.

The Constitution Process (an upcoming, flag-gated per-constitution
progression layer on top of the existing Thể Chất system) will add an
optional ``process_levels`` argument to
``cultivation.compute_constitution_bonuses(...)`` plus a settings flag
``constitution_process_enabled`` (default ``False``). Before any of that
lands, this module freezes the *current* combat output of a fixed-seed
fight whose player is built through the REAL stat pipeline from a
``Character`` carrying a registered constitution.

The contract this test guards:

    Once Constitution Process exists, resolving a constitution with the
    flag OFF / ``process_levels=None`` MUST yield EXACTLY its current flat
    ``stat_bonuses`` — i.e. byte-identical combat. Running the IDENTICAL
    fixed-seed fight below with the feature inert MUST reproduce the exact
    golden values asserted here. Any drift means the new code path is not
    truly inert when a constitution has no process levels — a regression.

The golden constants were captured on the clean ``feat/season-2`` tree by
running the fight twice and confirming byte-identical output (final HP of
both combatants, total turns, ``CombatResult`` reason, log length, and a
SHA-256 of the joined log). The player is a Kim crit/bleed body (a SYNTHETIC
``hp_pct`` / ``crit_rating`` / ``bleed_on_hit_pct`` body injected into the
registry — see ``_SYNTH_GUARD_DATA`` — with the same footprint the original
``ConstitutionKimCotThe`` carried, so the goldens are unchanged)
whose bonuses are baked into the combatant via the production path
(``character_stats.compute_combat_stats`` → ``compute_constitution_bonuses``
→ ``build_player_combatant``). The fight ends in a genuine kill, so the
victory / damage-pipeline path — exactly what a process-level multiplier
would perturb — is what gets pinned.

IMPORTANT: this file references only symbols that exist today. It does NOT
import or mention ``process_levels``, ``constitution_process_enabled``, or
any ``constitution_process`` module — it is purely a "freeze current combat
output" snapshot plus a sanity check that the constitution path is exercised.
"""
from __future__ import annotations

import hashlib
import random

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.character_stats import compute_combat_stats
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.cultivation import compute_constitution_bonuses

# A SYNTHETIC, content-independent Kim crit/bleed body with a clear, non-empty
# stat_bonuses footprint: hp_pct 0.03, crit_rating 60, bleed_on_hit_pct 0.04.
# The v12 roster rebuild emptied the real roster, so this guard injects its own
# body into the registry (autouse ``monkeypatch`` fixture below) rather than
# depending on shipped content. The footprint is deliberately identical to the
# old ``ConstitutionKimCotThe`` this guard used to read, so the frozen golden
# combat output below stays byte-for-byte unchanged. NO process block — this
# body must stay process-free so the flat golden never moves.
_CONSTITUTION = "SynthGuardBody"

_SYNTH_GUARD_DATA = {
    "key": _CONSTITUTION,
    "vi": "Thể Bảo Vệ Thử Nghiệm",
    "en": "Synthetic Guard Body",
    "rarity": "common",
    "element": "kim",
    "roll_weight": 0,
    "stat_bonuses": {
        "hp_pct": 0.03,
        "crit_rating": 60,
        "bleed_on_hit_pct": 0.04,
    },
    "special_requirements": None,
}


@pytest.fixture(autouse=True)
def _inject_synthetic_body(monkeypatch):
    """Register the synthetic guard body for each test, leaving the real
    (near-empty) roster untouched."""
    monkeypatch.setitem(registry.constitutions, _CONSTITUTION, _SYNTH_GUARD_DATA)

# A real, registered low-cost attack skill so the fight exercises the full
# _take_turn → damage pipeline each round (same key as the skill-mastery guard).
_ATTACK_SKILL = "EnemyKim_T1"

# A simple registered enemy (realm-03 Kim tinh-anh). build_enemy_combatant
# scales it against the player's realm total.
_ENEMY_KEY = "TinhKimTho"

# Realm shape for the snapshot player. body_realm 5 + active_axis "body"
# keeps the constitution in its (always-active) first standard slot and gives
# a stable realm total for enemy scaling.
_BODY_REALM = 5
_QI_REALM = 5
_FORMATION_REALM = 5
_PLAYER_REALM_TOTAL = _BODY_REALM + _QI_REALM + _FORMATION_REALM  # 15

# ── Golden constants — captured on clean feat/season-2 ─────────────────────
# Kim-body player (built through the real constitution pipeline) vs a
# realm-scaled TinhKimTho, both spamming _ATTACK_SKILL, seed=0, max_turns=50.
# The player wins with the enemy dead and these exact survivor values.
# Captured by running the fight twice in-process and confirming the output is
# byte-identical (see _build_fight + the freeze-by-running-twice assertion).
_GOLDEN_PLAYER_HP = 35_003
_GOLDEN_ENEMY_HP = 0
_GOLDEN_TURNS = 18
_GOLDEN_REASON = CombatEndReason.PLAYER_WIN
_GOLDEN_LOG_LEN = 118
_GOLDEN_LOG_SHA256 = (
    "1256fa069ea7f06ae850cb59f69bd4c93e3604fe0ee31d6f02541bdae21ad6ba"
)


def _make_char(constitution_type: str) -> Character:
    """A fixed Kim-body Character; constitution_type is the only knob."""
    return Character(
        player_id=1,
        discord_id=1,
        name="KimTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=_QI_REALM, qi_level=1,
        formation_realm=_FORMATION_REALM, formation_level=1,
        active_axis="body",
        constitution_type=constitution_type,
        linh_can=["kim"],
        linh_can_levels={"kim": 5},
        stats=CharacterStats(),
    )


def _build_fight() -> tuple[CombatSession, "object", "object"]:
    """Build the fixed-seed snapshot fight. Returns (session, player, enemy).

    The player is built through the REAL stat pipeline so the constitution's
    flat stat_bonuses are merged exactly the way production does it. MP is
    pinned high on both sides so neither falls back to a basic attack (which
    would make the snapshot depend on MP-regen tuning rather than skills).
    """
    char = _make_char(_CONSTITUTION)
    player = build_player_combatant(char, player_skill_keys=[_ATTACK_SKILL])
    player.mp = player.mp_max = 9_999

    enemy = build_enemy_combatant(_ENEMY_KEY, player_realm_total=_PLAYER_REALM_TOTAL)
    assert enemy is not None, f"enemy {_ENEMY_KEY!r} must be registered"
    enemy.mp = enemy.mp_max = 9_999

    session = CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(0),
        max_turns=50,
    )
    return session, player, enemy


def _run_snapshot() -> tuple[int, int, int, "CombatEndReason", int, str]:
    """Run one snapshot fight and return its observable golden tuple."""
    session, player, enemy = _build_fight()
    result = session.run()
    log_sha256 = hashlib.sha256("\n".join(session.log).encode("utf-8")).hexdigest()
    return (
        player.hp,
        enemy.hp,
        result.turns,
        result.reason,
        len(session.log),
        log_sha256,
    )


def test_fight_byte_identical_with_inert_constitution_process() -> None:
    """The fixed-seed constitution fight reproduces its frozen golden output.

    This is the Phase-0 anchor: with Constitution Process absent, the
    combat pipeline for a constitution-bearing player must be byte-for-byte
    identical to today.
    """
    # Sanity: the registry entries we snapshot against must exist, so a
    # silent registry change surfaces here rather than as a confusing drift.
    assert registry.get_constitution(_CONSTITUTION) is not None
    assert registry.get_skill(_ATTACK_SKILL) is not None
    assert registry.get_enemy(_ENEMY_KEY) is not None

    # Freeze-by-running-twice: confirm the fight is reproducible in-process
    # before comparing against the pinned golden tuple.
    first = _run_snapshot()
    second = _run_snapshot()
    assert first == second, f"fight is non-deterministic: {first!r} != {second!r}"

    player_hp, enemy_hp, turns, reason, log_len, log_sha256 = first
    assert player_hp == _GOLDEN_PLAYER_HP
    assert enemy_hp == _GOLDEN_ENEMY_HP
    assert turns == _GOLDEN_TURNS
    assert reason == _GOLDEN_REASON
    assert log_len == _GOLDEN_LOG_LEN
    assert log_sha256 == _GOLDEN_LOG_SHA256


def test_constitution_contributes_nonzero_bonuses_to_combatant() -> None:
    """Load-bearing sanity: the guard fight is genuinely a constitution fight.

    If a future change silently drops constitution bonuses on the real build
    path, this fails loudly — proving the byte-identical guard above is
    exercising the constitution path, not a constitution-less fight.
    """
    # The merged bonus dict the production pipeline feeds into the player.
    merged = compute_constitution_bonuses(_CONSTITUTION, "body", _BODY_REALM)
    assert merged, "constitution must contribute a non-empty stat_bonuses dict"
    assert merged.get("hp_pct", 0) > 0
    assert merged.get("crit_rating", 0) > 0
    assert merged.get("bleed_on_hit_pct", 0) > 0

    # End-to-end: build WITH and WITHOUT the constitution through the same
    # real path and confirm the bonuses actually move the built combatant.
    with_const = build_player_combatant(
        _make_char(_CONSTITUTION), player_skill_keys=[_ATTACK_SKILL]
    )
    without_const = build_player_combatant(
        _make_char(""), player_skill_keys=[_ATTACK_SKILL]
    )

    # hp_pct 0.03 → strictly larger HP pool.
    assert with_const.hp_max > without_const.hp_max
    # crit_rating 60 → strictly higher crit rating.
    assert with_const.crit_rating > without_const.crit_rating
    # bleed_on_hit_pct 0.04 → on the combatant, absent without the body.
    assert with_const.bleed_on_hit_pct > 0
    assert without_const.bleed_on_hit_pct == 0

    # Cross-check the deltas match the constitution's declared footprint, so
    # the same compute path the snapshot relies on is what's being verified.
    cs_with = compute_combat_stats(_make_char(_CONSTITUTION))
    cs_without = compute_combat_stats(_make_char(""))
    assert cs_with.hp_max - cs_without.hp_max > 0
    assert cs_with.crit_rating - cs_without.crit_rating == 60
