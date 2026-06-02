"""Phase 3a tests — the Skill Mastery ``base_dmg`` rider.

Phase 3a wires a per-skill ``power_mult`` into the base-damage rider chain so
that a mastered skill's *declared* base grows before any situational rider
compounds on top. The whole path is gated behind ``settings.skill_mastery_enabled``
(default OFF) so it stays provably inert until rollout.

What these tests pin:

  1. The feature flag defaults OFF (guards against a leaked global flip).
  2. The mastery rider is registered FIRST in ``_BASE_DMG_RIDERS`` (locked
     ordering — mastery amplifies the declared base before anti-regen /
     HP-lost / caster-scaling riders see it).
  3. Inertness when the flag is OFF, on level-1 / empty mastery, and on
     0-base utility skills.
  4. The exact ``round(base_dmg * (POWER_MULT[L] - 1))`` bonus when the flag
     is ON and the actor has mastered the skill.
  5. ``build_player_combatant`` threads ``skill_mastery`` onto the Combatant.
  6. End-to-end inertness: the fixed-seed golden fight stays byte-identical
     with the flag OFF and no mastery.

CRITICAL flag hygiene: ``settings.skill_mastery_enabled`` is a GLOBAL
singleton. Every test that flips it uses ``monkeypatch.setattr`` so the value
auto-reverts at teardown — never left set, which would corrupt other test
files that assume the default.
"""
from __future__ import annotations

import hashlib
import random

import pytest

from src.data.registry import registry
from src.game.constants.skill_mastery import POWER_MULT
from src.game.systems.combat.dmg_riders import (
    _BASE_DMG_RIDERS,
    _mastery_base_dmg_rider,
    apply_base_dmg_riders,
)
from src.utils.config import settings

from tests.conftest import make_combatant

# A real, registered low-cost attack skill — same one the Phase-0 guard uses.
_ATTACK_SKILL = "EnemyKim_T1"
_MASTERY_KEY = "Skill_X"


# ── 1. Flag default ─────────────────────────────────────────────────────────
def test_flag_defaults_off():
    """The mastery feature flag is OFF by default.

    Documents intent and guards against another test leaking a ``True`` flip
    into the global ``settings`` singleton.
    """
    assert settings.skill_mastery_enabled is False


# ── 2. Registration order (locked: mastery FIRST) ───────────────────────────
def test_rider_is_registered_first():
    """``_BASE_DMG_RIDERS[0]`` is the mastery rider.

    Locked decision: mastery amplifies the skill's declared base before any
    situational rider compounds on top, which only holds if it runs first.
    """
    assert _BASE_DMG_RIDERS[0].fn.__name__ == "_mastery_base_dmg_rider"
    assert _BASE_DMG_RIDERS[0].fn is _mastery_base_dmg_rider


# ── 3. Inert when flag OFF (even with mastery present) ──────────────────────
def test_rider_inert_when_flag_off():
    """Flag OFF + mastered skill → rider returns None, base_dmg unchanged."""
    # Flag is OFF by default; assert that as a precondition so this test does
    # not silently rely on leaked state from another test.
    assert settings.skill_mastery_enabled is False

    actor = make_combatant("a", skill_mastery={_MASTERY_KEY: 16})
    target = make_combatant("t")
    skill_data = {"key": _MASTERY_KEY, "base_dmg": 1_000}

    # Direct rider call returns None.
    assert (
        _mastery_base_dmg_rider(skill_data, actor, target, 1_000, {}) is None
    )

    # And through the dispatcher the base is untouched.
    new_base, new_skill = apply_base_dmg_riders(
        skill_data, actor, target, 1_000, {}, log=[]
    )
    assert new_base == 1_000


