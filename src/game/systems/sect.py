"""Tông Môn (sect) rules — pure logic, no DB.

The level curve / caps load from ``src/data/sects/sect_levels.json`` via the
GameRegistry; everything else here (rank matrix, donation split, validation)
is code-level rules mirroring ``docs/tong_mon_design.md`` §2–3.

Repo-coupled orchestration lives in ``src.db.repositories.sect_repo`` — this
module must stay importable without a DB so the rules are unit-testable.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.game.constants.currencies import CURRENCY_CAP

# ── Ranks ─────────────────────────────────────────────────────────────────────

RANK_TONG_CHU = "tong_chu"
RANK_TRUONG_LAO = "truong_lao"
RANK_CHAP_SU = "chap_su"
RANK_DE_TU = "de_tu"

# Power ordering — higher acts on lower. Unknown ranks rate 0 (Đệ Tử).
_RANK_POWER = {
    RANK_DE_TU: 0,
    RANK_CHAP_SU: 1,
    RANK_TRUONG_LAO: 2,
    RANK_TONG_CHU: 3,
}

RANK_LABELS = {
    RANK_TONG_CHU: "Tông Chủ",
    RANK_TRUONG_LAO: "Trưởng Lão",
    RANK_CHAP_SU: "Chấp Sự",
    RANK_DE_TU: "Đệ Tử",
}

# Chấp Sự seat count is flat; Trưởng Lão seats grow with sect level — see
# ``truong_lao_cap``. Đệ Tử is uncapped (bounded by the sect member cap).
CHAP_SU_CAP = 4


def rank_power(rank: str) -> int:
    return _RANK_POWER.get(rank, 0)


def can_review_applications(rank: str) -> bool:
    """Chấp Sự and above approve/reject join applications."""
    return rank_power(rank) >= _RANK_POWER[RANK_CHAP_SU]


def can_set_announcement(rank: str) -> bool:
    return rank_power(rank) >= _RANK_POWER[RANK_TRUONG_LAO]


def can_kick(actor_rank: str, target_rank: str) -> bool:
    """Officers kick strictly below their own rank; Tông Chủ is untouchable."""
    return (
        rank_power(actor_rank) >= _RANK_POWER[RANK_CHAP_SU]
        and rank_power(actor_rank) > rank_power(target_rank)
    )


def can_set_rank(actor_rank: str, target_rank: str, new_rank: str) -> bool:
    """Promotion/demotion matrix (docs §3.2).

    * Any change touching Trưởng Lão (either direction) needs Tông Chủ.
    * Đệ Tử ↔ Chấp Sự needs Trưởng Lão or above.
    * The Tông Chủ seat never changes hands here — leadership transfer is a
      dedicated flow (``nhuongvi``).
    * Actor must strictly outrank the target's *current* rank.
    """
    if new_rank not in (RANK_DE_TU, RANK_CHAP_SU, RANK_TRUONG_LAO):
        return False
    if target_rank == RANK_TONG_CHU or new_rank == target_rank:
        return False
    if RANK_TRUONG_LAO in (target_rank, new_rank):
        required = _RANK_POWER[RANK_TONG_CHU]
    else:
        required = _RANK_POWER[RANK_TRUONG_LAO]
    return (
        rank_power(actor_rank) >= required
        and rank_power(actor_rank) > rank_power(target_rank)
    )


def officer_cap(rank: str, sect_level: int) -> int | None:
    """Seat cap for a rank at ``sect_level`` — None means uncapped."""
    if rank == RANK_TRUONG_LAO:
        return truong_lao_cap(sect_level)
    if rank == RANK_CHAP_SU:
        return CHAP_SU_CAP
    return None


def truong_lao_cap(sect_level: int) -> int:
    return 2 + sect_level // 3


# ── Creation gate ─────────────────────────────────────────────────────────────

SECT_CREATE_COST = 200_000          # Công Đức, burned (not into the new sect's funds)
SECT_CREATE_MIN_REALM = 3           # realm index, ANY axis (user-locked)


def meets_realm_gate(body_realm: int, qi_realm: int, formation_realm: int) -> bool:
    return max(body_realm, qi_realm, formation_realm) >= SECT_CREATE_MIN_REALM


# ── Name / tag validation ─────────────────────────────────────────────────────

NAME_MIN_LEN = 3
NAME_MAX_LEN = 32
TAG_MIN_LEN = 2
TAG_MAX_LEN = 6


def normalize_sect_name(raw: str) -> str:
    """NFC-normalize (Vietnamese diacritics have two Unicode forms), trim,
    and collapse internal whitespace runs to single spaces."""
    name = unicodedata.normalize("NFC", raw or "").strip()
    return " ".join(name.split())


def validate_sect_name(raw: str) -> tuple[str | None, str | None]:
    """Return ``(normalized_name, None)`` on success or ``(None, error_vi)``."""
    name = normalize_sect_name(raw)
    if len(name) < NAME_MIN_LEN or len(name) > NAME_MAX_LEN:
        return None, f"Tên Tông Môn phải dài {NAME_MIN_LEN}–{NAME_MAX_LEN} ký tự."
    if not all(ch.isalnum() or ch == " " for ch in name):
        return None, "Tên Tông Môn chỉ được chứa chữ, số và khoảng trắng."
    return name, None


def validate_sect_tag(raw: str) -> tuple[str | None, str | None]:
    """Tags are ASCII alphanumeric, stored uppercase — ``(tag, None)`` or ``(None, error_vi)``."""
    tag = (raw or "").strip().upper()
    if len(tag) < TAG_MIN_LEN or len(tag) > TAG_MAX_LEN:
        return None, f"Tag phải dài {TAG_MIN_LEN}–{TAG_MAX_LEN} ký tự."
    if not tag.isascii() or not tag.isalnum():
        return None, "Tag chỉ được chứa chữ cái không dấu và số."
    return tag, None


# ── Level curve (data-driven) ─────────────────────────────────────────────────

MAX_SECT_LEVEL = 10


def _level_rows() -> list[dict]:
    from src.data.registry import registry
    return registry.sect_levels


def level_row(level: int) -> dict:
    """Row for ``level``, clamped into the table's range."""
    rows = _level_rows()
    if not rows:
        raise RuntimeError("sect_levels.json missing — GameRegistry loaded without sect data")
    idx = max(0, min(len(rows) - 1, level - 1))
    return rows[idx]


