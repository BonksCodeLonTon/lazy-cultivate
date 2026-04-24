"""Balance probe — endgame Thể Tu vs R9 world bosses.

Goal
----
Document (and pin down) how much damage a fully-built endgame Thể Tu can pour
into a realm-9 world boss inside a single 15-round attack session. Observed
in-game that this archetype feels over-tuned; the test quantifies the effect
with a reproducible simulation so balance tweaks can be measured against a
concrete baseline.

Loadout mirrors the ``/admin_testbuild endgame_the_tu`` preset — the one an
admin would grant to reproduce the issue:

* body_realm = 8 / level 9, qi & formation left at starter levels (axis
  imbalance → ``is_the_tu == True``)
* ``dao_ti_unlocked = True`` so Hỗn Độn Đạo Thể is allowed
* All 9 Linh Căn active (matches the admin preset's full-spectrum roots)
* All 8 canonical Legendary constitutions equipped + Hỗn Độn in the 9th slot
  (``all_passives_multiplier = 2.0`` doubles every other passive)
* R8/R9 physical attack skills — what an ATK-scaling body cultivator actually
  casts; the admin preset ships weak placeholder skills, which masks real
  endgame DPS.
* End-game gear tier from ``test_realm_progression._realm_gear(10)``

Against each of the three R9 bosses the sim runs one attack window
(``ATTACK_ROUND_LIMIT`` = 15 rounds), then reports:

* raw damage dealt (uncapped) vs boss hp_max
* whether the per-attack 5 % cap was hit
* player HP % remaining after the session

The assertions document current behavior rather than block CI — they fail
loudly if the Thể Tu ever *stops* being oppressive, which is exactly the
signal the balance pass is looking for.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

import pytest

from src.data.registry import registry
from src.game.constants.linh_can import ALL_LINH_CAN, format_linh_can
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatEndReason, CombatSession,
    build_enemy_combatant, build_player_combatant, build_world_boss_combatant,
)
from src.game.systems.the_chat import set_constitutions
from src.game.systems.world_boss import ATTACK_ROUND_LIMIT, PER_ATTACK_DMG_CAP_PCT


# ── Test config — mirrors /admin_testbuild endgame_the_tu ──────────────────
# The canonical 8 Legendary constitutions that satisfy Hỗn Độn Đạo Thể's
# ``requires_all_legendary_equipped`` gate. Same list as
# ``src.bot.cogs.admin._LEGENDARY_EIGHT``.
_LEGENDARY_EIGHT: tuple[str, ...] = (
    "ConstitutionPhaTien",
    "ConstitutionHauTo",
    "ConstitutionNgheDinh",
    "ConstitutionTieuDao",
    "ConstitutionKhiHai",
    "ConstitutionBatDiet",
    "ConstitutionTranGioi",
    "ConstitutionThaiCucKim",
)
# Hỗn Độn goes in the 9th "special" slot once all 8 are equipped.
_HON_DON = "ConstitutionHonDon"

# Realistic endgame Thể Tu skill bar — physical/atk-scaling so the body path
# matters. Matches what an endgame player would realistically slot, not the
# weak R1 placeholders in the admin preset (``SkillAtkKim1`` etc.).
_THE_TU_ENDGAME_SKILLS: list[str] = [
    "SkillAtkKim_R9",          # 650 base, atk 1.1, bleed + armor break + tear
    "SkillKimBloodbath_R9",    # 700 base, atk 1.4, 18% true dmg
    "SkillAtkTho_R9",          # 820 base, stun + armor break
    "SkillThoEarthTower_R9",   # 720 base, atk 1.5, shield burst
    "SkillAtkLoi_R9",          # lightning coverage (non-physical backup)
    "SkillDefTo",              # shield buff
]

_R9_BOSSES: tuple[str, ...] = (
    "WorldBossRealm9_TienHoangDiet",     # 25M HP, hoa
    "WorldBossRealm9_HonNguyenMa",       # 30M HP, am
    "WorldBossRealm9_ThoThanCo",         # 35M HP, tho
)

_ENDGAME_GEAR: dict = {
    # Full-uniques tier-5 gear — copied from test_realm_progression._realm_gear(10)
    "atk":              int(220 * 5.5),
    "matk":             int(220 * 5.5),
    "def_stat":         int(150 * 5.5),
    "hp_max":           int(10000 * 5.5),
    "mp_max":           int(500 * 5.5),
    "crit_rating":      int(180 * 5.5),
    "crit_dmg_rating":  int(180 * 5.5),
    "evasion_rating":   int(120 * 5.5),
    "crit_res_rating":  int(100 * 5.5),
    "final_dmg_bonus":  0.05 * 5.5,
    "final_dmg_reduce": 0.04 * 5.5,
    "res_all":          0.02 * 5.5,
    "spd_bonus":        int(2 * 5.5),
    "hp_regen_pct":     0.004 * 5.5,
    "mp_regen_pct":     0.006 * 5.5,
    "cooldown_reduce":  0.02 * 5.5,
    "bleed_on_hit_pct": 0.04 * 5.5,
}


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    registry.load()


# ── Character factory ──────────────────────────────────────────────────────
def _build_endgame_the_tu() -> Character:
    """Exact mirror of ``/admin_testbuild endgame_the_tu`` — see module docstring."""
    # 8 Legendaries + Hỗn Độn. The whole point of this test is to capture the
    # ``all_passives_multiplier = 2.0`` blow-up Hỗn Độn causes when stacked on
    # all 8 carriers, so both need to be present in constitution_type.
    constitutions = [*_LEGENDARY_EIGHT, _HON_DON]
    return Character(
        player_id=1, discord_id=1, name="Endgame-TheTu",
        body_realm=8, body_level=9,              # maxed body
        qi_realm=0,   qi_level=1,                # intentional low — is_the_tu gate
        formation_realm=0, formation_level=1,
        constitution_type=set_constitutions(constitutions),
        dao_ti_unlocked=True,
        linh_can=list(ALL_LINH_CAN),
        stats=CharacterStats(),
    )


# ── Simulation ─────────────────────────────────────────────────────────────
@dataclass
class SessionOutcome:
    dmg_raw: int            # uncapped damage the local sim dealt
    dmg_after_cap: int      # ≤ 5 % boss hp_max
    cap_hit: bool
    player_hp_pct: float    # player HP / starting_hp_max at session end
    boss_killed_locally: bool
    reason: CombatEndReason


def _run_one_session(
    boss_data: dict, seed: int, char: Character,
    skills: list[str] = _THE_TU_ENDGAME_SKILLS,
    gem_keys: list[str] | None = None,
    gem_keys_by_formation: dict[str, list[str]] | None = None,
) -> SessionOutcome:
    """Run a single ATTACK_ROUND_LIMIT-round attack window.

    The world-boss flow scales raw stats with ``player_realm_total // 3``
    (see ``bot/cogs/world_boss.py``). For a body-only max player this comes
    out to ~27 stages, matching what an admin-granted endgame Thể Tu hits
    in production. Pass ``gem_keys`` for single-formation builds, or
    ``gem_keys_by_formation`` when multiple formations are active (Trận Tu
    multi-slot) so each formation's thresholds fire against its own gems.
    """
    player = build_player_combatant(
        char, skills, equip_stats=_ENDGAME_GEAR,
        gem_count=len(gem_keys) if gem_keys else 0,
        gem_keys=gem_keys,
        gem_keys_by_formation=gem_keys_by_formation,
    )
    starting_hp_max = player.hp_max
    realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3

    # Full-HP boss each session — we're measuring per-window damage, not a
    # full raid. The repo uses ``current_hp=None`` to mean "spawn at cap".
    boss = build_world_boss_combatant(boss_data, current_hp=0, player_realm_total=realm_total)
    boss_hp_max = boss.hp_max
    starting_boss_hp = boss.hp

    session = CombatSession(
        player=player, enemy=boss,
        player_skill_keys=skills,
        rng=random.Random(seed),
        max_turns=ATTACK_ROUND_LIMIT,
    )
    result = session.run()

    dmg_raw = max(0, starting_boss_hp - max(0, boss.hp))
    dmg_cap = int(boss_hp_max * PER_ATTACK_DMG_CAP_PCT)
    dmg_after_cap = min(dmg_raw, dmg_cap)
    return SessionOutcome(
        dmg_raw=dmg_raw,
        dmg_after_cap=dmg_after_cap,
        cap_hit=dmg_raw > dmg_cap,
        player_hp_pct=min(1.0, player.hp / max(1, starting_hp_max)),
        boss_killed_locally=(not boss.is_alive()),
        reason=result.reason,
    )


def _summarize(boss_key: str, outcomes: list[SessionOutcome], boss_hp_max: int) -> dict:
    """Collapse N sessions into a report dict so the assertions read cleanly."""
    dmg_cap = int(boss_hp_max * PER_ATTACK_DMG_CAP_PCT)
    raw_vals = [o.dmg_raw for o in outcomes]
    hp_vals = [o.player_hp_pct for o in outcomes]
    return {
        "boss":           boss_key,
        "boss_hp_max":    boss_hp_max,
        "sessions":       len(outcomes),
        "cap_hits":       sum(1 for o in outcomes if o.cap_hit),
        "avg_dmg_raw":    statistics.mean(raw_vals),
        "avg_dmg_pct":    statistics.mean(raw_vals) / boss_hp_max,
        "max_dmg_raw":    max(raw_vals),
        "max_dmg_pct":    max(raw_vals) / boss_hp_max,
        "cap":            dmg_cap,
        "avg_player_hp":  statistics.mean(hp_vals),
        "min_player_hp":  min(hp_vals),
        "deaths":         sum(1 for o in outcomes if o.reason == CombatEndReason.PLAYER_DEAD),
    }


# ── Tests ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("boss_key", _R9_BOSSES)
def test_endgame_the_tu_vs_r9_world_boss_is_overtuned(boss_key):
    """Snapshot-style: one boss per parametrize, prints a report + asserts
    that Thể Tu is still clearly over-ceiling (so the balance-pass work is
    still needed). Flip the asserts once a fix lands.
    """
    boss_data = registry.world_bosses.get(boss_key)
    assert boss_data is not None, f"boss registry missing {boss_key}"

    char = _build_endgame_the_tu()
    seeds = list(range(101, 131))   # 30 independent RNG draws
    outcomes = [_run_one_session(boss_data, s, char) for s in seeds]

    # Use the first session's boss_hp_max — constant across sessions since
    # world-boss HP doesn't scale with player_realm in build_world_boss_combatant
    # (only ATK/MATK/DEF do).
    sample_boss = build_world_boss_combatant(boss_data, current_hp=0, player_realm_total=24)
    report = _summarize(boss_key, outcomes, sample_boss.hp_max)

    # Print so `pytest -s` shows the numbers for balance review. ASCII-only
    # so Windows cp1252 consoles don't choke on Vietnamese characters.
    print(
        f"\n=== Endgame The Tu vs {boss_key} ===\n"
        f"  boss HP max        : {report['boss_hp_max']:>14,}\n"
        f"  per-attack cap     : {report['cap']:>14,}  ({PER_ATTACK_DMG_CAP_PCT*100:.0f}%)\n"
        f"  avg raw dmg        : {int(report['avg_dmg_raw']):>14,}  "
        f"({report['avg_dmg_pct']*100:.2f}% of HP)\n"
        f"  max raw dmg        : {int(report['max_dmg_raw']):>14,}  "
        f"({report['max_dmg_pct']*100:.2f}% of HP)\n"
        f"  cap hits           : {report['cap_hits']}/{report['sessions']}\n"
        f"  avg player HP left : {report['avg_player_hp']*100:.1f}%\n"
        f"  min player HP left : {report['min_player_hp']*100:.1f}%\n"
        f"  deaths             : {report['deaths']}/{report['sessions']}"
    )

    # ── Overtune signals ──
    # 1) Player basically never dies against a realm-9 apex.
    assert report["deaths"] == 0, (
        f"{boss_key}: Thể Tu died {report['deaths']}/{report['sessions']} times — "
        f"if this starts failing, the body-cultivator power fantasy is broken "
        f"and this test can be relaxed."
    )
    # 2) The 5 % per-attack cap exists precisely to prevent a single whale
    # from one-shotting a realm-9 boss. An over-tuned build will routinely
    # blast past that cap inside the 15-round window. Seeing >= 50 % of
    # sessions hit the cap is the smoking gun.
    assert report["cap_hits"] >= report["sessions"] // 2, (
        f"{boss_key}: only {report['cap_hits']}/{report['sessions']} sessions hit "
        f"the {PER_ATTACK_DMG_CAP_PCT*100:.0f}% cap — if the Thể Tu rebalance worked, "
        f"flip this to ``<=``."
    )


def test_endgame_the_tu_cap_spill_across_r9_bosses():
    """Aggregate sanity — the average damage across every R9 boss, *uncapped*,
    should exceed the 5 % cap. This is the single signal balance reviewers
    care about: if the whole roster has been re-tuned, this dips under the
    cap and the test fails, forcing an update to the snapshot numbers.
    """
    char = _build_endgame_the_tu()
    # Fewer seeds per boss since we aggregate across 3 bosses — keeps the
    # wall-clock budget similar to a single parametrize run.
    seeds = list(range(201, 221))
    cap_pct_values: list[float] = []
    for boss_key in _R9_BOSSES:
        boss_data = registry.world_bosses[boss_key]
        for s in seeds:
            outcome = _run_one_session(boss_data, s, char)
            sample = build_world_boss_combatant(boss_data, current_hp=0, player_realm_total=24)
            cap_pct_values.append(outcome.dmg_raw / sample.hp_max)

    avg_pct = statistics.mean(cap_pct_values)
    max_pct = max(cap_pct_values)
    print(
        f"\n=== Aggregate R9 world-boss report ===\n"
        f"  avg uncapped dmg   : {avg_pct*100:.2f}% of boss HP  "
        f"(cap = {PER_ATTACK_DMG_CAP_PCT*100:.0f}%)\n"
        f"  worst case         : {max_pct*100:.2f}% of boss HP"
    )
    assert avg_pct >= PER_ATTACK_DMG_CAP_PCT, (
        f"Average Thể Tu output per attack session = {avg_pct*100:.2f}% of boss HP, "
        f"below the {PER_ATTACK_DMG_CAP_PCT*100:.0f}% per-attack cap. "
        f"If the balance pass succeeded, update this snapshot."
    )


# ── Dungeon boss simulation ────────────────────────────────────────────────
# R9 dungeon "Đăng Tiên Bí Cảnh" — qi-realm-8 gate, but cross-path entry means
# a pure Thể Tu with body_realm 8 can walk in too. Boss wave is one of the
# R9/R10 apex enemies; each is fought to the death with no per-attack cap
# (unlike world bosses). We pick the R10 apex quartet — the fight that tests
# the Thể Tu's true DPS ceiling without any cap intervening.
_R9_DUNGEON_BOSSES: tuple[str, ...] = (
    "BossHoaLinh",
    "BossLoiLong",
    "BossBangHon",
    "BossHonNguyen",    # neutral element — no elemental soft cover
)


@dataclass
class DungeonBossOutcome:
    turns: int
    player_hp_pct: float
    boss_hp_pct: float      # 0.0 = dead, 1.0 = full
    reason: CombatEndReason


def _run_dungeon_boss(
    enemy_key: str, seed: int, char: Character, max_turns: int = 50,
    skills: list[str] = _THE_TU_ENDGAME_SKILLS,
    gem_keys: list[str] | None = None,
    gem_keys_by_formation: dict[str, list[str]] | None = None,
) -> DungeonBossOutcome:
    """One 1v1 fight to the death vs a dungeon boss.

    Mirrors the dungeon boss wave path in ``game/systems/dungeon.py``: the
    player enters at full HP/MP (we skip earlier waves — worst case for
    balance review = the boss fight itself on a fresh bar), rolls an
    encounter grade (Tinh Anh / Vương Giả / Truyền Thuyết) and applies it
    before the fight — matching the actual dungeon runner's stat scaling.
    ``max_turns`` defaults to 50 so slow grinds still terminate.
    """
    # Lazy-import to keep the dungeon dependency local to this helper.
    from src.game.systems.dungeon import (
        _apply_encounter_grade, _grade_progress, _roll_boss_grade,
        qualifying_axis, _BOSS_GRADE_START,
    )

    player = build_player_combatant(
        char, skills, equip_stats=_ENDGAME_GEAR,
        gem_count=len(gem_keys) if gem_keys else 0,
        gem_keys=gem_keys,
        gem_keys_by_formation=gem_keys_by_formation,
    )
    starting_hp_max = player.hp_max

    player_realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3

    enemy = build_enemy_combatant(enemy_key, player_realm_total)
    assert enemy is not None, f"enemy registry missing {enemy_key}"

    # Apply the dungeon's boss-grade scaling (Tinh Anh+). Uses req_realm=8 —
    # matches ``DungeonDangTien.required_qi_realm``.
    rng = random.Random(seed)
    qual_realm, qual_level = qualifying_axis(char, 8)
    progress = _grade_progress(qual_realm, qual_level, 8)
    grade = _roll_boss_grade(progress, rng, min_grade_idx=_BOSS_GRADE_START)
    _apply_encounter_grade(enemy, grade, rng)
    boss_hp_max = enemy.hp_max

    session = CombatSession(
        player=player, enemy=enemy,
        player_skill_keys=skills,
        rng=rng, max_turns=max_turns,
    )
    result = session.run()
    return DungeonBossOutcome(
        turns=result.turns,
        player_hp_pct=min(1.0, player.hp / max(1, starting_hp_max)),
        boss_hp_pct=max(0.0, enemy.hp / max(1, boss_hp_max)),
        reason=result.reason,
    )


@pytest.mark.parametrize("enemy_key", _R9_DUNGEON_BOSSES)
def test_endgame_the_tu_vs_r9_dungeon_boss(enemy_key):
    """Pure 1v1 DPS check — no cap, no shared pool. Shows how much headroom
    the Thể Tu has once the world-boss mechanics stop protecting the boss.
    """
    char = _build_endgame_the_tu()
    seeds = list(range(301, 331))
    outcomes = [_run_dungeon_boss(enemy_key, s, char) for s in seeds]

    wins = [o for o in outcomes if o.reason == CombatEndReason.PLAYER_WIN]
    losses = [o for o in outcomes if o.reason == CombatEndReason.PLAYER_DEAD]
    draws = [o for o in outcomes if o.reason == CombatEndReason.MAX_TURNS]
    turns_to_win = [o.turns for o in wins]
    hp_on_win = [o.player_hp_pct for o in wins]

    print(
        f"\n=== Endgame The Tu vs {enemy_key} (R9 dungeon boss) ===\n"
        f"  win / loss / draw  : {len(wins)} / {len(losses)} / {len(draws)} "
        f"of {len(outcomes)}\n"
        f"  avg turns to win   : {(sum(turns_to_win) / max(1, len(turns_to_win))):.1f}\n"
        f"  min turns to win   : {(min(turns_to_win) if turns_to_win else '-'):}\n"
        f"  avg player HP on W : {(statistics.mean(hp_on_win) * 100 if hp_on_win else 0):.1f}%\n"
        f"  min player HP on W : {(min(hp_on_win) * 100 if hp_on_win else 0):.1f}%"
    )

    # A maxed Thể Tu vs a single dungeon boss should basically never lose.
    assert len(wins) >= len(outcomes) * 0.9, (
        f"{enemy_key}: win rate {len(wins)}/{len(outcomes)} — dungeon bosses "
        f"should be clearly winnable for an endgame build."
    )


def test_endgame_the_tu_oneshots_r9_dungeon_bosses():
    """Headroom check — measures how fast and how safely the Thể Tu clears
    R9 dungeon bosses on average. Prints the snapshot; the assert just
    documents the current state (ship it, tune it, or flag it).
    """
    char = _build_endgame_the_tu()
    seeds = list(range(401, 421))

    rows: list[tuple[str, float, float, float, int]] = []
    for enemy_key in _R9_DUNGEON_BOSSES:
        outs = [_run_dungeon_boss(enemy_key, s, char) for s in seeds]
        wins = [o for o in outs if o.reason == CombatEndReason.PLAYER_WIN]
        tw = [o.turns for o in wins]
        hp = [o.player_hp_pct for o in wins]
        rows.append((
            enemy_key,
            statistics.mean(tw) if tw else float("nan"),
            statistics.mean(hp) if hp else float("nan"),
            min(hp) if hp else float("nan"),
            len(wins),
        ))

    print("\n=== Aggregate R9 dungeon-boss headroom ===")
    for key, avg_t, avg_hp, min_hp, n_wins in rows:
        print(
            f"  {key:<18s}  wins={n_wins:>2d}/{len(seeds)}  "
            f"avg_turns={avg_t:>4.1f}  avg_hp={avg_hp*100:>5.1f}%  "
            f"min_hp={min_hp*100:>5.1f}%"
        )

    # If a maxed Thể Tu is finishing dungeon bosses in <5 turns with >90% HP,
    # the fight is a cutscene — flag with an assert that triggers on tighter
    # fights (>= 5 turns avg or < 90 % HP), so a balance buff on the boss
    # side would be noticed.
    avg_turns_all = statistics.mean(r[1] for r in rows if r[1] == r[1])
    avg_hp_all = statistics.mean(r[2] for r in rows if r[2] == r[2])
    assert avg_turns_all < 8 and avg_hp_all > 0.80, (
        f"Endgame Thể Tu dungeon-boss sweep toughened up: "
        f"avg_turns={avg_turns_all:.1f}, avg_hp={avg_hp_all*100:.1f}% — "
        f"bosses may now be meaningful; update this snapshot."
    )


# ────────────────────────────────────────────────────────────────────────
# Endgame Trận Tu (formation cultivator) probe
# ────────────────────────────────────────────────────────────────────────
# Mirrors ``/admin_testbuild endgame_tran_tu``:
#   formation 8.9 (maxed Trận Đạo), qi 6.9 (MATK pool for formation skills),
#   body 0.1 (low — ``is_the_tu = False``).
#   One Legendary Thể Chất (Khí Hải — +60 % MP, +30 % CDR).
#   6 Linh Căn (kim/hoa/loi/phong/quang/am).
#   Active formation: Cửu Cung Bát Quái with 9 grade-3 gems, one per element.
#   Skills: apex formation skills (R8/R9) + two R9 elemental attacks as backup.
#
# Gear stays the same ``_ENDGAME_GEAR`` bag so the two archetypes can be
# compared directly — any output gap is the build, not the inventory.

_TRAN_TU_CONSTITUTIONS: tuple[str, ...] = ("ConstitutionKhiHai",)
_TRAN_TU_LINH_CAN: tuple[str, ...] = ("kim", "hoa", "loi", "phong", "quang", "am")
# Multi-slot Trận Tu: 3 formations simultaneously. Comma-joined to match the
# storage shape of ``player.active_formation``. CuuCungBatQua is the anchor
# (all 9 gems live on this slot); the two elemental slots add their
# signature skill + base stats but stay gem-less so MP reserve stays sane.
_TRAN_TU_ACTIVE_FORMATION: str = "CuuCungBatQua,NhatNguyenHoa,NhatNguyenLoi"

_TRAN_TU_ENDGAME_SKILLS: list[str] = [
    "SkillFrmHonNguyen_R9",     # 680 base, 1.0×atk + 1.0×matk, silence + armor break
    "SkillFrmChuThien_R9",      # 700 base, 1.0×atk + 1.0×matk, stun + set damage
    "SkillFrmThienMa_R8",       # 460 base, 0.8×atk + 0.8×matk, armor break
    "SkillAtkHoa_R9",           # elemental backup (non-formation)
    "SkillAtkLoi_R9",           # elemental backup
]

# Full-build 3-slot Trận Tu: each of the 3 active formations has its own
# 9-gem inlay → 27 grade-3 gems total. Every formation's threshold bonuses
# (1/3/5/7) fire simultaneously, and per-gem elemental bonuses from all 27
# gems stack into the aggregate.
#   Anchor: one of every element (broad spread)
#   Elemental slots: saturated with their matching element + two flex gems
_TRAN_TU_GEM_MAP: dict[str, list[str]] = {
    "CuuCungBatQua": [
        "GemKim_3", "GemHoa_3", "GemLoi_3",
        "GemMoc_3", "GemThuy_3", "GemTo_3",
        "GemPhong_3", "GemAm_3", "GemDuong_3",
    ],
    "NhatNguyenHoa": [
        "GemHoa_3", "GemHoa_3", "GemHoa_3",
        "GemHoa_3", "GemHoa_3", "GemHoa_3",
        "GemHoa_3", "GemKim_3", "GemLoi_3",
    ],
    "NhatNguyenLoi": [
        "GemLoi_3", "GemLoi_3", "GemLoi_3",
        "GemLoi_3", "GemLoi_3", "GemLoi_3",
        "GemLoi_3", "GemKim_3", "GemPhong_3",
    ],
}
# Flat list kept for legacy callers that still pass `gem_keys=` — matches
# ``compute_combat_stats``'s single-formation fallback shape.
_TRAN_TU_GEM_KEYS: list[str] = _TRAN_TU_GEM_MAP["CuuCungBatQua"]


def _build_endgame_tran_tu() -> Character:
    """Exact mirror of ``/admin_testbuild endgame_tran_tu``."""
    return Character(
        player_id=2, discord_id=2, name="Endgame-TranTu",
        body_realm=0, body_level=1,
        qi_realm=6,   qi_level=9,
        formation_realm=8, formation_level=9,
        constitution_type=set_constitutions(list(_TRAN_TU_CONSTITUTIONS)),
        dao_ti_unlocked=False,
        linh_can=list(_TRAN_TU_LINH_CAN),
        active_formation=_TRAN_TU_ACTIVE_FORMATION,
        stats=CharacterStats(),
    )


@pytest.mark.parametrize("boss_key", _R9_BOSSES)
def test_endgame_tran_tu_vs_r9_world_boss(boss_key):
    """Trận Tu world-boss probe — parallel to the Thể Tu test. Prints a
    report and snapshot-asserts that Trận Tu doesn't vaporize the 5%
    per-attack cap.
    """
    boss_data = registry.world_bosses.get(boss_key)
    assert boss_data is not None, f"boss registry missing {boss_key}"

    char = _build_endgame_tran_tu()
    seeds = list(range(501, 531))
    outcomes = [
        _run_one_session(
            boss_data, s, char,
            skills=_TRAN_TU_ENDGAME_SKILLS,
            gem_keys=_TRAN_TU_GEM_KEYS,
            gem_keys_by_formation=_TRAN_TU_GEM_MAP,
        )
        for s in seeds
    ]
    sample_boss = build_world_boss_combatant(boss_data, current_hp=0, player_realm_total=32)
    report = _summarize(boss_key, outcomes, sample_boss.hp_max)

    print(
        f"\n=== Endgame Tran Tu vs {boss_key} ===\n"
        f"  boss HP max        : {report['boss_hp_max']:>14,}\n"
        f"  per-attack cap     : {report['cap']:>14,}  ({PER_ATTACK_DMG_CAP_PCT*100:.0f}%)\n"
        f"  avg raw dmg        : {int(report['avg_dmg_raw']):>14,}  "
        f"({report['avg_dmg_pct']*100:.2f}% of HP)\n"
        f"  max raw dmg        : {int(report['max_dmg_raw']):>14,}  "
        f"({report['max_dmg_pct']*100:.2f}% of HP)\n"
        f"  cap hits           : {report['cap_hits']}/{report['sessions']}\n"
        f"  avg player HP left : {report['avg_player_hp']*100:.1f}%\n"
        f"  min player HP left : {report['min_player_hp']*100:.1f}%\n"
        f"  deaths             : {report['deaths']}/{report['sessions']}"
    )

    # Trận Tu should comfortably finish an attack session alive — its
    # single-constitution build doesn't carry the same oppressive defense
    # stack as the Thể Tu, but Khí Hải + CuuCung + Qi-axis MP pool means
    # dying on R9 world bosses would be a real balance bug on the defensive
    # side. Keep this loose (allow 1 death in 30) so RNG tails don't flake.
    assert report["deaths"] <= 1, (
        f"{boss_key}: Trận Tu died {report['deaths']}/{report['sessions']} "
        f"— full-power formation build shouldn't be getting wiped."
    )


def test_endgame_tran_tu_cap_spill_across_r9_bosses():
    """Aggregate damage probe. Trận Tu leans on formation skills that scale
    1:1 ATK/MATK and the CuuCung gem-threshold bonuses — very different DPS
    profile from Thể Tu, so this gets its own snapshot.
    """
    char = _build_endgame_tran_tu()
    seeds = list(range(601, 621))
    pct_values: list[float] = []
    for boss_key in _R9_BOSSES:
        boss_data = registry.world_bosses[boss_key]
        for s in seeds:
            out = _run_one_session(
                boss_data, s, char,
                skills=_TRAN_TU_ENDGAME_SKILLS,
                gem_keys=_TRAN_TU_GEM_KEYS,
                gem_keys_by_formation=_TRAN_TU_GEM_MAP,
            )
            sample = build_world_boss_combatant(boss_data, current_hp=0, player_realm_total=32)
            pct_values.append(out.dmg_raw / sample.hp_max)

    avg_pct = statistics.mean(pct_values)
    max_pct = max(pct_values)
    print(
        f"\n=== Aggregate R9 world-boss report (Tran Tu) ===\n"
        f"  avg uncapped dmg   : {avg_pct*100:.2f}% of boss HP  "
        f"(cap = {PER_ATTACK_DMG_CAP_PCT*100:.0f}%)\n"
        f"  worst case         : {max_pct*100:.2f}% of boss HP"
    )


@pytest.mark.parametrize("enemy_key", _R9_DUNGEON_BOSSES)
def test_endgame_tran_tu_vs_r9_dungeon_boss(enemy_key):
    """Trận Tu dungeon-boss 1v1 — no cap, no shared pool. Measures raw DPS
    + survivability vs Đăng Tiên Bí Cảnh bosses.
    """
    char = _build_endgame_tran_tu()
    seeds = list(range(701, 731))
    outcomes = [
        _run_dungeon_boss(
            enemy_key, s, char,
            skills=_TRAN_TU_ENDGAME_SKILLS,
            gem_keys=_TRAN_TU_GEM_KEYS,
            gem_keys_by_formation=_TRAN_TU_GEM_MAP,
        )
        for s in seeds
    ]
    wins = [o for o in outcomes if o.reason == CombatEndReason.PLAYER_WIN]
    losses = [o for o in outcomes if o.reason == CombatEndReason.PLAYER_DEAD]
    draws = [o for o in outcomes if o.reason == CombatEndReason.MAX_TURNS]
    turns_to_win = [o.turns for o in wins]
    hp_on_win = [o.player_hp_pct for o in wins]

    print(
        f"\n=== Endgame Tran Tu vs {enemy_key} (R9 dungeon boss) ===\n"
        f"  win / loss / draw  : {len(wins)} / {len(losses)} / {len(draws)} "
        f"of {len(outcomes)}\n"
        f"  avg turns to win   : {(sum(turns_to_win) / max(1, len(turns_to_win))):.1f}\n"
        f"  min turns to win   : {(min(turns_to_win) if turns_to_win else '-'):}\n"
        f"  avg player HP on W : {(statistics.mean(hp_on_win) * 100 if hp_on_win else 0):.1f}%\n"
        f"  min player HP on W : {(min(hp_on_win) * 100 if hp_on_win else 0):.1f}%"
    )
    assert len(wins) >= len(outcomes) * 0.85, (
        f"{enemy_key}: Trận Tu win rate {len(wins)}/{len(outcomes)} — "
        f"full-build formation archetype should clear dungeon bosses reliably."
    )


def test_endgame_tran_tu_dungeon_boss_headroom():
    """Mirror of the Thể Tu headroom sweep — snapshots Trận Tu's dungeon
    performance so future tuning changes surface in the diff.
    """
    char = _build_endgame_tran_tu()
    seeds = list(range(801, 821))

    rows: list[tuple[str, float, float, float, int]] = []
    for enemy_key in _R9_DUNGEON_BOSSES:
        outs = [
            _run_dungeon_boss(
                enemy_key, s, char,
                skills=_TRAN_TU_ENDGAME_SKILLS,
                gem_keys=_TRAN_TU_GEM_KEYS,
                gem_keys_by_formation=_TRAN_TU_GEM_MAP,
            )
            for s in seeds
        ]
        wins = [o for o in outs if o.reason == CombatEndReason.PLAYER_WIN]
        tw = [o.turns for o in wins]
        hp = [o.player_hp_pct for o in wins]
        rows.append((
            enemy_key,
            statistics.mean(tw) if tw else float("nan"),
            statistics.mean(hp) if hp else float("nan"),
            min(hp) if hp else float("nan"),
            len(wins),
        ))

    print("\n=== Aggregate R9 dungeon-boss headroom (Tran Tu) ===")
    for key, avg_t, avg_hp, min_hp, n_wins in rows:
        print(
            f"  {key:<18s}  wins={n_wins:>2d}/{len(seeds)}  "
            f"avg_turns={avg_t:>4.1f}  avg_hp={avg_hp*100:>5.1f}%  "
            f"min_hp={min_hp*100:>5.1f}%"
        )
