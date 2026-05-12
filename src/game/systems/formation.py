"""Trận Pháp (formation) game logic — gem parsing and aggregated active bonuses.

Per-formation bonus computation lives in ``cultivation.compute_formations_bonuses``;
this module handles cog-side composition (combining gem reserves with the
player's equipped formation skills, capped at ``FORMATION_MAX_RESERVE_PCT``).
"""
from __future__ import annotations

from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
from src.game.systems.character_stats import active_formation_gem_map
from src.game.systems.cultivation import (
    compute_formation_skill_reserve_pct,
    compute_formations_bonuses,
    get_active_formations,
)


def gem_element(gem_key: str) -> str | None:
    """Parse the element prefix from a gem key (``GemHoa_2`` → ``hoa``).

    Unique gems use the ``GemUnique_<Name>`` naming scheme — their actual
    element lives on the registry entry, not in the key, so resolve it
    there. Returns ``None`` for non-gem keys so callers can short-circuit
    on inventory items that aren't gems.
    """
    if not gem_key.startswith("Gem"):
        return None
    if gem_key.startswith("GemUnique_"):
        from src.data.registry import registry
        item = registry.get_item(gem_key) or {}
        elem = item.get("element")
        return elem.lower() if isinstance(elem, str) and elem else None
    return gem_key[3:].split("_", 1)[0].lower()


def compute_active_formation_bonuses(player) -> dict:
    """Aggregate every active formation's bonus dict into one merged result.

    Folds in MP reservation from currently-learned formation skills so the
    ``_mp_reserve_pct`` returned reflects the *real* MP lock (gems +
    formation skills in the bar), capped at ``FORMATION_MAX_RESERVE_PCT``.
    Returns ``{}`` if no formations are active — callers then know to
    render the zero-state hub.
    """
    active_keys = get_active_formations(getattr(player, "active_formation", None))
    if not active_keys:
        return {}

    gem_map = active_formation_gem_map(player)
    stages = (player.formation_realm or 0) * 9 + (player.formation_level or 0)

    bonuses = compute_formations_bonuses(
        active_keys,
        getattr(player, "active_axis", None),
        int(getattr(player, "formation_realm", 0) or 0),
        gem_keys_by_formation=gem_map,
        formation_stages=stages,
    )

    skill_reserve = compute_formation_skill_reserve_pct(
        [s.skill_key for s in (player.skills or [])],
        formation_stages=stages,
    )
    if skill_reserve > 0:
        gem_reserve = float(bonuses.get("_mp_reserve_pct", 0.0))
        bonuses["_mp_reserve_pct"] = min(
            FORMATION_MAX_RESERVE_PCT, gem_reserve + skill_reserve,
        )
    return bonuses
