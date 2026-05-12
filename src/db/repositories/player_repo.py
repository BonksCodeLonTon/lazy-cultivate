"""Player repository — all DB operations for player + related tables."""
from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.db.models.player import Player
from src.db.models.turn_tracker import TurnTracker
from src.game.constants.currencies import BONUS_TURNS
from src.game.constants.linh_can import (
    ALL_LINH_CAN, format_linh_can, parse_linh_can, parse_linh_can_levels,
)


# Loader options shared by ``get_by_discord_id`` / ``get_by_id`` — the full
# eager chain that lets every cog access player.inventory, .skills, etc.
# without lazy-load failures. ``turn_tracker`` is 1:1 so we ``joinedload``
# (single JOIN, no extra round-trip) while the collections use
# ``selectinload`` (batched IN-list query, avoids a Cartesian explosion
# when the player owns hundreds of inventory rows + dozens of skills).
def _full_loader_options() -> tuple:
    return (
        joinedload(Player.turn_tracker),
        selectinload(Player.inventory),
        selectinload(Player.skills),
        selectinload(Player.artifacts),
        selectinload(Player.formations),
        selectinload(Player.item_instances),
    )


class PlayerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_discord_id(self, discord_id: int) -> Player | None:
        result = await self._session.execute(
            select(Player)
            .where(Player.discord_id == discord_id)
            .options(*_full_loader_options())
        )
        return result.unique().scalar_one_or_none()

    async def get_by_id(self, player_id: int) -> Player | None:
        result = await self._session.execute(
            select(Player)
            .where(Player.id == player_id)
            .options(*_full_loader_options())
        )
        return result.unique().scalar_one_or_none()

    async def get_by_discord_id_lite(self, discord_id: int) -> Player | None:
        """Lightweight Player + turn_tracker fetch — no collection loads.

        Use this for command paths that only need scalar fields (merit /
        karma / realm / hp / mp / titles) or the ×2 merit buff state.
        Skipping the four selectinloads turns a 5-query lookup into a
        1-query lookup; on a cold connection that's the difference
        between hitting Discord's 3 s interaction window or not.

        Caller MUST NOT touch ``player.inventory``, ``.skills``,
        ``.artifacts``, ``.formations``, or ``.item_instances`` — those
        are unloaded and will raise ``MissingGreenlet`` under async.
        """
        result = await self._session.execute(
            select(Player)
            .where(Player.discord_id == discord_id)
            .options(joinedload(Player.turn_tracker))
        )
        return result.unique().scalar_one_or_none()

    async def get_names_by_ids(self, player_ids: list[int]) -> dict[int, str]:
        """Return ``{player_id: name}`` for ids in ``player_ids``.

        Lightweight projection (id + name only) for leaderboard rendering —
        avoids the heavy selectinload chain in ``get_by_id`` when callers
        only need display names.
        """
        if not player_ids:
            return {}
        result = await self._session.execute(
            select(Player.id, Player.name).where(Player.id.in_(player_ids))
        )
        return {pid: name for pid, name in result.all()}

    async def create(self, discord_id: int, name: str) -> Player:
        # Randomly assign 1–3 Linh Căn at registration
        count = random.randint(1, 3)
        linh_can_list = random.sample(ALL_LINH_CAN, count)

        # Randomly roll a constitution from the rollable pool (top-tier
        # thần thể are excluded via roll_weight=0).
        constitution_key = _roll_starter_constitution(linh_can_list)

        player = Player(
            discord_id=discord_id,
            name=name,
            linh_can=format_linh_can(linh_can_list),
            constitution_type=constitution_key,
        )
        self._session.add(player)
        await self._session.flush()  # get player.id

        tracker = TurnTracker(
            player_id=player.id,
            turns_today=0,
            bonus_turns_remaining=BONUS_TURNS,
            last_tick_at=datetime.now(timezone.utc),
        )
        self._session.add(tracker)
        await self._session.flush()

        # Set initial HP/MP from realm stats
        from src.game.systems.character_stats import active_formation_gem_keys, compute_combat_stats

        char = _player_to_model(player)
        init_gem_keys = active_formation_gem_keys(player)
        init_cs = compute_combat_stats(char, gem_count=len(init_gem_keys), gem_keys=init_gem_keys)
        player.hp_current = init_cs.hp_max
        player.mp_current = init_cs.mp_max
        player.shield_current = init_cs.shield_max



        # Assign one starting skill that matches a random element from the player's Linh Căn
        from src.data.registry import registry as _registry
        from src.db.models.skill import CharacterSkill

        # Try each element in the player's linh căn (shuffled for randomness)
        shuffled_elements = list(linh_can_list)
        random.shuffle(shuffled_elements)
        start_skill_key: str | None = None
        for elem in shuffled_elements:
            candidates = [
                s["key"] for s in _registry.skills.values()
                if not s.get("key", "").startswith("Enemy")
                and s.get("element") == elem
                and s.get("category") == "attack"
                and s.get("realm", 99) == 1
                and s.get("mp_cost", 999) <= 15
            ]
            if candidates:
                start_skill_key = random.choice(candidates)
                break

        if start_skill_key:
            self._session.add(CharacterSkill(
                player_id=player.id,
                skill_key=start_skill_key,
                slot_index=0,
            ))
            await self._session.flush()

        return player

    async def exists(self, discord_id: int) -> bool:
        result = await self._session.execute(
            select(Player.id).where(Player.discord_id == discord_id)
        )
        return result.scalar_one_or_none() is not None

    async def save(self, player: Player) -> None:
        """Persist mutations on ``player``. Skips ``session.add`` when the
        instance is already managed by this session — calling ``add`` on a
        persistent instance triggers a cascade walk through eagerly-loaded
        relationships, and any child marked deleted earlier in the same
        session (e.g. an inventory row consumed by ``remove_any_grade``)
        raises ``InvalidRequestError: Instance has been deleted``. The
        managed-instance path lets SQLAlchemy's dirty tracking flush the
        attribute changes (merit, hp_current, …) without re-cascading
        through children. Transient / detached players still get added so
        new-player creation flows keep working.
        """
        state = inspect(player)
        if state.transient or state.detached:
            self._session.add(player)
        await self._session.flush()


