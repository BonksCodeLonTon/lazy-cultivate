"""Data-integrity tests for the Thập Vạn Đại Sơn dungeon family."""
from __future__ import annotations

import pytest

from src.data.registry import registry


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _tvds_dungeons() -> list[dict]:
    return sorted(
        registry.dungeons_of_type("thap_van_dai_son"),
        key=lambda d: d.get("required_qi_realm", 0),
    )


def test_five_tiers_with_progressive_requirements():
    dungeons = _tvds_dungeons()
    assert len(dungeons) == 5
    assert [d["required_qi_realm"] for d in dungeons] == [0, 2, 4, 6, 8]
    assert all(d["key"].startswith("DungeonTVDS_") for d in dungeons)


def test_enemy_pools_resolve_with_valid_skills_and_loot():
    for d in _tvds_dungeons():
        assert d["enemy_pool"], f"{d['key']} has an empty pool"
        for enemy_key in d["enemy_pool"]:
            enemy = registry.get_enemy(enemy_key)
            assert enemy is not None, f"{enemy_key} missing from registry"
            for sk in enemy["skill_keys"]:
                assert registry.get_skill(sk) is not None, (
                    f"{enemy_key} references unknown skill {sk}"
                )
            table_key = enemy.get("loot_table_key")
            assert table_key, f"{enemy_key} missing loot_table_key"
            table = registry.get_loot_table(table_key)
            assert table, f"loot table {table_key} is empty"
            for entry in table:
                assert registry.get_item(entry["item_key"]) is not None, (
                    f"{table_key} drops unknown item {entry['item_key']}"
                )


def test_every_beast_drops_a_vital_essence():
    for d in _tvds_dungeons():
        for enemy_key in d["enemy_pool"]:
            enemy = registry.get_enemy(enemy_key)
            table = registry.get_loot_table(enemy["loot_table_key"])
            essence_drops = [
                e for e in table
                if (registry.get_item(e["item_key"]) or {}).get("type") == "vital_essence"
            ]
            assert essence_drops, f"{enemy_key} drops no vital essence"


def test_mythic_beasts_only_from_tier_five_up():
    for d in _tvds_dungeons():
        req = d["required_qi_realm"]
        for enemy_key in d["enemy_pool"]:
            enemy = registry.get_enemy(enemy_key)
            if enemy.get("beast_type") == "than_thu":
                assert req >= 4, f"mythic {enemy_key} appears below tier 5"


def test_mythic_essences_drop_only_from_mythic_beasts():
    mythic_items = {
        i["key"] for i in registry.items.values()
        if i.get("type") == "vital_essence" and i.get("essence_tier") == "mythic"
    }
    assert len(mythic_items) == 4
    for d in _tvds_dungeons():
        for enemy_key in d["enemy_pool"]:
            enemy = registry.get_enemy(enemy_key)
            table = registry.get_loot_table(enemy["loot_table_key"])
            drops_mythic = any(e["item_key"] in mythic_items for e in table)
            if enemy.get("beast_type") == "than_thu":
                assert drops_mythic, f"mythic beast {enemy_key} drops no mythic essence"
            else:
                assert not drops_mythic, f"normal beast {enemy_key} drops a mythic essence"


def test_beast_drops_match_tier_rarity_ladder():
    """R1→normal, R3→magic, R5→rare, R7/R9→legendary (mythic kits excepted)."""
    expected_by_req = {0: "normal", 2: "magic", 4: "rare", 6: "legendary", 8: "legendary"}
    for d in _tvds_dungeons():
        expected = expected_by_req[d["required_qi_realm"]]
        for enemy_key in d["enemy_pool"]:
            enemy = registry.get_enemy(enemy_key)
            table = registry.get_loot_table(enemy["loot_table_key"])
            for entry in table:
                item = registry.get_item(entry["item_key"])
                if item.get("type") != "vital_essence":
                    continue
                if item.get("essence_tier") == "mythic":
                    continue  # divine-beast signature drop, tier-independent
                assert item["essence_tier"] == expected, (
                    f"{enemy_key} drops {item['key']} ({item['essence_tier']}) "
                    f"but its zone should drop {expected}"
                )


def test_all_nine_elements_represented():
    elements = set()
    for d in _tvds_dungeons():
        for enemy_key in d["enemy_pool"]:
            elements.add(registry.get_enemy(enemy_key).get("element"))
    assert elements == {
        "kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am",
    }


def test_essences_never_drop_on_auto_repeat_runs():
    """AFK farming must not feed body progression — mirrors the Constitution
    Process "trash grade" rule. Manual runs still drop essences."""
    import random
    from src.game.models.character import Character
    from src.game.systems.combat import (
        CombatSession, build_enemy_combatant, build_player_combatant,
    )

    char = Character(player_id=1, discord_id=1, name="t",
                     body_realm=8, qi_realm=8, active_axis="body",
                     constitution_type="")

    def _drops(auto: bool, seeds: range) -> set[str]:
        out: set[str] = set()
        for seed in seeds:
            player_c = build_player_combatant(char, ["SkillAtkKim3"])
            enemy_c = build_enemy_combatant("BeastManhHo_R1", 10)
            session = CombatSession(
                player=player_c, enemy=enemy_c,
                player_skill_keys=["SkillAtkKim3"],
                rng=random.Random(seed), auto_mode=auto,
            )
            for entry in session._roll_loot():
                out.add(entry["item_key"])
        return out

    auto_items = _drops(auto=True, seeds=range(300))
    for key in auto_items:
        assert (registry.get_item(key) or {}).get("type") != "vital_essence", (
            f"auto-repeat run dropped essence {key}"
        )
    manual_items = _drops(auto=False, seeds=range(300))
    assert any(
        (registry.get_item(k) or {}).get("type") == "vital_essence"
        for k in manual_items
    ), "manual runs should still drop essences"


def test_no_key_collision_with_other_dungeon_families():
    tvds_keys = {d["key"] for d in _tvds_dungeons()}
    for family in ("normal", "duoc_vien", "the_chat", "linh_can", "cam_dia"):
        other = {d["key"] for d in registry.dungeons_of_type(family)}
        assert tvds_keys.isdisjoint(other)
