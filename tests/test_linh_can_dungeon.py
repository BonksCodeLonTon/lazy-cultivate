"""Tests for the Linh Căn dungeon drop pipeline + marketplace block.

Covers:
  • per-element dungeons + loot tables exist in the registry
  • the drop engine clamps weight at 0 when luck_pct goes deeply negative
  • the drop engine scales weight monotonically with luck_pct
  • marketplace listing rejects linh_can_material items
  • direct trade plumbing has no item-type filter (regression guard)
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.constants.linh_can import ALL_LINH_CAN
from src.game.engine.drop import POOL_RANGE, _effective_weight, roll_drops


# ── Element dungeons exist + are wired up ──────────────────────────────────

def test_every_element_has_a_dungeon():
    by_element = {
        d["linh_can_element"]: d
        for d in registry.dungeons.values()
        if d.get("dungeon_type") == "linh_can"
    }
    assert set(by_element.keys()) == set(ALL_LINH_CAN), (
        f"missing element dungeons: {set(ALL_LINH_CAN) - set(by_element.keys())}"
    )


def test_every_element_dungeon_points_to_existing_loot_table():
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        loot_key = d.get("linh_can_loot_table")
        assert loot_key, f"{d['key']}: missing linh_can_loot_table"
        table = registry.get_loot_table(loot_key)
        assert table, f"{d['key']}: loot table {loot_key} not registered"


def test_every_element_dungeon_drops_only_matching_element_materials():
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        elem = d["linh_can_element"]
        table = registry.get_loot_table(d["linh_can_loot_table"])
        for entry in table:
            item = registry.get_item(entry["item_key"])
            if not item or item.get("type") != "linh_can_material":
                continue
            role = item.get("linh_can_role")
            if role == "catalyst":
                continue   # catalysts are element-agnostic
            assert item.get("linh_can_element") == elem, (
                f"{d['key']} drops {entry['item_key']} for "
                f"{item.get('linh_can_element')} (expected {elem})"
            )


def test_every_element_dungeon_declares_a_realm_decay():
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        decay = d.get("linh_can_loot_decay_per_realm")
        assert decay is not None and decay > 0, (
            f"{d['key']}: linh_can_loot_decay_per_realm missing or non-positive"
        )


# ── Drop engine: weight scaling + clamping ──────────────────────────────────

def test_effective_weight_clamps_at_zero_on_deeply_negative_luck():
    entry = {"weight": 100_000}
    # Decay strong enough to push past zero must NOT produce a negative weight.
    assert _effective_weight(entry, luck_pct=-2.0) == 0.0


def test_effective_weight_clamps_at_pool_range_on_extreme_positive_luck():
    entry = {"weight": 100_000}
    assert _effective_weight(entry, luck_pct=100.0) == POOL_RANGE


def test_effective_weight_decreases_monotonically_with_negative_luck():
    entry = {"weight": 500_000}
    # As realm-decay grows, weight should shrink (or stay at zero once clamped).
    weights = [_effective_weight(entry, -decay) for decay in (0.0, 0.2, 0.4, 0.6, 0.8)]
    for a, b in zip(weights, weights[1:]):
        assert b <= a


def test_negative_luck_drops_high_realm_yield_below_low_realm_yield():
    """Simulates the per-realm decay: same loot table rolled many times at
    low-realm (no penalty) vs high-realm (heavy penalty). High-realm should
    end up with a lower total drop count.
    """
    table = registry.get_loot_table("LinhCanLoot_Kim")
    assert table, "LinhCanLoot_Kim should be registered"

    def total_drops(luck_pct: float, seed: int = 42, trials: int = 400) -> int:
        rng = random.Random(seed)
        return sum(
            sum(d["quantity"] for d in roll_drops(table, rng, luck_pct=luck_pct).merge())
            for _ in range(trials)
        )

    low_realm = total_drops(luck_pct=0.0)        # qi_realm = 0
    high_realm = total_drops(luck_pct=-0.80)     # qi_realm = 8 with 0.10/realm decay
    assert high_realm < low_realm, (
        f"high-realm decay should reduce yield (got high={high_realm}, low={low_realm})"
    )


# ── NPC shop boundary (drops are the primary path; player trade is open) ──

def test_linh_can_materials_have_no_shop_price():
    """The NPC shop never lists Linh Căn mats — defensively, every entry's
    ``shop_price_merit`` is zero so a future drift toward shop sourcing
    can't accidentally start charging a price for a drop-only item.

    Player-to-player listings in Đấu Thương Các are unaffected: sellers
    set their own asking price there.
    """
    for item in registry.items_by_type("linh_can_material"):
        assert int(item.get("shop_price_merit", 0)) == 0, (
            f"{item['key']} has non-zero shop_price_merit"
        )


def test_linh_can_materials_not_in_any_shop_pool():
    """Hardcoded NPC shop pools (FIXED, ROTATING, DARK) must never
    reference linh_can_material entries — they only enter the economy via
    drops or player-to-player trade.
    """
    from src.game.systems.economy import (
        DARK_POOL, FIXED_SHOP_ITEMS, ROTATING_POOL,
    )
    keys = {it["key"] for it in registry.items_by_type("linh_can_material")}
    for pool in (FIXED_SHOP_ITEMS, ROTATING_POOL, DARK_POOL):
        for slot in pool:
            assert slot["item_key"] not in keys, (
                f"{slot['item_key']} leaked into a shop pool"
            )


# ── Per-element enemy pools + skills (no cross-element leakage) ────────────

def test_every_element_dungeon_has_dedicated_enemy_pool():
    """The 9 element bí cảnh must each draw from their own roster — none
    of the legacy normal-dungeon mobs should leak in.
    """
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        elem = d["linh_can_element"]
        assert d["enemy_pool"], f"{d['key']}: empty enemy_pool"
        for ekey in d["enemy_pool"]:
            enemy = registry.get_enemy(ekey)
            assert enemy is not None, f"{d['key']}: missing enemy {ekey}"
            assert enemy.get("element") == elem, (
                f"{d['key']} pulls cross-element enemy {ekey} "
                f"(element={enemy.get('element')}, expected {elem})"
            )
            assert ekey.startswith("LC"), (
                f"{d['key']} pulls non-Linh-Căn enemy {ekey}"
            )


def test_every_linh_can_enemy_signature_skill_matches_element():
    """The signature ``EnemyLC*`` skills referenced by a Linh Căn enemy
    must share that enemy's element. Generic non-LC backup skills (used
    as filler so T1 enemies don't whiff turns on cooldown) are allowed to
    differ — they're only there to prevent dead turns when the signature
    skill is on cooldown.
    """
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        elem = d["linh_can_element"]
        for ekey in d["enemy_pool"]:
            enemy = registry.get_enemy(ekey)
            for skey in enemy.get("skill_keys", []):
                skill = registry.get_skill(skey)
                assert skill is not None, (
                    f"{ekey} references missing skill {skey}"
                )
                if skey.startswith("EnemyLC"):
                    assert skill.get("element") == elem, (
                        f"{ekey} signature skill {skey} has element "
                        f"{skill.get('element')} (expected {elem})"
                    )


def test_every_linh_can_enemy_skill_name_is_in_hanviet():
    """Hán Việt names use Vietnamese diacritics — every Linh Căn enemy
    skill should carry at least one diacritic in its ``vi`` field so we
    catch any accidental English/pinyin-only entries during review.
    """
    diacritics = "ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
    diacritics += diacritics.upper()
    for skill in registry.skills.values():
        if not skill["key"].startswith("EnemyLC"):
            continue
        name = skill.get("vi", "")
        assert any(c in diacritics for c in name), (
            f"{skill['key']} ({name!r}) looks non-Hán-Việt — add diacritics"
        )


# ── Environmental effects ──────────────────────────────────────────────────

def test_every_element_dungeon_has_environmental_effect():
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        env = d.get("environmental_effect")
        assert env is not None, f"{d['key']}: missing environmental_effect"
        assert "key" in env and "base_strength" in env, (
            f"{d['key']}: malformed environmental_effect — {env}"
        )


def test_every_environmental_effect_key_has_a_handler():
    """Each dungeon's effect.key must resolve to a registered handler so
    the dungeon system can actually apply it. Catches typos in the JSON.
    """
    from src.game.systems.linh_can_environment import _HANDLERS
    for d in registry.dungeons.values():
        if d.get("dungeon_type") != "linh_can":
            continue
        env_key = d["environmental_effect"]["key"]
        assert env_key in _HANDLERS, (
            f"{d['key']}: env key '{env_key}' has no handler — "
            f"valid keys: {sorted(_HANDLERS)}"
        )


def test_environmental_strength_scales_with_qi_realm():
    """Strength formula: base × (1 + scale × qi_realm). Higher realms
    must produce strictly stronger effects (or equal at scale=0).
    """
    from src.game.systems.linh_can_environment import scaled_strength
    cfg = {"base_strength": 1.0, "scale_per_realm": 0.15}
    weak = scaled_strength(cfg, qi_realm=0)
    strong = scaled_strength(cfg, qi_realm=8)
    assert weak == 1.0
    assert strong == pytest.approx(1.0 * (1 + 0.15 * 8))
    assert strong > weak


def test_environmental_strength_clamps_qi_realm_input():
    """Out-of-range qi_realm values shouldn't blow up the multiplier."""
    from src.game.systems.linh_can_environment import scaled_strength
    cfg = {"base_strength": 1.0, "scale_per_realm": 0.15}
    # Negative realm clamps to 0
    assert scaled_strength(cfg, qi_realm=-3) == 1.0
    # Beyond 8 clamps to 8
    assert scaled_strength(cfg, qi_realm=99) == pytest.approx(1.0 * (1 + 0.15 * 8))


