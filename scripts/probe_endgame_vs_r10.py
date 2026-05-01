"""Balance probe — endgame Thể Tu / Khí Tu / Trận Tu vs the R10 dungeon boss.

Mirrors the three ``/admin_testbuild`` endgame presets, gives each archetype
the same end-game gear (``test_realm_progression._realm_gear(10)``), and
runs N seeded fights vs ``BossHonNguyen`` (the realm_level=10 apex dungeon
boss in ``data/enemies/normal/realm_10.json``).

Run with:  python -m scripts.probe_endgame_vs_r10
"""
from __future__ import annotations

import random
import statistics

from src.data.registry import registry
from src.game.constants.linh_can import ALL_LINH_CAN
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatEndReason, CombatSession,
    build_enemy_combatant, build_player_combatant,
)
from src.game.systems.cultivation import set_active_formations
from src.game.systems.the_chat import set_constitutions


# ── Shared config ──────────────────────────────────────────────────────────

BOSS_KEY = "BossHonNguyen"   # R10 chí tôn, neutral element, 42k base HP, 2.2× hp_scale
SEEDS = list(range(2001, 2061))   # 60 trials per archetype
MAX_TURNS = 80                    # gives slow Tran Tu builds enough time

# Truyen Thuyet grade scaling — what the realm-10 dungeon's boss wave actually
# rolls in production. Applied AFTER build_enemy_combatant so the comparison
# matches what a player would face when entering DungeonDangTien at qi_realm=8.
TRUYEN_THUYET_STAT_MULT = 3.50

