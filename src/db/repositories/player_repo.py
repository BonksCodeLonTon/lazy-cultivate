"""Player repository — all DB operations for player + related tables."""
from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.player import Player
from src.db.models.turn_tracker import TurnTracker
from src.game.constants.currencies import BONUS_TURNS
from src.game.constants.linh_can import ALL_LINH_CAN, format_linh_can, parse_linh_can


class PlayerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_discord_id(self, discord_id: int) -> Player | None:
        result = await self._session.execute(
            select(Player)
            .where(Player.discord_id == discord_id)
            .options(
                selectinload(Player.turn_tracker),
                selectinload(Player.inventory),
                selectinload(Player.skills),
                selectinload(Player.artifacts),
                selectinload(Player.formations),
                selectinload(Player.item_instances),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, player_id: int) -> Player | None:
        result = await self._session.execute(
            select(Player)
            .where(Player.id == player_id)
            .options(
                selectinload(Player.turn_tracker),
                selectinload(Player.inventory),
                selectinload(Player.skills),
                selectinload(Player.artifacts),
                selectinload(Player.formations),
                selectinload(Player.item_instances),
            )
        )
        return result.scalar_one_or_none()

    async def create(self, discord_id: int, name: str) -> Player:
        # Randomly assign 1–3 Linh Căn at registration
        count = random.randint(1, 3)
        linh_can_list = random.sample(ALL_LINH_CAN, count)
        player = Player(discord_id=discord_id, name=name, linh_can=format_linh_can(linh_can_list))
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
                if s.get("element") == elem
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
        self._session.add(player)
        await self._session.flush()


def _player_to_model(player: Player):
    """Convert ORM Player to game Character dataclass for stat computation."""
    from src.game.models.character import Character as CharModel
    tracker = player.turn_tracker
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
        hp_current=player.hp_current,
        mp_current=player.mp_current,
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
        linh_can=parse_linh_can(player.linh_can or ""),
    )
