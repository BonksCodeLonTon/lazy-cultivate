"""Skill Mastery persistence repository.

Thin DB I/O over ``CharacterSkillMastery``. All math/branching lives in the
pure core (``src.game.systems.skill_mastery`` + ``...constants.skill_mastery``)
— this module only loads/writes rows and spends inventory.

Functions are module-level and take an ``AsyncSession`` as the first arg
(callers wrap them in ``get_session()``), mirroring the pure-core call style.
Inventory is grade-agnostic for these material/talisman items, so we count via
``InventoryRepository.get_all`` and spend via ``remove_any_grade`` — the same
pattern ``linh_can._consume`` uses for split-grade material stacks.
"""
from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.skill_mastery import CharacterSkillMastery
from src.db.repositories.inventory_repo import InventoryRepository
from src.game.constants.skill_mastery import (
    DINH_DAO_CHAU_KEY,
    GATES,
    HO_DAO_PHU_KEY,
)
from src.game.systems.skill_mastery import apply_combat_xp, is_band_ceiling, resolve_breakthrough

# The hidden gate — crossing it (success at level 20) reveals levels 21-25.
_HIDDEN_GATE_LEVEL = 20


async def get_mastery_map(session: AsyncSession, player_id: int) -> dict[str, int]:
    """Return ``{skill_key: level}`` for every mastery row the player has."""
    result = await session.execute(
        select(CharacterSkillMastery.skill_key, CharacterSkillMastery.level).where(
            CharacterSkillMastery.player_id == player_id
        )
    )
    return {skill_key: level for skill_key, level in result.all()}


async def get_or_create(
    session: AsyncSession, player_id: int, skill_key: str
) -> CharacterSkillMastery:
    """Load the mastery row for ``(player, skill)``, creating it at level 1."""
    result = await session.execute(
        select(CharacterSkillMastery).where(
            CharacterSkillMastery.player_id == player_id,
            CharacterSkillMastery.skill_key == skill_key,
        )
    )
    mastery = result.scalar_one_or_none()
    if mastery is not None:
        return mastery

    mastery = CharacterSkillMastery(
        player_id=player_id,
        skill_key=skill_key,
        level=1,
        xp=0,
        gate_fails=0,
        hidden_unlocked=False,
    )
    session.add(mastery)
    await session.flush()
    return mastery


async def add_combat_xp(
    session: AsyncSession,
    player_id: int,
    usage_counts: dict[str, int],
    victory: bool,
) -> dict[str, tuple]:
    """Award post-combat XP per used skill.

    For each skill cast at least once: ``gained = casts + (5 if victory else 0)``,
    feed it through the pure ``apply_combat_xp``, and persist the new level/xp.
    Skills with 0 casts are skipped.

    Returns ``{skill_key: (level_before, xp_before, level_after, xp_after,
    at_ceiling)}`` for caller logging.
    """
    results: dict[str, tuple] = {}
    win_bonus = 5 if victory else 0

    for skill_key, casts in usage_counts.items():
        if casts <= 0:
            continue
        gained = casts + win_bonus
        mastery = await get_or_create(session, player_id, skill_key)
        level_before, xp_before = mastery.level, mastery.xp
        new_level, new_xp, at_ceiling = apply_combat_xp(level_before, xp_before, gained)
        mastery.level = new_level
        mastery.xp = new_xp
        results[skill_key] = (level_before, xp_before, new_level, new_xp, at_ceiling)

    return results


async def attempt_breakthrough(
    session: AsyncSession,
    player_id: int,
    skill_key: str,
    *,
    use_ho_dao_phu: bool = False,
    use_dinh_dao_chau: bool = False,
    roll: float | None = None,
) -> dict:
    """Resolve one item-gated breakthrough attempt for ``(player, skill)``.

    Guards: the skill must sit at a band ceiling (5/10/15/20) and the player
    must hold the gate items (plus any requested talismans). On insufficient
    stock returns ``{"ok": False, "reason": "insufficient_items", ...}``.
    Otherwise rolls (RNG injected here), resolves via the pure core, spends
    inventory exactly per the returned consumption fields, persists
    ``level``/``gate_fails``, flips ``hidden_unlocked`` on a successful hidden
    gate crossing, and returns ``{"ok": True, **pure_result}``.
    """
    mastery = await get_or_create(session, player_id, skill_key)
    gate_level = mastery.level

    if not is_band_ceiling(gate_level):
        return {
            "ok": False,
            "reason": "not_at_gate",
            "level": gate_level,
        }

    gate = GATES[gate_level]
    gate_item_key: str = gate["item_key"]
    gate_qty: int = gate["qty"]

    irepo = InventoryRepository(session)
    owned = await _owned_counts(irepo, player_id)

    missing: list[str] = []
    if owned.get(gate_item_key, 0) < gate_qty:
        missing.append(gate_item_key)
    if use_ho_dao_phu and owned.get(HO_DAO_PHU_KEY, 0) < 1:
        missing.append(HO_DAO_PHU_KEY)
    if use_dinh_dao_chau and owned.get(DINH_DAO_CHAU_KEY, 0) < 1:
        missing.append(DINH_DAO_CHAU_KEY)

    if missing:
        return {
            "ok": False,
            "reason": "insufficient_items",
            "level": gate_level,
            "missing": missing,
        }

    if roll is None:
        roll = random.random()

    result = resolve_breakthrough(
        gate_level,
        mastery.gate_fails,
        use_ho_dao_phu,
        use_dinh_dao_chau,
        roll,
    )

    # Spend inventory exactly per the resolved consumption fields.
    if result["consumed_gate_qty"] > 0:
        await irepo.remove_any_grade(player_id, gate_item_key, result["consumed_gate_qty"])
    if result["consumed_ho_dao_phu"]:
        await irepo.remove_any_grade(player_id, HO_DAO_PHU_KEY, 1)
    if result["consumed_dinh_dao_chau"]:
        await irepo.remove_any_grade(player_id, DINH_DAO_CHAU_KEY, 1)

    # Persist progression.
    mastery.level = result["new_level"]
    mastery.gate_fails = result["new_fails"]
    if result["success"] and gate_level == _HIDDEN_GATE_LEVEL:
        mastery.hidden_unlocked = True

    return {"ok": True, **result}


async def _owned_counts(irepo: InventoryRepository, player_id: int) -> dict[str, int]:
    """Sum quantity per ``item_key`` across all grade rows for the player."""
    owned: dict[str, int] = {}
    for row in await irepo.get_all(player_id):
        owned[row.item_key] = owned.get(row.item_key, 0) + row.quantity
    return owned
