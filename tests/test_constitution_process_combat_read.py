"""Phase 3 — flag-gated Constitution Process combat *read* path.

Phase 1 froze the pure composition (``effective_stat_bonuses``); Phase 2 froze
the per-(player, constitution) persistence. Phase 3 wires a stored level into
the real stat build through the existing ``process_levels`` seam on
``cultivation.compute_constitution_bonuses`` — but ONLY when
``settings.constitution_process_enabled`` is True. With the flag OFF (the
default) the call site passes ``process_levels=None`` so the inert flat path
runs and the Phase-0 guard stays byte-identical.

The carrier is ``Character.constitution_levels`` (``{constitution_key: level}``),
populated at the ORM→build boundary exactly where Skill Mastery sources its
per-skill map. These tests bypass the DB and set the carrier directly — the
repo round-trip is already covered by ``test_constitution_process_repo.py``;
here we pin the *read*: flag gating, L1 identity, the L9 delta, no-row default,
and the Hỗn Độn amplification-exclusion safety.

Flag hygiene: every flag-ON test flips the global via
``monkeypatch.setattr`` so it auto-reverts — the dormant default is never left
mutated for the next test.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import build_player_combatant
from src.game.systems.cultivation import compute_constitution_bonuses
from src.game.systems.constitution_process import effective_stat_bonuses
from src.utils.config import settings

# ── Synthetic, content-independent test bodies ───────────────────────────────
# The v12 roster rebuild emptied the real constitution roster (only Phàm Thể
# survives), so this engine test no longer leans on any shipped body. Instead
# it injects two SYNTHETIC constitutions into the registry via an autouse
# ``monkeypatch`` fixture — a process-bearing canary and a Hỗn Độn-style
# amplifier — and pins the engine's flag-gating + milestone-composition +
# amplification-exclusion contracts against THOSE. Roster content can be
# rebuilt freely without ever touching this file again.
#
# The canary mirrors the shape the engine contract cares about: a process block
# with milestones [1, 3, 6, 9] whose crit_rating milestones sum to +150
# (40+50+60), whose m9 adds final_dmg_bonus +0.12 and true_dmg_pct +0.03 on top
# of a flat true_dmg_pct 0.08 (→ 0.11 at L9). Flat stat_bonuses carry a
# crit_rating + bleed_on_hit_pct so the body is a recognisable crit/bleed build.
_CANARY = "SynthCanaryBody"

# The Hỗn Độn-style carrier — ``all_passives_multiplier: 1.25`` amplifies OTHER
# active bodies' numeric stats, but never the compound-offensive stats in
# ``cultivation._AMP_EXCLUDED_STATS`` (final_dmg_bonus / true_dmg_pct / …). A
# crit_rating is included so the amplifier is genuinely active and the exclusion
# assertion is meaningful, not a no-op.
_HON_DON = "ConstitutionHonDon"

_ATTACK_SKILL = "EnemyKim_T1"

# Realm shape: body axis at realm 5 → ≥1 standard slot, so the canary is
# always hosted in the first (always-active) slot.
_BODY_REALM = 5


_SYNTH_CANARY_DATA = {
    "key": _CANARY,
    "vi": "Thể Thử Nghiệm",
    "en": "Synthetic Canary Body",
    "rarity": "epic",
    "element": "kim",
    "roll_weight": 0,
    "stat_bonuses": {
        "crit_rating": 220,
        "crit_dmg_rating": 200,
        "bleed_on_hit_pct": 0.15,
        "true_dmg_pct": 0.08,
        "armor_pen_pct": 0.08,
    },
    "process": {
        "milestones": [1, 3, 6, 9],
        "levels": {
            "1": {"stat_bonuses": {}},
            "3": {"stat_bonuses": {"crit_rating": 40, "bleed_on_hit_pct": 0.02}},
            "6": {"stat_bonuses": {"crit_rating": 50, "crit_dmg_rating": 60, "armor_pen_pct": 0.02}},
            "9": {
                "stat_bonuses": {
                    "crit_rating": 60,
                    "true_dmg_pct": 0.03,
                    "final_dmg_bonus": 0.12,
                    "dot_dmg_bonus_by_kind": {"bleed": 0.1},
                }
            },
        },
    },
    "special_requirements": None,
}

# Hỗn Độn must exist under its canonical key — the amplification machinery
# special-cases the ``all_passives_multiplier`` carrier by key identity (9th
# slot). Inject a minimal 1.25x carrier carrying a single non-excluded stat.
_SYNTH_HON_DON_DATA = {
    "key": _HON_DON,
    "vi": "Hỗn Độn Thử Nghiệm",
    "en": "Synthetic Chaos Body",
    "rarity": "legendary",
    "element": None,
    "roll_weight": 0,
    "stat_bonuses": {
        "all_passives_multiplier": 1.25,
        "crit_rating": 100,
    },
    "special_requirements": None,
}


@pytest.fixture(autouse=True)
def _inject_synthetic_bodies(monkeypatch):
    """Register the synthetic canary + Hỗn Độn carrier for the duration of each
    test, leaving the (now near-empty) real roster untouched."""
    monkeypatch.setitem(registry.constitutions, _CANARY, _SYNTH_CANARY_DATA)
    monkeypatch.setitem(registry.constitutions, _HON_DON, _SYNTH_HON_DON_DATA)


def _make_char(constitution_type: str, levels: dict[str, int] | None = None) -> Character:
    """A fixed Kim-body Character; constitution_type + levels are the knobs."""
    return Character(
        player_id=1,
        discord_id=1,
        name="KimTester",
        body_realm=_BODY_REALM, body_level=1,
        qi_realm=5, qi_level=1,
        formation_realm=5, formation_level=1,
        active_axis="body",
        constitution_type=constitution_type,
        linh_can=["kim"],
        linh_can_levels={"kim": 5},
        constitution_levels=dict(levels or {}),
        stats=CharacterStats(),
    )


def _canary_data() -> dict:
    data = registry.get_constitution(_CANARY)
    assert data is not None, f"{_CANARY!r} must be registered"
    assert data.get("process"), f"{_CANARY!r} must carry a process block (canary)"
    return data


# ── 1. Flag OFF (default): level is ignored — inert flat read ────────────────


def test_flag_off_ignores_level_equals_flat() -> None:
    """Flag OFF: even a stored L9 builds the flat constitution — no process read.

    This is the dormant-path contract: the level carrier exists on the
    Character but the call site never consults it, so the combatant matches a
    flat (level-less) build exactly.
    """
    assert settings.constitution_process_enabled is False  # default

    flat = build_player_combatant(
        _make_char(_CANARY), player_skill_keys=[_ATTACK_SKILL]
    )
    leveled = build_player_combatant(
        _make_char(_CANARY, {_CANARY: 9}), player_skill_keys=[_ATTACK_SKILL]
    )

    assert leveled.crit_rating == flat.crit_rating
    assert leveled.final_dmg_bonus == flat.final_dmg_bonus
    assert leveled.true_dmg_pct == flat.true_dmg_pct
    assert leveled.bleed_on_hit_pct == flat.bleed_on_hit_pct


# ── 2. Flag ON + stored level 1: the level-1 identity ───────────────────────


def test_flag_on_level_one_is_identity(monkeypatch) -> None:
    """Flag ON, L1: the process layer is the flat body (milestone 1 is empty)."""
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    flat_off = build_player_combatant(  # the canonical flat reference
        _make_char(_CANARY), player_skill_keys=[_ATTACK_SKILL]
    )
    # Recompute the flat reference under the SAME (ON) flag but without the
    # process seam to be airtight: an L1 build must reproduce it.
    l1 = build_player_combatant(
        _make_char(_CANARY, {_CANARY: 1}), player_skill_keys=[_ATTACK_SKILL]
    )

    assert l1.crit_rating == flat_off.crit_rating
    assert l1.final_dmg_bonus == flat_off.final_dmg_bonus
    assert l1.true_dmg_pct == flat_off.true_dmg_pct
    assert l1.bleed_on_hit_pct == flat_off.bleed_on_hit_pct

    # And the pure seam agrees: L1 == flat stat_bonuses verbatim.
    canary = _canary_data()
    assert effective_stat_bonuses(canary, 1) == canary["stat_bonuses"]


# ── 3. Flag ON + stored level 9: strictly stronger, exact milestone delta ───


def test_flag_on_level_nine_applies_milestone_delta(monkeypatch) -> None:
    """Flag ON, L9: bonuses equal ``effective_stat_bonuses(canary, 9)`` merged.

    crit_rating must rise by the canary's milestone delta over the flat build.
    The L9 milestones add +40 (m3) +50 (m6) +60 (m9) = +150 crit_rating on top
    of the flat 220, so the built combatant's crit_rating beats the flat build
    by exactly 150.
    """
    flat_off = build_player_combatant(  # captured with flag OFF (default)
        _make_char(_CANARY), player_skill_keys=[_ATTACK_SKILL]
    )

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    l9 = build_player_combatant(
        _make_char(_CANARY, {_CANARY: 9}), player_skill_keys=[_ATTACK_SKILL]
    )

    # Strictly stronger, with the exact milestone-summed crit_rating delta.
    canary = _canary_data()
    eff9 = effective_stat_bonuses(canary, 9)
    flat = canary["stat_bonuses"]
    expected_crit_delta = eff9["crit_rating"] - flat["crit_rating"]
    assert expected_crit_delta == 150  # 40 + 50 + 60 milestone sum

    assert l9.crit_rating == flat_off.crit_rating + expected_crit_delta
    assert l9.crit_rating > flat_off.crit_rating

    # The other milestone stats also land on the combatant (additive deltas).
    assert l9.final_dmg_bonus == pytest.approx(flat_off.final_dmg_bonus + 0.12)
    assert l9.true_dmg_pct == pytest.approx(flat_off.true_dmg_pct + 0.03)


# ── 4. Flag ON but NO progress row → default level 1 (== flat) ──────────────


def test_flag_on_no_progress_row_defaults_to_level_one(monkeypatch) -> None:
    """Flag ON, empty level carrier: equipped body defaults to L1 → flat build.

    Mirrors production: when ``load_constitution_levels`` finds no row for an
    equipped body it stores level 1, so a freshly-equipped (un-progressed)
    constitution is identical to the flat path.
    """
    flat_off = build_player_combatant(
        _make_char(_CANARY), player_skill_keys=[_ATTACK_SKILL]
    )

    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    # An empty carrier collapses to ``process_levels=None`` at the call site
    # (``char.constitution_levels or None``) → inert flat read, == L1 identity.
    no_row = build_player_combatant(
        _make_char(_CANARY, {}), player_skill_keys=[_ATTACK_SKILL]
    )
    # An explicit {key: 1} (what the repo would store) must match too.
    explicit_l1 = build_player_combatant(
        _make_char(_CANARY, {_CANARY: 1}), player_skill_keys=[_ATTACK_SKILL]
    )

    assert no_row.crit_rating == flat_off.crit_rating
    assert explicit_l1.crit_rating == flat_off.crit_rating


# ── 5. Hỗn Độn safety: L9 milestone offensive stats are NOT amplified ───────


def test_flag_on_hon_don_does_not_amplify_excluded_milestone_stats(monkeypatch) -> None:
    """Regression: a 1.25x Hỗn Độn carrier must not amplify the canary's L9
    ``final_dmg_bonus`` / ``true_dmg_pct``.

    These compound-offensive stats live in ``_AMP_EXCLUDED_STATS`` precisely so
    stacking a leveled body under Hỗn Độn can't manufacture one-shot damage vs
    world bosses. We isolate the canary's contribution inside the amplified
    merge by subtracting the carrier's solo bonuses, and assert it equals the
    canary's *un-amplified* L9 value — NOT the value × 1.25.
    """
    monkeypatch.setattr(settings, "constitution_process_enabled", True)

    # Hỗn Độn occupies a dedicated 9th slot; any body_realm hosts both.
    carrier_realm = 8
    levels = {_CANARY: 9, _HON_DON: 1}

    hon_alone = compute_constitution_bonuses(
        _HON_DON, "body", carrier_realm, process_levels={_HON_DON: 1}
    )
    both = compute_constitution_bonuses(
        f"{_CANARY},{_HON_DON}", "body", carrier_realm, process_levels=levels
    )
    canary_l9 = compute_constitution_bonuses(
        _CANARY, "body", _BODY_REALM, process_levels={_CANARY: 9}
    )

    # Canary's contribution inside the amplified merge = both − carrier's solo.
    fdb_contrib = both.get("final_dmg_bonus", 0.0) - hon_alone.get("final_dmg_bonus", 0.0)
    tdp_contrib = both.get("true_dmg_pct", 0.0) - hon_alone.get("true_dmg_pct", 0.0)

    # Un-amplified: equals the canary's own L9 milestone value, not ×1.25.
    assert fdb_contrib == pytest.approx(canary_l9["final_dmg_bonus"])
    assert tdp_contrib == pytest.approx(canary_l9["true_dmg_pct"])
    assert fdb_contrib == pytest.approx(0.12)
    assert tdp_contrib == pytest.approx(0.11)

    # And strictly LESS than the amplified value would be — proves exclusion.
    assert fdb_contrib < canary_l9["final_dmg_bonus"] * 1.25
    assert tdp_contrib < canary_l9["true_dmg_pct"] * 1.25

    # Sanity: a non-excluded stat (crit_rating) IS amplified, so the carrier is
    # genuinely active and the exclusion above is meaningful, not a no-op.
    # crit_rating is an int, so the 1.25x scale truncates (int(370 * 1.25) = 462).
    crit_contrib = both["crit_rating"] - hon_alone.get("crit_rating", 0)
    assert crit_contrib == int(canary_l9["crit_rating"] * 1.25)
    assert crit_contrib > canary_l9["crit_rating"]  # genuinely amplified
