"""Characterization tests for the passive skill ``SkillPasHaoNhienChinhKhi``.

Pins the contract of the new general-pool passive:

  * ``SkillPasHaoNhienChinhKhi`` (src/data/skills/player/general.json) — a
    grade-3 self-aura passive whose ``effects`` list resolves to a single
    permanent buff.
  * ``BuffHaoNhienChinhKhi`` (src/data/effects/buffs.json) — the buff
    that exposes four stat keys (``debuff_immune_pct``,
    ``cleanse_on_turn_pct``, ``cleanse_heal_pct``, ``res_am``), is
    non-stealable and lasts the entire fight (duration 99).
  * ``apply_passive_auras`` — when a combatant whose ``skill_keys``
    contains the passive enters combat, the buff is stamped on self with
    the duration declared in JSON.
  * ``get_combat_modifiers`` — while the buff is active, every stat key
    declared on the buff bubbles up through the aggregator.

The pattern mirrors the SkillAmMaKhiHoThe_R9 / SkillAmChanMaChiTam_R9
aura tests in ``tests/test_am_skills.py`` — drive the engine through
``session._apply_passive_auras`` rather than mocking the registry.
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, default_duration, get_combat_modifiers

from tests.conftest import make_combatant, make_session

_SKILL_KEY = "SkillPasHaoNhienChinhKhi"
_BUFF_KEY = "BuffHaoNhienChinhKhi"

# Canonical magnitudes — single source of truth for the spec.
_DEBUFF_IMMUNE_PCT = 0.2
_CLEANSE_ON_TURN_PCT = 0.3
_CLEANSE_HEAL_PCT = 0.04
_RES_AM = 0.3
_DURATION = 99


# ── Schema load ─────────────────────────────────────────────────────────


def test_skill_schema_load():
    """Registry exposes the passive skill with the documented shape."""
    skill = registry.get_skill(_SKILL_KEY)

    assert skill is not None, f"{_SKILL_KEY} missing from registry"
    assert skill["mp_cost"] == 0
    assert skill["cooldown"] == 0
    assert skill["scroll_grade"] == 3
    assert skill["category"] == "passive"
    assert skill["element"] is None
    assert skill.get("aura") is True
    assert list(skill.get("aura_targets") or []) == ["self"]
    assert _BUFF_KEY in skill["effects"]


# ── Buff meta load ──────────────────────────────────────────────────────


def test_buff_meta_load():
    """Buff exists in EFFECTS and carries the 4 stat keys at the right
    magnitudes; duration matches JSON; stealable is explicitly False."""
    meta = EFFECTS.get(_BUFF_KEY)

    assert meta is not None, f"{_BUFF_KEY} missing from EFFECTS"
    assert meta.kind.value == "buff"
    assert meta.stat_bonus["debuff_immune_pct"] == _DEBUFF_IMMUNE_PCT
    assert meta.stat_bonus["cleanse_on_turn_pct"] == _CLEANSE_ON_TURN_PCT
    assert meta.stat_bonus["cleanse_heal_pct"] == _CLEANSE_HEAL_PCT
    assert meta.stat_bonus["res_am"] == _RES_AM
    # Exactly the four keys — guards against drift adding stealth stats.
    assert set(meta.stat_bonus.keys()) == {
        "debuff_immune_pct",
        "cleanse_on_turn_pct",
        "cleanse_heal_pct",
        "res_am",
    }
    assert default_duration(_BUFF_KEY) == _DURATION


def test_buff_is_not_stealable():
    """Hạo Nhiên is a permanent passive — it must never be lifted by an
    ApplyBuffSteal proc. ``stealable`` defaults to True for buffs, so the
    JSON declares ``stealable: false`` explicitly. Pin that here."""
    meta = EFFECTS.get(_BUFF_KEY)
    assert meta is not None
    assert meta.stealable is False


# ── Aura stamping at combat start ───────────────────────────────────────


def test_aura_stamps_buff_on_combat_start():
    """A combatant whose ``skill_keys`` contains the passive picks up
    ``BuffHaoNhienChinhKhi`` the first time ``_apply_passive_auras``
    runs. Mirrors the SkillAmMaKhiHoThe_R9 aura-stamp pattern."""
    actor = make_combatant("caster")
    actor.skill_keys = [_SKILL_KEY]
    opponent = make_combatant("enemy")
    session = make_session(actor, opponent)

    assert not actor.has_effect(_BUFF_KEY), "buff should not be stamped pre-combat"

    session._apply_passive_auras(actor, opponent)

    assert actor.has_effect(_BUFF_KEY)
    # Duration honors the JSON default (99 turns ≈ entire fight).
    assert actor.effects[_BUFF_KEY] == _DURATION
    # ``aura_targets: ["self"]`` — must not leak to the opponent.
    assert not opponent.has_effect(_BUFF_KEY)


def test_aura_does_not_stamp_when_skill_absent():
    """No passive in skill_keys → no buff. Pins the gating contract."""
    actor = make_combatant("caster")
    actor.skill_keys = []  # explicitly clear
    opponent = make_combatant("enemy")
    session = make_session(actor, opponent)

    session._apply_passive_auras(actor, opponent)

    assert not actor.has_effect(_BUFF_KEY)


# ── Stat propagation through get_combat_modifiers ───────────────────────


def test_combat_modifiers_expose_all_four_stat_keys():
    """While the buff is active, ``get_combat_modifiers`` aggregates all
    four declared stat keys at the expected magnitudes."""
    actor = make_combatant("caster")
    actor.skill_keys = [_SKILL_KEY]
    opponent = make_combatant("enemy")
    session = make_session(actor, opponent)

    session._apply_passive_auras(actor, opponent)
    mods = get_combat_modifiers(actor)

    assert mods.get("debuff_immune_pct") == _DEBUFF_IMMUNE_PCT
    assert mods.get("cleanse_on_turn_pct") == _CLEANSE_ON_TURN_PCT
    assert mods.get("cleanse_heal_pct") == _CLEANSE_HEAL_PCT
    assert mods.get("res_am") == _RES_AM
