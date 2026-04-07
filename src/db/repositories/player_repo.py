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
        from src.game.systems.cultivation import compute_hp_max, compute_mp_max
        from src.game.models.character import Character as CharModel

        char = _player_to_model(player)
        player.hp_current = compute_hp_max(char)
        player.mp_current = compute_mp_max(char)

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
        linh_can=parse_linh_can(player.linh_can or ""),
    )