# End-game gear bundle (tier 5 from test_realm_progression._realm_gear(10)).
_GEAR = {
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

_LEGENDARY_EIGHT = (
    "ConstitutionPhaTien", "ConstitutionHauTo", "ConstitutionNgheDinh",
    "ConstitutionTieuDao", "ConstitutionKhiHai", "ConstitutionBatDiet",
    "ConstitutionTranGioi", "ConstitutionThaiCucKim",
)


# ── Archetype builds ───────────────────────────────────────────────────────

def build_the_tu() -> tuple[Character, list[str], dict | None]:
    """Body 8/9 + 8 Legendaries + Hỗn Độn + every Linh Căn at Lv1."""
    char = Character(
        player_id=1, discord_id=1, name="Endgame-TheTu",
        body_realm=8, body_level=9,
        qi_realm=0,   qi_level=1,
        formation_realm=0, formation_level=1,
        constitution_type=set_constitutions([*_LEGENDARY_EIGHT, "ConstitutionHonDon"]),
        dao_ti_unlocked=True,
        linh_can=list(ALL_LINH_CAN),
        linh_can_levels={elem: 1 for elem in ALL_LINH_CAN},
        stats=CharacterStats(),
    )
    skills = [
        "SkillAtkKim_R9", "SkillKimBloodbath_R9",
        "SkillAtkTho_R9", "SkillThoEarthTower_R9",
        "SkillAtkLoi_R9", "SkillDefTo",
    ]
    return char, skills, None


def build_khi_tu() -> tuple[Character, list[str], dict | None]:
    """Qi 8/9 + 9 Linh Căn at Lv9 + Phá Thiên — wide elemental coverage."""
    char = Character(
        player_id=2, discord_id=2, name="Endgame-KhiTu",
        body_realm=1, body_level=1,
        qi_realm=8,   qi_level=9,
        formation_realm=4, formation_level=5,
        constitution_type=set_constitutions(["ConstitutionPhaTien"]),
        dao_ti_unlocked=False,
        linh_can=list(ALL_LINH_CAN),
        linh_can_levels={elem: 9 for elem in ALL_LINH_CAN},
        stats=CharacterStats(),
    )
    skills = [
        "SkillAtkKim_R8", "SkillAtkHoa_R8", "SkillAtkLoi_R8",
        "SkillAtkPhong_R8", "SkillAtkQuang_R8", "SkillAtkAm_R8",
    ]
    return char, skills, None


def build_tran_tu() -> tuple[Character, list[str], dict | None]:
    """Formation 8/9 + 3 active formations + Khí Hải. Multi-slot Trận Tu rule."""
    char = Character(
        player_id=3, discord_id=3, name="Endgame-TranTu",
        body_realm=0, body_level=1,
        qi_realm=6,   qi_level=9,
        formation_realm=8, formation_level=9,
        constitution_type=set_constitutions(["ConstitutionKhiHai"]),
        dao_ti_unlocked=False,
        linh_can=["kim", "hoa", "loi", "phong", "quang", "am"],
        linh_can_levels={"kim": 7, "hoa": 7, "loi": 7, "phong": 7, "quang": 7, "am": 7},
        active_formation=set_active_formations(
            ["CuuCungBatQua", "NhatNguyenHoa", "NhatNguyenLoi"],
        ),
        stats=CharacterStats(),
    )
    skills = [
        "SkillFrmHonNguyen_R9", "SkillFrmChuThien_R9", "SkillFrmThienMa_R8",
        "SkillAtkHoa_R9", "SkillAtkLoi_R9",
    ]
    # Trận Tu carries gem inlays on every active formation.
    gem_keys_by_formation = {
        "CuuCungBatQua": [
            "GemKim_3", "GemHoa_3", "GemLoi_3", "GemMoc_3", "GemThuy_3",
            "GemTo_3", "GemPhong_3", "GemAm_3", "GemDuong_3",
        ],
        "NhatNguyenHoa": ["GemHoa_3"] * 7 + ["GemKim_3", "GemLoi_3"],
        "NhatNguyenLoi": ["GemLoi_3"] * 7 + ["GemKim_3", "GemPhong_3"],
    }
    return char, skills, gem_keys_by_formation


# ── Simulator ──────────────────────────────────────────────────────────────

def simulate(name: str, char: Character, skills: list[str],
             gem_keys_by_formation: dict | None) -> dict:
    """Run SEEDS-many seeded fights vs BOSS_KEY and collapse to summary."""
    enemy_realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    )

    outcomes = {"win": 0, "lose": 0, "draw": 0}
    win_turns: list[int] = []
    win_hp_pct: list[float] = []
    boss_hp_left_pct: list[float] = []   # only filled on lose/draw
    raw_dmg_dealt_pct: list[float] = []
    dmg_per_turn: list[float] = []
    end_player_hp_pct: list[float] = []

    gem_keys_flat: list[str] | None = None
    if gem_keys_by_formation is not None and len(gem_keys_by_formation) == 1:
        # Single-formation flow allows the flat path.
        gem_keys_flat = next(iter(gem_keys_by_formation.values()))

    for seed in SEEDS:
        player = build_player_combatant(
            char, skills, equip_stats=_GEAR,
            gem_count=sum(len(v) for v in (gem_keys_by_formation or {}).values()),
            gem_keys=gem_keys_flat,
            gem_keys_by_formation=gem_keys_by_formation,
        )
        enemy = build_enemy_combatant(BOSS_KEY, enemy_realm_total)
        if enemy is None:
            raise RuntimeError(f"Missing boss enemy: {BOSS_KEY}")
        # Apply Truyen Thuyet (apex) grade scaling — the actual stat block
        # an endgame R10 boss wave rolls. 3.5x HP/ATK/MATK/DEF.
        enemy.hp = int(enemy.hp * TRUYEN_THUYET_STAT_MULT)
        enemy.hp_max = int(enemy.hp_max * TRUYEN_THUYET_STAT_MULT)
        enemy.atk = int(enemy.atk * TRUYEN_THUYET_STAT_MULT)
        enemy.matk = int(enemy.matk * TRUYEN_THUYET_STAT_MULT)
        enemy.def_stat = int(enemy.def_stat * TRUYEN_THUYET_STAT_MULT)
        starting_boss_hp = enemy.hp_max
        starting_player_hp = player.hp_max

        session = CombatSession(
            player=player, enemy=enemy,
            player_skill_keys=skills,
            rng=random.Random(seed), max_turns=MAX_TURNS,
        )
        result = session.run()

        dmg_dealt = max(0, starting_boss_hp - max(0, enemy.hp))
        raw_dmg_dealt_pct.append(dmg_dealt / starting_boss_hp)
        dmg_per_turn.append(dmg_dealt / max(1, result.turns))
        end_player_hp_pct.append(max(0.0, player.hp / max(1, starting_player_hp)))

        if result.reason == CombatEndReason.PLAYER_WIN:
            outcomes["win"] += 1
            win_turns.append(result.turns)
            win_hp_pct.append(min(1.0, player.hp / max(1, starting_player_hp)))
        elif result.reason == CombatEndReason.PLAYER_DEAD:
            outcomes["lose"] += 1
            boss_hp_left_pct.append(max(0.0, enemy.hp / starting_boss_hp))
        else:
            outcomes["draw"] += 1
            boss_hp_left_pct.append(max(0.0, enemy.hp / starting_boss_hp))

    return {
        "name": name,
        "outcomes": outcomes,
        "win_rate": outcomes["win"] / len(SEEDS),
        "avg_win_turns": statistics.mean(win_turns) if win_turns else None,
        "min_win_turns": min(win_turns) if win_turns else None,
        "max_win_turns": max(win_turns) if win_turns else None,
        "avg_win_hp_pct": statistics.mean(win_hp_pct) if win_hp_pct else None,
        "min_end_hp_pct": min(end_player_hp_pct),
        "avg_dmg_dealt_pct": statistics.mean(raw_dmg_dealt_pct),
        "avg_dmg_per_turn": statistics.mean(dmg_per_turn),
        "avg_end_hp_pct": statistics.mean(end_player_hp_pct),
        "avg_boss_hp_left_on_loss_pct": (
            statistics.mean(boss_hp_left_pct) if boss_hp_left_pct else None
        ),
        "boss_hp_max": starting_boss_hp,
        "player_hp_max": starting_player_hp,
    }


