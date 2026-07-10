"""Tông Môn cog — sect creation, membership, ranks, donations (Phase 1).

Discord layer only: every rule lives in ``game/systems/sect.py`` and every
mutation goes through ``db/repositories/sect_repo.py``. Views re-validate
permissions inside a fresh session on every click — button state is never
trusted (ranks / membership may have changed since the view was rendered).
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy.exc import IntegrityError

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.sect import SectApplication
from src.db.repositories.constitution_process import load_constitution_levels
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.db.repositories.sect_boss_repo import SectBossRepository
from src.db.repositories.sect_mine_repo import SectMineRepository
from src.db.repositories.sect_repo import SectRepository
from src.db.repositories.skill_mastery import add_combat_xp, get_mastery_map
from src.game.constants.grades import GRADE_LABELS, Grade
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems import sect as sect_rules
from src.game.systems import sect_boss as boss_rules
from src.game.systems import sect_mine as mine_rules
from src.game.systems import sect_missions as mission_rules
from src.game.systems import sect_shop as shop_rules
from src.game.systems.combat import CombatSession, build_player_combatant
from src.game.systems.dungeon import compute_realm_total
from src.game.systems.world_boss import format_leaderboard
from src.utils.config import settings
from src.utils.discord_safe import safe_defer
from src.utils.embed_builder import (
    base_embed,
    battle_embed,
    error_embed,
    progress_bar,
    success_embed,
)

log = logging.getLogger(__name__)

SECT_COLOR = 0xB8860B

RANK_ICONS = {
    sect_rules.RANK_TONG_CHU: "👑",
    sect_rules.RANK_TRUONG_LAO: "⭐",
    sect_rules.RANK_CHAP_SU: "🔰",
    sect_rules.RANK_DE_TU: "🧘",
}

_LOG_ICONS = {
    sect_rules.LOG_CREATE: "🏯",
    sect_rules.LOG_JOIN: "📥",
    sect_rules.LOG_LEAVE: "📤",
    sect_rules.LOG_KICK: "🥾",
    sect_rules.LOG_REJECT: "🚫",
    sect_rules.LOG_PROMOTE: "⬆️",
    sect_rules.LOG_DEMOTE: "⬇️",
    sect_rules.LOG_TRANSFER: "👑",
    sect_rules.LOG_DONATE: "💰",
    sect_rules.LOG_ANNOUNCE: "📣",
    sect_rules.LOG_LEVEL_UP: "🎆",
    sect_rules.LOG_UPGRADE: "🏗️",
    sect_rules.LOG_DEPOSIT: "📦",
    sect_rules.LOG_STORAGE_APPROVE: "✅",
    sect_rules.LOG_STORAGE_REJECT: "🚫",
    sect_rules.LOG_BOSS_KILL: "🐲",
    sect_rules.LOG_MINE_DECLARE: "⚔️",
    sect_rules.LOG_MINE_CAPTURE: "⛏️",
    sect_rules.LOG_MINE_DEFEND: "🛡️",
    sect_rules.LOG_MINE_LOST: "💔",
}


def _grade_tag(grade: int) -> str:
    """Short grade suffix — enum grades get their Vietnamese label, legacy
    grades (5–6 material rows) fall back to a numeric tag."""
    try:
        return GRADE_LABELS[Grade(int(grade))][0]
    except ValueError:
        return f"Cấp {grade}"


def _item_display(item_key: str, grade: int) -> str:
    item = registry.get_item(item_key)
    name = item["vi"] if item else item_key
    return f"{name} ({_grade_tag(grade)})"


def _parse_item_value(raw: str) -> tuple[str, int] | None:
    """Decode an autocomplete value ``item_key|grade``; tolerate a bare key
    typed by hand by falling back to the item's template grade."""
    raw = (raw or "").strip()
    if "|" in raw:
        key, _, g = raw.rpartition("|")
        if key and g.isdigit():
            return key, int(g)
        return None
    item = registry.get_item(raw)
    if item:
        return raw, int(item.get("grade", 1) or 1)
    return None


def _rank_label(rank: str) -> str:
    return f"{RANK_ICONS.get(rank, '🧘')} {sect_rules.RANK_LABELS.get(rank, rank)}"


def _ts(dt: datetime | None, style: str = "R") -> str:
    if dt is None:
        return "?"
    return f"<t:{int(dt.timestamp())}:{style}>"


# ── Shared embed builders ─────────────────────────────────────────────────────

async def _build_sect_info_embed(session, sect) -> discord.Embed:
    srepo = SectRepository(session)
    prepo = PlayerRepository(session)

    member_count = await srepo.count_members(sect.id)
    cap = sect_rules.member_cap(sect.level)
    members = await srepo.list_members(sect.id)
    names = await prepo.get_names_by_ids(
        [m.player_id for m in members[:5]] + [sect.leader_player_id]
    )

    need = sect_rules.exp_to_next(sect.level)
    if need > 0:
        exp_line = f"`{progress_bar(sect.exp, need, 12)}` {sect.exp:,}/{need:,} EXP"
    else:
        exp_line = "🌟 **Cấp tối đa**"

    embed = base_embed(f"🏯 [{sect.tag}] {sect.name}", color=SECT_COLOR)
    embed.add_field(name="Cấp Tông Môn", value=f"**{sect.level}**\n{exp_line}", inline=False)
    embed.add_field(name="👑 Tông Chủ", value=names.get(sect.leader_player_id, "?"), inline=True)
    embed.add_field(name="👥 Thành Viên", value=f"{member_count}/{cap}", inline=True)
    embed.add_field(name="💰 Công Quỹ", value=f"{int(sect.funds):,}", inline=True)

    if sect.announcement:
        embed.add_field(name="📣 Thông Báo", value=sect.announcement, inline=False)

    facility_levels = await srepo.get_facility_levels(sect.id)
    fac_lines = []
    for fdef in sect_rules.all_facilities():
        lvl = facility_levels.get(fdef["key"], 0)
        line = f"{fdef['emoji']} **{fdef['vi']}** — Cấp {lvl}/{fdef['max_level']}"
        buff = sect_rules.facility_buff_line(fdef["key"], lvl)
        if buff and lvl > 0:
            line += f" · {buff}"
        fac_lines.append(line)
    if fac_lines:
        embed.add_field(name="🏗️ Công Trình", value="\n".join(fac_lines), inline=False)

    if members:
        top_lines = [
            f"{RANK_ICONS.get(m.rank, '🧘')} **{names.get(m.player_id, '?')}** — {int(m.contribution_total):,} Cống Hiến"
            for m in members[:5]
        ]
        embed.add_field(name="🏅 Cống Hiến Cao Nhất", value="\n".join(top_lines), inline=False)

    embed.add_field(
        name="Thành lập", value=_ts(sect.created_at, "D"), inline=True,
    )
    return embed


async def _render_applications(interaction: discord.Interaction, discord_id: int) -> None:
    """(Re)build the pending-applications review screen. Called after defer."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        if not sect_rules.can_review_applications(member.rank):
            await interaction.edit_original_response(
                embed=error_embed("Cần chức vị Chấp Sự trở lên để duyệt đơn."), view=None
            )
            return

        apps = await srepo.list_applications(member.sect_id)
        names = await prepo.get_names_by_ids([a.player_id for a in apps])
        sect = member.sect

    if not apps:
        await interaction.edit_original_response(
            embed=base_embed(
                f"📥 Đơn Xin Gia Nhập — [{sect.tag}] {sect.name}",
                "Hiện không có đơn nào chờ duyệt.",
                color=SECT_COLOR,
            ),
            view=None,
        )
        return

    lines = [
        f"`#{i + 1}` **{names.get(a.player_id, '?')}** — {_ts(a.created_at)}"
        + (f"\n> {a.message}" if a.message else "")
        for i, a in enumerate(apps[:25])
    ]
    embed = base_embed(
        f"📥 Đơn Xin Gia Nhập — [{sect.tag}] {sect.name}",
        "\n".join(lines),
        color=SECT_COLOR,
    )
    options = [
        (a.id, f"#{i + 1} · {names.get(a.player_id, '?')}"[:100])
        for i, a in enumerate(apps[:25])
    ]
    await interaction.edit_original_response(
        embed=embed, view=ApplicationReviewView(discord_id, options)
    )


# ── Tông Môn hub (interactive navigation) ────────────────────────────────────
# One screen with buttons into every sect feature. Reachable from the main
# /status view, `/tongmon thongtin` (own sect), and the 🏯 button every sect
# screen carries. All feature screens and slash commands share the same
# module-level renderers, so both entry points stay behaviorally identical.


def _hub_button(discord_id: int, row: int = 4) -> discord.ui.Button:
    """The universal 'back to sect hub' button appended to sect screens."""
    btn = discord.ui.Button(label="🏯 Tông Môn", style=discord.ButtonStyle.primary, row=row)

    async def _cb(interaction: discord.Interaction) -> None:
        if interaction.user.id != discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _render_sect_hub(interaction, discord_id)

    btn.callback = _cb
    return btn


class BackToHubView(discord.ui.View):
    """Single-button view for info screens — returns to the sect hub."""

    def __init__(self, discord_id: int) -> None:
        super().__init__(timeout=300)
        self.add_item(_hub_button(discord_id, row=0))


async def _render_sect_hub(interaction: discord.Interaction, discord_id: int) -> None:
    """The sect home screen: info embed + feature buttons. Sect-less players
    get the world sect list with join hints instead."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật."), view=None
            )
            return
        member = await srepo.get_membership(player.id)
        if member is None:
            total = await srepo.count_sects()
            rows = await srepo.list_sects_with_counts(offset=0, limit=10)
            names = await prepo.get_names_by_ids([s.leader_player_id for s, _ in rows])
            if total == 0:
                body = (
                    "Chưa có Tông Môn nào trong thiên hạ.\n\n"
                    f"*Sáng lập bằng `/tongmon tao` — cần {sect_rules.SECT_CREATE_COST:,} "
                    "Công Đức và cảnh giới thứ 4 ở một trục bất kỳ.*"
                )
            else:
                lines = [
                    f"`{i + 1:>2}.` **[{s.tag}] {s.name}** — Cấp {s.level} · "
                    f"👥 {count}/{sect_rules.member_cap(s.level)} · "
                    f"👑 {names.get(s.leader_player_id, '?')}"
                    for i, (s, count) in enumerate(rows)
                ]
                body = (
                    "Bạn chưa gia nhập Tông Môn nào.\n\n" + "\n".join(lines)
                    + "\n\n*Nộp đơn bằng `/tongmon xinvao` hoặc sáng lập bằng `/tongmon tao`.*"
                )
            await interaction.edit_original_response(
                embed=base_embed("🏯 Tông Môn Thiên Hạ", body, color=SECT_COLOR), view=None
            )
            return

        embed = await _build_sect_info_embed(session, member.sect)
        rank_line = f"Chức vị của bạn: {_rank_label(member.rank)} · 🏅 {int(member.contribution_points):,} Cống Hiến"
        embed.add_field(name="Bạn", value=rank_line, inline=False)

    await interaction.edit_original_response(
        embed=embed, view=SectHubView(discord_id)
    )


async def _render_members(interaction: discord.Interaction, discord_id: int) -> None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        sect = member.sect
        members = await srepo.list_members(sect.id)
        names = await prepo.get_names_by_ids([m.player_id for m in members])

    members.sort(key=lambda m: (-sect_rules.rank_power(m.rank), -int(m.contribution_total)))
    lines = [
        f"{RANK_ICONS.get(m.rank, '🧘')} **{names.get(m.player_id, '?')}** · "
        f"{sect_rules.RANK_LABELS.get(m.rank, m.rank)} · {int(m.contribution_total):,} Cống Hiến"
        for m in members
    ]
    embed = base_embed(
        f"👥 Thành Viên — [{sect.tag}] {sect.name} "
        f"({len(members)}/{sect_rules.member_cap(sect.level)})",
        "\n".join(lines)[:4000],
        color=SECT_COLOR,
    )
    await interaction.edit_original_response(embed=embed, view=BackToHubView(discord_id))


async def _render_logs(interaction: discord.Interaction, discord_id: int) -> None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        sect = member.sect
        logs = await srepo.list_logs(sect.id, limit=10)

    if not logs:
        body = "Chưa có hoạt động nào."
    else:
        body = "\n".join(
            f"{_LOG_ICONS.get(entry.action, '•')} {entry.detail or entry.action} — {_ts(entry.created_at)}"
            for entry in logs
        )
    await interaction.edit_original_response(
        embed=base_embed(f"📜 Nhật Ký — [{sect.tag}] {sect.name}", body, color=SECT_COLOR),
        view=BackToHubView(discord_id),
    )


async def _do_checkin(interaction: discord.Interaction, discord_id: int) -> None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        today = datetime.now(timezone.utc).date()
        result = await srepo.apply_checkin_atomic(member.sect_id, player.id, today)
        if result.already_checked_in:
            await interaction.edit_original_response(
                embed=error_embed("Hôm nay bạn đã điểm danh rồi — quay lại vào ngày mai."),
                view=BackToHubView(discord_id),
            )
            return
        if not result.ok:
            await interaction.edit_original_response(
                embed=error_embed("Không thể điểm danh lúc này."), view=None
            )
            return
        if result.levels_gained > 0:
            await srepo.add_log(
                member.sect_id, None, sect_rules.LOG_LEVEL_UP,
                f"Tông môn thăng cấp {result.new_level}!",
            )

    lines = [
        f"🏅 Cống Hiến **+{result.ch_added}** (hiện có {result.ch_total:,})",
        f"✨ EXP tông môn **+{result.exp_added}**",
    ]
    if result.levels_gained > 0:
        lines.append(f"\n🎆 **Tông môn thăng cấp {result.new_level}!**")
    await interaction.edit_original_response(
        embed=base_embed("📿 Điểm Danh Thành Công", "\n".join(lines), color=SECT_COLOR),
        view=BackToHubView(discord_id),
    )


