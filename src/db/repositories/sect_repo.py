"""Sect repository — membership CRUD, applications, and atomic fund/EXP/CH ops.

Concurrency discipline (mirrors ``world_boss_repo``): every mutation of shared
sect state goes through a method here that acquires ``SELECT … FOR UPDATE``
first, and callers consume the *returned/applied* values — never their own
inputs. **Lock order is always sect row → member row**; taking them in the
other order anywhere would open a deadlock window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.db.models.sect import (
    Sect,
    SectApplication,
    SectCooldown,
    SectFacility,
    SectLog,
    SectMember,
    SectShopPurchase,
    SectStorageItem,
    SectStorageRequest,
)
from src.game.systems import sect as sect_rules


@dataclass(frozen=True)
class DonationApplyResult:
    """Outcome of an atomic donation. ``applied`` is authoritative — the cog
    deducts exactly this much Công Đức from the player."""
    applied: int
    funds_added: int = 0
    exp_added: int = 0
    contribution_added: int = 0
    new_level: int = 0
    levels_gained: int = 0
    remaining_today: int = 0
    sect_missing: bool = False
    member_missing: bool = False


@dataclass(frozen=True)
class AddMemberResult:
    member: SectMember | None
    sect_full: bool = False
    sect_missing: bool = False


@dataclass(frozen=True)
class StorageDepositResult:
    """Outcome of an atomic storage deposit. ``applied`` ≤ requested (stack
    cap clamp); the caller removes exactly ``applied`` from the player's
    inventory in the same transaction."""
    applied: int
    slot_quantity: int = 0
    not_built: bool = False
    storage_full: bool = False
    stack_full: bool = False
    sect_missing: bool = False


@dataclass(frozen=True)
class StorageApproveResult:
    """Outcome of an atomic withdrawal approval. On ``ok`` the caller grants
    ``(item_key, grade, quantity)`` to ``requester_player_id`` in the same
    transaction (single commit ⇒ stock deduction + grant are atomic)."""
    ok: bool
    item_key: str = ""
    grade: int = 1
    quantity: int = 0
    requester_player_id: int = 0
    not_found: bool = False
    self_approval: bool = False
    requester_left: bool = False
    weekly_cap_hit: bool = False
    insufficient_stock: bool = False
    stock_available: int = 0


@dataclass(frozen=True)
class CheckinResult:
    ok: bool
    already_checked_in: bool = False
    member_missing: bool = False
    ch_added: int = 0
    exp_added: int = 0
    ch_total: int = 0
    new_level: int = 0
    levels_gained: int = 0


@dataclass(frozen=True)
class MissionClaimResult:
    """Outcome of a claim-all missions pass. ``claimed`` lists the mission
    keys that paid out this call (empty + no flags = nothing claimable)."""
    claimed: tuple[str, ...] = ()
    ch_added: int = 0
    exp_added: int = 0
    ch_total: int = 0
    new_level: int = 0
    levels_gained: int = 0
    locked: bool = False            # sect below MISSIONS_MIN_SECT_LEVEL
    member_missing: bool = False


@dataclass(frozen=True)
class ShopPurchaseResult:
    ok: bool
    cost: int = 0
    ch_left: int = 0
    member_missing: bool = False
    insufficient_ch: bool = False
    weekly_limited: bool = False
    weekly_remaining: int = 0


@dataclass(frozen=True)
class FacilityUpgradeResult:
    """Outcome of an atomic facility upgrade — exactly one flag/ok is the story."""
    ok: bool
    new_level: int = 0
    cost: int = 0
    funds_after: int = 0
    sect_missing: bool = False
    unknown_facility: bool = False
    maxed: bool = False
    level_gated: bool = False       # facility would exceed the sect's level / unlock gate
    sect_level_needed: int = 0      # set when level_gated
    insufficient_funds: bool = False
    funds_needed: int = 0


class SectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Lookups ──────────────────────────────────────────────────────────────

    async def get_sect_by_id(self, sect_id: int) -> Sect | None:
        result = await self._session.execute(select(Sect).where(Sect.id == sect_id))
        return result.scalar_one_or_none()

    async def get_sect_by_name(self, name: str) -> Sect | None:
        """Case-insensitive name lookup (matches the functional unique index)."""
        normalized = sect_rules.normalize_sect_name(name)
        result = await self._session.execute(
            select(Sect).where(func.lower(Sect.name) == normalized.lower())
        )
        return result.scalar_one_or_none()

    async def get_membership(self, player_id: int) -> SectMember | None:
        """The player's member row with ``.sect`` eagerly loaded (or None)."""
        result = await self._session.execute(
            select(SectMember)
            .where(SectMember.player_id == player_id)
            .options(joinedload(SectMember.sect))
        )
        return result.scalar_one_or_none()

    async def get_tag_for_player(self, player_id: int) -> str | None:
        """Lightweight ``[TAG]`` projection for status embeds — 1 indexed join."""
        result = await self._session.execute(
            select(Sect.tag)
            .join(SectMember, SectMember.sect_id == Sect.id)
            .where(SectMember.player_id == player_id)
        )
        return result.scalar_one_or_none()

    async def count_members(self, sect_id: int) -> int:
        result = await self._session.execute(
            select(func.count(SectMember.id)).where(SectMember.sect_id == sect_id)
        )
        return int(result.scalar_one() or 0)

    async def count_rank(self, sect_id: int, rank: str) -> int:
        result = await self._session.execute(
            select(func.count(SectMember.id)).where(
                SectMember.sect_id == sect_id, SectMember.rank == rank
            )
        )
        return int(result.scalar_one() or 0)

    async def list_members(self, sect_id: int) -> list[SectMember]:
        """All members, highest lifetime contribution first (rank grouping is
        a display concern — the cog sorts by rank power in Python)."""
        result = await self._session.execute(
            select(SectMember)
            .where(SectMember.sect_id == sect_id)
            .order_by(desc(SectMember.contribution_total), SectMember.id)
        )
        return list(result.scalars().all())

    async def list_sects_with_counts(
        self, offset: int = 0, limit: int = 10
    ) -> list[tuple[Sect, int]]:
        """Sects ordered by level/exp desc with member counts — powers ``/tongmon tim``."""
        result = await self._session.execute(
            select(Sect, func.count(SectMember.id))
            .outerjoin(SectMember, SectMember.sect_id == Sect.id)
            .group_by(Sect.id)
            .order_by(desc(Sect.level), desc(Sect.exp), Sect.id)
            .offset(offset)
            .limit(limit)
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def count_sects(self) -> int:
        result = await self._session.execute(select(func.count(Sect.id)))
        return int(result.scalar_one() or 0)

    async def search_sect_names(self, query: str, limit: int = 25) -> list[str]:
        """Substring name search (case-insensitive) — powers slash autocomplete."""
        q = (query or "").strip().lower()
        stmt = select(Sect.name).order_by(desc(Sect.level), Sect.name).limit(limit)
        if q:
            stmt = stmt.where(func.lower(Sect.name).like(f"%{q}%"))
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def create_sect(self, name: str, tag: str, leader_player_id: int) -> Sect:
        """Insert sect + Tông Chủ member row. Name/tag uniqueness races surface
        as ``IntegrityError`` at flush — the cog maps them to user messages."""
        sect = Sect(name=name, tag=tag, leader_player_id=leader_player_id)
        self._session.add(sect)
        await self._session.flush()  # get sect.id (and trip unique indexes)

        member = SectMember(
            sect_id=sect.id,
            player_id=leader_player_id,
            rank=sect_rules.RANK_TONG_CHU,
        )
        self._session.add(member)
        # Founding a sect supersedes any pending applications elsewhere.
        await self._session.execute(
            delete(SectApplication).where(SectApplication.player_id == leader_player_id)
        )
        await self._session.flush()
        return sect

    async def delete_sect(self, sect: Sect) -> None:
        """ORM-level delete — cascades members/facilities/applications/logs."""
        await self._session.delete(sect)
        await self._session.flush()

    async def add_member_atomic(
        self, sect_id: int, player_id: int, rank: str = sect_rules.RANK_DE_TU
    ) -> AddMemberResult:
        """Admit ``player_id`` under the sect row lock so the member-cap check
        and the INSERT are one atomic step (two concurrent approvals cannot
        overshoot the cap). Double-join across sects is stopped by the
        ``uq_sect_members_player`` constraint — callers catch IntegrityError.
        """
        sect = await self._lock_sect(sect_id)
        if sect is None:
            return AddMemberResult(member=None, sect_missing=True)

        current = await self.count_members(sect_id)
        if current >= sect_rules.member_cap(sect.level):
            return AddMemberResult(member=None, sect_full=True)

        member = SectMember(sect_id=sect_id, player_id=player_id, rank=rank)
        self._session.add(member)
        # Joining consumes every pending application this player had anywhere.
        await self._session.execute(
            delete(SectApplication).where(SectApplication.player_id == player_id)
        )
        await self._session.flush()
        return AddMemberResult(member=member)

    async def remove_member(self, member: SectMember, cooldown: bool = True) -> None:
        """Delete the member row (Cống Hiến is wiped by design — D3), drop any
        pending storage requests, and stamp the rejoin cooldown. Resolved
        storage requests stay — they're the audit trail."""
        player_id = member.player_id
        sect_id = member.sect_id
        await self.cancel_pending_requests(sect_id, player_id)
        await self._session.delete(member)
        if cooldown:
            await self.set_rejoin_cooldown(player_id)
        await self._session.flush()

    async def transfer_leadership_atomic(
        self, sect_id: int, from_player_id: int, to_player_id: int
    ) -> bool:
        """Swap the Tông Chủ seat under the sect row lock. The outgoing leader
        lands on Trưởng Lão — this may transiently overflow the elder cap,
        which is only enforced at promote-time (deliberate: never brick a
        transfer because the council is full)."""
        sect = await self._lock_sect(sect_id)
        if sect is None or sect.leader_player_id != from_player_id:
            return False

        old = await self._lock_member_by_player(sect_id, from_player_id)
        new = await self._lock_member_by_player(sect_id, to_player_id)
        if old is None or new is None:
            return False

        sect.leader_player_id = to_player_id
        old.rank = sect_rules.RANK_TRUONG_LAO
        new.rank = sect_rules.RANK_TONG_CHU
        await self._session.flush()
        return True

    # ── Cooldowns ────────────────────────────────────────────────────────────

    async def set_rejoin_cooldown(self, player_id: int) -> None:
        until = datetime.now(timezone.utc) + timedelta(
            hours=sect_rules.REJOIN_COOLDOWN_HOURS
        )
        existing = await self._session.get(SectCooldown, player_id)
        if existing is None:
            self._session.add(SectCooldown(player_id=player_id, rejoin_after=until))
        else:
            existing.rejoin_after = until
        await self._session.flush()

    async def get_rejoin_cooldown(self, player_id: int) -> datetime | None:
        """Active embargo timestamp, or None when the player may join."""
        row = await self._session.get(SectCooldown, player_id)
        if row is None:
            return None
        if row.rejoin_after <= datetime.now(timezone.utc):
            return None
        return row.rejoin_after

    # ── Applications ─────────────────────────────────────────────────────────

    async def get_application(self, sect_id: int, player_id: int) -> SectApplication | None:
        result = await self._session.execute(
            select(SectApplication).where(
                SectApplication.sect_id == sect_id,
                SectApplication.player_id == player_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_applications(self, sect_id: int) -> list[SectApplication]:
        result = await self._session.execute(
            select(SectApplication)
            .where(SectApplication.sect_id == sect_id)
            .order_by(SectApplication.created_at)
        )
        return list(result.scalars().all())

    async def list_applications_for_player(self, player_id: int) -> list[SectApplication]:
        result = await self._session.execute(
            select(SectApplication)
            .where(SectApplication.player_id == player_id)
            .options(joinedload(SectApplication.sect))
            .order_by(SectApplication.created_at)
        )
        return list(result.scalars().all())

    async def count_pending_for_player(self, player_id: int) -> int:
        result = await self._session.execute(
            select(func.count(SectApplication.id)).where(
                SectApplication.player_id == player_id
            )
        )
        return int(result.scalar_one() or 0)

    async def create_application(
        self, sect_id: int, player_id: int, message: str | None
    ) -> SectApplication:
        app = SectApplication(sect_id=sect_id, player_id=player_id, message=message)
        self._session.add(app)
        await self._session.flush()
        return app

    async def delete_application(self, app: SectApplication) -> None:
        await self._session.delete(app)
        await self._session.flush()

    # ── Atomic economy ops ───────────────────────────────────────────────────

    async def apply_donation_atomic(
        self, sect_id: int, player_id: int, amount: int, today: date
    ) -> DonationApplyResult:
        """Clamp ``amount`` to the member's remaining daily budget and apply the
        fund/EXP/CH split under locks (sect → member). Returns what actually
        landed; the caller deducts ``result.applied`` Công Đức from the player
        in the same transaction."""
        amount = max(0, int(amount))

        sect = await self._lock_sect(sect_id)
        if sect is None:
            return DonationApplyResult(applied=0, sect_missing=True)

        member = await self._lock_member_by_player(sect_id, player_id)
        if member is None:
            return DonationApplyResult(applied=0, member_missing=True)

        spent = sect_rules.donated_so_far_today(
            member.donated_today, member.donation_date, today
        )
        cap = sect_rules.donation_cap(sect.level)
        applied = min(amount, max(0, cap - spent))
        if applied <= 0:
            return DonationApplyResult(
                applied=0, new_level=sect.level, remaining_today=max(0, cap - spent)
            )

        split = sect_rules.split_donation(applied)
        exp_res = sect_rules.apply_exp(sect.level, sect.exp, split.exp)

        sect.funds = int(sect.funds or 0) + split.funds
        sect.level = exp_res.level
        sect.exp = exp_res.exp

        member.contribution_points = min(
            int(member.contribution_points or 0) + split.contribution,
            sect_rules.CONTRIBUTION_CAP,
        )
        member.contribution_total = int(member.contribution_total or 0) + split.contribution
        member.donated_today = spent + applied
        member.donation_date = today

        await self._session.flush()
        return DonationApplyResult(
            applied=applied,
            funds_added=split.funds,
            exp_added=split.exp,
            contribution_added=split.contribution,
            new_level=exp_res.level,
            levels_gained=exp_res.levels_gained,
            remaining_today=max(0, cap - (spent + applied)),
        )

    # ── Facilities ───────────────────────────────────────────────────────────

    async def get_facility_levels(self, sect_id: int) -> dict[str, int]:
        """``{facility_key: level}`` for a sect — unbuilt facilities absent (level 0)."""
        result = await self._session.execute(
            select(SectFacility.facility_key, SectFacility.level).where(
                SectFacility.sect_id == sect_id
            )
        )
        return {key: int(level) for key, level in result.all()}

    async def get_facility_levels_for_player(self, player_id: int) -> dict[str, int]:
        """Facility levels of the player's sect via one indexed join — ``{}``
        when sect-less. Backs ``sect.get_member_buffs`` on hot paths (offline
        tick), so it deliberately avoids loading any ORM entities."""
        result = await self._session.execute(
            select(SectFacility.facility_key, SectFacility.level)
            .join(SectMember, SectMember.sect_id == SectFacility.sect_id)
            .where(SectMember.player_id == player_id)
        )
        return {key: int(level) for key, level in result.all()}

    async def upgrade_facility_atomic(
        self, sect_id: int, facility_key: str
    ) -> FacilityUpgradeResult:
        """Raise ``facility_key`` one level, spending sect funds — all checks
        (max level, sect-level gate, funds) run under the sect row lock so two
        officers clicking at once can't double-spend or skip a level."""
        fdef = sect_rules.facility_def(facility_key)
        if fdef is None:
            return FacilityUpgradeResult(ok=False, unknown_facility=True)

        sect = await self._lock_sect(sect_id)
        if sect is None:
            return FacilityUpgradeResult(ok=False, sect_missing=True)

        row = await self._lock_facility(sect_id, facility_key)
        current = int(row.level) if row is not None else 0

        if current >= sect_rules.facility_max_level(facility_key):
            return FacilityUpgradeResult(ok=False, maxed=True, new_level=current)
        # Two level gates: the facility can't exceed the sect's level, and some
        # facilities (Kho Tàng) additionally unlock only from a min sect level.
        needed = max(current + 1, sect_rules.facility_min_sect_level(facility_key))
        if int(sect.level) < needed:
            return FacilityUpgradeResult(
                ok=False, level_gated=True, new_level=current, sect_level_needed=needed,
            )

        cost = sect_rules.facility_upgrade_cost(facility_key, current)
        if cost is None:
            return FacilityUpgradeResult(ok=False, maxed=True, new_level=current)
        if int(sect.funds or 0) < cost:
            return FacilityUpgradeResult(
                ok=False, insufficient_funds=True, new_level=current,
                cost=cost, funds_after=int(sect.funds or 0), funds_needed=cost,
            )

        sect.funds = int(sect.funds) - cost
        if row is None:
            row = SectFacility(sect_id=sect_id, facility_key=facility_key, level=1)
            self._session.add(row)
        else:
            row.level = current + 1
        await self._session.flush()
        return FacilityUpgradeResult(
            ok=True, new_level=current + 1, cost=cost, funds_after=int(sect.funds),
        )

    # ── Check-in & missions (Phase 4) ────────────────────────────────────────

    async def apply_checkin_atomic(
        self, sect_id: int, player_id: int, today: date
    ) -> CheckinResult:
        """Daily check-in under locks (sect → member): +CH to the member,
        +EXP to the sect, once per UTC day."""
        sect = await self._lock_sect(sect_id)
        if sect is None:
            return CheckinResult(ok=False, member_missing=True)
        member = await self._lock_member_by_player(sect_id, player_id)
        if member is None:
            return CheckinResult(ok=False, member_missing=True)
        if member.last_checkin_date == today:
            return CheckinResult(
                ok=False, already_checked_in=True,
                ch_total=int(member.contribution_points),
            )

        ch = sect_rules.CHECKIN_CONTRIBUTION
        exp = sect_rules.CHECKIN_SECT_EXP
        member.last_checkin_date = today
        member.contribution_points = min(
            int(member.contribution_points or 0) + ch, sect_rules.CONTRIBUTION_CAP
        )
        member.contribution_total = int(member.contribution_total or 0) + ch

        exp_res = sect_rules.apply_exp(sect.level, sect.exp, exp)
        sect.level, sect.exp = exp_res.level, exp_res.exp

        await self._session.flush()
        return CheckinResult(
            ok=True, ch_added=ch, exp_added=exp,
            ch_total=int(member.contribution_points),
            new_level=exp_res.level, levels_gained=exp_res.levels_gained,
        )

    async def claim_missions_atomic(
        self, sect_id: int, player_id: int, today: date
    ) -> MissionClaimResult:
        """Claim every complete-but-unclaimed daily mission under locks
        (sect → member). Rewards batch into one CH/EXP application."""
        from src.game.systems import sect_missions as sm

        sect = await self._lock_sect(sect_id)
        if sect is None:
            return MissionClaimResult(member_missing=True)
        if int(sect.level) < sm.MISSIONS_MIN_SECT_LEVEL:
            return MissionClaimResult(locked=True)
        member = await self._lock_member_by_player(sect_id, player_id)
        if member is None:
            return MissionClaimResult(member_missing=True)

        state = sm.parse_progress(member.mission_progress, today)
        keys = sm.claimable_keys(state)
        if not keys:
            return MissionClaimResult(ch_total=int(member.contribution_points))

        ch_total_add = 0
        exp_total_add = 0
        for key in keys:
            mdef = sm.mission_def(key) or {}
            ch_total_add += int(mdef.get("reward_ch", 0))
            exp_total_add += int(mdef.get("reward_exp", 0))

        state["claimed"] = list(state.get("claimed", [])) + keys
        member.mission_progress = sm.encode_progress(state)
        member.contribution_points = min(
            int(member.contribution_points or 0) + ch_total_add,
            sect_rules.CONTRIBUTION_CAP,
        )
        member.contribution_total = int(member.contribution_total or 0) + ch_total_add

        exp_res = sect_rules.apply_exp(sect.level, sect.exp, exp_total_add)
        sect.level, sect.exp = exp_res.level, exp_res.exp

        await self._session.flush()
        return MissionClaimResult(
            claimed=tuple(keys),
            ch_added=ch_total_add,
            exp_added=exp_total_add,
            ch_total=int(member.contribution_points),
            new_level=exp_res.level,
            levels_gained=exp_res.levels_gained,
        )

    # ── Sect boss payouts (Phase 5) ──────────────────────────────────────────

    async def grant_sect_rewards_atomic(
        self, sect_id: int, exp: int, funds: int
    ) -> tuple[int, int]:
        """One-time sect-side boss-kill grant under the sect lock. The caller
        must have won ``flag_rewards_distributed`` first — that flag is what
        makes this once-per-instance. Returns ``(new_level, levels_gained)``."""
        sect = await self._lock_sect(sect_id)
        if sect is None:
            return 0, 0
        sect.funds = int(sect.funds or 0) + max(0, int(funds))
        exp_res = sect_rules.apply_exp(sect.level, sect.exp, max(0, int(exp)))
        sect.level, sect.exp = exp_res.level, exp_res.exp
        await self._session.flush()
        return exp_res.level, exp_res.levels_gained

    async def add_contribution_atomic(
        self, sect_id: int, player_id: int, amount: int
    ) -> bool:
        """Grant Cống Hiến to a member under their row lock — False when the
        player is no longer in that sect (boss claims after leaving pay the
        chest but the CH has nowhere to land)."""
        member = await self._lock_member_by_player(sect_id, player_id)
        if member is None:
            return False
        amount = max(0, int(amount))
        member.contribution_points = min(
            int(member.contribution_points or 0) + amount, sect_rules.CONTRIBUTION_CAP
        )
        member.contribution_total = int(member.contribution_total or 0) + amount
        await self._session.flush()
        return True

    # ── Sect shop (Phase 4) ──────────────────────────────────────────────────

    async def get_weekly_purchases(
        self, sect_id: int, player_id: int, week_key: str
    ) -> dict[tuple[str, int], int]:
        """``{(item_key, grade): quantity}`` bought by the member this week —
        renders the "còn x/limit" hints in the shop view."""
        result = await self._session.execute(
            select(
                SectShopPurchase.item_key,
                SectShopPurchase.grade,
                SectShopPurchase.quantity,
            ).where(
                SectShopPurchase.sect_id == sect_id,
                SectShopPurchase.player_id == player_id,
                SectShopPurchase.week_key == week_key,
            )
        )
        return {(key, int(grade)): int(qty) for key, grade, qty in result.all()}

    async def purchase_shop_item_atomic(
        self,
        sect_id: int,
        player_id: int,
        item_key: str,
        grade: int,
        unit_price: int,
        quantity: int,
        weekly_limit: int,
        week_key: str,
    ) -> ShopPurchaseResult:
        """CH deduction + weekly-limit ledger under the member row lock. The
        caller grants the items to inventory in the same transaction."""
        quantity = max(1, int(quantity))
        cost = int(unit_price) * quantity

        member = await self._lock_member_by_player(sect_id, player_id)
        if member is None:
            return ShopPurchaseResult(ok=False, member_missing=True)

        ledger = None
        if weekly_limit > 0:
            result = await self._session.execute(
                select(SectShopPurchase)
                .where(
                    SectShopPurchase.sect_id == sect_id,
                    SectShopPurchase.player_id == player_id,
                    SectShopPurchase.item_key == item_key,
                    SectShopPurchase.grade == int(grade),
                    SectShopPurchase.week_key == week_key,
                )
                .with_for_update()
            )
            ledger = result.scalar_one_or_none()
            bought = int(ledger.quantity) if ledger is not None else 0
            if bought + quantity > int(weekly_limit):
                return ShopPurchaseResult(
                    ok=False, weekly_limited=True,
                    weekly_remaining=max(0, int(weekly_limit) - bought),
                )

        have = int(member.contribution_points or 0)
        if have < cost:
            return ShopPurchaseResult(
                ok=False, insufficient_ch=True, cost=cost, ch_left=have
            )

        member.contribution_points = have - cost
        if weekly_limit > 0:
            if ledger is None:
                ledger = SectShopPurchase(
                    sect_id=sect_id, player_id=player_id, item_key=item_key,
                    grade=int(grade), week_key=week_key, quantity=quantity,
                )
                self._session.add(ledger)
            else:
                ledger.quantity = int(ledger.quantity) + quantity

        await self._session.flush()
        return ShopPurchaseResult(
            ok=True, cost=cost, ch_left=int(member.contribution_points)
        )

    # ── Kho Tàng storage ─────────────────────────────────────────────────────

    async def list_storage_items(self, sect_id: int) -> list[SectStorageItem]:
        result = await self._session.execute(
            select(SectStorageItem)
            .where(SectStorageItem.sect_id == sect_id, SectStorageItem.quantity > 0)
            .order_by(SectStorageItem.item_key, SectStorageItem.grade)
        )
        return list(result.scalars().all())

    async def count_storage_slots(self, sect_id: int) -> int:
        result = await self._session.execute(
            select(func.count(SectStorageItem.id)).where(
                SectStorageItem.sect_id == sect_id
            )
        )
        return int(result.scalar_one() or 0)

    async def deposit_storage_atomic(
        self, sect_id: int, item_key: str, grade: int, quantity: int
    ) -> StorageDepositResult:
        """Add stock under the sect row lock — capacity (slot count vs Kho Tàng
        level) and the per-stack cap are enforced inside the locked window.
        Caller removes ``result.applied`` from the depositor's inventory in the
        same transaction (rolling back everything if that fails)."""
        quantity = max(0, int(quantity))
        sect = await self._lock_sect(sect_id)
        if sect is None:
            return StorageDepositResult(applied=0, sect_missing=True)

        levels = await self.get_facility_levels(sect_id)
        capacity = sect_rules.storage_slots(levels.get("kho_tang", 0))
        if capacity <= 0:
            return StorageDepositResult(applied=0, not_built=True)

        result = await self._session.execute(
            select(SectStorageItem)
            .where(
                SectStorageItem.sect_id == sect_id,
                SectStorageItem.item_key == item_key,
                SectStorageItem.grade == int(grade),
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()

        if row is None:
            used = await self.count_storage_slots(sect_id)
            if used >= capacity:
                return StorageDepositResult(applied=0, storage_full=True)
            applied = min(quantity, sect_rules.STORAGE_STACK_CAP)
            if applied <= 0:
                return StorageDepositResult(applied=0)
            row = SectStorageItem(
                sect_id=sect_id, item_key=item_key, grade=int(grade), quantity=applied
            )
            self._session.add(row)
        else:
            room = sect_rules.STORAGE_STACK_CAP - int(row.quantity)
            applied = min(quantity, max(0, room))
            if applied <= 0:
                return StorageDepositResult(
                    applied=0, stack_full=True, slot_quantity=int(row.quantity)
                )
            row.quantity = int(row.quantity) + applied

        await self._session.flush()
        return StorageDepositResult(applied=applied, slot_quantity=int(row.quantity))

    # ── Storage requests ─────────────────────────────────────────────────────

    async def list_pending_requests(self, sect_id: int) -> list[SectStorageRequest]:
        result = await self._session.execute(
            select(SectStorageRequest)
            .where(
                SectStorageRequest.sect_id == sect_id,
                SectStorageRequest.status == sect_rules.REQ_PENDING,
            )
            .order_by(SectStorageRequest.created_at)
        )
        return list(result.scalars().all())

    async def list_pending_requests_for_player(
        self, sect_id: int, player_id: int
    ) -> list[SectStorageRequest]:
        result = await self._session.execute(
            select(SectStorageRequest).where(
                SectStorageRequest.sect_id == sect_id,
                SectStorageRequest.requester_player_id == player_id,
                SectStorageRequest.status == sect_rules.REQ_PENDING,
            )
        )
        return list(result.scalars().all())

    async def count_weekly_approved(
        self, sect_id: int, player_id: int, week_start: datetime
    ) -> int:
        result = await self._session.execute(
            select(func.count(SectStorageRequest.id)).where(
                SectStorageRequest.sect_id == sect_id,
                SectStorageRequest.requester_player_id == player_id,
                SectStorageRequest.status == sect_rules.REQ_APPROVED,
                SectStorageRequest.resolved_at >= week_start,
            )
        )
        return int(result.scalar_one() or 0)

    async def create_storage_request(
        self, sect_id: int, player_id: int, item_key: str, grade: int, quantity: int
    ) -> SectStorageRequest:
        req = SectStorageRequest(
            sect_id=sect_id,
            requester_player_id=player_id,
            item_key=item_key,
            grade=int(grade),
            quantity=int(quantity),
        )
        self._session.add(req)
        await self._session.flush()
        return req

    async def expire_stale_requests(self, sect_id: int, cutoff: datetime) -> int:
        """Lazily flip pending requests older than ``cutoff`` to expired.
        Called from every review/list path so stale rows never pile up."""
        result = await self._session.execute(
            update(SectStorageRequest)
            .where(
                SectStorageRequest.sect_id == sect_id,
                SectStorageRequest.status == sect_rules.REQ_PENDING,
                SectStorageRequest.created_at < cutoff,
            )
            .values(
                status=sect_rules.REQ_EXPIRED,
                resolved_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def cancel_pending_requests(self, sect_id: int, player_id: int) -> int:
        """Delete a member's own pending requests (also used on leave/kick)."""
        result = await self._session.execute(
            delete(SectStorageRequest).where(
                SectStorageRequest.sect_id == sect_id,
                SectStorageRequest.requester_player_id == player_id,
                SectStorageRequest.status == sect_rules.REQ_PENDING,
            )
        )
        return int(result.rowcount or 0)

    async def approve_storage_request_atomic(
        self, request_id: int, sect_id: int, reviewer_player_id: int,
        week_start: datetime,
    ) -> StorageApproveResult:
        """Approve under locks (sect → request → storage row). Re-validates
        everything the UI showed: request still pending & of this sect, no
        self-approval, requester still a member and under the weekly cap,
        stock still sufficient. Stock is deducted here; the caller grants the
        items to the requester's inventory in the same transaction."""
        sect = await self._lock_sect(sect_id)
        if sect is None:
            return StorageApproveResult(ok=False, not_found=True)

        result = await self._session.execute(
            select(SectStorageRequest)
            .where(
                SectStorageRequest.id == request_id,
                SectStorageRequest.sect_id == sect_id,
                SectStorageRequest.status == sect_rules.REQ_PENDING,
            )
            .with_for_update()
        )
        req = result.scalar_one_or_none()
        if req is None:
            return StorageApproveResult(ok=False, not_found=True)

        if req.requester_player_id == reviewer_player_id:
            return StorageApproveResult(ok=False, self_approval=True)

        member_check = await self._session.execute(
            select(SectMember.id).where(
                SectMember.sect_id == sect_id,
                SectMember.player_id == req.requester_player_id,
            )
        )
        if member_check.scalar_one_or_none() is None:
            req.status = sect_rules.REQ_EXPIRED
            req.resolved_at = datetime.now(timezone.utc)
            await self._session.flush()
            return StorageApproveResult(ok=False, requester_left=True)

        approved = await self.count_weekly_approved(
            sect_id, req.requester_player_id, week_start
        )
        if approved >= sect_rules.MAX_WEEKLY_STORAGE_WITHDRAWALS:
            return StorageApproveResult(ok=False, weekly_cap_hit=True)

        stock_result = await self._session.execute(
            select(SectStorageItem)
            .where(
                SectStorageItem.sect_id == sect_id,
                SectStorageItem.item_key == req.item_key,
                SectStorageItem.grade == req.grade,
            )
            .with_for_update()
        )
        stock = stock_result.scalar_one_or_none()
        available = int(stock.quantity) if stock is not None else 0
        if stock is None or available < int(req.quantity):
            return StorageApproveResult(
                ok=False, insufficient_stock=True, stock_available=available
            )

        stock.quantity = available - int(req.quantity)
        if stock.quantity <= 0:
            await self._session.delete(stock)

        req.status = sect_rules.REQ_APPROVED
        req.reviewed_by_player_id = reviewer_player_id
        req.resolved_at = datetime.now(timezone.utc)
        await self._session.flush()
        return StorageApproveResult(
            ok=True,
            item_key=req.item_key,
            grade=int(req.grade),
            quantity=int(req.quantity),
            requester_player_id=int(req.requester_player_id),
        )

    async def reject_storage_request(
        self, req: SectStorageRequest, reviewer_player_id: int
    ) -> None:
        req.status = sect_rules.REQ_REJECTED
        req.reviewed_by_player_id = reviewer_player_id
        req.resolved_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def get_storage_request(self, request_id: int) -> SectStorageRequest | None:
        return await self._session.get(SectStorageRequest, request_id)

    # ── Logs ─────────────────────────────────────────────────────────────────

    async def add_log(
        self, sect_id: int, actor_player_id: int | None, action: str, detail: str = ""
    ) -> None:
        """Append an audit line, then prune the sect's tail beyond
        ``SECT_LOG_KEEP`` newest rows (id order ≙ insertion order)."""
        self._session.add(SectLog(
            sect_id=sect_id,
            actor_player_id=actor_player_id,
            action=action,
            detail=detail[:200],
        ))
        await self._session.flush()

        keep_ids = (
            select(SectLog.id)
            .where(SectLog.sect_id == sect_id)
            .order_by(desc(SectLog.id))
            .limit(sect_rules.SECT_LOG_KEEP)
            .scalar_subquery()
        )
        await self._session.execute(
            delete(SectLog).where(
                SectLog.sect_id == sect_id,
                SectLog.id.not_in(keep_ids),
            )
        )

    async def list_logs(self, sect_id: int, limit: int = 10) -> list[SectLog]:
        result = await self._session.execute(
            select(SectLog)
            .where(SectLog.sect_id == sect_id)
            .order_by(desc(SectLog.id))
            .limit(limit)
        )
        return list(result.scalars().all())

    # ── Internal lock helpers (lock order: sect → member) ────────────────────

    async def _lock_sect(self, sect_id: int) -> Sect | None:
        result = await self._session.execute(
            select(Sect).where(Sect.id == sect_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _lock_member_by_player(
        self, sect_id: int, player_id: int
    ) -> SectMember | None:
        """Lock a member row, re-verifying it still belongs to ``sect_id`` —
        the membership may have changed between the caller's read and the lock."""
        result = await self._session.execute(
            select(SectMember)
            .where(
                SectMember.sect_id == sect_id,
                SectMember.player_id == player_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _lock_facility(
        self, sect_id: int, facility_key: str
    ) -> SectFacility | None:
        """Lock a facility row (child lock — always taken AFTER the sect row).
        None when the facility was never built (implicit level 0)."""
        result = await self._session.execute(
            select(SectFacility)
            .where(
                SectFacility.sect_id == sect_id,
                SectFacility.facility_key == facility_key,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()
