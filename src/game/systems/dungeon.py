"""Dungeon (Bí Cảnh) system — multi-wave sequential combat."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from src.data.registry import registry
from src.game.constants.realms import QI_REALMS
from src.game.models.character import Character
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)


@dataclass
class DungeonResult:
    success: bool
    waves_cleared: int
    total_waves: int
    loot: list[dict] = field(default_factory=list)
    merit_gained: int = 0
    stone_gained: int = 0
    log: list[str] = field(default_factory=list)
    died_on: str | None = None  # enemy name that killed the player
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


def run_dungeon(
    char: Character,
    dungeon_key: str,
    skill_keys: list[str],
    gem_count: int = 0,
) -> DungeonResult:
    """Run all waves of a dungeon sequentially.

    Player recovers 50% HP and 33% MP between waves.
    If the player dies during any wave the run ends immediately.
    """
    dungeon = registry.get_dungeon(dungeon_key)
    if not dungeon:
        return DungeonResult(success=False, waves_cleared=0, total_waves=0)

    player_c = build_player_combatant(char, skill_keys, gem_count)
    player_realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3

    all_loot: list[dict] = []
    all_logs: list[str] = []
    merit_total = 0
    boss_key = dungeon.get("boss_key")

    enemy_keys: list[str] = dungeon.get("enemy_keys", [])
    total_waves = len(enemy_keys)

    for i, enemy_key in enumerate(enemy_keys):
        is_boss = enemy_key == boss_key
        _edata = registry.get_enemy(enemy_key)
        wave_label = (f"👑 Boss: **{_edata['vi']}**" if is_boss else f"Đợt {i + 1}") if _edata else f"Đợt {i + 1}"
        all_logs.append(f"\n{'═' * 20}")
        all_logs.append(f"⚔️ **{wave_label}**")

        enemy_c = build_enemy_combatant(enemy_key, player_realm_total)
        if not enemy_c:
            continue

        # Partial restore between waves (not before first wave)
        if i > 0:
            player_c.hp = min(player_c.hp_max, player_c.hp + player_c.hp_max // 2)
            player_c.mp = min(player_c.mp_max, player_c.mp + player_c.mp_max // 3)
            all_logs.append(f"💚 Hồi phục: +{player_c.hp_max // 2} HP | ❤️ {player_c.hp}/{player_c.hp_max}")

        session = CombatSession(
            player=player_c,
            enemy=enemy_c,
            player_skill_keys=skill_keys,
            rng=random.Random(),
        )
        result = session.run()
        all_logs.extend(result.log)
        all_loot.extend(result.loot)
        merit_total += result.merit_gained

        if result.reason == CombatEndReason.PLAYER_DEAD:
            enemy_data = registry.get_enemy(enemy_key)
            return DungeonResult(
                success=False,
                waves_cleared=i,
                total_waves=total_waves,
                loot=all_loot,
                merit_gained=merit_total,
                stone_gained=0,
                log=all_logs,
                died_on=enemy_data["vi"] if enemy_data else enemy_key,
                hp_remaining=0,
            )

    # All waves cleared
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
