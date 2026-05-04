"""World boss repository — active boss lookup, participation tracking, damage ranking."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.world_boss import WorldBossInstance, WorldBossParticipation


@dataclass(frozen=True)
class DamageApplyResult:
    """Outcome of an atomic damage application."""
    applied: int            # Damage actually removed from the shared pool
    new_hp: int             # Boss HP after the update
    is_finisher: bool       # True if this transaction's UPDATE set hp to 0
    instance_missing: bool  # True if the boss was already inactive on lock acquisition
    cap_hit: bool = False   # True if damage_cap clamped the input below the request


class WorldBossRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Active instances ─────────────────────────────────────────────────────

    async def get_active(self, boss_key: str) -> WorldBossInstance | None:
        """Return the single active instance for a boss_key (or None)."""
        result = await self._session.execute(
            select(WorldBossInstance).where(
                WorldBossInstance.boss_key == boss_key,
                WorldBossInstance.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[WorldBossInstance]:
        """Return all currently active world boss instances ordered by realm."""
        result = await self._session.execute(
            select(WorldBossInstance)
            .where(WorldBossInstance.is_active.is_(True))
            .order_by(WorldBossInstance.realm, WorldBossInstance.spawned_at)
        )
        return list(result.scalars().all())

    async def create_instance(
        self,
        boss_key: str,
        realm: int,
        hp_max: int,
        spawned_at: datetime,
        expires_at: datetime,
    ) -> WorldBossInstance:
        instance = WorldBossInstance(
            boss_key=boss_key,
            realm=realm,
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

    async def mark_killed(
        self, instance: WorldBossInstance, finisher_player_id: int
    ) -> None:
        instance.hp_current = 0
        instance.is_active = False
        instance.killed_at = datetime.now(timezone.utc)
        instance.finisher_player_id = finisher_player_id

    async def expire_instance(self, instance: WorldBossInstance) -> None:
        """Mark a boss instance expired without a finisher (time ran out)."""
        instance.is_active = False

    async def apply_damage_atomic(
        self, instance_id: int, damage: int, player_id: int,
        damage_cap: int | None = None,
    ) -> DamageApplyResult:
        """Apply damage to the shared HP pool under a row lock — safe under concurrency.

        Acquires SELECT...FOR UPDATE on the WorldBossInstance row, mutates
        ``hp_current`` within the locked window, and decides the finisher
        atomically. Postgres serializes concurrent callers on the same row, so
        no damage is ever lost and at most one caller is marked finisher.

        ``damage_cap`` (optional) clamps the *requested* damage BEFORE it is
        compared to remaining HP — this is the authoritative enforcement of the
        per-attack ``PER_ATTACK_DMG_CAP_PCT`` limit. Even if client-side logic
        is bypassed, the DB row-lock still applies the cap. Callers should use
        ``result.applied`` (not the raw input) to credit participation so the
        leaderboard can't be gamed by inflating local simulation damage.

        Returns ``DamageApplyResult`` — ``cap_hit`` is True when the cap
        actually truncated the request.
        """
        damage = max(0, int(damage))
        cap_hit = False
        if damage_cap is not None and damage > damage_cap:
            damage = max(0, int(damage_cap))
            cap_hit = True

        result = await self._session.execute(
            select(WorldBossInstance)
            .where(
                WorldBossInstance.id == instance_id,
                WorldBossInstance.is_active.is_(True),
            )
            .with_for_update()
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            return DamageApplyResult(
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

        return DamageApplyResult(
            applied=applied, new_hp=new_hp, is_finisher=is_finisher,
            instance_missing=False, cap_hit=cap_hit,
        )

    async def has_instance_for_window(
        self, boss_key: str, spawned_at: datetime
    ) -> bool:
        """True if any instance (active or past) exists for this boss+spawn_at.

        Used by the scheduler to avoid re-spawning a boss that was killed
        mid-window.
        """
        result = await self._session.execute(
            select(WorldBossInstance.id).where(
                WorldBossInstance.boss_key == boss_key,
                WorldBossInstance.spawned_at == spawned_at,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ── Participation ────────────────────────────────────────────────────────

    async def get_participation(
        self, boss_instance_id: int, player_id: int
    ) -> WorldBossParticipation | None:
        result = await self._session.execute(
            select(WorldBossParticipation).where(
                WorldBossParticipation.boss_instance_id == boss_instance_id,
                WorldBossParticipation.player_id == player_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_damage(
        self, boss_instance_id: int, player_id: int, damage: int
    ) -> WorldBossParticipation:
        part = await self.get_participation(boss_instance_id, player_id)
        if part is None:
            part = WorldBossParticipation(
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
    ) -> list[WorldBossParticipation]:
        """Return all participations for a boss, sorted by damage desc."""
        result = await self._session.execute(
            select(WorldBossParticipation)
            .where(WorldBossParticipation.boss_instance_id == boss_instance_id)
            .order_by(desc(WorldBossParticipation.damage_dealt))
        )
        return list(result.scalars().all())

    async def get_instance_with_parts(
        self, instance_id: int
    ) -> WorldBossInstance | None:
        result = await self._session.execute(
            select(WorldBossInstance)
            .options(selectinload(WorldBossInstance.participations))
            .where(WorldBossInstance.id == instance_id)
        )
        return result.scalar_one_or_none()

    async def flag_rewards_distributed(self, instance_id: int) -> bool:
        """Flip ``rewards_distributed`` from False→True atomically.

        Returns True iff this call won the race — the ``WHERE
        rewards_distributed IS FALSE`` guard ensures exactly one finisher
        observes a rowcount of 1, so concurrent kills don't double-flag.
        """
        result = await self._session.execute(
            update(WorldBossInstance)
            .where(
                WorldBossInstance.id == instance_id,
                WorldBossInstance.rewards_distributed.is_(False),
            )
            .values(rewards_distributed=True)
        )
        return (result.rowcount or 0) == 1

    async def claim_reward_atomic(self, participation_id: int) -> bool:
        """Flip ``reward_claimed`` from False→True atomically. Returns True iff we won the race.

        Concurrent ``/world_boss rewards`` invocations could otherwise double-grant
        loot. The ``WHERE reward_claimed IS FALSE`` guard ensures exactly one
        caller observes a rowcount of 1.
        """
        result = await self._session.execute(
            update(WorldBossParticipation)
            .where(
                WorldBossParticipation.id == participation_id,
                WorldBossParticipation.reward_claimed.is_(False),
            )
            .values(reward_claimed=True)
        )
        return (result.rowcount or 0) == 1

    async def list_pending_rewards_for_player(
        self, player_id: int
    ) -> list[WorldBossParticipation]:
        """Return participations on *killed* bosses the player has not yet claimed."""
        result = await self._session.execute(
            select(WorldBossParticipation)
            .join(WorldBossInstance)
            .where(
                WorldBossParticipation.player_id == player_id,
                WorldBossParticipation.reward_claimed.is_(False),
                WorldBossInstance.is_active.is_(False),
                WorldBossInstance.killed_at.isnot(None),
                WorldBossInstance.rewards_distributed.is_(True),
            )
            .order_by(desc(WorldBossInstance.killed_at))
        )
        return list(result.scalars().all())
