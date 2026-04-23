"""Dungeon (Bí Cảnh) system — multi-wave sequential combat."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from src.data.registry import registry
from src.game.constants.balance import (
    ENCOUNTER_GRADES,
    ENCOUNTER_GRADE_SECONDARY_STATS,
)
from src.game.constants.realms import QI_REALMS
from src.game.models.character import Character
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combatant import Combatant

# Boss wave uses grades from this index onward (Tinh Anh = index 2).
_BOSS_GRADE_START = 2


@dataclass
class DungeonResult:
    success: bool
    waves_cleared: int
    total_waves: int
    loot: list[dict] = field(default_factory=list)
    merit_gained: int = 0
    stone_gained: int = 0
    log: list[str] = field(default_factory=list)
    died_on: str | None = None
    hp_remaining: int = 0


def check_can_enter(char: Character, dungeon_key: str) -> tuple[bool, str]:
    dungeon = registry.get_dungeon(dungeon_key)
    if not dungeon:
        return False, "Bí cảnh không tồn tại."
    req = dungeon.get("required_qi_realm", 0)
    if char.qi_realm < req:
        req_label = QI_REALMS[req].vi if req < len(QI_REALMS) else f"Realm {req}"
        return False, f"Cần đạt **{req_label}** Luyện Khí để vào bí cảnh này."
    return True, ""


def _grade_progress(player_qi_realm: int, player_qi_level: int, dungeon_req_realm: int) -> float:
    """0.0 = just entered dungeon realm; 1.0 = max level or beyond."""
    if player_qi_realm > dungeon_req_realm:
        return 1.0
    return (player_qi_level - 1) / 8.0


def _roll_encounter_grade(progress: float, rng: random.Random) -> dict:
    """Weighted random grade from all tiers based on player progress."""
    weights = [
        g["w_min"] + (g["w_max"] - g["w_min"]) * progress
        for g in ENCOUNTER_GRADES
    ]
    total = sum(weights)
    pick = rng.uniform(0, total)
    cumulative = 0.0
    for grade, w in zip(ENCOUNTER_GRADES, weights):
        cumulative += w
        if pick <= cumulative:
            return grade
    return ENCOUNTER_GRADES[0]


def _roll_boss_grade(progress: float, rng: random.Random) -> dict:
    """Weighted random grade for the boss wave — minimum Tinh Anh."""
    boss_grades = ENCOUNTER_GRADES[_BOSS_GRADE_START:]
    weights = [
        g["w_min"] + (g["w_max"] - g["w_min"]) * progress
        for g in boss_grades
    ]
    total = sum(weights)
    pick = rng.uniform(0, total)
    cumulative = 0.0
    for grade, w in zip(boss_grades, weights):
        cumulative += w
        if pick <= cumulative:
            return grade
    return boss_grades[0]


def _apply_encounter_grade(enemy_c: Combatant, grade: dict, rng: random.Random) -> None:
    """Scale enemy stats by grade multiplier then apply random secondary buffs."""
    mult = grade["stat_mult"]
    enemy_c.hp = int(enemy_c.hp * mult)
    enemy_c.hp_max = int(enemy_c.hp_max * mult)
    enemy_c.atk = int(enemy_c.atk * mult)
    enemy_c.matk = int(enemy_c.matk * mult)
    enemy_c.def_stat = int(enemy_c.def_stat * mult)

    count = grade["secondary_count"]
    if count == 0:
        return

    lo, hi = grade["secondary_lo"], grade["secondary_hi"]
    chosen = rng.sample(ENCOUNTER_GRADE_SECONDARY_STATS, min(count, len(ENCOUNTER_GRADE_SECONDARY_STATS)))
    for stat in chosen:
        bonus = rng.uniform(lo, hi)
        if stat == "hp":
            extra = int(enemy_c.hp * bonus)
            enemy_c.hp += extra
            enemy_c.hp_max += extra
        elif stat == "atk":
            enemy_c.atk = int(enemy_c.atk * (1 + bonus))
        elif stat == "matk":
            enemy_c.matk = int(enemy_c.matk * (1 + bonus))
        elif stat == "def_stat":
            enemy_c.def_stat = int(enemy_c.def_stat * (1 + bonus))
        elif stat == "evasion_rating":
            enemy_c.evasion_rating = int(enemy_c.evasion_rating * (1 + bonus)) + int(bonus * 100)


def _build_wave_list(dungeon: dict, rng: random.Random) -> list[str]:
    """Return enemy_keys for all waves; all picks are random from enemy_pool."""
    enemy_pool = dungeon.get("enemy_pool", [])
    wave_count = dungeon.get("wave_count", 3)

    # Backward compat: old fixed enemy_keys format
    if not enemy_pool and "enemy_keys" in dungeon:
        return list(dungeon["enemy_keys"])

    if not enemy_pool:
        return []
    return rng.choices(enemy_pool, k=wave_count)


def run_dungeon(
    char: Character,
    dungeon_key: str,
    skill_keys: list[str],
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
) -> DungeonResult:
    """Run all waves of a dungeon sequentially.

    All enemies (including the last/boss wave) are randomly drawn from enemy_pool.
    The last wave always receives a minimum Tinh Anh encounter grade.
    Grade probability shifts toward rarer tiers as the player advances through
    the dungeon's required realm.

    Player recovers 50% HP and 33% MP between waves.
    """
    dungeon = registry.get_dungeon(dungeon_key)
    if not dungeon:
        return DungeonResult(success=False, waves_cleared=0, total_waves=0)

    rng = random.Random()
    player_c = build_player_combatant(
        char, skill_keys, gem_count, equip_stats=equip_stats, gem_keys=gem_keys,
    )
    player_realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3

    req_realm = dungeon.get("required_qi_realm", 0)
    progress = _grade_progress(char.qi_realm, char.qi_level, req_realm)
    wave_enemies = _build_wave_list(dungeon, rng)
    total_waves = len(wave_enemies)

    all_loot: list[dict] = []
    all_logs: list[str] = []
    merit_total = 0

    for i, enemy_key in enumerate(wave_enemies):
        is_boss = (i == total_waves - 1)
        _edata = registry.get_enemy(enemy_key)

        grade = _roll_boss_grade(progress, rng) if is_boss else _roll_encounter_grade(progress, rng)
        grade_badge = f" {grade['emoji']} **{grade['vi']}**" if grade["emoji"] else ""

        if is_boss:
            wave_label = (f"👑 Boss: **{_edata['vi']}**{grade_badge}" if _edata else f"👑 Boss{grade_badge}")
        else:
            enemy_name = f"**{_edata['vi']}**" if _edata else enemy_key
            wave_label = f"Đợt {i + 1}: {enemy_name}{grade_badge}"

        all_logs.append(f"\n{'═' * 20}")
        all_logs.append(f"⚔️ {wave_label}")

        enemy_c = build_enemy_combatant(enemy_key, player_realm_total)
        if not enemy_c:
            continue

        _apply_encounter_grade(enemy_c, grade, rng)

        if i > 0:
            recovered_hp = player_c.hp_max // 2
            player_c.hp = min(player_c.hp_max, player_c.hp + recovered_hp)
            player_c.mp = min(player_c.mp_max, player_c.mp + player_c.mp_max // 3)
            all_logs.append(f"💚 Hồi phục: +{recovered_hp} HP | ❤️ {player_c.hp}/{player_c.hp_max}")

        session = CombatSession(
            player=player_c,
            enemy=enemy_c,
            player_skill_keys=skill_keys,
            rng=rng,
            loot_qty_multiplier=grade["loot_mult"],
            loot_luck_pct=grade.get("luck_pct", 0.0),
        )
        result = session.run()
        all_logs.extend(result.log)
        all_loot.extend(result.loot)
        merit_total += int(result.merit_gained * grade["merit_mult"])

        if result.reason == CombatEndReason.PLAYER_DEAD:
            return DungeonResult(
                success=False,
                waves_cleared=i,
                total_waves=total_waves,
                loot=all_loot,
                merit_gained=merit_total,
                stone_gained=0,
                log=all_logs,
                died_on=_edata["vi"] if _edata else enemy_key,
                hp_remaining=0,
            )

    merit_total += dungeon.get("merit_reward", 0)
    stone_gained = dungeon.get("stone_reward", 0)

    return DungeonResult(
        success=True,
        waves_cleared=total_waves,
        total_waves=total_waves,
        loot=all_loot,
        merit_gained=merit_total,
        stone_gained=stone_gained,
        log=all_logs,
        died_on=None,
        hp_remaining=player_c.hp,
    )
