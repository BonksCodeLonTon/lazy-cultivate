"""Inventory-side game logic — scroll classification and elixir effects.

Operates on the ORM ``Player`` object directly (mutates ``hp_current``,
``mp_current``, karma fields). The dungeon-prep counterpart in
``src.game.systems.dungeon.apply_healing_elixir`` does the same shape of
heal but on a runtime ``Combatant`` — they're intentionally separate
because the two contexts use different data models.
"""
from __future__ import annotations

from src.db.repositories.player_repo import _player_to_model
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems.character_stats import (
    active_formation_gem_keys,
    active_formation_gem_map,
    compute_combat_stats,
)


# Scroll item-key prefix → skill categories that scroll can teach.
SCROLL_TYPE_MAP: dict[str, list[str]] = {
    "ScrollAtk": ["attack"],
    "ScrollDef": ["defense"],
    "ScrollSup": ["movement", "passive"],
    "ScrollFrm": ["formation"],
}


def scroll_skill_type(scroll_key: str) -> list[str]:
    """Return the skill categories a scroll can teach (empty if unknown prefix)."""
    for prefix, categories in SCROLL_TYPE_MAP.items():
        if scroll_key.startswith(prefix):
            return categories
    return []


def skill_tier_from_mp(skill_data: dict) -> int:
    """Infer scroll-grade tier required to teach this skill, from its mp_cost.

    The grade ladder (Hoàng/Huyền/Địa/Thiên = 1..4) is gated by mp_cost
    bands so a low-MP skill is teachable from a basic scroll while a
    Thiên-tier scroll is needed for top-tier abilities.
    """
    mp = skill_data.get("mp_cost", 0)
    if mp <= 15:
        return 1
    if mp <= 30:
        return 2
    if mp <= 60:
        return 3
    return 4


def apply_elixir(player, item_key: str, quantity: int) -> list[str]:
    """Apply ``quantity`` uses of the elixir to ``player`` in place.

    Mutates ``player.hp_current`` / ``player.mp_current`` / karma fields
    and returns user-facing effect description lines. Unknown keys fall
    through to a generic "applied" message — they're typically buffs
    handled elsewhere or no-ops.

    Caps go through ``compute_combat_stats`` (not just cultivation's
    ``compute_hp_max``) so equipment hp_max affixes, formation bonuses,
    and Linh Căn / constitution mods are all respected — otherwise a
    player wearing +HP gear can never refill past the gear-less cap.
    """
    char = _player_to_model(player)
    gem_keys = active_formation_gem_keys(player)
    gem_map = active_formation_gem_map(player)
    equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
    cs = compute_combat_stats(
        char,
        gem_count=len(gem_keys),
        equip_stats=compute_equipment_stats(equipped),
        gem_keys=gem_keys,
        gem_keys_by_formation=gem_map,
        learned_skill_keys=[s.skill_key for s in (player.skills or [])],
    )
    hp_max = cs.hp_max
    mp_max = cs.mp_max

    effects: list[str] = []

    if "HoiHPFull" in item_key:
        player.hp_current = hp_max
        effects.append(f"❤️ HP hồi đầy: **{hp_max:,}**")
    elif "HoiHPLarge" in item_key:
        heal = int(hp_max * 0.5 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        effects.append(f"❤️ +{heal:,} HP")
    elif "HoiHPMid" in item_key:
        heal = int(hp_max * 0.25 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        effects.append(f"❤️ +{heal:,} HP")
    elif "HoiHPSmall" in item_key:
        heal = int(hp_max * 0.10 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        effects.append(f"❤️ +{heal:,} HP")
    elif "HoiHPMiss" in item_key:
        missing = hp_max - player.hp_current
        player.hp_current = min(hp_max, player.hp_current + int(missing * 0.5 * quantity))
        effects.append("❤️ Hồi 50% HP thiếu")
    elif "HoiMPLarge" in item_key:
        regen = int(mp_max * 0.5 * quantity)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"💙 +{regen:,} MP")
    elif "HoiMPMid" in item_key:
        regen = int(mp_max * 0.25 * quantity)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"💙 +{regen:,} MP")
    elif "HoiMPSmall" in item_key:
        regen = int(mp_max * 0.10 * quantity)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"💙 +{regen:,} MP")
    elif "HoiHPMP" in item_key:
        heal = int(hp_max * 0.15 * quantity)
        regen = int(mp_max * 0.15 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"❤️ +{heal:,} HP | 💙 +{regen:,} MP")
    elif "HoiFull" in item_key:
        player.hp_current = hp_max
        player.mp_current = mp_max
        effects.append("❤️💙 Hồi đầy cả HP và MP")
    elif "TayNghiep" in item_key:
        reduce = min(player.karma_accum, 10000 * quantity)
        player.karma_accum = max(0, player.karma_accum - reduce)
        effects.append(f"☯️ Nghiệp Lực Tích Lũy -{reduce:,}")
    elif "KarmaDown" in item_key:
        reduce = min(player.karma_usable, 5000 * quantity)
        player.karma_usable = max(0, player.karma_usable - reduce)
        effects.append(f"☯️ Nghiệp Lực Khả Dụng -{reduce:,} (đã tiêu thụ)")
    else:
        effects.append("✨ Hiệu ứng đã được áp dụng.")

    return effects