def exp_to_next(level: int) -> int:
    """EXP needed to go ``level`` → ``level+1``; 0 at/after max level."""
    if level >= MAX_SECT_LEVEL:
        return 0
    return int(level_row(level)["exp_to_next"])


def member_cap(level: int) -> int:
    return int(level_row(level)["member_cap"])


def donation_cap(level: int) -> int:
    return int(level_row(level)["donation_cap"])


@dataclass(frozen=True)
class ExpApplyResult:
    level: int
    exp: int
    levels_gained: int


def apply_exp(level: int, exp: int, gained: int) -> ExpApplyResult:
    """Add ``gained`` EXP with multi-level carry-over, clamped at max level.

    At max level the EXP pool is pinned to 0 — there is nothing left to
    progress toward, and a frozen non-zero number reads like a bug in the UI.
    """
    level = max(1, min(MAX_SECT_LEVEL, level))
    exp = max(0, exp) + max(0, gained)
    levels_gained = 0
    while level < MAX_SECT_LEVEL:
        need = exp_to_next(level)
        if need <= 0 or exp < need:
            break
        exp -= need
        level += 1
        levels_gained += 1
    if level >= MAX_SECT_LEVEL:
        exp = 0
    return ExpApplyResult(level=level, exp=exp, levels_gained=levels_gained)


# ── Donation economy ──────────────────────────────────────────────────────────
# Per 1,000 Công Đức donated: +1,000 funds · +100 sect EXP · +100 Cống Hiến.
# Integer floor on the 10:1 rates — donate in round numbers to avoid dust.

DONATION_EXP_DIVISOR = 10
DONATION_CH_DIVISOR = 10

# Cống Hiến pools share the global currency ceiling.
CONTRIBUTION_CAP = CURRENCY_CAP


@dataclass(frozen=True)
class DonationSplit:
    funds: int
    exp: int
    contribution: int