async def _do_donate(interaction: discord.Interaction, discord_id: int, amount: int) -> None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        if player.merit <= 0:
            await interaction.edit_original_response(
                embed=error_embed("Bạn không có Công Đức để quyên góp."),
                view=BackToHubView(discord_id),
            )
            return

        today = datetime.now(timezone.utc).date()
        requested = min(int(amount), int(player.merit))
        result = await srepo.apply_donation_atomic(
            member.sect_id, player.id, requested, today
        )
        if result.sect_missing or result.member_missing:
            await interaction.edit_original_response(
                embed=error_embed("Tông môn không còn tồn tại hoặc bạn đã rời khỏi."),
                view=None,
            )
            return
        if result.applied <= 0:
            await interaction.edit_original_response(
                embed=error_embed(
                    "Hôm nay bạn đã quyên góp đủ hạn mức của tông môn — quay lại vào ngày mai."
                ),
                view=BackToHubView(discord_id),
            )
            return

        player.merit -= result.applied
        await prepo.save(player)
        await srepo.add_log(
            member.sect_id, player.id, sect_rules.LOG_DONATE,
            f"{player.name} quyên góp {result.applied:,} Công Đức",
        )
        if result.levels_gained > 0:
            await srepo.add_log(
                member.sect_id, None, sect_rules.LOG_LEVEL_UP,
                f"Tông môn thăng cấp {result.new_level}!",
            )
        sect = member.sect
        tag, name = sect.tag, sect.name
        ch_total = int(member.contribution_points)

    lines = [
        f"💰 Công quỹ **+{result.funds_added:,}**",
        f"✨ EXP tông môn **+{result.exp_added:,}**",
        f"🏅 Cống Hiến **+{result.contribution_added:,}** (hiện có {ch_total:,})",
        f"📅 Hạn mức hôm nay còn **{result.remaining_today:,}** Công Đức",
    ]
    if result.applied < amount:
        lines.append(f"*(Chỉ nhận {result.applied:,} do hạn mức ngày / Công Đức hiện có.)*")
    if result.levels_gained > 0:
        lines.append(f"\n🎆 **Tông môn thăng cấp {result.new_level}!**")
    await interaction.edit_original_response(
        embed=base_embed(
            f"🙏 Quyên Góp — [{tag}] {name}", "\n".join(lines), color=SECT_COLOR
        ),
        view=BackToHubView(discord_id),
    )


async def _render_missions(interaction: discord.Interaction, discord_id: int) -> None:
    """Mission board — auto-claims everything completed on open."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        today = datetime.now(timezone.utc).date()
        claim = await srepo.claim_missions_atomic(member.sect_id, player.id, today)
        if claim.locked:
            await interaction.edit_original_response(
                embed=error_embed(
                    f"Nhiệm vụ tông môn mở tại Tông Môn cấp {mission_rules.MISSIONS_MIN_SECT_LEVEL}."
                ),
                view=BackToHubView(discord_id),
            )
            return
        if claim.levels_gained > 0:
            await srepo.add_log(
                member.sect_id, None, sect_rules.LOG_LEVEL_UP,
                f"Tông môn thăng cấp {claim.new_level}!",
            )
        state = mission_rules.parse_progress(member.mission_progress, today)
        sect = member.sect
        tag, name = sect.tag, sect.name

    lines: list[str] = []
    if claim.claimed:
        lines.append(
            f"🎉 Hoàn thành **{len(claim.claimed)}** nhiệm vụ: "
            f"**+{claim.ch_added} Cống Hiến** · **+{claim.exp_added} EXP tông môn**"
        )
        if claim.levels_gained > 0:
            lines.append(f"🎆 **Tông môn thăng cấp {claim.new_level}!**")
        lines.append("")
    for mdef in mission_rules.mission_defs():
        key = str(mdef.get("key"))
        cur, target = mission_rules.progress_of(state, mdef)
        if mission_rules.is_claimed(state, key):
            status = "✅ Đã nhận"
        elif cur >= target:
            status = "🎁 Sẵn sàng nhận"
        else:
            status = f"`{progress_bar(cur, target, 8)}` {cur}/{target}"
        lines.append(
            f"{mdef.get('emoji', '•')} **{mdef.get('vi', key)}** — "
            f"+{mdef.get('reward_ch', 0)} CH · {status}\n> {mdef.get('desc', '')}"
        )
    lines.append("\n*Nhiệm vụ đặt lại mỗi ngày (UTC). Mở bảng này để nhận thưởng.*")
    await interaction.edit_original_response(
        embed=base_embed(
            f"📜 Nhiệm Vụ — [{tag}] {name}", "\n".join(lines)[:4000], color=SECT_COLOR
        ),
        view=BackToHubView(discord_id),
    )


async def _render_storage(
    interaction: discord.Interaction, discord_id: int, page: int = 1
) -> None:
    page = max(1, page)
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        sect = member.sect
        levels = await srepo.get_facility_levels(sect.id)
        kho_level = levels.get("kho_tang", 0)
        capacity = sect_rules.storage_slots(kho_level)
        if capacity <= 0:
            await interaction.edit_original_response(
                embed=base_embed(
                    f"📦 Kho Tàng — [{sect.tag}] {sect.name}",
                    "Tông môn chưa xây **Kho Tàng** — Trưởng Lão dùng `/tongmon nangcap` "
                    f"(cần Tông Môn cấp {sect_rules.facility_min_sect_level('kho_tang')}).",
                    color=SECT_COLOR,
                ),
                view=BackToHubView(discord_id),
            )
            return
        items = await srepo.list_storage_items(sect.id)
        pending = await srepo.list_pending_requests(sect.id)

    per_page = 12
    pages = max(1, (len(items) + per_page - 1) // per_page)
    page = min(page, pages)
    chunk = items[(page - 1) * per_page: page * per_page]

    header = (
        f"Ô chứa: **{len(items)}/{capacity}** · Kho Tàng cấp **{kho_level}** · "
        f"📥 {len(pending)} đơn chờ duyệt"
    )
    if chunk:
        body = "\n".join(
            f"• **{_item_display(it.item_key, it.grade)}** — ×{it.quantity:,}"
            for it in chunk
        )
    else:
        body = "*Kho đang trống — hãy là người đầu tiên đóng góp!*"
    footer = (
        f"\n\n*Trang {page}/{pages} · `/tongmon kho gui` để gửi vật phẩm · "
        "`/tongmon kho xin` để xin (Chấp Sự duyệt).*"
    )
    await interaction.edit_original_response(
        embed=base_embed(
            f"📦 Kho Tàng — [{sect.tag}] {sect.name}",
            f"{header}\n\n{body}{footer}",
            color=SECT_COLOR,
        ),
        view=BackToHubView(discord_id),
    )


async def _render_shop(
    interaction: discord.Interaction, discord_id: int, page: int = 1
) -> None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        sect = member.sect
        levels = await srepo.get_facility_levels(sect.id)
        now = datetime.now(timezone.utc)
        wk = sect_rules.week_key(now)
        weekly = await srepo.get_weekly_purchases(sect.id, player.id, wk)
        ch_balance = int(member.contribution_points)

    def _line(slot: shop_rules.SectShopSlot) -> str:
        text = f"• **{_item_display(slot.item_key, slot.grade)}** — 💠 {slot.price_ch:,} CH"
        if slot.weekly_limit > 0:
            bought = weekly.get((slot.item_key, slot.grade), 0)
            text += f" · còn {max(0, slot.weekly_limit - bought)}/{slot.weekly_limit} tuần"
        return text

    parts = [f"🏅 Cống Hiến của bạn: **{ch_balance:,}**"]
    parts.append("\n__Gian Cố Định__")
    parts.extend(_line(s) for s in shop_rules.fixed_slots())

    rot = shop_rules.rotating_slots(sect.id, wk, levels.get("tu_bao_cac", 0))
    if rot:
        parts.append(f"\n__Gian Luân Chuyển ({wk})__")
        parts.extend(_line(s) for s in rot)
    else:
        parts.append(
            "\n__Gian Luân Chuyển__\n*Nâng cấp **Tụ Bảo Các** để mở ô hàng luân chuyển "
            "(+1 ô mỗi 2 cấp).*"
        )

    tkc = levels.get("tang_kinh_cac", 0)
    scrolls = shop_rules.scroll_slots(tkc)
    if scrolls:
        per_page = 10
        pages = max(1, (len(scrolls) + per_page - 1) // per_page)
        page = min(max(1, page), pages)
        chunk = scrolls[(page - 1) * per_page: page * per_page]
        discount = shop_rules.scroll_discount(tkc)
        header = f"\n__Bí Tịch — Tàng Kinh Các cấp {tkc}"
        if discount > 0:
            header += f" (giảm {discount * 100:.0f}%)"
        header += f"__ · trang {page}/{pages}"
        parts.append(header)
        parts.extend(_line(s) for s in chunk)
    else:
        parts.append(
            "\n__Bí Tịch__\n*Nâng cấp **Tàng Kinh Các** để mua bí tịch bằng Cống Hiến.*"
        )

    parts.append("\n*Mua bằng `/tongmon cuahang mua`.*")
    await interaction.edit_original_response(
        embed=base_embed(
            f"🏪 Tàng Bảo Các — [{sect.tag}] {sect.name}",
            "\n".join(parts)[:4000],
            color=SECT_COLOR,
        ),
        view=BackToHubView(discord_id),
    )


class DonateModal(discord.ui.Modal, title="Quyên Góp Công Đức"):
    """Amount prompt for the hub's donate button — validates digits, then
    routes through the same donation flow as `/tongmon quyengop`."""

    amount = discord.ui.TextInput(
        label="Số Công Đức muốn quyên góp",
        placeholder="Nhập số nguyên dương",
        required=True,
        max_length=9,
    )

    def __init__(self, discord_id: int) -> None:
        super().__init__()
        self._discord_id = discord_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        raw = (self.amount.value or "").strip().replace(",", "").replace(".", "")
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message(
                embed=error_embed("Nhập số nguyên dương."), ephemeral=True
            )
            return
        if not await safe_defer(interaction):
            return
        await _do_donate(interaction, self._discord_id, int(raw))


class SectHubView(discord.ui.View):
    """Feature navigation for the sect hub. Every target re-validates
    membership/permissions in its own renderer — buttons are just doors."""

    def __init__(self, discord_id: int) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id

        configs = [
            ("📿 Điểm Danh",   discord.ButtonStyle.success,   self._checkin_cb,   0),
            ("📜 Nhiệm Vụ",    discord.ButtonStyle.success,   self._missions_cb,  0),
            ("💰 Quyên Góp",   discord.ButtonStyle.success,   self._donate_cb,    0),
            ("👥 Thành Viên",  discord.ButtonStyle.secondary, self._members_cb,   0),
            ("📦 Kho Tàng",    discord.ButtonStyle.secondary, self._storage_cb,   1),
            ("🏪 Cửa Hàng",    discord.ButtonStyle.secondary, self._shop_cb,      1),
            ("🏗️ Công Trình",  discord.ButtonStyle.secondary, self._facility_cb,  1),
            ("📥 Duyệt Đơn",   discord.ButtonStyle.secondary, self._review_cb,    1),
            ("🐲 Boss",        discord.ButtonStyle.danger,    self._boss_cb,      2),
            ("🗺️ Khoáng Mạch", discord.ButtonStyle.danger,    self._mine_cb,      2),
            ("📖 Nhật Ký",     discord.ButtonStyle.secondary, self._logs_cb,      2),
            ("🔄 Làm Mới",     discord.ButtonStyle.secondary, self._refresh_cb,   2),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _open(self, interaction: discord.Interaction, renderer) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await renderer(interaction, self._discord_id)

    async def _checkin_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _do_checkin)

    async def _missions_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_missions)

    async def _donate_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        # A modal must be the interaction's FIRST response — no defer here.
        await interaction.response.send_modal(DonateModal(self._discord_id))

    async def _members_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_members)

    async def _storage_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_storage)

    async def _shop_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_shop)

    async def _facility_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_facilities)

    async def _review_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_applications)

    async def _boss_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_boss_hub)

    async def _mine_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_mine_map)

    async def _logs_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_logs)

    async def _refresh_cb(self, interaction: discord.Interaction) -> None:
        await self._open(interaction, _render_sect_hub)


async def _render_facilities(interaction: discord.Interaction, discord_id: int) -> None:
    """(Re)build the facility-upgrade screen. Called after defer. Trưởng Lão+."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        if sect_rules.rank_power(member.rank) < sect_rules.rank_power(sect_rules.RANK_TRUONG_LAO):
            await interaction.edit_original_response(
                embed=error_embed("Cần chức vị Trưởng Lão trở lên để nâng cấp công trình."),
                view=None,
            )
            return
        sect = member.sect
        levels = await srepo.get_facility_levels(sect.id)

    lines = [f"💰 Công quỹ: **{int(sect.funds):,}** · Tông môn cấp **{sect.level}**", ""]
    options: list[tuple[str, str]] = []
    for fdef in sect_rules.all_facilities():
        key = fdef["key"]
        lvl = levels.get(key, 0)
        max_lvl = int(fdef["max_level"])
        header = f"{fdef['emoji']} **{fdef['vi']}** — Cấp {lvl}/{max_lvl}"
        detail = sect_rules.facility_buff_line(key, lvl) if lvl > 0 else None

        cost = sect_rules.facility_upgrade_cost(key, lvl)
        needed = max(lvl + 1, sect_rules.facility_min_sect_level(key))
        if lvl >= max_lvl:
            status = "🌟 Tối đa"
        elif sect.level < needed:
            status = f"🔒 Cần Tông Môn cấp {needed}"
        else:
            status = f"Nâng cấp: {cost:,} quỹ"
        body = f"> {fdef['desc']}"
        if detail:
            body += f"\n> Hiện tại: **{detail}**"
        body += f"\n> {status}"
        lines.append(f"{header}\n{body}")
        options.append((key, f"{fdef['vi']} — Cấp {lvl}/{max_lvl}"))

    embed = base_embed(
        f"🏗️ Công Trình — [{sect.tag}] {sect.name}", "\n\n".join(lines)[:4000], color=SECT_COLOR
    )
    await interaction.edit_original_response(
        embed=embed, view=FacilityUpgradeView(discord_id, options)
    )