LEGENDARY_STARTER_RATE = 0.005


def _roll_starter_constitution(linh_can_list: list[str]) -> str:
    """Weighted random roll among constitutions matching the player's Linh Căn.

    Hard-filters the rollable pool to **universal** (element=None) constitutions
    plus **matching-element** ones — newcomers can never roll a body whose
    element clashes with their rolled roots. Falls back to the default Vạn
    Tượng body if the filtered pool is ever empty.

    Legendary bodies are gated behind a flat ``LEGENDARY_STARTER_RATE`` (0.5%)
    pre-roll. On a hit, one is chosen uniformly from the matching legendary
    pool; otherwise the standard weighted pick runs as before. Legendary
    entries keep ``roll_weight=0`` and stay out of the weighted pool — the
    rate is independent of weight rebalancing.

    Chain heads (``ConstitutionHoangCoChain1`` and similar entries with no
    ``progresses_from``) are eligible to roll — they're the legitimate entry
    point for their progression chain. Mid-chain entries (Chain 2..9) carry
    ``progresses_from`` and are filtered out so a starter never leap-frogs
    into a partially-broken seal.
    """
    from src.data.registry import registry

    player_elems = set(linh_can_list)

    if random.random() < LEGENDARY_STARTER_RATE:
        leg_pool = [
            c for c in registry.constitutions.values()
            if c.get("rarity") == "legendary"
            and not c.get("special_requirements")
            and not c.get("progresses_from")
            and (c.get("element") is None or c.get("element") in player_elems)
        ]
        if leg_pool:
            return random.choice(leg_pool)["key"]

    pool = [
        c for c in registry.rollable_constitutions()
        if c.get("element") is None or c.get("element") in player_elems
    ]
    if not pool:
        return "ConstitutionPhamThe"

    weights = [float(c.get("roll_weight", 0)) for c in pool]
    chosen = random.choices(pool, weights=weights, k=1)[0]
    return chosen["key"]


def _parse_pill_buff_counts(raw: str | None) -> dict[str, int]:
    """Decode the JSON-encoded pill_buff_counts column into a plain dict."""
    from src.game.systems.pill_buffs import parse_counts
    return parse_counts(raw)


def _player_to_model(player: Player):
    """Convert ORM Player to game Character dataclass for stat computation."""
    from src.game.models.character import Character as CharModel
    tracker = player.turn_tracker
    raw_linh_can = player.linh_can or ""
    levels = parse_linh_can_levels(raw_linh_can)
    return CharModel(
        player_id=player.id,
        discord_id=player.discord_id,
        name=player.name,
        body_realm=player.body_realm,
        body_level=player.body_level,
        qi_realm=player.qi_realm,
        qi_level=player.qi_level,
        formation_realm=player.formation_realm,
        formation_level=player.formation_level,
        constitution_type=player.constitution_type,
        dao_ti_unlocked=player.dao_ti_unlocked,
        merit=player.merit,
        karma_accum=player.karma_accum,
        karma_usable=player.karma_usable,
        primordial_stones=player.primordial_stones,
        dan_doc=player.dan_doc,
        pill_buff_counts=_parse_pill_buff_counts(player.pill_buff_counts),
        hp_current=player.hp_current,
        mp_current=player.mp_current,
        shield_current=player.shield_current,
        active_formation=player.active_formation,
        main_title=player.main_title,
        sub_title=player.sub_title,
        evil_title=player.evil_title,
        active_axis=player.active_axis,
        body_xp=player.body_xp,
        qi_xp=player.qi_xp,
        formation_xp=player.formation_xp,
        turns_today=tracker.turns_today if tracker else 0,
        bonus_turns_remaining=tracker.bonus_turns_remaining if tracker else 440,
        linh_can=list(levels.keys()),
        linh_can_levels=dict(levels),
    )
