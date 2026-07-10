"""Sect boss repository — weekly instance lifecycle, atomic damage, claims.

Structural clone of ``world_boss_repo`` scoped to (sect, ISO week). The same
concurrency contract holds: damage lands under a row lock with the per-attack
cap enforced at the lock (client bypass hits the same wall), at most one
caller becomes finisher, and every claim flips a guarded flag exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.sect import Sect, SectBossInstance, SectBossParticipation


@dataclass(frozen=True)
class BossDamageApplyResult:
    """Outcome of an atomic damage application (mirrors the world-boss shape)."""
    applied: int
    new_hp: int
    is_finisher: bool
    instance_missing: bool
    cap_hit: bool = False


class SectBossRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Instances ────────────────────────────────────────────────────────────

    async def get_for_week(self, sect_id: int, week_key: str) -> SectBossInstance | None:
        """The sect's instance for this ISO week — active OR finished (the
        UNIQUE(sect_id, week_key) row is also the 'already killed this week'
        marker, so a dead boss doesn't lazily respawn)."""
        result = await self._session.execute(
            select(SectBossInstance).where(
                SectBossInstance.sect_id == sect_id,
                SectBossInstance.week_key == week_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_instance(
        self,
        sect_id: int,
        boss_key: str,
        week_key: str,
        hp_max: int,
        spawned_at: datetime,
        expires_at: datetime,
    ) -> SectBossInstance:
        """Insert the weekly spawn. Concurrent first-openers race on
        ``uq_sect_boss_week`` — callers catch IntegrityError and re-fetch."""
        instance = SectBossInstance(
            sect_id=sect_id,
            boss_key=boss_key,
            week_key=week_key,
            hp_max=hp_max,
            hp_current=hp_max,
            spawned_at=spawned_at,
            expires_at=expires_at,
            is_active=True,
            rewards_distributed=False,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def apply_damage_atomic(
        self, instance_id: int, damage: int, player_id: int,
        damage_cap: int | None = None,
    ) -> BossDamageApplyResult:
        """Apply damage to the shared pool under SELECT…FOR UPDATE — verbatim
        port of ``WorldBossRepository.apply_damage_atomic`` semantics."""
        damage = max(0, int(damage))
        cap_hit = False
        if damage_cap is not None and damage > damage_cap:
            damage = max(0, int(damage_cap))
            cap_hit = True

        result = await self._session.execute(
            select(SectBossInstance)
            .where(
                SectBossInstance.id == instance_id,
                SectBossInstance.is_active.is_(True),
            )
            .with_for_update()
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            return BossDamageApplyResult(
                applied=0, new_hp=0, is_finisher=False,
                instance_missing=True, cap_hit=cap_hit,
            )

        applied = min(damage, instance.hp_current)
        new_hp = max(0, instance.hp_current - damage)
        instance.hp_current = new_hp

        is_finisher = False
        if new_hp <= 0:
            instance.is_active = False
            if instance.killed_at is None:
                instance.killed_at = datetime.now(timezone.utc)
            if instance.finisher_player_id is None:
                instance.finisher_player_id = player_id
                is_finisher = True

        return BossDamageApplyResult(
            applied=applied, new_hp=new_hp, is_finisher=is_finisher,
            instance_missing=False, cap_hit=cap_hit,
        )

    async def list_expired_active(self, now: datetime) -> list[SectBossInstance]:
        result = await self._session.execute(
            select(SectBossInstance).where(
                SectBossInstance.is_active.is_(True),
                SectBossInstance.expires_at <= now,
            )
        )
        return list(result.scalars().all())

    async def expire_instance(self, instance: SectBossInstance) -> None:
        """Week rolled over with the boss alive — close it out and open the
        participation tier for claiming (mirror of the world-boss expiry)."""
        instance.is_active = False
        instance.rewards_distributed = True

    async def flag_rewards_distributed(self, instance_id: int) -> bool:
        """False→True exactly once — the finisher's transaction wins this and
        applies the one-time sect-side EXP/funds grant."""
        result = await self._session.execute(
            update(SectBossInstance)
            .where(
                SectBossInstance.id == instance_id,
                SectBossInstance.rewards_distributed.is_(False),
            )
            .values(rewards_distributed=True)
        )
        return (result.rowcount or 0) == 1

    async def get_instance_with_parts(self, instance_id: int) -> SectBossInstance | None:
        result = await self._session.execute(
            select(SectBossInstance)
            .options(selectinload(SectBossInstance.participations))
            .where(SectBossInstance.id == instance_id)
        )
        return result.scalar_one_or_none()

    # ── Participation ────────────────────────────────────────────────────────

    async def get_participation(
        self, boss_instance_id: int, player_id: int
    ) -> SectBossParticipation | None:
        result = await self._session.execute(
            select(SectBossParticipation).where(
                SectBossParticipation.boss_instance_id == boss_instance_id,
                SectBossParticipation.player_id == player_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_damage(
        self, boss_instance_id: int, player_id: int, damage: int
    ) -> SectBossParticipation:
        part = await self.get_participation(boss_instance_id, player_id)
        if part is None:
            part = SectBossParticipation(
                boss_instance_id=boss_instance_id,
                player_id=player_id,
                damage_dealt=damage,
                attack_count=1,
            )
            self._session.add(part)
        else:
            part.damage_dealt += damage
            part.attack_count += 1
        await self._session.flush()
        return part

    async def list_participations(
        self, boss_instance_id: int
    ) -> list[SectBossParticipation]:
        result = await self._session.execute(
            select(SectBossParticipation)
            .where(SectBossParticipation.boss_instance_id == boss_instance_id)
            .order_by(desc(SectBossParticipation.damage_dealt))
        )
        return list(result.scalars().all())

    async def claim_reward_atomic(self, participation_id: int) -> bool:
        """reward_claimed False→True exactly once (claim-race guard)."""
        result = await self._session.execute(
            update(SectBossParticipation)
            .where(
                SectBossParticipation.id == participation_id,
                SectBossParticipation.reward_claimed.is_(False),
            )
            .values(reward_claimed=True)
        )
        return (result.rowcount or 0) == 1

    async def list_pending_rewards_for_player(
        self, player_id: int
    ) -> list[SectBossParticipation]:
        """Unclaimed participations on finished (killed or expired) bosses —
        eagerly loads ``boss_instance`` for tiering/labels."""
        result = await self._session.execute(
            select(SectBossParticipation)
            .join(SectBossInstance)
            .options(selectinload(SectBossParticipation.boss_instance))
            .where(
                SectBossParticipation.player_id == player_id,
                SectBossParticipation.reward_claimed.is_(False),
                SectBossInstance.is_active.is_(False),
                SectBossInstance.rewards_distributed.is_(True),
            )
            .order_by(desc(SectBossInstance.expires_at))
        )
        return list(result.scalars().all())

    # ── Cross-sect weekly leaderboard (/tongmon bxh) ─────────────────────────

    async def weekly_sect_damage(
        self, week_key: str, limit: int = 10
    ) -> list[tuple[int, str, str, int, bool]]:
        """Top sects by total boss damage this week:
        ``(sect_id, tag, name, total_damage, killed)`` rows, damage desc."""
        result = await self._session.execute(
            select(
                Sect.id,
                Sect.tag,
                Sect.name,
                func.coalesce(func.sum(SectBossParticipation.damage_dealt), 0),
                SectBossInstance.killed_at.is_not(None),
            )
            .join(SectBossInstance, SectBossInstance.sect_id == Sect.id)
            .outerjoin(
                SectBossParticipation,
                SectBossParticipation.boss_instance_id == SectBossInstance.id,
            )
            .where(SectBossInstance.week_key == week_key)
            .group_by(Sect.id, Sect.tag, Sect.name, SectBossInstance.killed_at)
            .order_by(desc(func.coalesce(func.sum(SectBossParticipation.damage_dealt), 0)))
            .limit(limit)
        )
        return [
            (int(sid), tag, name, int(total), bool(killed))
            for sid, tag, name, total, killed in result.all()
        ]