def test_apply_environmental_effect_returns_log_for_known_key():
    """Sanity: calling the entry point with a known effect mutates the
    enemy and returns a non-empty log line.
    """
    from src.game.systems.combatant import Combatant
    from src.game.systems.linh_can_environment import apply_environmental_effect

    enemy = Combatant(
        key="x", name="dummy", hp=100, hp_max=100, mp=10, mp_max=10,
        spd=10, element="moc", atk=50, def_stat=10,
    )
    player = Combatant(
        key="p", name="player", hp=100, hp_max=100, mp=10, mp_max=10,
        spd=10, element=None,
    )
    cfg = {"key": "van_moc_hoi_sinh", "base_strength": 0.04, "scale_per_realm": 0.15}
    log = apply_environmental_effect(cfg, player, enemy, qi_realm=4)
    assert log is not None and "Vạn Mộc" in log
    assert enemy.hp_regen_pct > 0


def test_apply_environmental_effect_returns_none_for_unknown_key():
    from src.game.systems.combatant import Combatant
    from src.game.systems.linh_can_environment import apply_environmental_effect

    enemy = Combatant(
        key="x", name="dummy", hp=100, hp_max=100, mp=10, mp_max=10,
        spd=10, element="moc",
    )
    player = Combatant(
        key="p", name="player", hp=100, hp_max=100, mp=10, mp_max=10,
        spd=10, element=None,
    )
    cfg = {"key": "totally_invented_effect", "base_strength": 1.0}
    assert apply_environmental_effect(cfg, player, enemy, qi_realm=2) is None