def main() -> None:
    print(f"Probe: 3 endgame archetypes vs {BOSS_KEY} ({len(SEEDS)} seeds each)\n")
    reports: list[dict] = []
    # ASCII-only labels so Windows cp1252 consoles don't choke on diacritics.
    for name, factory in (
        ("The Tu",  build_the_tu),
        ("Khi Tu",  build_khi_tu),
        ("Tran Tu", build_tran_tu),
    ):
        char, skills, gem_map = factory()
        reports.append(simulate(name, char, skills, gem_map))

    # Comparison table — ASCII-only so it prints on Windows cp1252.
    print(f"Boss reference HP (Truyen Thuyet apex): "
          f"{reports[0]['boss_hp_max']:,}\n")
    print(f"{'archetype':<10} {'win%':>5} {'turns(min/avg/max)':>20} "
          f"{'dmg/turn':>12} {'final_hp%(min/avg)':>20} {'player_hp_max':>15}")
    print("-" * 92)
    for r in reports:
        turns_label = (
            f"{r['min_win_turns']}/{r['avg_win_turns']:.1f}/{r['max_win_turns']}"
            if r['avg_win_turns'] is not None else "-"
        )
        hp_label = f"{r['min_end_hp_pct']*100:.0f}%/{r['avg_end_hp_pct']*100:.0f}%"
        print(
            f"{r['name']:<10} {r['win_rate']*100:>4.0f}% "
            f"{turns_label:>20} "
            f"{int(r['avg_dmg_per_turn']):>12,} "
            f"{hp_label:>20} "
            f"{r['player_hp_max']:>15,}"
        )


if __name__ == "__main__":
    main()
