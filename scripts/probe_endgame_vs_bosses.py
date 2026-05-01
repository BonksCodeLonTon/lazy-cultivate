"""Balance probe — endgame Thể Tu / Khí Tu / Trận Tu vs themed-dungeon bosses.

Extends ``probe_endgame_vs_r10`` to also cover the dedicated dungeon
bosses introduced for Linh Căn Bí Cảnh and Thần Cốt Địa:

  • R10 normal dungeon boss (BossHonNguyen) — baseline reference
  • Thần Cốt Địa apex (ApexDaoCot_R9) — solo boss with the new
    Đạo Cốt signature skills (heavy debuff stamps)
  • Linh Căn apex bosses (LCKim/Hoa/Loi/Am _Apex_*) — element-tagged
    bosses with the new EnemyLC<elem>_Apex skills + environmental
    pressure baked into the dungeon

Each boss gets the same Truyền Thuyết grade scaling that its dungeon's
boss wave actually rolls in production.

Run:  python -m scripts.probe_endgame_vs_bosses
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


SEEDS = list(range(2001, 2061))   # 60 trials per archetype × per boss
MAX_TURNS = 80
TRUYEN_THUYET_STAT_MULT = 3.50

# Bosses to probe — each entry is (display_label, enemy_key).
# Linh Căn apex picks span all-physical (Kim), all-magical (Hoa/Loi),
# and a debuff-heavy one (Am) so different damage patterns are stressed.
_BOSSES: list[tuple[str, str]] = [
    ("R10 Dungeon (BossHonNguyen)",   "BossHonNguyen"),
    ("Than Cot R9 (Apex DaoCot)",     "ApexDaoCot_R9"),
    ("LC Kim Apex (Hon Kim Dai De)",  "LCKim_Apex_HonKimDaiDe"),
    ("LC Hoa Apex (Thai Duong Tam)",  "LCHoa_Apex_ThaiDuongDanTam"),
    ("LC Loi Apex (Thai So Loi De)",  "LCLoi_Apex_ThaiSoLoiDe"),
    ("LC Am Apex (Diet The Hac De)",  "LCAm_Apex_DietTheHacDe"),
]


# ── Endgame gear (same as probe_endgame_vs_r10) ────────────────────────────

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


# ── Archetype builds (same shapes as the R10 probe) ────────────────────────

def build_the_tu() -> tuple[Character, list[str], dict | None]:
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
             gem_keys_by_formation: dict | None, boss_key: str) -> dict:
    enemy_realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    )

    outcomes = {"win": 0, "lose": 0, "draw": 0}
    win_turns: list[int] = []
    win_hp_pct: list[float] = []
    boss_hp_left_pct: list[float] = []
    raw_dmg_dealt_pct: list[float] = []
    dmg_per_turn: list[float] = []
    end_player_hp_pct: list[float] = []

    gem_keys_flat: list[str] | None = None
    if gem_keys_by_formation is not None and len(gem_keys_by_formation) == 1:
        gem_keys_flat = next(iter(gem_keys_by_formation.values()))

    for seed in SEEDS:
        player = build_player_combatant(
            char, skills, equip_stats=_GEAR,
            gem_count=sum(len(v) for v in (gem_keys_by_formation or {}).values()),
            gem_keys=gem_keys_flat,
            gem_keys_by_formation=gem_keys_by_formation,
        )
        enemy = build_enemy_combatant(boss_key, enemy_realm_total)
        if enemy is None:
            raise RuntimeError(f"Missing boss enemy: {boss_key}")
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
        "min_end_hp_pct": min(end_player_hp_pct),
        "avg_dmg_per_turn": statistics.mean(dmg_per_turn),
        "avg_end_hp_pct": statistics.mean(end_player_hp_pct),
        "avg_boss_hp_left_on_loss_pct": (
            statistics.mean(boss_hp_left_pct) if boss_hp_left_pct else None
        ),
        "boss_hp_max": starting_boss_hp,
        "player_hp_max": starting_player_hp,
    }


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    archetypes = [
        ("The Tu",  build_the_tu),
        ("Khi Tu",  build_khi_tu),
        ("Tran Tu", build_tran_tu),
    ]
    chars = [(n, *fac()) for n, fac in archetypes]

    for label, boss_key in _BOSSES:
        print(f"\n=== {label}  ({len(SEEDS)} seeds × 3 archetypes) ===")
        reports = [
            simulate(n, char, skills, gem_map, boss_key)
            for n, char, skills, gem_map in chars
        ]
        print(f"  boss reference HP (Truyen Thuyet apex): {reports[0]['boss_hp_max']:,}")
        print(f"  {'archetype':<9} {'win%':>5} {'turns(min/avg/max)':>20} "
              f"{'dmg/turn':>12} {'final_hp%(min/avg)':>20} "
              f"{'boss_hp%@loss':>14}")
        print("  " + "-" * 90)
        for r in reports:
            turns_label = (
                f"{r['min_win_turns']}/{r['avg_win_turns']:.1f}/{r['max_win_turns']}"
                if r['avg_win_turns'] is not None else "-/-/-"
            )
            hp_label = f"{r['min_end_hp_pct']*100:.0f}%/{r['avg_end_hp_pct']*100:.0f}%"
            boss_left = (
                f"{r['avg_boss_hp_left_on_loss_pct']*100:.0f}%"
                if r['avg_boss_hp_left_on_loss_pct'] is not None else "-"
            )
            print(
                f"  {r['name']:<9} {r['win_rate']*100:>4.0f}% "
                f"{turns_label:>20} "
                f"{int(r['avg_dmg_per_turn']):>12,} "
                f"{hp_label:>20} {boss_left:>14}"
            )


if __name__ == "__main__":
    main()
