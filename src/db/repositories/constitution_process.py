"""Constitution Process persistence repository (Phase 2).

Thin DB I/O over ``CharacterConstitutionProgress``. All math/branching lives in
the pure core (``src.game.systems.constitution_process`` +
``...constants.constitution_process``) — this module only loads/writes rows.

Progress is stored per ``(player_id, constitution_key)`` (NOT a player column)
so a body's level SURVIVES body-swaps: swapping away and back later resumes the
old body's level, exactly like Skill Mastery stores per ``(player, skill)``.

Functions are module-level and take an ``AsyncSession`` as the first arg
(callers wrap them in ``get_session()``), mirroring the skill-mastery repo.

This phase is storage + the two write-back helpers only. Material consumption
and combat wiring land in Phase 5; this module never touches inventory or RNG.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.constitution_process import CharacterConstitutionProgress
from src.db.repositories.inventory_repo import InventoryRepository


async def get_progress(
    session: AsyncSession, player_id: int, constitution_key: str
) -> CharacterConstitutionProgress | None:
    """Load the progress row for ``(player, constitution)``, or None if absent."""
    result = await session.execute(
        select(CharacterConstitutionProgress).where(
            CharacterConstitutionProgress.player_id == player_id,
            CharacterConstitutionProgress.constitution_key == constitution_key,
        )
    )
    return result.scalar_one_or_none()


async def get_all(
    session: AsyncSession, player_id: int
) -> list[CharacterConstitutionProgress]:
    """Return every progress row the player has, in id order."""
    result = await session.execute(
        select(CharacterConstitutionProgress)
        .where(CharacterConstitutionProgress.player_id == player_id)
        .order_by(CharacterConstitutionProgress.id)
    )
    return list(result.scalars().all())


async def get_or_create(
    session: AsyncSession, player_id: int, constitution_key: str
) -> CharacterConstitutionProgress:
    """Load the progress row for ``(player, constitution)``, creating it at L1."""
    existing = await get_progress(session, player_id, constitution_key)
    if existing is not None:
        return existing

    progress = CharacterConstitutionProgress(
        player_id=player_id,
        constitution_key=constitution_key,
        level=1,
        xp=0,
        gate_fails=0,
    )
    session.add(progress)
    await session.flush()
    return progress


async def get_levels_map(
    session: AsyncSession,
    player_id: int,
    equipped_keys: list[str],
) -> dict[str, int]:
    """Build ``{constitution_key: level}`` for EVERY equipped constitution.

    Every equipped body contributes at its stored level; a body with no
    progress row defaults to level 1 (the L1 identity — flat ``stat_bonuses``).
    Mirrors ``skill_mastery.get_mastery_map`` as the ORM→stat-build boundary
    for the Constitution Process read path. ``equipped_keys`` is the player's
    currently-equipped constitution list (e.g. ``the_chat.get_constitutions``),
    so only bodies that actually contribute bonuses are leveled.

    Bodies the player has progressed but isn't currently equipping are
    intentionally omitted — they apply nothing this fight, exactly like the
    flat path skips unequipped bodies.
    """
    if not equipped_keys:
        return {}
    rows = await get_all(session, player_id)
    by_key = {r.constitution_key: r.level for r in rows}
    return {key: by_key.get(key, 1) for key in equipped_keys}


async def load_constitution_levels(
    session: AsyncSession, player_id: int, constitution_type: str | None
) -> dict[str, int]:
    """Flag-gated convenience over ``get_levels_map`` for combat build sites.

    Splits ``constitution_type`` (the player's comma-joined equipped column)
    into its equipped keys and returns ``{key: level}`` for each. Returns an
    empty map when the feature flag is OFF or nothing is equipped — so the
    caller can pass ``char.constitution_levels`` straight through and the
    downstream stat read collapses to the inert flat path.
    """
    from src.game.systems.the_chat import get_constitutions
    from src.utils.config import settings

    if not settings.constitution_process_enabled:
        return {}
    equipped = get_constitutions(constitution_type)
    if not equipped:
        return {}
    return await get_levels_map(session, player_id, equipped)


async def persist_combat_xp_result(
    session: AsyncSession,
    player_id: int,
    constitution_key: str,
    xp_result: tuple[int, int, bool],
) -> CharacterConstitutionProgress:
    """Write back the pure ``apply_combat_xp`` output for one constitution.

    ``xp_result`` is the ``(new_level, new_xp, at_ceiling)`` tuple returned by
    ``systems.constitution_process.apply_combat_xp``. The pure math stays in the
    systems module; this only persists ``level``/``xp`` (``at_ceiling`` is a
    caller signal, not stored). Returns the updated row.
    """
    new_level, new_xp, _at_ceiling = xp_result
    progress = await get_or_create(session, player_id, constitution_key)
    progress.level = new_level
    progress.xp = new_xp
    await session.flush()
    return progress


async def award_constitution_xp(
    session: AsyncSession,
    player,
    grade: str,
    won: bool,
) -> CharacterConstitutionProgress | None:
    """Accrue one combat's worth of XP onto the player's PRIMARY constitution.

    Flag-gated, single-active-body model — XP goes ONLY to the first equipped
    key (slot 0) of ``the_chat.get_constitutions(player.constitution_type)``,
    even though every equipped body APPLIES bonuses at its own level. No-ops
    (returning None, touching no row) when:

    - the feature flag is OFF,
    - the player has no equipped constitution (Phàm Thể / empty), or
    - ``combat_xp_gain(grade, won)`` rolls 0 (e.g. ``trash`` / unknown grade) —
      so AFK-repeat trash kills never create or advance a row.

    The pure math lives in ``systems.constitution_process``
    (``combat_xp_gain`` → ``apply_combat_xp``); this only loads the row, feeds
    its current ``(level, xp)`` forward, and persists the result on the SAME
    session the caller already holds open. Mirrors Skill Mastery's once-per-
    resolved-combat discipline — the caller invokes it once per combat.

    Returns the updated row, or None on any no-op path.
    """
    from src.data.registry import registry
    from src.game.systems.constitution_process import apply_combat_xp, combat_xp_gain
    from src.game.systems.the_chat import get_constitutions
    from src.utils.config import settings

    if not settings.constitution_process_enabled:
        return None

    equipped = get_constitutions(getattr(player, "constitution_type", None))
    if not equipped:
        return None

    gained = combat_xp_gain(grade, won)
    if gained <= 0:
        return None

    primary_key = equipped[0]
    row = await get_or_create(session, player.id, primary_key)
    # Per-body XP table: a capstone with an empty ``xp_to_next`` advances ONLY
    # by breakthrough, never by XP (apply_combat_xp returns at_ceiling at once).
    process = (registry.get_constitution(primary_key) or {}).get("process")
    return await persist_combat_xp_result(
        session, player.id, primary_key,
        apply_combat_xp(row.level, row.xp, gained, process),
    )


async def persist_breakthrough_result(
    session: AsyncSession,
    player_id: int,
    constitution_key: str,
    breakthrough_result: dict,
) -> CharacterConstitutionProgress:
    """Write back the pure ``resolve_breakthrough`` output for one constitution.

    ``breakthrough_result`` is the dict returned by
    ``systems.constitution_process.resolve_breakthrough`` — only its
    ``new_level`` and ``new_fails`` fields are persisted here (consumption
    fields are spent by the Phase 5 caller, not this storage layer). Returns the
    updated row.
    """
    progress = await get_or_create(session, player_id, constitution_key)
    progress.level = breakthrough_result["new_level"]
    progress.gate_fails = breakthrough_result["new_fails"]
    await session.flush()
    return progress


async def _owned_counts(irepo, player_id: int) -> dict[str, int]:
    """Sum quantity per ``item_key`` across all grade rows for the player.

    Mirrors ``skill_mastery._owned_counts`` — constitution-process mats /
    talismans / essence are grade-agnostic, so a single-grade lookup would miss
    split stacks. Spend goes through ``remove_any_grade`` to match.
    """
    owned: dict[str, int] = {}
    for row in await irepo.get_all(player_id):
        owned[row.item_key] = owned.get(row.item_key, 0) + row.quantity
    return owned


async def apply_breakthrough(
    session: AsyncSession,
    player,
    *,
    use_ho_the_phu: bool,
    use_dinh_the_chau: bool,
    trial_won: bool,
    rng,
) -> dict:
    """Resolve + persist one full Constitution Process breakthrough on the active body.

    The Phase-6 service seam the ⚔️ Đột Phá cog button calls AFTER the trial
    fight resolves. Mirrors ``skill_mastery.attempt_breakthrough``: load the row,
    read owned gate-item qty + talisman ownership (grade-agnostic), inject the
    RNG (``roll=rng.random()``) into the pure
    ``resolve_constitution_breakthrough`` resolver, spend inventory exactly per
    the returned ``consumed`` map, and persist ``new_level``/``new_fails``.

    Operates on the PRIMARY (slot-0) equipped body — the single XP-earning /
    breakthrough body, matching ``award_constitution_xp``. No-ops cleanly
    (consuming nothing, persisting nothing) on:

    - the feature flag being OFF,
    - no equipped constitution,
    - a resolver guard (``NOT_AT_GATE`` / ``TRIAL_LOST`` / ``NOT_ENOUGH_MATERIALS``).

    Returns the resolver dict, augmented for the UI with ``constitution_key``,
    the gate ``item_key``/``required_qty``, and (on SUCCESS/FAIL) ``new_band`` —
    the ``(vi, en)`` band label of the resulting level.
    """
    from src.data.registry import registry
    from src.game.constants.constitution_process import (
        DINH_THE_CHAU_KEY,
        HO_THE_PHU_KEY,
    )
    from src.game.systems.constitution_process import (
        band_label,
        gates_for,
        resolve_constitution_breakthrough,
    )
    from src.game.systems.the_chat import get_constitutions
    from src.utils.config import settings

    if not settings.constitution_process_enabled:
        return {"outcome": "DISABLED"}

    equipped = get_constitutions(getattr(player, "constitution_type", None))
    if not equipped:
        return {"outcome": "NO_CONSTITUTION"}

    primary_key = equipped[0]
    row = await get_or_create(session, player.id, primary_key)
    level = row.level

    irepo = InventoryRepository(session)
    owned = await _owned_counts(irepo, player.id)

    # Per-body gate table (a capstone gates at every level with its own mats);
    # default → global GATES for every standard body.
    process = (registry.get_constitution(primary_key) or {}).get("process")
    gate = gates_for(process).get(level)
    gate_item = gate["item_key"] if gate else None
    owned_gate_qty = owned.get(gate_item, 0) if gate_item else 0

    result = resolve_constitution_breakthrough(
        level=level,
        gate_fails=row.gate_fails,
        owned_gate_qty=owned_gate_qty,
        use_ho_the_phu=use_ho_the_phu and owned.get(HO_THE_PHU_KEY, 0) >= 1,
        use_dinh_the_chau=use_dinh_the_chau and owned.get(DINH_THE_CHAU_KEY, 0) >= 1,
        trial_won=trial_won,
        roll=rng.random(),
        process=process,
    )

    augmented = dict(result)
    augmented["constitution_key"] = primary_key
    if gate_item is not None:
        augmented["gate_item_key"] = gate_item
        augmented["required_qty"] = gate["qty"]

    # Guard outcomes consume nothing and persist nothing.
    if result["outcome"] not in ("SUCCESS", "FAIL"):
        return augmented

    # Spend inventory exactly per the resolver's consumption table (0-qty
    # entries are skipped — e.g. refunded gate mats on a Định Thể Châu fail).
    for item_key, qty in result["consumed"].items():
        if qty > 0:
            await irepo.remove_any_grade(player.id, item_key, qty)

    await persist_breakthrough_result(session, player.id, primary_key, result)
    augmented["new_band"] = band_label(result["new_level"])
    return augmented


async def apply_swap(session: AsyncSession, player, target_key: str) -> dict:
    """Resolve + persist a Hoán Thể body-swap — change the cultivated PRIMARY.

    The Phase-6 service seam the 🔄 Hoán Thể cog select calls. Delegates the
    branching to the pure ``resolve_constitution_swap`` (which owns the reorder
    + guard table); on ``SWAPPED`` it spends 1 Hoán Thể Tinh and writes the
    re-serialized equipped column back onto ``player.constitution_type`` (the
    caller persists the row on the open session). Progress rows are NEVER
    touched — the swapped-to body resumes its stored level, the
    swap-retains-progress invariant.

    No-ops cleanly (consuming nothing, mutating nothing) on the feature flag
    being OFF or on any resolver guard rejection. Returns the resolver dict.
    """
    from src.game.systems.constitution_process import resolve_constitution_swap
    from src.utils.config import settings

    if not settings.constitution_process_enabled:
        return {"outcome": "DISABLED"}

    from src.game.constants.constitution_process import THIEN_MENH_THACH_KEY

    irepo = InventoryRepository(session)
    owned = await _owned_counts(irepo, player.id)
    essence_key = "ConsProcHoanTheTinh"

    result = resolve_constitution_swap(
        constitution_type=getattr(player, "constitution_type", None),
        constitution_tracker=getattr(player, "constitution_tracker", None),
        target_key=target_key,
        owned_essence_qty=owned.get(essence_key, 0),
        owned_stone_qty=owned.get(THIEN_MENH_THACH_KEY, 0),
    )

    if result["outcome"] != "SWAPPED":
        return result

    for item_key, qty in result["consumed"].items():
        if qty > 0:
            await irepo.remove_any_grade(player.id, item_key, qty)
    player.constitution_type = result["new_constitution_type"]
    await session.flush()
    return result
