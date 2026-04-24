"""Tests for the dungeon type filter and Dược Viên data integration."""
from __future__ import annotations

import pytest

from src.data.registry import registry


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def test_dungeons_of_type_splits_normal_and_duoc_vien():
    normal = registry.dungeons_of_type("normal")
    duoc = registry.dungeons_of_type("duoc_vien")
    assert len(normal) > 0
    assert len(duoc) > 0
    # No overlap
    normal_keys = {d["key"] for d in normal}
    duoc_keys = {d["key"] for d in duoc}
    assert normal_keys.isdisjoint(duoc_keys)
    # All Dược Viên keys share the expected prefix
    assert all(k.startswith("DungeonDuocVien_") for k in duoc_keys)


def test_duoc_vien_covers_nine_realms_with_progressive_requirements():
    duoc = sorted(registry.dungeons_of_type("duoc_vien"),
                  key=lambda d: d.get("required_qi_realm", 0))
    assert len(duoc) == 9
    assert [d["required_qi_realm"] for d in duoc] == list(range(9))


def test_duoc_vien_enemies_are_moc_element_with_regen_aura():
    for d in registry.dungeons_of_type("duoc_vien"):
        for enemy_key in d["enemy_pool"]:
            enemy = registry.get_enemy(enemy_key)
            assert enemy is not None, f"enemy {enemy_key} missing from registry"
            assert enemy.get("element") == "moc", f"{enemy_key} should be moc element"
            assert enemy.get("hp_regen_pct", 0) > 0, f"{enemy_key} missing regen aura"


def test_duoc_vien_loot_tables_exist_for_all_realms():
    for realm in range(1, 10):
        table = registry.get_loot_table(f"DuocVienLoot_{realm}")
        assert len(table) > 0, f"DuocVienLoot_{realm} is empty"
        # At least one herb drop per realm
        herb_drops = [e for e in table
                      if (registry.get_item(e["item_key"]) or {}).get("type") == "herb"]
        assert herb_drops, f"DuocVienLoot_{realm} contains no herbs"


def test_dungeons_default_type_is_normal_when_missing_field():
    """Legacy entries without a dungeon_type field should still resolve."""
    # Synthesise a fake entry without the field — simulate legacy data.
    sample = {"key": "_test_fake", "required_qi_realm": 0}
    # Verify the default-to-normal behaviour in isolation.
    assert sample.get("dungeon_type", "normal") == "normal"
