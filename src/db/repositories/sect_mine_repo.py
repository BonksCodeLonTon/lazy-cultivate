"""Mine-war repository — occupancy, declarations, war attempts, payouts.

Lock-order contract (deadlock prevention):
  * flows touching a sect AND a mine lock **sect first, mine second**
    (declaration fee, hourly payouts);
  * flows touching a war AND a mine lock **war first, mine second**
    (garrison finisher, window resolution);
  * nothing ever takes a mine lock first when another lock follows.
Every mutation clamps/validates inside the locked window and callers consume
returned values — same discipline as the world/sect-boss repos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.sect import Sect
from src.db.models.sect_mine import MineWar, MineWarAttack, SectMine
from src.game.systems import sect_mine as mine_rules


@dataclass(frozen=True)
class DeclareResult:
    war: MineWar | None = None
    is_siege: bool = False
    sect_missing: bool = False
    level_too_low: bool = False
    insufficient_funds: bool = False
    funds_needed: int = 0
    occupancy_capped: bool = False
    already_at_war: bool = False        # attacker sect already fights somewhere
    mine_contested: bool = False        # mine already has an active war
    mine_shielded: bool = False
    shield_until: datetime | None = None
    own_mine: bool = False
    redeclare_blocked: bool = False
    blocked_until: datetime | None = None


@dataclass(frozen=True)
class GarrisonDamageResult:
    applied: int
    new_hp: int
    captured: bool                      # this attack destroyed the garrison
    war_missing: bool = False
    cap_hit: bool = False


@dataclass(frozen=True)
class AttemptResult:
    ok: bool
    attempts_used: int = 0
    out_of_attempts: bool = False
    war_missing: bool = False


class SectMineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Mine rows (lazily seeded from mines.json) ────────────────────────────

    async def ensure_mines_seeded(self) -> None:
        """Insert a row for every registry mine that lacks one. Races on the
        UNIQUE(mine_key) are harmless — callers run inside a transaction that
        simply retries the read after a conflict."""
        existing = {
            key for (key,) in (
                await self._session.execute(select(SectMine.mine_key))
            ).all()
        }
        for key in mine_rules.mine_defs():
            if key not in existing:
                self._session.add(SectMine(mine_key=key))
        await self._session.flush()

    async def get_mine(self, mine_key: str) -> SectMine | None:
        result = await self._session.execute(
            select(SectMine).where(SectMine.mine_key == mine_key)
        )
        return result.scalar_one_or_none()

    async def list_mines(self) -> list[SectMine]:
        result = await self._session.execute(select(SectMine))
        return list(result.scalars().all())

    async def count_occupied_by(self, sect_id: int) -> int:
        result = await self._session.execute(
            select(SectMine.id).where(SectMine.occupier_sect_id == sect_id)
        )
        return len(result.all())

    # ── Wars: lookups ────────────────────────────────────────────────────────

    async def get_active_war_for_mine(self, mine_key: str) -> MineWar | None:
        result = await self._session.execute(
            select(MineWar).where(
                MineWar.mine_key == mine_key,
                MineWar.status == mine_rules.WAR_ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def list_active_wars(self) -> list[MineWar]:
        result = await self._session.execute(
            select(MineWar).where(MineWar.status == mine_rules.WAR_ACTIVE)
        )
        return list(result.scalars().all())

    async def get_active_war_for_sect(self, sect_id: int) -> MineWar | None:
        """The sect's current war on either side (attacker OR defender)."""
        result = await self._session.execute(
            select(MineWar).where(
                MineWar.status == mine_rules.WAR_ACTIVE,
                (MineWar.attacker_sect_id == sect_id)
                | (MineWar.defender_sect_id == sect_id),
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_war(self, war_id: int) -> MineWar | None:
        return await self._session.get(MineWar, war_id)

    async def last_lost_attack_on_mine(
        self, mine_key: str, attacker_sect_id: int
    ) -> MineWar | None:
        """Most recent resolved war this sect ATTACKED this mine and did not
        win — drives the 72 h re-declare cooldown."""
        result = await self._session.execute(
            select(MineWar)
            .where(
                MineWar.mine_key == mine_key,
                MineWar.attacker_sect_id == attacker_sect_id,
                MineWar.status == mine_rules.WAR_RESOLVED,
                (MineWar.winner_sect_id.is_(None))
                | (MineWar.winner_sect_id != attacker_sect_id),
            )
            .order_by(desc(MineWar.resolved_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ── Declaration ──────────────────────────────────────────────────────────

    async def declare_war_atomic(
        self, mine_key: str, attacker_sect_id: int, now: datetime
    ) -> DeclareResult:
        """Full declaration under locks (sect → mine): level gate, fee,
        occupancy cap, one-war-per-sect, shield, contested-mine, and the
        re-declare cooldown all validate inside the locked window."""
        mine_def = mine_rules.get_mine(mine_key)
        if mine_def is None:
            return DeclareResult(mine_contested=True)

        sect = (
            await self._session.execute(
                select(Sect).where(Sect.id == attacker_sect_id).with_for_update()
            )
        ).scalar_one_or_none()
        if sect is None:
            return DeclareResult(sect_missing=True)
        if int(sect.level) < mine_rules.MINE_WAR_MIN_SECT_LEVEL:
            return DeclareResult(level_too_low=True)
        if int(sect.funds or 0) < mine_rules.DECLARE_FEE_FUNDS:
            return DeclareResult(
                insufficient_funds=True, funds_needed=mine_rules.DECLARE_FEE_FUNDS
            )

        mine = (
            await self._session.execute(
                select(SectMine).where(SectMine.mine_key == mine_key).with_for_update()
            )
        ).scalar_one_or_none()
        if mine is None:
            return DeclareResult(mine_contested=True)
        if mine.occupier_sect_id == attacker_sect_id:
            return DeclareResult(own_mine=True)
        if mine.shield_until is not None and mine.shield_until > now:
            return DeclareResult(mine_shielded=True, shield_until=mine.shield_until)
        if await self.count_occupied_by(attacker_sect_id) >= mine_rules.OCCUPANCY_CAP:
            return DeclareResult(occupancy_capped=True)
        if await self.get_active_war_for_mine(mine_key) is not None:
            return DeclareResult(mine_contested=True)
        if await self.get_active_war_for_sect(attacker_sect_id) is not None:
            return DeclareResult(already_at_war=True)

        lost = await self.last_lost_attack_on_mine(mine_key, attacker_sect_id)
        if lost is not None and lost.resolved_at is not None:
            blocked_until = lost.resolved_at + timedelta(
                hours=mine_rules.REDECLARE_COOLDOWN_HOURS
            )
            if blocked_until > now:
                return DeclareResult(redeclare_blocked=True, blocked_until=blocked_until)

        sect.funds = int(sect.funds) - mine_rules.DECLARE_FEE_FUNDS

        is_siege = mine.occupier_sect_id is None
        garrison_hp = mine_rules.garrison_hp_max(mine_def) if is_siege else None
        war = MineWar(
            mine_key=mine_key,
            attacker_sect_id=attacker_sect_id,
            defender_sect_id=mine.occupier_sect_id,
            window_start=now,
            window_end=now + timedelta(hours=mine_rules.WAR_WINDOW_HOURS),
            garrison_hp_max=garrison_hp,
            garrison_hp_current=garrison_hp,
            status=mine_rules.WAR_ACTIVE,
        )
        self._session.add(war)
        await self._session.flush()
        return DeclareResult(war=war, is_siege=is_siege)

    # ── War attempts ─────────────────────────────────────────────────────────

    async def consume_attempt_atomic(
        self, war_id: int, player_id: int, side: str
    ) -> AttemptResult:
        """Reserve one attempt under the attack-row lock (insert on first use).
        Points land separately via ``add_points_atomic`` after the fight."""
        war = await self.get_war(war_id)
        if war is None or war.status != mine_rules.WAR_ACTIVE:
            return AttemptResult(ok=False, war_missing=True)

        row = (
            await self._session.execute(
                select(MineWarAttack)
                .where(
                    MineWarAttack.war_id == war_id,
                    MineWarAttack.player_id == player_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            row = MineWarAttack(
                war_id=war_id, player_id=player_id, side=side, attempts_used=0, points=0
            )
            self._session.add(row)
            await self._session.flush()
        if int(row.attempts_used) >= mine_rules.ATTEMPTS_PER_WAR:
            return AttemptResult(
                ok=False, out_of_attempts=True, attempts_used=int(row.attempts_used)
            )
        row.attempts_used = int(row.attempts_used) + 1
        await self._session.flush()
        return AttemptResult(ok=True, attempts_used=int(row.attempts_used))

    async def add_points_atomic(
        self, war_id: int, player_id: int, side: str, points: int
    ) -> bool:
        """Credit duel points to the player row AND the war's side total,
        under the war row lock. False when the war resolved mid-fight (the
        window closed or the garrison fell) — points are simply lost."""
        war = (
            await self._session.execute(
                select(MineWar)
                .where(MineWar.id == war_id, MineWar.status == mine_rules.WAR_ACTIVE)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if war is None:
            return False
        if side == mine_rules.SIDE_ATTACK:
            war.attacker_points = int(war.attacker_points) + int(points)
        else:
            war.defender_points = int(war.defender_points) + int(points)
        row = (
            await self._session.execute(
                select(MineWarAttack).where(
                    MineWarAttack.war_id == war_id,
                    MineWarAttack.player_id == player_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            row.points = int(row.points) + int(points)
        await self._session.flush()
        return True

    async def list_war_attacks(self, war_id: int) -> list[MineWarAttack]:
        result = await self._session.execute(
            select(MineWarAttack)
            .where(MineWarAttack.war_id == war_id)
            .order_by(desc(MineWarAttack.points))
        )
        return list(result.scalars().all())

    # ── Siege damage ─────────────────────────────────────────────────────────

    async def apply_garrison_damage_atomic(
        self, war_id: int, damage: int, damage_cap: int | None = None
    ) -> GarrisonDamageResult:
        """Damage the siege pool under the war row lock; on kill, flip the
        mine to the attacker inside the same transaction (war → mine order)."""
        damage = max(0, int(damage))
        cap_hit = False
        if damage_cap is not None and damage > damage_cap:
            damage = max(0, int(damage_cap))
            cap_hit = True

        war = (
            await self._session.execute(
                select(MineWar)
                .where(
                    MineWar.id == war_id,
                    MineWar.status == mine_rules.WAR_ACTIVE,
                    MineWar.garrison_hp_current.is_not(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if war is None:
            return GarrisonDamageResult(
                applied=0, new_hp=0, captured=False, war_missing=True, cap_hit=cap_hit
            )

        current = int(war.garrison_hp_current or 0)
        applied = min(damage, current)
        new_hp = max(0, current - damage)
        war.garrison_hp_current = new_hp

        captured = False
        if new_hp <= 0:
            now = datetime.now(timezone.utc)
            war.status = mine_rules.WAR_RESOLVED
            war.winner_sect_id = war.attacker_sect_id
            war.resolved_at = now
            mine = (
                await self._session.execute(
                    select(SectMine)
                    .where(SectMine.mine_key == war.mine_key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if mine is not None:
                mine.occupier_sect_id = war.attacker_sect_id
                mine.occupied_since = now
                mine.last_payout_at = now
                mine.shield_until = None
                captured = True

        await self._session.flush()
        return GarrisonDamageResult(
            applied=applied, new_hp=new_hp, captured=captured, cap_hit=cap_hit
        )

    # ── Window resolution & payouts (sweep + lazy) ───────────────────────────

    async def resolve_due_wars(self, now: datetime) -> list[MineWar]:
        """Resolve every active war whose window ended. Returns the resolved
        wars so the caller can write sect logs. PvP: strictly more attacker
        points flips the mine (an idle defender loses to any showing); ties
        and sieges with a living garrison keep the status quo."""
        due = (
            await self._session.execute(
                select(MineWar.id).where(
                    MineWar.status == mine_rules.WAR_ACTIVE,
                    MineWar.window_end <= now,
                )
            )
        ).all()
        resolved: list[MineWar] = []
        for (war_id,) in due:
            war = (
                await self._session.execute(
                    select(MineWar)
                    .where(MineWar.id == war_id, MineWar.status == mine_rules.WAR_ACTIVE)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if war is None:
                continue

            attacker_won = (
                war.defender_sect_id is not None
                and int(war.attacker_points) > int(war.defender_points)
            )
            mine = (
                await self._session.execute(
                    select(SectMine)
                    .where(SectMine.mine_key == war.mine_key)
                    .with_for_update()
                )
            ).scalar_one_or_none()

            if attacker_won:
                war.winner_sect_id = war.attacker_sect_id
                if mine is not None:
                    mine.occupier_sect_id = war.attacker_sect_id
                    mine.occupied_since = now
                    mine.last_payout_at = now
                    mine.shield_until = None
            elif war.defender_sect_id is not None:
                war.winner_sect_id = war.defender_sect_id
                if mine is not None:
                    mine.shield_until = now + timedelta(
                        hours=mine_rules.DEFENSE_SHIELD_HOURS
                    )
            # Siege that timed out with a living garrison: no winner.

            war.status = mine_rules.WAR_RESOLVED
            war.resolved_at = now
            resolved.append(war)

        if resolved:
            await self._session.flush()
        return resolved

    async def accrue_payouts(self, now: datetime) -> int:
        """Settle whole-hour payouts for every occupied mine (sect → mine
        lock order). Returns total funds distributed this pass."""
        rows = (
            await self._session.execute(
                select(SectMine.id, SectMine.occupier_sect_id).where(
                    SectMine.occupier_sect_id.is_not(None),
                    SectMine.last_payout_at.is_not(None),
                    SectMine.last_payout_at <= now - timedelta(hours=1),
                )
            )
        ).all()
        total = 0
        for mine_id, sect_id in rows:
            sect = (
                await self._session.execute(
                    select(Sect).where(Sect.id == sect_id).with_for_update()
                )
            ).scalar_one_or_none()
            mine = (
                await self._session.execute(
                    select(SectMine).where(SectMine.id == mine_id).with_for_update()
                )
            ).scalar_one_or_none()
            if sect is None or mine is None or mine.occupier_sect_id != sect.id:
                continue
            hours, new_marker = mine_rules.payout_hours(mine.last_payout_at, now)
            if hours <= 0:
                continue
            payout = hours * mine_rules.funds_per_hour(mine.mine_key)
            sect.funds = int(sect.funds or 0) + payout
            mine.last_payout_at = new_marker
            total += payout
        if total:
            await self._session.flush()
        return total