def split_donation(amount: int) -> DonationSplit:
    amount = max(0, int(amount))
    return DonationSplit(
        funds=amount,
        exp=amount // DONATION_EXP_DIVISOR,
        contribution=amount // DONATION_CH_DIVISOR,
    )


def donated_so_far_today(donated_today: int, donation_date: date | None, today: date) -> int:
    """The member's spent donation budget for ``today`` — 0 after a date rollover."""
    if donation_date != today:
        return 0
    return max(0, donated_today)


def remaining_donation_today(
    donated_today: int, donation_date: date | None, today: date, sect_level: int
) -> int:
    cap = donation_cap(sect_level)
    return max(0, cap - donated_so_far_today(donated_today, donation_date, today))


# ── Facilities (Phase 2) ──────────────────────────────────────────────────────
# Definitions live in ``src/data/sects/facilities.json``. Two kinds:
#   * buff facilities (``buff_key`` set) — aggregate into ``Character.sect_buffs``
#     via ``facility_bonuses``; utility-only, never combat stats (design D2).
#   * structural facilities (``buff_key`` null) — later phases read their level
#     directly (shop gates, storage slots).
# A facility's level may never exceed the sect's level (progression gate) nor
# its own ``max_level``.


def _facility_defs() -> dict[str, dict]:
    from src.data.registry import registry
    return registry.sect_facilities


def all_facilities() -> list[dict]:
    """Facility definitions in display order."""
    return sorted(_facility_defs().values(), key=lambda f: int(f.get("order", 99)))


def facility_def(key: str) -> dict | None:
    return _facility_defs().get(key)


def facility_max_level(key: str) -> int:
    d = facility_def(key)
    return int(d.get("max_level", 0)) if d else 0


def facility_min_sect_level(key: str) -> int:
    """Sect level required before the facility may be BUILT at all (e.g.
    Kho Tàng unlocks at sect L2). Independent of the per-level gate
    ``facility_level < sect_level``."""
    d = facility_def(key)
    return int(d.get("min_sect_level", 1)) if d else 1


def facility_upgrade_cost(key: str, current_level: int) -> int | None:
    """Funds cost for ``current_level → current_level + 1``; None when maxed
    or unknown. ``upgrade_costs[i]`` is the price of reaching level ``i+1``."""
    d = facility_def(key)
    if d is None:
        return None
    costs = d.get("upgrade_costs") or []
    if current_level < 0 or current_level >= min(len(costs), int(d.get("max_level", 0))):
        return None
    return int(costs[current_level])


def facility_bonuses(levels: dict[str, int]) -> dict[str, float]:
    """Aggregate ``{facility_key: level}`` into ``{buff_key: value}``.

    Levels clamp into ``[0, max_level]``; structural facilities and unknown
    keys contribute nothing. Same-key bonuses from different facilities would
    stack additively (none share a key today).
    """
    out: dict[str, float] = {}
    for key, level in (levels or {}).items():
        d = facility_def(key)
        if not d or not d.get("buff_key"):
            continue
        lvl = max(0, min(int(level), int(d.get("max_level", 0))))
        if lvl <= 0:
            continue
        buff_key = str(d["buff_key"])
        out[buff_key] = out.get(buff_key, 0.0) + float(d.get("buff_per_level", 0.0)) * lvl
    return out


def facility_buff_line(key: str, level: int) -> str | None:
    """Human line for the facility's current total buff (None for structural)."""
    d = facility_def(key)
    if not d or not d.get("buff_key") or not d.get("buff_display"):
        return None
    lvl = max(0, min(int(level), int(d.get("max_level", 0))))
    total = float(d.get("buff_per_level", 0.0)) * lvl
    return str(d["buff_display"]).format(pct=f"{total * 100:g}%")


async def get_member_buffs(session, player_id: int) -> dict[str, float]:
    """Aggregated facility buffs for a player — ``{}`` for sect-less players.

    The one repo-coupled function in this module: one indexed join
    (sect_members → sect_facilities), then the pure ``facility_bonuses``.
    """
    from src.db.repositories.sect_repo import SectRepository
    levels = await SectRepository(session).get_facility_levels_for_player(player_id)
    if not levels:
        return {}
    return facility_bonuses(levels)