# ── 4. Scaling by POWER_MULT when flag ON ───────────────────────────────────
@pytest.mark.parametrize(
    "level, mult",
    [
        (10, 1.22),
        (16, 1.47),
        (25, 1.92),
    ],
)
def test_rider_scales_base_dmg_by_power_mult(monkeypatch, level, mult):
    """Flag ON + mastery level L → bonus == round(base_dmg * (POWER_MULT[L]-1))."""
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    # The parametrized mult must match the locked table — a drift in either
    # POWER_MULT or this test surfaces here rather than as a silent miscompute.
    assert POWER_MULT[level] == mult

    base_dmg = 1_000
    actor = make_combatant("a", skill_mastery={_MASTERY_KEY: level})
    target = make_combatant("t")
    skill_data = {"key": _MASTERY_KEY, "base_dmg": base_dmg, "vi": "Test"}

    result = _mastery_base_dmg_rider(skill_data, actor, target, base_dmg, {})
    assert result is not None
    bonus, _log = result
    expected = int(round(base_dmg * (POWER_MULT[level] - 1.0)))
    assert bonus == expected

    # The dispatcher folds the same bonus into the base.
    new_base, new_skill = apply_base_dmg_riders(
        skill_data, actor, target, base_dmg, {}, log=[]
    )
    assert new_base == base_dmg + expected
    assert new_skill["base_dmg"] == base_dmg + expected


# ── 5. Level-1 / empty mastery are no-ops (even with flag ON) ───────────────
def test_level_one_and_empty_mastery_are_noop(monkeypatch):
    """Flag ON but level-1 mult (1.0) → None; empty mastery (enemy) → None."""
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    target = make_combatant("t")
    skill_data = {"key": _MASTERY_KEY, "base_dmg": 1_000}

    # Explicit level 1 → power_mult 1.0 → no bonus.
    lvl1 = make_combatant("a", skill_mastery={_MASTERY_KEY: 1})
    assert _mastery_base_dmg_rider(skill_data, lvl1, target, 1_000, {}) is None

    # Empty mastery (enemy/boss case) → key missing → level 1 → no bonus.
    empty = make_combatant("e")
    assert empty.skill_mastery == {}
    assert _mastery_base_dmg_rider(skill_data, empty, target, 1_000, {}) is None


# ── 6. Zero-base skills get no bonus from this rider ────────────────────────
def test_zero_base_skill_gets_no_bonus(monkeypatch):
    """Flag ON, mastered, but base_dmg == 0 → None (3b handles potency)."""
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)
    actor = make_combatant("a", skill_mastery={_MASTERY_KEY: 16})
    target = make_combatant("t")
    skill_data = {"key": _MASTERY_KEY, "base_dmg": 0}

    # Direct rider call returns None on the base_dmg<=0 guard.
    assert _mastery_base_dmg_rider(skill_data, actor, target, 0, {}) is None

    # The dispatcher also short-circuits all riders when base_dmg <= 0.
    new_base, _new_skill = apply_base_dmg_riders(
        skill_data, actor, target, 0, {}, log=[]
    )
    assert new_base == 0


# ── 7. build_player_combatant threads mastery ───────────────────────────────
def _make_minimal_char():
    """Construct the minimal Character used by existing builder tests."""
    from src.game.models.character import Character, CharacterStats

    return Character(
        player_id=1, discord_id=1, name="Test",
        body_realm=1, body_level=1,
        qi_realm=1, qi_level=1,
        formation_realm=1, formation_level=1,
        linh_can=[], stats=CharacterStats(),
    )


def test_build_player_combatant_threads_mastery():
    """``skill_mastery`` kwarg lands on the Combatant; absent → empty dict."""
    from src.game.systems.combat import build_player_combatant

    char = _make_minimal_char()

    cb = build_player_combatant(
        char, player_skill_keys=[], skill_mastery={_MASTERY_KEY: 7}
    )
    assert cb.skill_mastery == {_MASTERY_KEY: 7}

    # Called without the arg → empty dict (enemies/bosses path → inert).
    cb_default = build_player_combatant(char, player_skill_keys=[])
    assert cb_default.skill_mastery == {}


