"""Formation repository — per-player formation state and gem slots."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.formation import CharacterFormation


class FormationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, player_id: int, formation_key: str) -> CharacterFormation | None:
        result = await self._session.execute(
            select(CharacterFormation).where(
                CharacterFormation.player_id == player_id,
                CharacterFormation.formation_key == formation_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_all(self, player_id: int) -> list[CharacterFormation]:
        result = await self._session.execute(
            select(CharacterFormation).where(CharacterFormation.player_id == player_id)
        )
        return list(result.scalars().all())

    async def get_or_create(self, player_id: int, formation_key: str) -> CharacterFormation:
        existing = await self.get(player_id, formation_key)
        if existing:
            return existing
        formation = CharacterFormation(
            player_id=player_id,
            formation_key=formation_key,
            gem_slots={},
        )
        self._session.add(formation)
        await self._session.flush()
        return formation

    async def inlay_gem(
        self, player_id: int, formation_key: str, slot_index: int, gem_key: str
    ) -> CharacterFormation:
        formation = await self.get_or_create(player_id, formation_key)
        # JSONB mutation requires reassigning the dict for SQLAlchemy to detect the change
        updated = dict(formation.gem_slots)
        updated[str(slot_index)] = gem_key
        formation.gem_slots = updated
        return formation

    async def remove_gem(
        self, player_id: int, formation_key: str, slot_index: int
    ) -> CharacterFormation | None:
        formation = await self.get(player_id, formation_key)
        if not formation:
            return None
        updated = dict(formation.gem_slots)
        updated.pop(str(slot_index), None)
        formation.gem_slots = updated
        return formation

    async def set_mastery(
        self, player_id: int, formation_key: str, mastery: str
    ) -> CharacterFormation | None:
        formation = await self.get(player_id, formation_key)
        if formation:
            formation.mastery = mastery
        return formation
