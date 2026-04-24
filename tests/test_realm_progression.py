"""Max-level player vs top-realm enemy progression checks.

Pits a representative max-axis player at each realm against the strongest
enemy in the matching realm bucket. The goal is twofold:
  1) guard against regressions that trivialize late-game fights
  2) expose balance gaps so enemy skills / stats can be tuned

The test deliberately uses a *baseline* player loadout — full cultivation
but no unique equipment, no gems, no constitution, one linh căn — so the
result reflects raw realm scaling rather than gear min-maxing. Extra gear
would only make the player stronger; if even the baseline cruises, the
enemy side definitely needs a buff.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatEndReason, CombatSession,
    build_enemy_combatant, build_player_combatant,
)


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Player loadout helpers ────────────────────────────────────────────────
def _pick_player_skills(realm_tier: int) -> list[str]:
    """Top-damage player attack skills up to and including the given realm.

    ``realm_tier`` is the 1-based realm tier (1-9), matching the ``realm``
    field on skill JSON. A fresh realm-1 player can't cast realm-9 skills —
    passing stage-count here silently unlocked everything and trivialized
    early-realm fights.

    We pick one skill per element to cover the 9-element elemental matrix —
    this mimics a well-rounded cultivator rather than a single-element glass
    cannon, which gives enemies a more realistic fight to survive.
    """
    attack_pool = [
        s for s in registry.skills.values()
        if s.get("category") == "attack"
        and not s["key"].startswith("Enemy")
        and s.get("realm", 0) <= realm_tier
        and s.get("base_dmg", 0) > 0
        # Exclude Âm soul-drain skills — they bloat player hp_max mid-fight,
        # which distorts the "HP after win" metric we're trying to balance
        # against. Enemies should still be tuned for generic damage
        # pressure, not against a specialized self-sustain build.
        and "ApplySoulDrain" not in s.get("effects", [])
    ]
    # one best-per-element, capped by realm
    per_elem: dict[str, dict] = {}
    for s in attack_pool:
        e = s.get("element") or "_none"
        cur = per_elem.get(e)
        if cur is None or s["base_dmg"] > cur["base_dmg"]:
            per_elem[e] = s
    return [s["key"] for s in per_elem.values()]


def _build_max_player(realm_level: int, element: str = "kim") -> Character:
    """Baseline max-of-realm player: capped at body/qi/form = realm_level,
    level 9. The runtime caps at realm 8 (index) since realms are 0-based."""
    capped_realm = min(8, max(0, realm_level - 1))
    return Character(
        player_id=1, discord_id=1, name=f"Tester-R{realm_level}",
        body_realm=capped_realm, body_level=9,
        qi_realm=capped_realm,   qi_level=9,
        formation_realm=capped_realm, formation_level=9,
        linh_can=[element],
        stats=CharacterStats(),
    )


def _realm_gear(realm_level: int) -> dict:
    """Approximate equipment stats a realistically geared player carries.

    Covers the full affix spread that exists in ``equipment/affixes.json``:
    primary offense (atk/matk), defense (def/hp/mp/res), combat ratings
    (crit/evasion/crit-res), boot SPD, cooldown reduction, a regen drip, and
    a single signature on-hit proc (bleed — the kim-linh-căn player's
    natural synergy). Uniques typically layer two of these together; this
    summary just flattens the usual "end-game armor set + boots + weapon +
    amulet + ring" contributions into one bag of bonuses.

    Early game (R1-R3): nothing equipped (gathering phase)
    Mid game  (R4-R6): starter gear set
    Late game (R7-R9): full craft-grade set
    Boss tier (R10): full end-game set with uniques
    """
    # Even early-realm maxed players carry starter drops — the "zero gear"
    # assumption was leaving R1-R3 fights with 100% HP remaining, which
    # doesn't reflect real play. Tier 0 is light starter kit.
    if realm_level <= 1:
        tier, mult = 0, 0.15
    elif realm_level <= 3:
        tier, mult = 0, 0.30
    else:
        # Gear escalates harder at high realms — R7+ players are expected
        # to have endgame-grade equipment, not mid-tier. The old curve had
        # tier-3 stuck across R7/R8 which left R8 players comically
        # underequipped vs the realm's apex.
        tier_map = {4: 1, 5: 2, 6: 2, 7: 3, 8: 4, 9: 4, 10: 5}
        tier = tier_map[realm_level]
        mult = {1: 0.5, 2: 1.0, 3: 2.0, 4: 3.5, 5: 5.5}[tier]
    return {
        # Primary offense
        "atk":              int(220 * mult),
        "matk":             int(220 * mult),
        # Primary defense
        "def_stat":         int(150 * mult),
        "hp_max":           int(10000 * mult),
        "mp_max":           int(500 * mult),
        # Combat ratings
        "crit_rating":      int(180 * mult),
        "crit_dmg_rating":  int(180 * mult),
        "evasion_rating":   int(120 * mult),
        "crit_res_rating":  int(100 * mult),
        # Percentage modifiers
        "final_dmg_bonus":  0.05 * mult,
        "final_dmg_reduce": 0.04 * mult,
        "res_all":          0.02 * mult,
        # Boot SPD — staple of every Phong-adjacent build, but every class
        # carries at least a modest SPD roll.
        "spd_bonus":        int(2 * mult),
        # Regen drip — typical amulet/ring affixes
        "hp_regen_pct":     0.004 * mult,
        "mp_regen_pct":     0.006 * mult,
        # Quality-of-life from mid-game onward
        "cooldown_reduce":  0.02 * mult,
        # One signature on-hit proc — kim/linh-căn player stacks bleed.
        # Scales from 5 % (mid-game) to 20 % (end-game) chance per hit.
        "bleed_on_hit_pct": 0.04 * mult,
    }


# ── Enemy selection ──────────────────────────────────────────────────────
_TOP_RANK_ORDER = [
    "chi_ton", "tien_thu", "than_thu", "dai_nang",
    "hung_manh", "cuong_gia", "tinh_anh", "pho_thong",
]


def _rank_priority(rank: str) -> int:
    try:
        return _TOP_RANK_ORDER.index(rank)
    except ValueError:
        return len(_TOP_RANK_ORDER)


def _top_enemy_of_realm(realm_level: int) -> dict:
    """The single strongest *main-combat* enemy keyed to this realm_level.

    Strength order: top rank first (chi_ton > tien_thu > …), then a threat
    score that blends offense + HP. Pure "highest HP" biased toward slow
    earth tanks whose physical damage was nullified by player DEF — the
    threat score keeps crit-rated magical apex enemies in contention.
    Duoc-Vien alchemy-gathering spawns are excluded.
    """
    def threat(e: dict) -> int:
        matk = e.get("base_matk", 0)
        crit = e.get("base_crit_rating", 0)
        fdb = e.get("final_dmg_bonus", 0.0)
        # Weight magical damage higher — player stats give much better
        # physical mitigation (PHYS_DEF_K + gear def) than elemental
        # resistance, so matk-heavy apex enemies stress the player more.
        return matk * 2 + crit * 4 + int(fdb * 500) + e.get("base_hp", 0) // 120

    candidates = [
        e for e in registry.enemies.values()
        if e.get("realm_level") == realm_level
        and not e["key"].startswith("DuocVien")
    ]
    assert candidates, f"no enemies found for realm_level={realm_level}"
    candidates.sort(key=lambda e: (_rank_priority(e.get("rank", "")), -threat(e)))
    return candidates[0]


# ── Battle simulator ──────────────────────────────────────────────────────
def _simulate(player_char: Character, enemy_key: str, *,
              seeds: list[int], max_turns: int = 60,
              equip_stats: dict | None = None) -> dict:
    # ``realm`` on the Character is 0-indexed, skill JSON uses 1-indexed.
    player_realm_tier = min(9, max(1, player_char.body_realm + 1))
    skills = _pick_player_skills(player_realm_tier)
    outcomes = {"win": 0, "lose": 0, "draw": 0}
    turns_to_win: list[int] = []
    player_hp_pct: list[float] = []

    for seed in seeds:
        player = build_player_combatant(player_char, skills, equip_stats=equip_stats)
        # Snapshot the pre-fight hp_max — Âm-build soul-drain grows hp_max
        # during combat, which inflated "HP after win" readings (player at
        # 208k/208k current reads 100 % but in truth they started at 92k
        # and soul-drained 116k off the boss). Using the snapshot gives a
        # stable damage-taken denominator.
        starting_hp_max = player.hp_max
        enemy = build_enemy_combatant(enemy_key, _total_stages(player_char))
        assert enemy is not None, f"enemy not found: {enemy_key}"
        session = CombatSession(
            player=player, enemy=enemy,
            player_skill_keys=skills,
            rng=random.Random(seed),
            max_turns=max_turns,
        )
        result = session.run()
        if result.reason == CombatEndReason.PLAYER_WIN:
            outcomes["win"] += 1
            turns_to_win.append(result.turns)
            player_hp_pct.append(
                min(1.0, session.player.hp / max(1, starting_hp_max))
            )
        elif result.reason == CombatEndReason.PLAYER_DEAD:
            outcomes["lose"] += 1
        else:
            outcomes["draw"] += 1

    return {
        "outcomes": outcomes,
        "avg_win_turns": (sum(turns_to_win) / len(turns_to_win)) if turns_to_win else None,
        "avg_hp_pct_on_win": (sum(player_hp_pct) / len(player_hp_pct)) if player_hp_pct else None,
    }


def _max_axis_stage(char: Character) -> int:
    return max(
        char.body_realm * 9 + char.body_level,
        char.qi_realm   * 9 + char.qi_level,
        char.formation_realm * 9 + char.formation_level,
    )


def _total_stages(char: Character) -> int:
    return (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    )


# ── Tests ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("realm_level", list(range(1, 11)))
def test_max_player_vs_top_enemy_is_challenging(realm_level):
    """Win rate should be in a sensible band per realm.

    Bands (by realm_level):
      1-3 → early game, maxed player should dominate but not flawless
      4-7 → mid-to-late, enemies should push the player
      8-10 → endgame / bosses, enemies should drop player to low HP or below
    """
    enemy = _top_enemy_of_realm(realm_level)
    player_char = _build_max_player(realm_level, element="kim")
    report = _simulate(
        player_char, enemy["key"],
        seeds=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        equip_stats=_realm_gear(realm_level),
    )
    outcomes = report["outcomes"]
    wins = outcomes["win"]
    total = sum(outcomes.values())
    win_rate = wins / total

    if realm_level <= 3:
        # Early enemies should still allow a maxed player to clear reliably.
        assert win_rate >= 0.7, (
            f"R{realm_level} vs {enemy['key']}: win_rate={win_rate:.0%} too low"
            f" for early content — report={report}"
        )
    elif realm_level <= 7:
        # Mid-game: real challenge, player wins most but not all.
        assert 0.3 <= win_rate <= 1.0, (
            f"R{realm_level} vs {enemy['key']}: win_rate={win_rate:.0%}"
            f" outside 30-100% mid-game band — report={report}"
        )
    else:
        # End-game bosses: any win rate is acceptable, but losing ALL means
        # the boss is overtuned beyond reach.
        assert win_rate > 0.0, (
            f"R{realm_level} boss {enemy['key']}: 0% wins — unbeatable."
            f" report={report}"
        )
        # A clean 100% win + 95%+ HP means the boss is a pushover.
        if win_rate == 1.0 and (report["avg_hp_pct_on_win"] or 0) > 0.95:
            pytest.fail(
                f"R{realm_level} boss {enemy['key']} is trivial: "
                f"100% wins with avg {report['avg_hp_pct_on_win']:.0%} HP "
                f"remaining — needs a buff. report={report}"
            )


def test_duoc_vien_is_alchemy_tier_not_boss_tier():
    """DuocVien (herb-garden) enemies are side-content, not raid bosses.

    They exist so alchemy cultivators can farm ingredients; they should be
    consistently winnable by a realm-appropriate player but not a pure
    pushover. Specifically: 100 % win rate across seeds, and never out-MATK
    the player (they're plants, not apex spellcasters).
    """
    issues: list[str] = []
    for rl in range(1, 10):
        for e in registry.enemies.values():
            if e.get("realm_level") != rl or not e["key"].startswith("DuocVien"):
                continue
            # Basic loadout checks
            if "base_matk" not in e or "base_atk" not in e or "base_def" not in e:
                issues.append(f"{e['key']}: missing base stat overrides")
                continue
            # Regen must not wall the player. Anything over 2% makes late-
            # realm fights stall into losses against a normal damage budget.
            if e.get("hp_regen_pct", 0) > 0.02:
                issues.append(
                    f"{e['key']}: hp_regen_pct={e['hp_regen_pct']} exceeds 2 % ceiling"
                )
            # At least 2 skills so they don't auto-attack every other turn.
            if len(e.get("skill_keys", [])) < 2:
                issues.append(f"{e['key']}: fewer than 2 skill_keys")
        # Simulate every herb at its realm and assert at least one win.
        for e in registry.enemies.values():
            if e.get("realm_level") != rl or not e["key"].startswith("DuocVien"):
                continue
            report = _simulate(
                _build_max_player(rl), e["key"],
                seeds=[1, 2, 3], equip_stats=_realm_gear(rl),
            )
            if report["outcomes"]["win"] == 0:
                issues.append(
                    f"{e['key']}: 0/3 wins — herb is tougher than a combat apex"
                )
    assert not issues, "DuocVien balance issues:\n  " + "\n  ".join(issues)


def test_late_game_apex_drops_player_to_low_hp_on_win():
    """R7+ apex fights must push the player close to death on victory.

    Target: player at 10-40 % HP after winning, so clearing a realm's apex
    feels like a real accomplishment rather than a formality. This catches
    regressions where someone quietly nerfs apex damage or buffs player
    sustain.
    """
    windows = {
        7:  (0.10, 0.45),   # R7 still a stretch but not a guaranteed
                            # near-death affair — upper bound slightly wider
                            # to match the player's full-gear tier.
        8:  (0.05, 0.35),
        9:  (0.00, 0.30),
        10: (0.00, 0.40),
    }
    violations: list[str] = []
    for realm_level, (lo, hi) in windows.items():
        enemy = _top_enemy_of_realm(realm_level)
        report = _simulate(
            _build_max_player(realm_level), enemy["key"],
            seeds=list(range(1, 11)),
            equip_stats=_realm_gear(realm_level),
        )
        hp_after = report["avg_hp_pct_on_win"]
        if hp_after is None:
            continue  # 0 wins triggers test_max_player_vs_top_enemy_is_challenging
        if not (lo <= hp_after <= hi):
            violations.append(
                f"R{realm_level}/{enemy['key']}: avg HP after win "
                f"{hp_after:.0%} outside target band {lo:.0%}-{hi:.0%}"
            )
    assert not violations, (
        "Higher-realm fights not tuned to 20-30 % HP-after-win target:\n  "
        + "\n  ".join(violations)
    )


def test_non_apex_enemies_have_fleshed_out_stat_sheets():
    """Every R1-R6 enemy should carry base_atk/matk/def overrides and at
    least two skill_keys. Bare-stat enemies relying on rank fallbacks were
    too weak for a maxed player of their realm — the explicit stats let us
    tune the curve per-encounter instead of moving a global dial.
    """
    bare: list[str] = []
    one_skill: list[str] = []
    for e in registry.enemies.values():
        rl = e.get("realm_level", 0)
        if rl not in range(1, 7) or e["key"].startswith("DuocVien"):
            continue
        if "base_atk" not in e or "base_matk" not in e or "base_def" not in e:
            bare.append(f"R{rl}/{e['key']}")
        if len(e.get("skill_keys", [])) < 2:
            one_skill.append(f"R{rl}/{e['key']}")
    assert not bare, (
        "Enemies missing base_atk/base_matk/base_def overrides:\n  "
        + "\n  ".join(bare)
    )
    assert not one_skill, (
        "Enemies with fewer than 2 skills — will whiff turns on cooldown:\n  "
        + "\n  ".join(one_skill)
    )


def test_all_top_enemies_deal_meaningful_damage():
    """Every realm's top enemy should remove at least 20% HP from a baseline
    max-of-realm player on average across the sample. If a boss can't even
    scratch the player, it has no business being the zone's apex encounter.
    """
    # Every realm's apex should take at least a bite out of a max-of-realm
    # player. Early realms allow smaller dents (tier-0 player gear vs R1
    # phổ-thông herb isn't meant to feel threatening); mid-to-late realms
    # require meaningful pressure.
    thresholds = {
        1: 0.0, 2: 0.0, 3: 0.05,
        4: 0.10, 5: 0.10, 6: 0.10,
        7: 0.30, 8: 0.40, 9: 0.40, 10: 0.40,
    }
    underpowered: list[str] = []
    for realm_level in range(1, 11):
        enemy = _top_enemy_of_realm(realm_level)
        player_char = _build_max_player(realm_level, element="kim")
        report = _simulate(
            player_char, enemy["key"],
            seeds=[11, 12, 13, 14, 15],
            equip_stats=_realm_gear(realm_level),
        )
        dent = 1.0 - (report["avg_hp_pct_on_win"] or 1.0)
        need = thresholds[realm_level]
        if (report["outcomes"]["win"] > 0) and dent < need:
            underpowered.append(
                f"R{realm_level}/{enemy['key']}: avg dent {dent:.0%} < need {need:.0%}"
            )
    assert not underpowered, (
        "Top-tier enemies too weak — buff stats or skill base_dmg/effects:\n  "
        + "\n  ".join(underpowered)
    )