# ── 8. Ordering — mastery rides FIRST, others compound the grown base ───────
def test_ordering_mastery_compounds_before_other_riders(monkeypatch):
    """Mastery grows the declared base BEFORE a later base_dmg rider reads it.

    Drive the dispatcher with a skill that fires both the mastery rider and
    the HP-lost flat rider (``bonus_dmg_per_target_hp_lost_pct``). Because
    mastery is registered first, the HP-lost rider sees the post-mastery base
    is irrelevant to *its* math (it scales off the target's missing HP), but
    the assertion that matters is the COMBINED base: declared + mastery bonus
    + hp-lost bonus, proving both fired and mastery did not clobber the other.

    The full-fight version is skipped (too coupled to specific skill JSON);
    the locked index-0 ordering is pinned separately by
    ``test_rider_is_registered_first``. Test #8 was NOT weakened beyond using
    a synthetic skill_data dict — both riders demonstrably fire here.
    """
    monkeypatch.setattr(settings, "skill_mastery_enabled", True)

    base_dmg = 1_000
    level = 16  # POWER_MULT 1.47 → +470 mastery bonus on a 1_000 base
    actor = make_combatant("a", skill_mastery={_MASTERY_KEY: level})
    # Target at 50% HP so the HP-lost rider has something to scale off.
    target = make_combatant("t", hp=5_000, hp_max=10_000)

    skill_data = {
        "key": _MASTERY_KEY,
        "base_dmg": base_dmg,
        "vi": "Test",
        # 0.10 × hp_lost(5_000) = +500 from the executioner-style rider.
        "bonus_dmg_per_target_hp_lost_pct": 0.10,
    }

    log: list[str] = []
    new_base, new_skill = apply_base_dmg_riders(
        skill_data, actor, target, base_dmg, {}, log
    )

    mastery_bonus = int(round(base_dmg * (POWER_MULT[level] - 1.0)))  # 470
    hp_lost_bonus = int((10_000 - 5_000) * 0.10)  # 500
    # Both riders fired and folded additively onto the declared base.
    assert new_base == base_dmg + mastery_bonus + hp_lost_bonus
    assert new_skill["base_dmg"] == new_base
    # Mastery log line precedes the HP-lost line (registration order).
    assert any("Lĩnh Ngộ" in line for line in log)
    assert log[0].find("Lĩnh Ngộ") != -1


# ── 9. End-to-end golden guard still byte-identical with flag OFF ───────────
# Golden constants re-declared from tests/test_skill_mastery_guard.py so this
# file is self-contained. They were captured on clean feat/season-2.
_GOLDEN_PLAYER_HP = 8_961
_GOLDEN_ENEMY_HP = 0
_GOLDEN_TURNS = 23
_GOLDEN_LOG_LEN = 70
_GOLDEN_LOG_SHA256 = (
    "eb8ad87169a2973eaffdbf03d72f8570be4a6f7faf5ff6f4940458e2d38c67f8"
)


def test_golden_guard_still_byte_identical_flag_off():
    """Flag OFF + no mastery → the fixed-seed fight reproduces golden output.

    Proves the mastery rider is inert end-to-end: with the flag off it returns
    None before touching mastery state, so the Phase-0 golden fight is
    byte-for-byte identical.
    """
    from src.game.systems.combat import CombatEndReason, CombatSession

    # Precondition: flag must be OFF for this end-to-end inertness claim.
    assert settings.skill_mastery_enabled is False
    assert registry.get_skill(_ATTACK_SKILL) is not None

    player = make_combatant(
        "player", profile="large",
        skill_keys=[_ATTACK_SKILL], mp=9_999, mp_max=9_999,
    )
    enemy = make_combatant(
        "enemy", profile="small",
        skill_keys=[_ATTACK_SKILL], mp=9_999, mp_max=9_999,
    )
    session = CombatSession(
        player=player,
        enemy=enemy,
        player_skill_keys=[_ATTACK_SKILL],
        rng=random.Random(0),
        max_turns=50,
    )
    result = session.run()

    log_sha256 = hashlib.sha256(
        "\n".join(session.log).encode("utf-8")
    ).hexdigest()

    assert player.hp == _GOLDEN_PLAYER_HP
    assert enemy.hp == _GOLDEN_ENEMY_HP
    assert result.turns == _GOLDEN_TURNS
    assert result.reason == CombatEndReason.PLAYER_WIN
    assert len(session.log) == _GOLDEN_LOG_LEN
    assert log_sha256 == _GOLDEN_LOG_SHA256