async def _render_storage_requests(interaction: discord.Interaction, discord_id: int) -> None:
    """(Re)build the withdrawal-request review screen. Called after defer.
    Chấp Sự+. Sweeps stale (>72 h) requests first so the list is honest."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        if not sect_rules.can_review_applications(member.rank):
            await interaction.edit_original_response(
                embed=error_embed("Cần chức vị Chấp Sự trở lên để duyệt kho."), view=None
            )
            return

        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=sect_rules.STORAGE_REQUEST_EXPIRE_HOURS
        )
        await srepo.expire_stale_requests(member.sect_id, cutoff)
        requests = await srepo.list_pending_requests(member.sect_id)
        names = await prepo.get_names_by_ids([r.requester_player_id for r in requests])
        sect = member.sect

    title = f"📦 Duyệt Kho Tàng — [{sect.tag}] {sect.name}"
    if not requests:
        await interaction.edit_original_response(
            embed=base_embed(title, "Không có đơn xin vật phẩm nào chờ duyệt.", color=SECT_COLOR),
            view=None,
        )
        return

    lines = [
        f"`#{i + 1}` **{names.get(r.requester_player_id, '?')}** xin "
        f"**{_item_display(r.item_key, r.grade)}** ×{r.quantity} — {_ts(r.created_at)}"
        for i, r in enumerate(requests[:25])
    ]
    embed = base_embed(title, "\n".join(lines), color=SECT_COLOR)
    options = [
        (
            r.id,
            f"#{i + 1} · {names.get(r.requester_player_id, '?')} · "
            f"{_item_display(r.item_key, r.grade)} ×{r.quantity}",
        )
        for i, r in enumerate(requests[:25])
    ]
    await interaction.edit_original_response(
        embed=embed, view=StorageReviewView(discord_id, options)
    )


# ── Sect boss (Phase 5) ───────────────────────────────────────────────────────

# In-memory guard against parallel attacks from one user double-crediting the
# leaderboard (mirror of the world-boss cog's set). Single-process bot.
_ACTIVE_SECT_BOSS_USERS: set[int] = set()


async def _render_boss_hub(interaction: discord.Interaction, discord_id: int) -> None:
    """(Re)build the weekly boss screen — lazily spawns this week's instance."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        brepo = SectBossRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        sect = member.sect
        if sect.level < boss_rules.SECT_BOSS_MIN_LEVEL:
            await interaction.edit_original_response(
                embed=base_embed(
                    "🐲 Trấn Sơn Thú",
                    f"Boss tông môn mở tại **Tông Môn cấp {boss_rules.SECT_BOSS_MIN_LEVEL}** "
                    f"(hiện tại: cấp {sect.level}).",
                    color=SECT_COLOR,
                ),
                view=None,
            )
            return

        now = datetime.now(timezone.utc)
        wk = sect_rules.week_key(now)
        instance = await brepo.get_for_week(sect.id, wk)
        if instance is None:
            bdef = boss_rules.select_boss(sect.level)
            count = await srepo.count_members(sect.id)
            cap = sect_rules.member_cap(sect.level)
            hp_max = boss_rules.compute_hp_max(bdef, sect.level, count, cap)
            try:
                instance = await brepo.create_instance(
                    sect.id, bdef["key"], wk, hp_max,
                    spawned_at=now, expires_at=boss_rules.next_week_start(now),
                )
            except IntegrityError:
                # Another member opened the hub in the same instant — theirs won.
                await session.rollback()
                instance = await brepo.get_for_week(sect.id, wk)
                if instance is None:
                    await interaction.edit_original_response(
                        embed=error_embed("Không thể triệu hồi boss — thử lại."), view=None
                    )
                    return

        bdef = boss_rules.get_boss(instance.boss_key) or {"vi": instance.boss_key}
        part = await brepo.get_participation(instance.id, player.id)
        parts = await brepo.list_participations(instance.id)
        names = await prepo.get_names_by_ids(
            [p.player_id for p in parts[:5]]
            + ([instance.finisher_player_id] if instance.finisher_player_id else [])
        )
        pending = await brepo.list_pending_rewards_for_player(player.id)
        tag, sect_name = sect.tag, sect.name

    attacks_used = int(part.attack_count) if part else 0
    lines = [
        f"**{bdef.get('vi', '?')}** — tuần {instance.week_key}",
        f"> {bdef.get('description_vi', '')}",
        "",
    ]
    if instance.killed_at is not None:
        finisher = names.get(instance.finisher_player_id, "?")
        lines.append(f"🎉 **Đã bị tiêu diệt!** Người kết liễu: **{finisher}**")
    elif not instance.is_active:
        lines.append("⏰ Boss đã rời đi khi tuần kết thúc — phần thưởng tham gia vẫn nhận được.")
    else:
        lines.append(
            f"❤️ `{progress_bar(instance.hp_current, instance.hp_max, 12)}` "
            f"{int(instance.hp_current):,}/{int(instance.hp_max):,}"
        )
        lines.append(f"⏳ Kết thúc {_ts(instance.expires_at)}")
    lines.append(
        f"\n⚔️ Lượt tấn công của bạn: **{attacks_used}/{boss_rules.ATTACKS_PER_WEEK}**"
    )
    if pending:
        lines.append(f"🎁 Bạn có **{len(pending)}** phần thưởng boss chưa nhận!")
    lines.append("\n__Sát Thương Tông Môn__")
    lines.append(format_leaderboard(parts, max_rows=5, name_lookup=names,
                                    boss_hp_max=int(instance.hp_max)))

    await interaction.edit_original_response(
        embed=base_embed(f"🐲 Trấn Sơn Thú — [{tag}] {sect_name}",
                         "\n".join(lines)[:4000], color=SECT_COLOR),
        view=SectBossView(discord_id),
    )


async def _execute_sect_boss_attack(interaction: discord.Interaction, discord_id: int) -> None:
    """One attack run — solo CombatSession vs the shared pool, world-boss style."""
    if discord_id in _ACTIVE_SECT_BOSS_USERS:
        await interaction.followup.send(
            embed=error_embed("Bạn đang trong một trận đánh boss — chờ trận hiện tại kết thúc."),
            ephemeral=True,
        )
        return
    _ACTIVE_SECT_BOSS_USERS.add(discord_id)
    try:
        await _run_sect_boss_attack(interaction, discord_id)
    finally:
        _ACTIVE_SECT_BOSS_USERS.discard(discord_id)


async def _run_sect_boss_attack(interaction: discord.Interaction, discord_id: int) -> None:
    rng = random.Random()
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        brepo = SectBossRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return

        wk = sect_rules.week_key(datetime.now(timezone.utc))
        instance = await brepo.get_for_week(member.sect_id, wk)
        if instance is None or not instance.is_active or instance.hp_current <= 0:
            await interaction.edit_original_response(
                embed=error_embed("Boss tuần này chưa xuất hiện hoặc đã bị hạ — mở `/tongmon boss`."),
                view=None,
            )
            return
        part = await brepo.get_participation(instance.id, player.id)
        if part is not None and part.attack_count >= boss_rules.ATTACKS_PER_WEEK:
            await interaction.edit_original_response(
                embed=error_embed(
                    f"Bạn đã dùng hết {boss_rules.ATTACKS_PER_WEEK} lượt tấn công tuần này."
                ),
                view=None,
            )
            return

        bdef = boss_rules.get_boss(instance.boss_key)
        if bdef is None:
            await interaction.edit_original_response(
                embed=error_embed("Dữ liệu boss không tồn tại — báo quản trị viên."), view=None
            )
            return

        char = _player_to_model(player)
        char.constitution_levels = await load_constitution_levels(
            session, player.id, player.constitution_type
        )
        from src.game.systems.character_stats import (
            active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
        )
        gem_keys = active_formation_gem_keys(player)
        gem_map = active_formation_gem_map(player)
        equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        equip_stats = compute_equipment_stats(equipped)
        cs = compute_combat_stats(
            char, gem_count=len(gem_keys), equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_map,
        )
        if player.hp_current <= 0:
            player.hp_current = cs.hp_max
            char.hp_current = player.hp_current

        skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
        realm_total = compute_realm_total(char)
        skill_mastery: dict[str, int] | None = None
        if settings.skill_mastery_enabled:
            skill_mastery = await get_mastery_map(session, player.id)

        player_c = build_player_combatant(
            char, skill_keys, len(gem_keys), equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_map,
            skill_mastery=skill_mastery,
        )
        boss_c = boss_rules.build_boss_combatant(
            bdef, int(instance.hp_current), int(instance.hp_max), realm_total
        )
        combat = CombatSession(
            player=player_c,
            enemy=boss_c,
            player_skill_keys=skill_keys,
            rng=rng,
            max_turns=boss_rules.ATTACK_ROUND_LIMIT,
            # Chip-away cadence: hitting the round limit is expected, not a defeat.
            max_turns_is_defeat=False,
        )
        starting_hp = boss_c.hp
        instance_id = int(instance.id)
        instance_hp_max = int(instance.hp_max)
        sect_id = int(member.sect_id)
        player_name = player.name

    # Combat runs OUTSIDE the DB session — the live-update loop spans seconds.
    wave_label = f"🐲 **{bdef['vi']}** — Trấn Sơn Thú"
    while True:
        new_lines, result = combat.step()
        if result is not None:
            combat_result = result
            break
        try:
            await interaction.edit_original_response(
                embed=battle_embed(
                    wave_label, 0, 1,
                    player_c.name, player_c.hp, player_c.hp_max,
                    player_c.mp, player_c.mp_max,
                    boss_c.name, boss_c.hp, boss_c.hp_max,
                    combat.turn, new_lines,
                    player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
                    enemy_shield=boss_c.shield, enemy_shield_cap=boss_c.shield_cap(),
                ),
                view=None,
            )
        except discord.HTTPException:
            pass
        await asyncio.sleep(0.5)

    damage_local = max(0, starting_hp - max(0, boss_c.hp))
    dmg_cap = int(instance_hp_max * boss_rules.PER_ATTACK_DMG_CAP_PCT)
    cap_hit = damage_local > dmg_cap
    damage_local = min(damage_local, dmg_cap)

    killed_by_us = False
    applied = 0
    new_hp = 0
    level_up_line = ""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        brepo = SectBossRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is not None:
            apply_result = await brepo.apply_damage_atomic(
                instance_id, damage_local, player.id, damage_cap=dmg_cap,
            )
            if not apply_result.instance_missing:
                applied = apply_result.applied
                new_hp = apply_result.new_hp
                killed_by_us = apply_result.is_finisher
                if apply_result.cap_hit:
                    cap_hit = True
                # Credit the attack even at 0 applied damage — it burns a slot.
                await brepo.upsert_damage(instance_id, player.id, applied)

            if killed_by_us and await brepo.flag_rewards_distributed(instance_id):
                reward = boss_rules.reward_block(bdef)
                new_level, gained = await srepo.grant_sect_rewards_atomic(
                    sect_id, int(reward.get("sect_exp", 0)), int(reward.get("sect_funds", 0)),
                )
                await srepo.add_log(
                    sect_id, player.id, sect_rules.LOG_BOSS_KILL,
                    f"{player_name} kết liễu {bdef['vi']}!",
                )
                if gained > 0:
                    await srepo.add_log(
                        sect_id, None, sect_rules.LOG_LEVEL_UP,
                        f"Tông môn thăng cấp {new_level}!",
                    )
                    level_up_line = f"\n🎆 **Tông môn thăng cấp {new_level}!**"

            # Post-attack full heal — mirror of the world-boss convenience.
            player.hp_current = cs.hp_max
            player.mp_current = cs.mp_max
            player.shield_current = cs.shield_max
            await prepo.save(player)

            if settings.skill_mastery_enabled:
                try:
                    from src.game.systems.combat import CombatEndReason
                    await add_combat_xp(
                        session, player.id, player_c.skill_usage_count,
                        victory=combat_result.reason != CombatEndReason.PLAYER_DEAD,
                    )
                except Exception as e:  # noqa: BLE001
                    log.exception("Skill mastery XP award failed (sect boss): %s", e)

    if killed_by_us:
        reward = boss_rules.reward_block(bdef)
        body = (
            f"⚔️ Sát thương: **{applied:,}**\n\n"
            f"🎉 **{bdef['vi']} đã bị tiêu diệt!** Người kết liễu: **{player_name}**\n"
            f"💰 Công quỹ +{int(reward.get('sect_funds', 0)):,} · "
            f"✨ EXP tông môn +{int(reward.get('sect_exp', 0)):,}{level_up_line}\n\n"
            "Dùng nút **🎁 Nhận Thưởng** để lĩnh rương + Cống Hiến."
        )
    else:
        cap_note = " *(chạm trần sát thương mỗi lượt)*" if cap_hit else ""
        body = (
            f"⚔️ Sát thương: **{applied:,}**{cap_note}\n"
            f"❤️ Boss còn `{progress_bar(new_hp, instance_hp_max, 12)}` "
            f"{new_hp:,}/{instance_hp_max:,}"
        )
    await interaction.edit_original_response(
        embed=base_embed("🐲 Kết Quả Tấn Công", body, color=SECT_COLOR),
        view=SectBossView(discord_id),
    )