async def attach_sect_buffs(session, char) -> None:
    """Populate ``char.sect_buffs`` in place — the one-liner callers use before
    a flow that reads the buffs (offline tick, formation study, alchemy craft)."""
    char.sect_buffs = await get_member_buffs(session, char.player_id)


# ── Kho Tàng — shared storage (Phase 3) ───────────────────────────────────────
# Deposits are instant and irreversible (items become sect property — D3/D6);
# withdrawals go through a request → officer-approval flow. Stock is checked
# at APPROVE time, never reserved. Only stackable registry items may enter —
# equipment ``item_instances`` stay in direct trade (D6).

STORAGE_STACK_CAP = 9_999            # per (item_key, grade) slot
STORAGE_BASE_SLOTS = 15              # + STORAGE_SLOTS_PER_LEVEL × Kho Tàng level
STORAGE_SLOTS_PER_LEVEL = 5          # L1 = 20 slots … L10 = 65 slots
MAX_PENDING_STORAGE_REQUESTS = 2     # per member
MAX_WEEKLY_STORAGE_WITHDRAWALS = 5   # approved requests per member per ISO week
STORAGE_REQUEST_EXPIRE_HOURS = 72    # pending requests lazily expire after this

REQ_PENDING = "pending"
REQ_APPROVED = "approved"
REQ_REJECTED = "rejected"
REQ_EXPIRED = "expired"

# Item types that never enter the shared storage: per-player tools and
# account-scoped specials. Everything else stackable is fair game (herbs,
# pills, materials, chests, scrolls, gems, tinh huyết, …).
NON_STORABLE_TYPES = frozenset({"furnace", "special"})


def storage_slots(kho_tang_level: int) -> int:
    """Slot capacity for a Kho Tàng level — 0 means the vault isn't built."""
    if kho_tang_level <= 0:
        return 0
    return STORAGE_BASE_SLOTS + STORAGE_SLOTS_PER_LEVEL * int(kho_tang_level)


def is_storable_item(item: dict | None) -> bool:
    """Whether a registry item definition may be deposited (D6 whitelist)."""
    if not item:
        return False
    return str(item.get("type", "")) not in NON_STORABLE_TYPES


def week_start_utc(now: datetime) -> datetime:
    """Monday 00:00 UTC of ``now``'s ISO week — the weekly-withdrawal window."""
    monday = now.date() - timedelta(days=now.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=now.tzinfo)


def week_key(now: datetime) -> str:
    """ISO week label (e.g. ``"2026-W28"``) — keys the weekly shop-purchase
    ledger and the deterministic rotating-shop sample."""
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ── Daily check-in (Phase 4) ──────────────────────────────────────────────────

CHECKIN_CONTRIBUTION = 50        # Cống Hiến per check-in
CHECKIN_SECT_EXP = 100           # sect EXP per check-in


# ── Misc lifecycle rules ──────────────────────────────────────────────────────

REJOIN_COOLDOWN_HOURS = 24          # after leave/kick — anti sect-hopping
MAX_PENDING_APPLICATIONS = 3        # per player, across all sects
ANNOUNCEMENT_MAX_LEN = 500
SECT_LOG_KEEP = 100                 # newest rows kept per sect

# ── Log action keys (sect_logs.action) ────────────────────────────────────────

LOG_CREATE = "create"
LOG_JOIN = "join"
LOG_LEAVE = "leave"
LOG_KICK = "kick"
LOG_REJECT = "reject"
LOG_PROMOTE = "promote"
LOG_DEMOTE = "demote"
LOG_TRANSFER = "transfer"
LOG_DONATE = "donate"
LOG_ANNOUNCE = "announce"
LOG_LEVEL_UP = "level_up"
LOG_UPGRADE = "upgrade"
LOG_DEPOSIT = "deposit"
LOG_STORAGE_APPROVE = "storage_approve"
LOG_STORAGE_REJECT = "storage_reject"
LOG_BOSS_KILL = "boss_kill"
LOG_MINE_DECLARE = "mine_declare"
LOG_MINE_CAPTURE = "mine_capture"
LOG_MINE_DEFEND = "mine_defend"
LOG_MINE_LOST = "mine_lost"