async def _claim_sect_boss_rewards(interaction: discord.Interaction, discord_id: int) -> None:
    """Claim every finished-boss reward: chest(s) + Cống Hiến per instance.
    Claim races are settled by ``claim_reward_atomic``; CH lands only while
    still a member of the sect that fought (chests always pay)."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        brepo = SectBossRepository(session)
        inv_repo = InventoryRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật."), view=None
            )
            return
        pending = await brepo.list_pending_rewards_for_player(player.id)
        if not pending:
            await interaction.edit_original_response(
                embed=error_embed("Không có phần thưởng boss nào chờ nhận."),
                view=SectBossView(discord_id),
            )
            return

        lines: list[str] = []
        for part in pending:
            inst = part.boss_instance
            bdef = boss_rules.get_boss(inst.boss_key) or {}
            parts = await brepo.list_participations(inst.id)
            rank = next(
                (i + 1 for i, p in enumerate(parts) if p.player_id == player.id),
                len(parts),
            )
            tier = boss_rules.reward_tier(rank, int(part.damage_dealt), int(inst.hp_max))
            if not await brepo.claim_reward_atomic(part.id):
                continue  # concurrent claim won
            label = bdef.get("vi", inst.boss_key)
            if tier == "none":
                lines.append(
                    f"• **{label}** ({inst.week_key}): sát thương quá thấp — không đủ điều kiện thưởng."
                )
                continue
            reward = boss_rules.reward_block(bdef)
            chests = [reward.get("participation_chest")]
            if tier == "top" and reward.get("top3_chest"):
                chests.append(reward.get("top3_chest"))
            granted: list[str] = []
            for chest_key in [c for c in chests if c]:
                chest_item = registry.get_item(chest_key) or {}
                await inv_repo.add_item_raw(
                    player.id, chest_key, int(chest_item.get("grade", 1) or 1), 1
                )
                granted.append(chest_item.get("vi", chest_key))
            ch = int(reward.get("participation_ch", 0))
            ch_note = ""
            if ch > 0:
                if await srepo.add_contribution_atomic(inst.sect_id, player.id, ch):
                    ch_note = f" · 🏅 +{ch} Cống Hiến"
                else:
                    ch_note = " · *(đã rời tông môn — không nhận Cống Hiến)*"
            rank_tag = f"hạng #{rank}" + (" 🏆" if tier == "top" else "")
            lines.append(
                f"• **{label}** ({inst.week_key}, {rank_tag}): "
                + " + ".join(f"🎁 {g}" for g in granted) + ch_note
            )

    if not lines:
        await interaction.edit_original_response(
            embed=error_embed("Phần thưởng đã được nhận ở nơi khác."),
            view=SectBossView(discord_id),
        )
        return
    await interaction.edit_original_response(
        embed=base_embed("🎁 Phần Thưởng Trấn Sơn Thú", "\n".join(lines)[:4000], color=SECT_COLOR),
        view=SectBossView(discord_id),
    )


class SectBossView(discord.ui.View):
    """Boss hub buttons — attack / claim / refresh."""

    def __init__(self, discord_id: int) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id

        attack = discord.ui.Button(label="⚔️ Tấn Công", style=discord.ButtonStyle.danger, row=0)
        attack.callback = self._on_attack
        self.add_item(attack)

        claim = discord.ui.Button(label="🎁 Nhận Thưởng", style=discord.ButtonStyle.success, row=0)
        claim.callback = self._on_claim
        self.add_item(claim)

        refresh = discord.ui.Button(label="🔄 Làm Mới", style=discord.ButtonStyle.secondary, row=0)
        refresh.callback = self._on_refresh
        self.add_item(refresh)

        self.add_item(_hub_button(discord_id, row=0))

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_attack(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _execute_sect_boss_attack(interaction, self._discord_id)

    async def _on_claim(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _claim_sect_boss_rewards(interaction, self._discord_id)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _render_boss_hub(interaction, self._discord_id)


# ── Mine wars — Đại Chiến Khoáng Mạch (Phase 6) ──────────────────────────────

_ACTIVE_MINE_WAR_USERS: set[int] = set()


async def _build_duel_combatant(session, player):
    """Full Combatant from a Player ORM row — used for both the attacker and
    the offline defender's *defense snapshot* (their persisted build fights
    back via the combat AI). Same construction path as the boss attack."""
    from src.game.systems.character_stats import (
        active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
    )
    char = _player_to_model(player)
    char.constitution_levels = await load_constitution_levels(
        session, player.id, player.constitution_type
    )
    gem_keys = active_formation_gem_keys(player)
    gem_map = active_formation_gem_map(player)
    equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
    equip_stats = compute_equipment_stats(equipped)
    cs = compute_combat_stats(
        char, gem_count=len(gem_keys), equip_stats=equip_stats,
        gem_keys=gem_keys, gem_keys_by_formation=gem_map,
    )
    # Snapshots always fight at full vitals — a defender's leftover dungeon
    # damage shouldn't decide a war.
    char.hp_current = cs.hp_max
    char.mp_current = cs.mp_max
    char.shield_current = cs.shield_max
    skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
    skill_mastery: dict[str, int] | None = None
    if settings.skill_mastery_enabled:
        skill_mastery = await get_mastery_map(session, player.id)
    combatant = build_player_combatant(
        char, skill_keys, len(gem_keys), equip_stats=equip_stats,
        gem_keys=gem_keys, gem_keys_by_formation=gem_map,
        skill_mastery=skill_mastery,
    )
    return combatant, skill_keys, char


async def _sect_label(srepo: SectRepository, sect_id: int | None) -> str:
    if sect_id is None:
        return "*vô chủ*"
    sect = await srepo.get_sect_by_id(sect_id)
    return f"[{sect.tag}] {sect.name}" if sect else "?"


async def _render_mine_map(interaction: discord.Interaction, discord_id: int) -> None:
    """World map — every mine with owner / shield / war state. Also the lazy
    payout + seed touchpoint so the map is always settled when viewed."""
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        srepo = SectRepository(session)
        mrepo = SectMineRepository(session)
        await mrepo.ensure_mines_seeded()
        await mrepo.accrue_payouts(now)
        mines = {m.mine_key: m for m in await mrepo.list_mines()}
        wars = {w.mine_key: w for w in await mrepo.list_active_wars()}

        lines: list[str] = []
        for mdef in mine_rules.all_mines():
            key = mdef["key"]
            row = mines.get(key)
            icon = mine_rules.TIER_ICONS.get(mdef.get("tier"), "⛰️")
            tier = mine_rules.TIER_LABELS.get(mdef.get("tier"), "?")
            header = (
                f"{icon} **{mdef['vi']}** ({tier}) — 💰 {mdef['funds_per_hour']:,}/giờ"
            )
            owner = await _sect_label(srepo, row.occupier_sect_id if row else None)
            state = ""
            war = wars.get(key)
            if war is not None:
                state = f" · ⚔️ đang chiến, kết thúc {_ts(war.window_end)}"
            elif row is not None and row.shield_until and row.shield_until > now:
                state = f" · 🛡️ hưu chiến đến {_ts(row.shield_until)}"
            lines.append(f"{header}\n> Chủ: {owner}{state}")

    lines.append(
        "\n*`/tongmon mo tuyenchien` để tuyên chiến (Trưởng Lão+, Tông Môn cấp "
        f"{mine_rules.MINE_WAR_MIN_SECT_LEVEL}+, {mine_rules.DECLARE_FEE_FUNDS:,} quỹ) · "
        "`/tongmon mo xuatchinh` để xuất chinh.*"
    )
    await interaction.edit_original_response(
        embed=base_embed("🗺️ Khoáng Mạch Thiên Hạ", "\n".join(lines)[:4000], color=SECT_COLOR),
        view=BackToHubView(discord_id),
    )


async def _execute_mine_attack(interaction: discord.Interaction, discord_id: int) -> None:
    if discord_id in _ACTIVE_MINE_WAR_USERS:
        await interaction.edit_original_response(
            embed=error_embed("Bạn đang trong một trận chiến khoáng mạch — chờ trận hiện tại kết thúc.")
        )
        return
    _ACTIVE_MINE_WAR_USERS.add(discord_id)
    try:
        await _run_mine_attack(interaction, discord_id)
    finally:
        _ACTIVE_MINE_WAR_USERS.discard(discord_id)


async def _run_mine_attack(interaction: discord.Interaction, discord_id: int) -> None:
    rng = random.Random()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        mrepo = SectMineRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), view=None
            )
            return
        war = await mrepo.get_active_war_for_sect(member.sect_id)
        if war is None or war.window_end <= now:
            await interaction.edit_original_response(
                embed=error_embed(
                    "Tông môn không trong cuộc chiến khoáng mạch nào — "
                    "xem `/tongmon mo xem` và tuyên chiến trước."
                ),
                view=None,
            )
            return

        mdef = mine_rules.get_mine(war.mine_key) or {"vi": war.mine_key}
        is_siege = war.defender_sect_id is None
        side = (
            mine_rules.SIDE_ATTACK
            if war.attacker_sect_id == member.sect_id
            else mine_rules.SIDE_DEFEND
        )

        attempt = await mrepo.consume_attempt_atomic(war.id, player.id, side)
        if attempt.out_of_attempts:
            await interaction.edit_original_response(
                embed=error_embed(
                    f"Bạn đã dùng hết {mine_rules.ATTEMPTS_PER_WAR} lượt xuất chinh trận này."
                ),
                view=None,
            )
            return
        if not attempt.ok:
            await interaction.edit_original_response(
                embed=error_embed("Cuộc chiến đã kết toán."), view=None
            )
            return

        attacker_c, skill_keys, char = await _build_duel_combatant(session, player)
        war_id = int(war.id)
        attempts_used = attempt.attempts_used

        if is_siege:
            garrison_hp = int(war.garrison_hp_current or 0)
            garrison_hp_max = int(war.garrison_hp_max or 1)
            enemy_c = mine_rules.build_garrison_combatant(
                mdef, garrison_hp, compute_realm_total(char)
            )
            max_turns = mine_rules.SIEGE_ROUND_LIMIT
        else:
            # Defense snapshot — a random member of the opposing sect.
            opposing_sect_id = (
                war.defender_sect_id if side == mine_rules.SIDE_ATTACK
                else war.attacker_sect_id
            )
            candidates = await srepo.list_members(opposing_sect_id)
            if not candidates:
                await interaction.edit_original_response(
                    embed=error_embed("Tông môn đối thủ không còn thành viên nào."), view=None
                )
                return
            target_row = await prepo.get_by_id(rng.choice(candidates).player_id)
            if target_row is None:
                await interaction.edit_original_response(
                    embed=error_embed("Không tìm thấy đối thủ — thử lại."), view=None
                )
                return
            enemy_c, _, _ = await _build_duel_combatant(session, target_row)
            if attacker_c.name == enemy_c.name:
                enemy_c.name = f"{enemy_c.name} (địch)"
            max_turns = mine_rules.DUEL_MAX_TURNS

    combat = CombatSession(
        player=attacker_c,
        enemy=enemy_c,
        player_skill_keys=skill_keys,
        rng=rng,
        max_turns=max_turns,
        max_turns_is_defeat=False,
    )
    wave_label = (
        f"⛏️ **{mdef['vi']}** — công phá thủ vệ" if is_siege
        else f"⚔️ **{mdef['vi']}** — {attacker_c.name} vs {enemy_c.name}"
    )
    starting_hp = enemy_c.hp
    while True:
        new_lines, result = combat.step()
        if result is not None:
            combat_result = result
            break
        try:
            await interaction.edit_original_response(
                embed=battle_embed(
                    wave_label, 0, 1,
                    attacker_c.name, attacker_c.hp, attacker_c.hp_max,
                    attacker_c.mp, attacker_c.mp_max,
                    enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                    combat.turn, new_lines,
                    player_shield=attacker_c.shield, player_shield_cap=attacker_c.shield_cap(),
                    enemy_shield=enemy_c.shield, enemy_shield_cap=enemy_c.shield_cap(),
                ),
                view=None,
            )
        except discord.HTTPException:
            pass
        await asyncio.sleep(0.5)

    attempts_left = mine_rules.ATTEMPTS_PER_WAR - attempts_used
    if is_siege:
        damage = max(0, starting_hp - max(0, enemy_c.hp))
        dmg_cap = int(garrison_hp_max * mine_rules.GARRISON_DMG_CAP_PCT)
        damage = min(damage, dmg_cap)
        async with get_session() as session:
            srepo = SectRepository(session)
            mrepo = SectMineRepository(session)
            gres = await mrepo.apply_garrison_damage_atomic(war_id, damage, dmg_cap)
            if gres.captured:
                await srepo.add_log(
                    member.sect_id, player.id, sect_rules.LOG_MINE_CAPTURE,
                    f"Chiếm được {mdef['vi']}! ({player.name} phá vỡ thủ vệ)",
                )
        if gres.war_missing:
            body = "Cuộc vây đã kết thúc trước khi đòn đánh được ghi nhận."
        elif gres.captured:
            body = (
                f"⚔️ Sát thương: **{gres.applied:,}**\n\n"
                f"⛏️ **Thủ vệ sụp đổ — tông môn đã chiếm {mdef['vi']}!**\n"
                f"💰 Từ giờ mỗi giờ +{mdef.get('funds_per_hour', 0):,} công quỹ."
            )
        else:
            cap_note = " *(chạm trần sát thương)*" if gres.cap_hit else ""
            body = (
                f"⚔️ Sát thương: **{gres.applied:,}**{cap_note}\n"
                f"🏰 Thủ vệ còn `{progress_bar(gres.new_hp, garrison_hp_max, 12)}` "
                f"{gres.new_hp:,}/{garrison_hp_max:,}\n"
                f"Lượt xuất chinh còn lại: **{attempts_left}**"
            )
    else:
        from src.game.systems.combat import CombatEndReason
        won = combat_result.reason == CombatEndReason.PLAYER_WIN
        points = mine_rules.duel_points(won)
        async with get_session() as session:
            mrepo = SectMineRepository(session)
            counted = await mrepo.add_points_atomic(war_id, player.id, side, points)
            war_now = await mrepo.get_war(war_id)
        outcome = "🏆 **Thắng!**" if won else "💀 Thua trận."
        if not counted:
            body = f"{outcome}\nCuộc chiến đã kết toán — điểm không được ghi nhận."
        else:
            body = (
                f"{outcome} +**{points}** điểm cho phe "
                f"{'công' if side == mine_rules.SIDE_ATTACK else 'thủ'}.\n"
                f"📊 Tỉ số: Công **{war_now.attacker_points}** — "
                f"Thủ **{war_now.defender_points}**\n"
                f"Lượt xuất chinh còn lại: **{attempts_left}**"
            )
    await interaction.edit_original_response(
        embed=base_embed("⚔️ Kết Quả Xuất Chinh", body, color=SECT_COLOR),
        view=BackToHubView(discord_id),
    )


async def _render_war_report(interaction: discord.Interaction, discord_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        prepo = PlayerRepository(session)
        srepo = SectRepository(session)
        mrepo = SectMineRepository(session)
        player = await prepo.get_by_discord_id_lite(discord_id)
        member = await srepo.get_membership(player.id) if player else None
        if player is None or member is None:
            await interaction.edit_original_response(
                embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
            )
            return
        war = await mrepo.get_active_war_for_sect(member.sect_id)
        if war is None:
            await interaction.edit_original_response(
                embed=base_embed(
                    "📜 Chiến Báo", "Tông môn không trong cuộc chiến khoáng mạch nào.",
                    color=SECT_COLOR,
                )
            )
            return
        mdef = mine_rules.get_mine(war.mine_key) or {"vi": war.mine_key}
        attacker_label = await _sect_label(srepo, war.attacker_sect_id)
        defender_label = (
            await _sect_label(srepo, war.defender_sect_id)
            if war.defender_sect_id else "🏰 Thủ vệ NPC"
        )
        attacks = await mrepo.list_war_attacks(war.id)
        names = await prepo.get_names_by_ids([a.player_id for a in attacks])

    lines = [
        f"⛰️ Mỏ: **{mdef['vi']}**",
        f"⚔️ Công: **{attacker_label}**  ·  🛡️ Thủ: **{defender_label}**",
        f"⏳ Kết toán {_ts(war.window_end)}",
        "",
    ]
    if war.defender_sect_id is None:
        hp_max = int(war.garrison_hp_max or 1)
        hp = int(war.garrison_hp_current or 0)
        lines.append(
            f"🏰 Thủ vệ: `{progress_bar(hp, hp_max, 12)}` {hp:,}/{hp_max:,}"
        )
    else:
        lines.append(
            f"📊 Tỉ số: Công **{war.attacker_points}** — Thủ **{war.defender_points}** "
            f"*(hòa → phe thủ giữ mỏ)*"
        )
    if attacks:
        lines.append("\n__Chiến Công__")
        for a in attacks[:8]:
            side_icon = "⚔️" if a.side == mine_rules.SIDE_ATTACK else "🛡️"
            stat = f"{a.points} điểm" if war.defender_sect_id else f"{a.attempts_used} đòn"
            lines.append(
                f"{side_icon} **{names.get(a.player_id, '?')}** — {stat} "
                f"({a.attempts_used}/{mine_rules.ATTEMPTS_PER_WAR} lượt)"
            )
    await interaction.edit_original_response(
        embed=base_embed("📜 Chiến Báo Khoáng Mạch", "\n".join(lines)[:4000], color=SECT_COLOR),
        view=BackToHubView(discord_id),
    )


# ── Views ─────────────────────────────────────────────────────────────────────

class StorageReviewView(discord.ui.View):
    """Select a withdrawal request, approve/reject. All rules re-validated
    atomically on click (rank, self-approval, weekly cap, live stock)."""

    def __init__(self, discord_id: int, options: list[tuple[int, str]]) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._selected: int | None = None

        select = discord.ui.Select(
            placeholder="Chọn đơn xin vật phẩm…",
            options=[
                discord.SelectOption(label=label[:100], value=str(req_id))
                for req_id, label in options
            ],
            row=0,
        )
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

        approve = discord.ui.Button(label="✅ Phát Vật Phẩm", style=discord.ButtonStyle.success, row=1)
        approve.callback = self._on_approve
        self.add_item(approve)

        reject = discord.ui.Button(label="🚫 Từ Chối", style=discord.ButtonStyle.danger, row=1)
        reject.callback = self._on_reject
        self.add_item(reject)

        self.add_item(_hub_button(discord_id, row=1))

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._selected = int(self._select.values[0])
        await interaction.response.defer()

    async def _resolve(self, interaction: discord.Interaction, approve: bool) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if self._selected is None:
            await interaction.response.send_message(
                embed=error_embed("Hãy chọn một đơn trước."), ephemeral=True
            )
            return
        if not await safe_defer(interaction):
            return

        feedback: discord.Embed
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            inv_repo = InventoryRepository(session)
            actor = await prepo.get_by_discord_id_lite(self._discord_id)
            member = await srepo.get_membership(actor.id) if actor else None
            if actor is None or member is None or not sect_rules.can_review_applications(member.rank):
                feedback = error_embed("Bạn không còn quyền duyệt kho.")
            elif approve:
                week_start = sect_rules.week_start_utc(datetime.now(timezone.utc))
                result = await srepo.approve_storage_request_atomic(
                    self._selected, member.sect_id, actor.id, week_start
                )
                if result.ok:
                    await inv_repo.add_item_raw(
                        result.requester_player_id, result.item_key,
                        result.grade, result.quantity,
                    )
                    names = await prepo.get_names_by_ids([result.requester_player_id])
                    label = _item_display(result.item_key, result.grade)
                    await srepo.add_log(
                        member.sect_id, actor.id, sect_rules.LOG_STORAGE_APPROVE,
                        f"{actor.name} phát {label} ×{result.quantity} cho "
                        f"{names.get(result.requester_player_id, '?')}",
                    )
                    feedback = success_embed(
                        f"Đã phát **{label}** ×{result.quantity} cho "
                        f"**{names.get(result.requester_player_id, '?')}**."
                    )
                elif result.self_approval:
                    feedback = error_embed("Không thể tự duyệt đơn của chính mình — cần một Chấp Sự khác.")
                elif result.requester_left:
                    feedback = error_embed("Người xin đã rời tông môn — đơn đã bị hủy.")
                elif result.weekly_cap_hit:
                    feedback = error_embed(
                        f"Người này đã nhận đủ {sect_rules.MAX_WEEKLY_STORAGE_WITHDRAWALS} lần trong tuần."
                    )
                elif result.insufficient_stock:
                    feedback = error_embed(
                        f"Kho không đủ vật phẩm (còn ×{result.stock_available}) — đơn giữ nguyên chờ bổ sung."
                    )
                else:
                    feedback = error_embed("Đơn này không còn tồn tại hoặc đã hết hạn.")
            else:
                req = await srepo.get_storage_request(self._selected)
                if (
                    req is None
                    or req.sect_id != member.sect_id
                    or req.status != sect_rules.REQ_PENDING
                ):
                    feedback = error_embed("Đơn này không còn tồn tại hoặc đã được xử lý.")
                else:
                    await srepo.reject_storage_request(req, actor.id)
                    names = await prepo.get_names_by_ids([req.requester_player_id])
                    await srepo.add_log(
                        member.sect_id, actor.id, sect_rules.LOG_STORAGE_REJECT,
                        f"{actor.name} từ chối đơn xin {_item_display(req.item_key, req.grade)} "
                        f"×{req.quantity} của {names.get(req.requester_player_id, '?')}",
                    )
                    feedback = success_embed("Đã từ chối đơn xin vật phẩm.")

        self._selected = None
        await _render_storage_requests(interaction, self._discord_id)
        await interaction.followup.send(embed=feedback, ephemeral=True)

    async def _on_approve(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, approve=True)

    async def _on_reject(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, approve=False)


class FacilityUpgradeView(discord.ui.View):
    """Pick a facility, hit upgrade. Rank + funds re-validated atomically on click."""

    def __init__(self, discord_id: int, options: list[tuple[str, str]]) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._selected: str | None = None

        select = discord.ui.Select(
            placeholder="Chọn công trình…",
            options=[
                discord.SelectOption(label=label[:100], value=key)
                for key, label in options
            ],
            row=0,
        )
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

        upgrade = discord.ui.Button(label="⬆️ Nâng Cấp", style=discord.ButtonStyle.success, row=1)
        upgrade.callback = self._on_upgrade
        self.add_item(upgrade)

        self.add_item(_hub_button(discord_id, row=1))

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._selected = self._select.values[0]
        await interaction.response.defer()

    async def _on_upgrade(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if self._selected is None:
            await interaction.response.send_message(
                embed=error_embed("Hãy chọn một công trình trước."), ephemeral=True
            )
            return
        if not await safe_defer(interaction):
            return

        feedback: discord.Embed
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            actor = await prepo.get_by_discord_id_lite(self._discord_id)
            member = await srepo.get_membership(actor.id) if actor else None
            if (
                actor is None
                or member is None
                or sect_rules.rank_power(member.rank)
                < sect_rules.rank_power(sect_rules.RANK_TRUONG_LAO)
            ):
                feedback = error_embed("Bạn không còn quyền nâng cấp công trình.")
            else:
                fdef = sect_rules.facility_def(self._selected)
                result = await srepo.upgrade_facility_atomic(member.sect_id, self._selected)
                if result.ok:
                    await srepo.add_log(
                        member.sect_id, actor.id, sect_rules.LOG_UPGRADE,
                        f"{actor.name} nâng {fdef['vi']} lên cấp {result.new_level}",
                    )
                    buff = sect_rules.facility_buff_line(self._selected, result.new_level)
                    line = (
                        f"{fdef['emoji']} **{fdef['vi']}** đạt **cấp {result.new_level}** "
                        f"(−{result.cost:,} quỹ, còn {result.funds_after:,})."
                    )
                    if buff:
                        line += f"\nHiệu quả hiện tại: **{buff}**"
                    feedback = success_embed(line)
                elif result.maxed:
                    feedback = error_embed("Công trình đã đạt cấp tối đa.")
                elif result.level_gated:
                    feedback = error_embed(
                        f"Cần Tông Môn cấp {result.sect_level_needed} để nâng công trình này."
                    )
                elif result.insufficient_funds:
                    feedback = error_embed(
                        f"Không đủ công quỹ: cần {result.funds_needed:,}, hiện có {result.funds_after:,}."
                    )
                else:
                    feedback = error_embed("Không thể nâng cấp công trình này.")

        await _render_facilities(interaction, self._discord_id)
        await interaction.followup.send(embed=feedback, ephemeral=True)


class ApplicationReviewView(discord.ui.View):
    """Select an application, then approve/reject. Every action re-validates
    officer rank + application existence in a fresh session."""

    def __init__(self, discord_id: int, options: list[tuple[int, str]]) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._selected: int | None = None

        select = discord.ui.Select(
            placeholder="Chọn đơn xin gia nhập…",
            options=[
                discord.SelectOption(label=label, value=str(app_id))
                for app_id, label in options
            ],
            row=0,
        )
        select.callback = self._on_select
        self._select = select
        self.add_item(select)

        approve = discord.ui.Button(label="✅ Chấp Thuận", style=discord.ButtonStyle.success, row=1)
        approve.callback = self._on_approve
        self.add_item(approve)

        reject = discord.ui.Button(label="🚫 Từ Chối", style=discord.ButtonStyle.danger, row=1)
        reject.callback = self._on_reject
        self.add_item(reject)

        self.add_item(_hub_button(discord_id, row=1))

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._selected = int(self._select.values[0])
        await interaction.response.defer()

    async def _resolve(self, interaction: discord.Interaction, approve: bool) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if self._selected is None:
            await interaction.response.send_message(
                embed=error_embed("Hãy chọn một đơn trước."), ephemeral=True
            )
            return
        if not await safe_defer(interaction):
            return

        feedback: discord.Embed | None = None
        try:
            async with get_session() as session:
                prepo = PlayerRepository(session)
                srepo = SectRepository(session)
                actor = await prepo.get_by_discord_id_lite(self._discord_id)
                member = await srepo.get_membership(actor.id) if actor else None
                if actor is None or member is None or not sect_rules.can_review_applications(member.rank):
                    feedback = error_embed("Bạn không còn quyền duyệt đơn.")
                else:
                    app = await session.get(SectApplication, self._selected)
                    if app is None or app.sect_id != member.sect_id:
                        feedback = error_embed("Đơn này không còn tồn tại.")
                    elif not approve:
                        await srepo.add_log(
                            member.sect_id, actor.id, sect_rules.LOG_REJECT,
                            f"Từ chối đơn của player#{app.player_id}",
                        )
                        await srepo.delete_application(app)
                        feedback = success_embed("Đã từ chối đơn xin gia nhập.")
                    else:
                        result = await srepo.add_member_atomic(member.sect_id, app.player_id)
                        if result.sect_full:
                            feedback = error_embed("Tông môn đã đầy — không thể nhận thêm.")
                        elif result.member is None:
                            feedback = error_embed("Không thể chấp thuận đơn này.")
                        else:
                            names = await prepo.get_names_by_ids([app.player_id])
                            await srepo.add_log(
                                member.sect_id, actor.id, sect_rules.LOG_JOIN,
                                f"{names.get(app.player_id, '?')} gia nhập",
                            )
                            feedback = success_embed(
                                f"**{names.get(app.player_id, '?')}** đã gia nhập tông môn."
                            )
        except IntegrityError:
            # Applicant joined another sect between render and click — the
            # UNIQUE(player_id) backstop fired. Clear their stale application.
            async with get_session() as session:
                srepo = SectRepository(session)
                app = await session.get(SectApplication, self._selected)
                if app is not None:
                    await srepo.delete_application(app)
            feedback = error_embed("Người này đã gia nhập tông môn khác — đơn đã bị hủy.")

        self._selected = None
        await _render_applications(interaction, self._discord_id)
        if feedback is not None:
            await interaction.followup.send(embed=feedback, ephemeral=True)

    async def _on_approve(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, approve=True)

    async def _on_reject(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, approve=False)


class ConfirmView(discord.ui.View):
    """Two-button confirm. ``on_confirm(interaction)`` runs after a defer."""

    def __init__(self, discord_id: int, confirm_label: str, on_confirm) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._on_confirm = on_confirm

        yes = discord.ui.Button(label=confirm_label, style=discord.ButtonStyle.danger, row=0)
        yes.callback = self._yes
        self.add_item(yes)

        no = discord.ui.Button(label="Hủy", style=discord.ButtonStyle.secondary, row=0)
        no.callback = self._no
        self.add_item(no)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _yes(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._on_confirm(interaction)

    async def _no(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=base_embed("Đã hủy", "Không có gì thay đổi.", color=SECT_COLOR), view=None
        )


class DisbandModal(discord.ui.Modal, title="Giải Tán Tông Môn"):
    """Type the exact sect name to confirm — funds, facilities, and every
    member's Cống Hiến evaporate with the sect."""

    confirm_name = discord.ui.TextInput(
        label="Gõ chính xác tên Tông Môn để xác nhận",
        required=True,
        max_length=64,
    )

    def __init__(self, discord_id: int, sect_id: int, sect_name: str) -> None:
        super().__init__()
        self._discord_id = discord_id
        self._sect_id = sect_id
        self._sect_name = sect_name
        self.confirm_name.placeholder = sect_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        typed = sect_rules.normalize_sect_name(self.confirm_name.value or "")
        if typed.lower() != self._sect_name.lower():
            await interaction.response.send_message(
                embed=error_embed("Tên không khớp — Tông Môn KHÔNG bị giải tán."),
                ephemeral=True,
            )
            return
        if not await safe_defer(interaction, ephemeral=True):
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(self._discord_id)
            sect = await srepo.get_sect_by_id(self._sect_id) if player else None
            if player is None or sect is None or sect.leader_player_id != player.id:
                await interaction.edit_original_response(
                    embed=error_embed("Chỉ Tông Chủ mới có thể giải tán."), view=None
                )
                return
            name, tag = sect.name, sect.tag
            await srepo.delete_sect(sect)

        await interaction.edit_original_response(
            embed=success_embed(f"Tông môn **[{tag}] {name}** đã giải tán. Công quỹ và Cống Hiến tan biến."),
            view=None,
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

class TongMonCog(commands.Cog, name="TongMon"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.boss_sweeper.start()

    def cog_unload(self) -> None:
        self.boss_sweeper.cancel()

    @tasks.loop(minutes=5)
    async def boss_sweeper(self) -> None:
        """Periodic closer for time-bound sect content:
        * Trấn Sơn Thú instances whose ISO week ended → claimable;
        * mine wars whose 24 h window ended → resolve (flip / shield) + logs;
        * hourly mine payouts (backstop — the map view also settles lazily)."""
        now = datetime.now(timezone.utc)
        try:
            async with get_session() as session:
                brepo = SectBossRepository(session)
                for inst in await brepo.list_expired_active(now):
                    await brepo.expire_instance(inst)
        except Exception as e:  # noqa: BLE001
            log.exception("Sect boss sweeper error: %s", e)

        try:
            async with get_session() as session:
                srepo = SectRepository(session)
                mrepo = SectMineRepository(session)
                resolved = await mrepo.resolve_due_wars(now)
                for war in resolved:
                    mdef = mine_rules.get_mine(war.mine_key) or {"vi": war.mine_key}
                    mine_vi = mdef["vi"]
                    if war.winner_sect_id == war.attacker_sect_id:
                        await srepo.add_log(
                            war.attacker_sect_id, None, sect_rules.LOG_MINE_CAPTURE,
                            f"Chiếm được {mine_vi} sau cuộc chiến!",
                        )
                        if war.defender_sect_id:
                            await srepo.add_log(
                                war.defender_sect_id, None, sect_rules.LOG_MINE_LOST,
                                f"Để mất {mine_vi} vào tay địch.",
                            )
                    elif war.winner_sect_id is not None:
                        await srepo.add_log(
                            war.winner_sect_id, None, sect_rules.LOG_MINE_DEFEND,
                            f"Bảo vệ thành công {mine_vi} — hưu chiến "
                            f"{mine_rules.DEFENSE_SHIELD_HOURS} giờ.",
                        )
                        await srepo.add_log(
                            war.attacker_sect_id, None, sect_rules.LOG_MINE_LOST,
                            f"Công phá {mine_vi} thất bại.",
                        )
                    else:
                        await srepo.add_log(
                            war.attacker_sect_id, None, sect_rules.LOG_MINE_LOST,
                            f"Vây hãm {mine_vi} thất bại — thủ vệ vẫn đứng vững.",
                        )
                await mrepo.accrue_payouts(now)
        except Exception as e:  # noqa: BLE001
            log.exception("Mine war sweeper error: %s", e)

    @boss_sweeper.before_loop
    async def _before_boss_sweeper(self) -> None:
        await self.bot.wait_until_ready()

    group = app_commands.Group(name="tongmon", description="Tông Môn — bang phái tu tiên")
    # Subgroups keep the top level under Discord's 25-entry cap and leave
    # room for the boss (Phase 5) / mine-war (Phase 6) surfaces.
    kho_group = app_commands.Group(
        name="kho", description="Kho Tàng chung của tông môn", parent=group
    )
    shop_group = app_commands.Group(
        name="cuahang", description="Cửa hàng Cống Hiến của tông môn", parent=group
    )
    mine_group = app_commands.Group(
        name="mo", description="Đại Chiến Khoáng Mạch — mỏ linh thạch", parent=group
    )

    # ── Shared helpers ──────────────────────────────────────────────────────

    async def _sect_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            async with get_session() as session:
                names = await SectRepository(session).search_sect_names(current)
        except Exception:  # noqa: BLE001 — autocomplete must never explode
            return []
        return [app_commands.Choice(name=n[:100], value=n) for n in names]

    # ── Creation ────────────────────────────────────────────────────────────

    @group.command(name="tao", description="Sáng lập Tông Môn (200,000 Công Đức, cảnh giới 4 bất kỳ trục)")
    @app_commands.describe(ten="Tên Tông Môn (3–32 ký tự)", tag="Tag hiển thị (2–6 ký tự không dấu)")
    async def create_cmd(self, interaction: discord.Interaction, ten: str, tag: str) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return

        name, err = sect_rules.validate_sect_name(ten)
        if err:
            await interaction.edit_original_response(embed=error_embed(err))
            return
        tag_norm, err = sect_rules.validate_sect_tag(tag)
        if err:
            await interaction.edit_original_response(embed=error_embed(err))
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)

            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."))
                return
            if await srepo.get_membership(player.id) is not None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn đã ở trong một Tông Môn — rời khỏi trước khi sáng lập.")
                )
                return
            cooldown = await srepo.get_rejoin_cooldown(player.id)
            if cooldown is not None:
                await interaction.edit_original_response(
                    embed=error_embed(f"Bạn vừa rời tông môn — có thể gia nhập/sáng lập lại {_ts(cooldown)}.")
                )
                return
            if not sect_rules.meets_realm_gate(
                player.body_realm, player.qi_realm, player.formation_realm
            ):
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Cần đạt cảnh giới thứ {sect_rules.SECT_CREATE_MIN_REALM + 1} "
                        "ở bất kỳ trục tu luyện nào để sáng lập Tông Môn."
                    )
                )
                return
            if player.merit < sect_rules.SECT_CREATE_COST:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Cần {sect_rules.SECT_CREATE_COST:,} Công Đức để sáng lập "
                        f"(hiện có {player.merit:,})."
                    )
                )
                return
            # Friendly pre-checks; the unique indexes stay the authority under races.
            if await srepo.get_sect_by_name(name) is not None:
                await interaction.edit_original_response(embed=error_embed("Tên Tông Môn đã tồn tại."))
                return

            try:
                sect = await srepo.create_sect(name, tag_norm, player.id)
                player.merit -= sect_rules.SECT_CREATE_COST
                await prepo.save(player)
                await srepo.add_log(
                    sect.id, player.id, sect_rules.LOG_CREATE, f"{player.name} sáng lập {name}"
                )
            except IntegrityError:
                await session.rollback()
                await interaction.edit_original_response(
                    embed=error_embed("Tên hoặc tag đã có Tông Môn khác sử dụng.")
                )
                return

        await interaction.edit_original_response(
            embed=success_embed(
                f"🏯 Tông môn **[{tag_norm}] {name}** đã thành lập!\n"
                f"Đã tiêu hao {sect_rules.SECT_CREATE_COST:,} Công Đức. "
                f"Dùng `/tongmon donxin` để duyệt thành viên."
            )
        )

    # ── Info / browse ───────────────────────────────────────────────────────

    @group.command(name="thongtin", description="Xem thông tin Tông Môn")
    @app_commands.describe(ten="Tên Tông Môn (bỏ trống = tông môn của bạn)")
    async def info_cmd(self, interaction: discord.Interaction, ten: str | None = None) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        if not ten:
            # Own sect → the interactive hub (info + feature buttons).
            await _render_sect_hub(interaction, interaction.user.id)
            return
        async with get_session() as session:
            srepo = SectRepository(session)
            sect = await srepo.get_sect_by_name(ten)
            if sect is None:
                await interaction.edit_original_response(
                    embed=error_embed(f"Không tìm thấy Tông Môn **{ten}**.")
                )
                return
            embed = await _build_sect_info_embed(session, sect)
        await interaction.edit_original_response(embed=embed)

    @info_cmd.autocomplete("ten")
    async def _info_ac(self, interaction: discord.Interaction, current: str):
        return await self._sect_name_autocomplete(interaction, current)

    @group.command(name="tim", description="Danh sách Tông Môn trên toàn thế giới")
    @app_commands.describe(trang="Trang (mặc định 1)")
    async def browse_cmd(self, interaction: discord.Interaction, trang: int = 1) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        page = max(1, trang)
        async with get_session() as session:
            srepo = SectRepository(session)
            prepo = PlayerRepository(session)
            total = await srepo.count_sects()
            rows = await srepo.list_sects_with_counts(offset=(page - 1) * 10, limit=10)
            names = await prepo.get_names_by_ids([s.leader_player_id for s, _ in rows])

        if total == 0:
            await interaction.edit_original_response(
                embed=base_embed(
                    "🗺️ Tông Môn Thiên Hạ",
                    "Chưa có Tông Môn nào — hãy là người đầu tiên với `/tongmon tao`!",
                    color=SECT_COLOR,
                )
            )
            return

        pages = (total + 9) // 10
        lines = []
        for i, (s, count) in enumerate(rows):
            cap = sect_rules.member_cap(s.level)
            lines.append(
                f"`{(page - 1) * 10 + i + 1:>2}.` **[{s.tag}] {s.name}** — Cấp {s.level} · "
                f"👥 {count}/{cap} · 👑 {names.get(s.leader_player_id, '?')}"
            )
        embed = base_embed(
            f"🗺️ Tông Môn Thiên Hạ ({total})",
            "\n".join(lines) + f"\n\n*Trang {page}/{pages} — `/tongmon xinvao` để xin gia nhập.*",
            color=SECT_COLOR,
        )
        await interaction.edit_original_response(embed=embed)

    @group.command(name="thanhvien", description="Danh sách thành viên tông môn của bạn")
    async def members_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_members(interaction, interaction.user.id)

    @group.command(name="nhatky", description="Nhật ký hoạt động tông môn (10 gần nhất)")
    async def logs_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_logs(interaction, interaction.user.id)

    # ── Joining ─────────────────────────────────────────────────────────────

    @group.command(name="xinvao", description="Nộp đơn xin gia nhập một Tông Môn")
    @app_commands.describe(ten="Tên Tông Môn", loi_nhan="Lời nhắn gửi ban quản lý (tùy chọn)")
    async def apply_cmd(
        self, interaction: discord.Interaction, ten: str, loi_nhan: str | None = None
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."))
                return
            if await srepo.get_membership(player.id) is not None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn đã ở trong một Tông Môn.")
                )
                return
            cooldown = await srepo.get_rejoin_cooldown(player.id)
            if cooldown is not None:
                await interaction.edit_original_response(
                    embed=error_embed(f"Bạn vừa rời tông môn — có thể xin gia nhập lại {_ts(cooldown)}.")
                )
                return
            sect = await srepo.get_sect_by_name(ten)
            if sect is None:
                await interaction.edit_original_response(
                    embed=error_embed(f"Không tìm thấy Tông Môn **{ten}**.")
                )
                return
            if await srepo.count_members(sect.id) >= sect_rules.member_cap(sect.level):
                await interaction.edit_original_response(
                    embed=error_embed("Tông môn này đã đầy thành viên.")
                )
                return
            if await srepo.get_application(sect.id, player.id) is not None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn đã nộp đơn vào tông môn này rồi — chờ duyệt.")
                )
                return
            if await srepo.count_pending_for_player(player.id) >= sect_rules.MAX_PENDING_APPLICATIONS:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Tối đa {sect_rules.MAX_PENDING_APPLICATIONS} đơn chờ duyệt cùng lúc — "
                        "dùng `/tongmon huyxin` để rút bớt."
                    )
                )
                return
            try:
                await srepo.create_application(
                    sect.id, player.id, (loi_nhan or "").strip()[:200] or None
                )
            except IntegrityError:
                await session.rollback()
                await interaction.edit_original_response(
                    embed=error_embed("Bạn đã nộp đơn vào tông môn này rồi.")
                )
                return
            tag, name = sect.tag, sect.name

        await interaction.edit_original_response(
            embed=success_embed(
                f"Đã nộp đơn xin gia nhập **[{tag}] {name}** — chờ Chấp Sự trở lên phê duyệt."
            )
        )

    @apply_cmd.autocomplete("ten")
    async def _apply_ac(self, interaction: discord.Interaction, current: str):
        return await self._sect_name_autocomplete(interaction, current)

    @group.command(name="huyxin", description="Rút đơn xin gia nhập")
    @app_commands.describe(ten="Tên Tông Môn (bỏ trống = rút tất cả đơn)")
    async def cancel_apply_cmd(
        self, interaction: discord.Interaction, ten: str | None = None
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."))
                return
            apps = await srepo.list_applications_for_player(player.id)
            if ten:
                normalized = sect_rules.normalize_sect_name(ten).lower()
                apps = [a for a in apps if a.sect and a.sect.name.lower() == normalized]
            if not apps:
                await interaction.edit_original_response(
                    embed=error_embed("Không có đơn nào để rút.")
                )
                return
            names = [f"[{a.sect.tag}] {a.sect.name}" for a in apps if a.sect]
            for a in apps:
                await srepo.delete_application(a)

        await interaction.edit_original_response(
            embed=success_embed("Đã rút đơn: " + ", ".join(f"**{n}**" for n in names))
        )

    @cancel_apply_cmd.autocomplete("ten")
    async def _cancel_ac(self, interaction: discord.Interaction, current: str):
        try:
            async with get_session() as session:
                prepo = PlayerRepository(session)
                srepo = SectRepository(session)
                player = await prepo.get_by_discord_id_lite(interaction.user.id)
                if player is None:
                    return []
                apps = await srepo.list_applications_for_player(player.id)
        except Exception:  # noqa: BLE001
            return []
        names = [a.sect.name for a in apps if a.sect]
        cur = (current or "").lower()
        return [
            app_commands.Choice(name=n[:100], value=n)
            for n in names if cur in n.lower()
        ][:25]

    @group.command(name="donxin", description="[Chấp Sự+] Duyệt đơn xin gia nhập")
    async def review_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_applications(interaction, interaction.user.id)

    # ── Donation ────────────────────────────────────────────────────────────

    @group.command(name="quyengop", description="Quyên góp Công Đức vào công quỹ tông môn")
    @app_commands.describe(so_luong="Số Công Đức muốn quyên góp")
    async def donate_cmd(
        self,
        interaction: discord.Interaction,
        so_luong: app_commands.Range[int, 1, 10_000_000],
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _do_donate(interaction, interaction.user.id, int(so_luong))

    # ── Membership lifecycle ────────────────────────────────────────────────

    @group.command(name="roikhoi", description="Rời khỏi tông môn hiện tại")
    async def leave_cmd(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None:
                await interaction.response.send_message(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào."), ephemeral=True
                )
                return
            if member.rank == sect_rules.RANK_TONG_CHU:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Tông Chủ không thể rời đi — hãy `/tongmon nhuongvi` hoặc `/tongmon giaitan` trước."
                    ),
                    ephemeral=True,
                )
                return
            sect_name = f"[{member.sect.tag}] {member.sect.name}"
            ch = int(member.contribution_points)

        async def _do_leave(followup_interaction: discord.Interaction) -> None:
            async with get_session() as session:
                prepo2 = PlayerRepository(session)
                srepo2 = SectRepository(session)
                player2 = await prepo2.get_by_discord_id_lite(interaction.user.id)
                member2 = await srepo2.get_membership(player2.id) if player2 else None
                if member2 is None or member2.rank == sect_rules.RANK_TONG_CHU:
                    await followup_interaction.edit_original_response(
                        embed=error_embed("Không thể rời tông môn lúc này."), view=None
                    )
                    return
                await srepo2.add_log(
                    member2.sect_id, player2.id, sect_rules.LOG_LEAVE,
                    f"{player2.name} rời tông môn",
                )
                await srepo2.remove_member(member2, cooldown=True)
            await followup_interaction.edit_original_response(
                embed=success_embed(
                    f"Bạn đã rời **{sect_name}**. Cống Hiến đã tan biến; "
                    f"{sect_rules.REJOIN_COOLDOWN_HOURS} giờ sau mới có thể gia nhập nơi khác."
                ),
                view=None,
            )

        await interaction.response.send_message(
            embed=base_embed(
                "⚠️ Rời Tông Môn?",
                f"Rời **{sect_name}** sẽ xóa **{ch:,} Cống Hiến** và khóa gia nhập "
                f"{sect_rules.REJOIN_COOLDOWN_HOURS} giờ.",
                color=0xED4245,
            ),
            view=ConfirmView(interaction.user.id, "Rời Khỏi", _do_leave),
            ephemeral=True,
        )

    @group.command(name="trucxuat", description="[Chấp Sự+] Trục xuất một thành viên")
    @app_commands.describe(thanh_vien="Thành viên cần trục xuất")
    async def kick_cmd(self, interaction: discord.Interaction, thanh_vien: discord.User) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            actor = await prepo.get_by_discord_id_lite(interaction.user.id)
            actor_m = await srepo.get_membership(actor.id) if actor else None
            if actor_m is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return
            target = await prepo.get_by_discord_id_lite(thanh_vien.id)
            target_m = await srepo.get_membership(target.id) if target else None
            if target_m is None or target_m.sect_id != actor_m.sect_id:
                await interaction.edit_original_response(
                    embed=error_embed("Người này không ở trong tông môn của bạn.")
                )
                return
            if not sect_rules.can_kick(actor_m.rank, target_m.rank):
                await interaction.edit_original_response(
                    embed=error_embed("Bạn không đủ chức vị để trục xuất người này.")
                )
                return
            await srepo.add_log(
                actor_m.sect_id, actor.id, sect_rules.LOG_KICK,
                f"{actor.name} trục xuất {target.name}",
            )
            await srepo.remove_member(target_m, cooldown=True)
            target_name = target.name

        await interaction.edit_original_response(
            embed=success_embed(f"Đã trục xuất **{target_name}** khỏi tông môn.")
        )

    # ── Ranks ───────────────────────────────────────────────────────────────

    async def _change_rank(
        self, interaction: discord.Interaction, target_user: discord.User, promote: bool
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        ladder = [sect_rules.RANK_DE_TU, sect_rules.RANK_CHAP_SU, sect_rules.RANK_TRUONG_LAO]
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            actor = await prepo.get_by_discord_id_lite(interaction.user.id)
            actor_m = await srepo.get_membership(actor.id) if actor else None
            if actor_m is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return
            target = await prepo.get_by_discord_id_lite(target_user.id)
            target_m = await srepo.get_membership(target.id) if target else None
            if target_m is None or target_m.sect_id != actor_m.sect_id:
                await interaction.edit_original_response(
                    embed=error_embed("Người này không ở trong tông môn của bạn.")
                )
                return
            if target_m.rank not in ladder:
                await interaction.edit_original_response(
                    embed=error_embed("Không thể thay đổi chức vị Tông Chủ.")
                )
                return

            idx = ladder.index(target_m.rank)
            new_idx = idx + 1 if promote else idx - 1
            if new_idx < 0 or new_idx >= len(ladder):
                await interaction.edit_original_response(
                    embed=error_embed(
                        "Không thể thăng cao hơn Trưởng Lão." if promote
                        else "Đã là Đệ Tử — không thể giáng thêm."
                    )
                )
                return
            new_rank = ladder[new_idx]

            if not sect_rules.can_set_rank(actor_m.rank, target_m.rank, new_rank):
                await interaction.edit_original_response(
                    embed=error_embed("Bạn không đủ chức vị cho thay đổi này.")
                )
                return
            if promote:
                cap = sect_rules.officer_cap(new_rank, actor_m.sect.level)
                if cap is not None and await srepo.count_rank(actor_m.sect_id, new_rank) >= cap:
                    await interaction.edit_original_response(
                        embed=error_embed(
                            f"Số ghế {sect_rules.RANK_LABELS[new_rank]} đã đầy ({cap})."
                        )
                    )
                    return

            target_m.rank = new_rank
            await srepo.add_log(
                actor_m.sect_id, actor.id,
                sect_rules.LOG_PROMOTE if promote else sect_rules.LOG_DEMOTE,
                f"{target.name} {'thăng' if promote else 'giáng'} chức {sect_rules.RANK_LABELS[new_rank]}",
            )
            target_name = target.name

        verb = "thăng chức" if promote else "giáng chức"
        await interaction.edit_original_response(
            embed=success_embed(f"**{target_name}** đã được {verb} thành {_rank_label(new_rank)}.")
        )

    @group.command(name="thangchuc", description="[Tông Chủ/Trưởng Lão] Thăng chức thành viên")
    @app_commands.describe(thanh_vien="Thành viên cần thăng chức")
    async def promote_cmd(self, interaction: discord.Interaction, thanh_vien: discord.User) -> None:
        await self._change_rank(interaction, thanh_vien, promote=True)

    @group.command(name="giangchuc", description="[Tông Chủ/Trưởng Lão] Giáng chức thành viên")
    @app_commands.describe(thanh_vien="Thành viên cần giáng chức")
    async def demote_cmd(self, interaction: discord.Interaction, thanh_vien: discord.User) -> None:
        await self._change_rank(interaction, thanh_vien, promote=False)

    @group.command(name="nhuongvi", description="[Tông Chủ] Nhượng ngôi Tông Chủ cho thành viên khác")
    @app_commands.describe(thanh_vien="Người kế nhiệm")
    async def transfer_cmd(self, interaction: discord.Interaction, thanh_vien: discord.User) -> None:
        if thanh_vien.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("Không thể nhượng vị cho chính mình."), ephemeral=True
            )
            return
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            actor = await prepo.get_by_discord_id_lite(interaction.user.id)
            actor_m = await srepo.get_membership(actor.id) if actor else None
            if actor_m is None or actor_m.rank != sect_rules.RANK_TONG_CHU:
                await interaction.response.send_message(
                    embed=error_embed("Chỉ Tông Chủ mới có thể nhượng vị."), ephemeral=True
                )
                return
            target = await prepo.get_by_discord_id_lite(thanh_vien.id)
            target_m = await srepo.get_membership(target.id) if target else None
            if target_m is None or target_m.sect_id != actor_m.sect_id:
                await interaction.response.send_message(
                    embed=error_embed("Người này không ở trong tông môn của bạn."), ephemeral=True
                )
                return
            sect_id = actor_m.sect_id
            actor_pid, target_pid = actor.id, target.id
            target_name, actor_name = target.name, actor.name

        async def _do_transfer(followup_interaction: discord.Interaction) -> None:
            async with get_session() as session:
                srepo2 = SectRepository(session)
                ok = await srepo2.transfer_leadership_atomic(sect_id, actor_pid, target_pid)
                if ok:
                    await srepo2.add_log(
                        sect_id, actor_pid, sect_rules.LOG_TRANSFER,
                        f"{actor_name} nhượng ngôi Tông Chủ cho {target_name}",
                    )
            if ok:
                await followup_interaction.edit_original_response(
                    embed=success_embed(f"👑 **{target_name}** giờ là Tông Chủ. Bạn trở thành Trưởng Lão."),
                    view=None,
                )
            else:
                await followup_interaction.edit_original_response(
                    embed=error_embed("Nhượng vị thất bại — kiểm tra lại thành viên."), view=None
                )

        await interaction.response.send_message(
            embed=base_embed(
                "⚠️ Nhượng Ngôi Tông Chủ?",
                f"Trao toàn quyền tông môn cho **{target_name}**? Bạn sẽ trở thành Trưởng Lão.",
                color=0xED4245,
            ),
            view=ConfirmView(interaction.user.id, "Nhượng Vị", _do_transfer),
            ephemeral=True,
        )

    @group.command(name="giaitan", description="[Tông Chủ] Giải tán tông môn — không thể hoàn tác")
    async def disband_cmd(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None or member.rank != sect_rules.RANK_TONG_CHU:
                await interaction.response.send_message(
                    embed=error_embed("Chỉ Tông Chủ mới có thể giải tán tông môn."), ephemeral=True
                )
                return
            sect_id, sect_name = member.sect_id, member.sect.name

        await interaction.response.send_modal(
            DisbandModal(interaction.user.id, sect_id, sect_name)
        )

    @group.command(name="nangcap", description="[Trưởng Lão+] Nâng cấp công trình tông môn")
    async def upgrade_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_facilities(interaction, interaction.user.id)

    # ── Kho Tàng storage ────────────────────────────────────────────────────

    @kho_group.command(name="xem", description="Xem Kho Tàng chung của tông môn")
    @app_commands.describe(trang="Trang (mặc định 1)")
    async def storage_cmd(self, interaction: discord.Interaction, trang: int = 1) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_storage(interaction, interaction.user.id, page=trang)

    @kho_group.command(name="gui", description="Gửi vật phẩm vào Kho Tàng (không thể rút lại tự do)")
    @app_commands.describe(
        vat_pham="Vật phẩm trong túi muốn gửi",
        so_luong="Số lượng (mặc định 1)",
    )
    async def deposit_cmd(
        self,
        interaction: discord.Interaction,
        vat_pham: str,
        so_luong: app_commands.Range[int, 1, 9_999] = 1,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        parsed = _parse_item_value(vat_pham)
        if parsed is None:
            await interaction.edit_original_response(
                embed=error_embed("Không nhận diện được vật phẩm — hãy chọn từ danh sách gợi ý.")
            )
            return
        item_key, grade = parsed
        item_def = registry.get_item(item_key)
        if not sect_rules.is_storable_item(item_def):
            await interaction.edit_original_response(
                embed=error_embed("Vật phẩm này không thể gửi vào Kho Tàng.")
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            inv_repo = InventoryRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return

            owned = await inv_repo.get_quantity_raw(player.id, item_key, grade)
            if owned <= 0:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn không có vật phẩm này trong túi.")
                )
                return

            requested = min(int(so_luong), owned)
            result = await srepo.deposit_storage_atomic(
                member.sect_id, item_key, grade, requested
            )
            if result.not_built:
                await interaction.edit_original_response(
                    embed=error_embed("Tông môn chưa xây Kho Tàng.")
                )
                return
            if result.storage_full:
                await interaction.edit_original_response(
                    embed=error_embed("Kho Tàng đã đầy ô chứa — nâng cấp Kho Tàng để mở rộng.")
                )
                return
            if result.stack_full or result.applied <= 0:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Ô chứa vật phẩm này đã đạt giới hạn ×{sect_rules.STORAGE_STACK_CAP:,}."
                    )
                )
                return

            removed = await inv_repo.try_remove_item_raw(
                player.id, item_key, grade, result.applied
            )
            if not removed:
                await session.rollback()
                await interaction.edit_original_response(
                    embed=error_embed("Không đủ vật phẩm trong túi — thao tác đã hủy.")
                )
                return

            label = _item_display(item_key, grade)
            await srepo.add_log(
                member.sect_id, player.id, sect_rules.LOG_DEPOSIT,
                f"{player.name} gửi {label} ×{result.applied} vào kho",
            )
            slot_qty = result.slot_quantity

        note = "" if result.applied == so_luong else f" *(giới hạn còn lại: {result.applied:,})*"
        await interaction.edit_original_response(
            embed=success_embed(
                f"Đã gửi **{label}** ×{result.applied:,} vào Kho Tàng{note}.\n"
                f"Ô chứa hiện có ×{slot_qty:,}. Vật phẩm đã thuộc về tông môn."
            )
        )

    @deposit_cmd.autocomplete("vat_pham")
    async def _deposit_ac(self, interaction: discord.Interaction, current: str):
        try:
            async with get_session() as session:
                prepo = PlayerRepository(session)
                inv_repo = InventoryRepository(session)
                player = await prepo.get_by_discord_id_lite(interaction.user.id)
                if player is None:
                    return []
                rows = await inv_repo.get_all(player.id)
        except Exception:  # noqa: BLE001 — autocomplete must never explode
            return []
        cur = (current or "").lower()
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            item = registry.get_item(row.item_key)
            if not sect_rules.is_storable_item(item):
                continue
            label = f"{_item_display(row.item_key, row.grade)} ×{row.quantity}"
            if cur and cur not in label.lower():
                continue
            choices.append(
                app_commands.Choice(name=label[:100], value=f"{row.item_key}|{row.grade}")
            )
            if len(choices) >= 25:
                break
        return choices

    @kho_group.command(name="xin", description="Xin vật phẩm từ Kho Tàng (Chấp Sự trở lên duyệt)")
    @app_commands.describe(
        vat_pham="Vật phẩm trong kho muốn xin",
        so_luong="Số lượng (mặc định 1)",
    )
    async def request_cmd(
        self,
        interaction: discord.Interaction,
        vat_pham: str,
        so_luong: app_commands.Range[int, 1, 9_999] = 1,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        parsed = _parse_item_value(vat_pham)
        if parsed is None:
            await interaction.edit_original_response(
                embed=error_embed("Không nhận diện được vật phẩm — hãy chọn từ danh sách gợi ý.")
            )
            return
        item_key, grade = parsed

        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return

            pending = await srepo.list_pending_requests_for_player(member.sect_id, player.id)
            if len(pending) >= sect_rules.MAX_PENDING_STORAGE_REQUESTS:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Tối đa {sect_rules.MAX_PENDING_STORAGE_REQUESTS} đơn chờ duyệt — "
                        "dùng `/tongmon kho huyxin` để rút bớt."
                    )
                )
                return
            now = datetime.now(timezone.utc)
            approved = await srepo.count_weekly_approved(
                member.sect_id, player.id, sect_rules.week_start_utc(now)
            )
            if approved >= sect_rules.MAX_WEEKLY_STORAGE_WITHDRAWALS:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Tuần này bạn đã nhận đủ {sect_rules.MAX_WEEKLY_STORAGE_WITHDRAWALS} lần từ kho."
                    )
                )
                return

            stock_rows = await srepo.list_storage_items(member.sect_id)
            stock = next(
                (r for r in stock_rows if r.item_key == item_key and r.grade == grade), None
            )
            if stock is None or stock.quantity <= 0:
                await interaction.edit_original_response(
                    embed=error_embed("Kho không có vật phẩm này.")
                )
                return

            qty = min(int(so_luong), int(stock.quantity))
            await srepo.create_storage_request(
                member.sect_id, player.id, item_key, grade, qty
            )
            label = _item_display(item_key, grade)

        await interaction.edit_original_response(
            embed=success_embed(
                f"Đã gửi đơn xin **{label}** ×{qty:,} — chờ Chấp Sự trở lên phê duyệt "
                f"(đơn tự hết hạn sau {sect_rules.STORAGE_REQUEST_EXPIRE_HOURS} giờ)."
            )
        )

    @request_cmd.autocomplete("vat_pham")
    async def _request_ac(self, interaction: discord.Interaction, current: str):
        try:
            async with get_session() as session:
                prepo = PlayerRepository(session)
                srepo = SectRepository(session)
                player = await prepo.get_by_discord_id_lite(interaction.user.id)
                member = await srepo.get_membership(player.id) if player else None
                if member is None:
                    return []
                rows = await srepo.list_storage_items(member.sect_id)
        except Exception:  # noqa: BLE001
            return []
        cur = (current or "").lower()
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            label = f"{_item_display(row.item_key, row.grade)} ×{row.quantity}"
            if cur and cur not in label.lower():
                continue
            choices.append(
                app_commands.Choice(name=label[:100], value=f"{row.item_key}|{row.grade}")
            )
            if len(choices) >= 25:
                break
        return choices

    @kho_group.command(name="huyxin", description="Rút toàn bộ đơn xin vật phẩm đang chờ của bạn")
    async def cancel_request_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return
            removed = await srepo.cancel_pending_requests(member.sect_id, player.id)

        if removed <= 0:
            await interaction.edit_original_response(
                embed=error_embed("Bạn không có đơn xin vật phẩm nào đang chờ.")
            )
            return
        await interaction.edit_original_response(
            embed=success_embed(f"Đã rút {removed} đơn xin vật phẩm.")
        )

    @kho_group.command(name="duyet", description="[Chấp Sự+] Duyệt đơn xin vật phẩm từ Kho Tàng")
    async def review_storage_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_storage_requests(interaction, interaction.user.id)

    # ── Check-in & missions (Phase 4) ───────────────────────────────────────

    @group.command(name="diemdanh", description="Điểm danh tông môn hằng ngày (+50 Cống Hiến)")
    async def checkin_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _do_checkin(interaction, interaction.user.id)

    @group.command(name="nhiemvu", description="Nhiệm vụ tông môn hằng ngày — tự nhận thưởng khi mở")
    async def missions_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_missions(interaction, interaction.user.id)

    # ── Sect shop (Phase 4) ─────────────────────────────────────────────────

    @shop_group.command(name="xem", description="Xem cửa hàng Cống Hiến của tông môn")
    @app_commands.describe(trang="Trang bí tịch (mặc định 1)")
    async def shop_view_cmd(self, interaction: discord.Interaction, trang: int = 1) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_shop(interaction, interaction.user.id, page=trang)

    @shop_group.command(name="mua", description="Mua vật phẩm bằng Cống Hiến")
    @app_commands.describe(
        mat_hang="Mặt hàng trong cửa hàng",
        so_luong="Số lượng (mặc định 1)",
    )
    async def shop_buy_cmd(
        self,
        interaction: discord.Interaction,
        mat_hang: str,
        so_luong: app_commands.Range[int, 1, 99] = 1,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        parsed = _parse_item_value(mat_hang)
        if parsed is None:
            await interaction.edit_original_response(
                embed=error_embed("Không nhận diện được mặt hàng — hãy chọn từ danh sách gợi ý.")
            )
            return
        item_key, grade = parsed

        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            inv_repo = InventoryRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return

            # Re-derive the authoritative shelf server-side — never trust a
            # price or availability the client remembered from an old view.
            levels = await srepo.get_facility_levels(member.sect_id)
            now = datetime.now(timezone.utc)
            wk = sect_rules.week_key(now)
            catalog = shop_rules.full_catalog(member.sect_id, wk, levels)
            slot = shop_rules.find_slot(catalog, item_key, grade)
            if slot is None:
                await interaction.edit_original_response(
                    embed=error_embed("Mặt hàng này hiện không có trong cửa hàng tông môn.")
                )
                return

            result = await srepo.purchase_shop_item_atomic(
                member.sect_id, player.id, slot.item_key, slot.grade,
                slot.price_ch, int(so_luong), slot.weekly_limit, wk,
            )
            if result.weekly_limited:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Vượt giới hạn tuần — bạn chỉ có thể mua thêm {result.weekly_remaining} món này."
                    )
                )
                return
            if result.insufficient_ch:
                await interaction.edit_original_response(
                    embed=error_embed(
                        f"Không đủ Cống Hiến: cần {result.cost:,}, hiện có {result.ch_left:,}."
                    )
                )
                return
            if not result.ok:
                await interaction.edit_original_response(
                    embed=error_embed("Không thể mua mặt hàng này lúc này.")
                )
                return

            await inv_repo.add_item_raw(player.id, slot.item_key, slot.grade, int(so_luong))
            label = _item_display(slot.item_key, slot.grade)

        await interaction.edit_original_response(
            embed=success_embed(
                f"Đã mua **{label}** ×{so_luong} với 💠 {result.cost:,} Cống Hiến "
                f"(còn {result.ch_left:,})."
            )
        )

    @shop_buy_cmd.autocomplete("mat_hang")
    async def _shop_buy_ac(self, interaction: discord.Interaction, current: str):
        try:
            async with get_session() as session:
                prepo = PlayerRepository(session)
                srepo = SectRepository(session)
                player = await prepo.get_by_discord_id_lite(interaction.user.id)
                member = await srepo.get_membership(player.id) if player else None
                if member is None:
                    return []
                levels = await srepo.get_facility_levels(member.sect_id)
        except Exception:  # noqa: BLE001 — autocomplete must never explode
            return []
        wk = sect_rules.week_key(datetime.now(timezone.utc))
        catalog = shop_rules.full_catalog(member.sect_id, wk, levels)
        cur = (current or "").lower()
        choices: list[app_commands.Choice[str]] = []
        for slot in catalog:
            label = f"{_item_display(slot.item_key, slot.grade)} — {slot.price_ch:,} CH"
            if cur and cur not in label.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=label[:100], value=f"{slot.item_key}|{slot.grade}"
                )
            )
            if len(choices) >= 25:
                break
        return choices

    # ── Sect boss (Phase 5) ─────────────────────────────────────────────────

    @group.command(name="boss", description="Trấn Sơn Thú — boss tông môn hằng tuần")
    async def boss_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_boss_hub(interaction, interaction.user.id)

    @group.command(name="bxh", description="Bảng xếp hạng sát thương Trấn Sơn Thú tuần này")
    async def boss_leaderboard_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        wk = sect_rules.week_key(datetime.now(timezone.utc))
        async with get_session() as session:
            rows = await SectBossRepository(session).weekly_sect_damage(wk, limit=10)

        if not rows:
            body = "Chưa có tông môn nào khiêu chiến Trấn Sơn Thú tuần này."
        else:
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, (_sid, tag, name, total, killed) in enumerate(rows):
                icon = medals[i] if i < len(medals) else f"`#{i + 1}`"
                kill_mark = " 🏆" if killed else ""
                lines.append(f"{icon} **[{tag}] {name}** — {total:,} sát thương{kill_mark}")
            body = "\n".join(lines) + "\n\n*🏆 = đã hạ gục boss tuần này.*"
        await interaction.edit_original_response(
            embed=base_embed(f"📊 BXH Trấn Sơn Thú — tuần {wk}", body, color=SECT_COLOR)
        )

    # ── Mine wars (Phase 6) ─────────────────────────────────────────────────

    @mine_group.command(name="xem", description="Bản đồ khoáng mạch thiên hạ")
    async def mine_map_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_mine_map(interaction, interaction.user.id)

    @mine_group.command(
        name="tuyenchien",
        description="[Trưởng Lão+] Tuyên chiến chiếm mỏ linh thạch (30,000 quỹ)",
    )
    @app_commands.describe(mo="Mỏ muốn chiếm")
    async def mine_declare_cmd(self, interaction: discord.Interaction, mo: str) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        mdef = mine_rules.get_mine(mo)
        if mdef is None:
            await interaction.edit_original_response(
                embed=error_embed("Không tìm thấy mỏ này — chọn từ danh sách gợi ý.")
            )
            return

        now = datetime.now(timezone.utc)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            mrepo = SectMineRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None:
                await interaction.edit_original_response(
                    embed=error_embed("Bạn chưa gia nhập Tông Môn nào.")
                )
                return
            if sect_rules.rank_power(member.rank) < sect_rules.rank_power(sect_rules.RANK_TRUONG_LAO):
                await interaction.edit_original_response(
                    embed=error_embed("Cần chức vị Trưởng Lão trở lên để tuyên chiến.")
                )
                return
            await mrepo.ensure_mines_seeded()
            result = await mrepo.declare_war_atomic(mo, member.sect_id, now)

            if result.war is not None:
                await srepo.add_log(
                    member.sect_id, player.id, sect_rules.LOG_MINE_DECLARE,
                    f"{player.name} tuyên chiến tại {mdef['vi']}",
                )
                if result.war.defender_sect_id:
                    await srepo.add_log(
                        result.war.defender_sect_id, None, sect_rules.LOG_MINE_DECLARE,
                        f"{mdef['vi']} của tông môn bị tuyên chiến!",
                    )
                window_end = result.war.window_end
                is_siege = result.is_siege

        if result.war is None:
            if result.level_too_low:
                msg = f"Cần Tông Môn cấp {mine_rules.MINE_WAR_MIN_SECT_LEVEL} để tuyên chiến."
            elif result.insufficient_funds:
                msg = f"Công quỹ không đủ lệ phí tuyên chiến ({result.funds_needed:,})."
            elif result.own_mine:
                msg = "Mỏ này đã thuộc về tông môn của bạn."
            elif result.mine_shielded:
                msg = f"Mỏ đang trong thời gian hưu chiến — mở lại {_ts(result.shield_until)}."
            elif result.occupancy_capped:
                msg = f"Tông môn chỉ có thể chiếm giữ {mine_rules.OCCUPANCY_CAP} mỏ."
            elif result.already_at_war:
                msg = "Tông môn đang trong một cuộc chiến khác — kết thúc trước đã."
            elif result.redeclare_blocked:
                msg = f"Vừa thất bại tại mỏ này — có thể tuyên chiến lại {_ts(result.blocked_until)}."
            elif result.mine_contested:
                msg = "Mỏ này đang có cuộc chiến khác."
            else:
                msg = "Không thể tuyên chiến lúc này."
            await interaction.edit_original_response(embed=error_embed(msg))
            return

        kind = (
            "🏰 Mỏ vô chủ — hãy phá **thủ vệ NPC** trước khi hết hạn!"
            if is_siege else
            "⚔️ Chiến tranh với tông môn chiếm giữ — tích điểm bằng `/tongmon mo xuatchinh`!"
        )
        await interaction.edit_original_response(
            embed=success_embed(
                f"Đã tuyên chiến tại **{mdef['vi']}** (−{mine_rules.DECLARE_FEE_FUNDS:,} quỹ).\n"
                f"{kind}\nKết toán {_ts(window_end)} — mỗi thành viên có "
                f"{mine_rules.ATTEMPTS_PER_WAR} lượt xuất chinh."
            )
        )

    @mine_declare_cmd.autocomplete("mo")
    async def _mine_ac(self, interaction: discord.Interaction, current: str):
        cur = (current or "").lower()
        choices: list[app_commands.Choice[str]] = []
        for mdef in mine_rules.all_mines():
            tier = mine_rules.TIER_LABELS.get(mdef.get("tier"), "?")
            label = f"{mdef['vi']} ({tier}) — {mdef['funds_per_hour']:,}/giờ"
            if cur and cur not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label[:100], value=mdef["key"]))
        return choices[:25]

    @mine_group.command(name="xuatchinh", description="Xuất chinh trong cuộc chiến khoáng mạch của tông môn")
    async def mine_attack_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _execute_mine_attack(interaction, interaction.user.id)

    @mine_group.command(name="chienbao", description="Chiến báo cuộc chiến khoáng mạch hiện tại")
    async def mine_report_cmd(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        await _render_war_report(interaction, interaction.user.id)

    @group.command(name="thongbao", description="[Trưởng Lão+] Đặt thông báo tông môn")
    @app_commands.describe(noi_dung="Nội dung thông báo (tối đa 500 ký tự, bỏ trống = xóa)")
    async def announce_cmd(
        self, interaction: discord.Interaction, noi_dung: str | None = None
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        content = (noi_dung or "").strip()
        if len(content) > sect_rules.ANNOUNCEMENT_MAX_LEN:
            await interaction.edit_original_response(
                embed=error_embed(f"Thông báo tối đa {sect_rules.ANNOUNCEMENT_MAX_LEN} ký tự.")
            )
            return
        async with get_session() as session:
            prepo = PlayerRepository(session)
            srepo = SectRepository(session)
            player = await prepo.get_by_discord_id_lite(interaction.user.id)
            member = await srepo.get_membership(player.id) if player else None
            if member is None or not sect_rules.can_set_announcement(member.rank):
                await interaction.edit_original_response(
                    embed=error_embed("Cần chức vị Trưởng Lão trở lên để đặt thông báo.")
                )
                return
            member.sect.announcement = content or None
            await srepo.add_log(
                member.sect_id, player.id, sect_rules.LOG_ANNOUNCE,
                f"{player.name} cập nhật thông báo",
            )

        await interaction.edit_original_response(
            embed=success_embed("Đã cập nhật thông báo tông môn." if content else "Đã xóa thông báo.")
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TongMonCog(bot))
