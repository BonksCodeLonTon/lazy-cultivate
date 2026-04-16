"""Equipment commands — forge, equip, unequip, view gear and bag."""
from __future__ import annotations

import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.engine.equipment import SLOT_LABELS, SLOT_ORDER, compute_equipment_stats, format_computed_stats, format_stat
from src.game.engine.item_generator import (
    FORGE_COST, generate_item, generate_unique, grade_from_realm,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

_SLOT_CHOICES = [app_commands.Choice(name=SLOT_LABELS[s], value=s) for s in SLOT_ORDER]

_GRADE_EMOJI = {1: "⚪", 2: "🟢", 3: "🔵", 4: "🟡"}


def _grade_label(grade: int) -> str:
    emoji = _GRADE_EMOJI.get(grade, "⚫")
    return f"{emoji} G{grade}"


def _gear_embed(player_name: str, equipped: list) -> discord.Embed:
    by_slot = {i.slot: i for i in equipped}
    embed = base_embed(f"⚔️ Trang Bị — {player_name}", color=0xFFD700)
    for slot in SLOT_ORDER:
        label = SLOT_LABELS[slot]
        inst = by_slot.get(slot)
        if inst:
            stats_str = format_computed_stats(inst.computed_stats)
            embed.add_field(
                name=f"{label} [{_grade_label(inst.grade)}]",
                value=f"**{inst.display_name}**\n{stats_str}",
                inline=True,
            )
        else:
            embed.add_field(name=label, value="*— Trống —*", inline=True)

    total = compute_equipment_stats(equipped)
    if total:
        summary = " | ".join(format_stat(k, v) for k, v in total.items())
        embed.add_field(name="📊 Tổng Bonus", value=summary, inline=False)
    return embed


def _bag_embed(player_name: str, items: list, slot_filter: str | None) -> discord.Embed:
    title = f"🎒 Túi Đồ — {player_name}"
    if slot_filter:
        title += f" ({SLOT_LABELS.get(slot_filter, slot_filter)})"
    embed = base_embed(title, color=0x4488FF)

    if not items:
        embed.description = "*Túi đồ trống. Dùng `/forge` để tạo trang bị.*"
        return embed

    # Group by slot
    by_slot: dict[str, list] = {}
    for inst in items:
        s = inst.slot or "unknown"
        by_slot.setdefault(s, []).append(inst)

    for slot in SLOT_ORDER:
        if slot_filter and slot != slot_filter:
            continue
        insts = by_slot.get(slot, [])
        if not insts:
            continue
        lines = []
        for inst in insts:
            stats_str = format_computed_stats(inst.computed_stats)
            lines.append(f"`ID:{inst.id}` {_grade_label(inst.grade)} **{inst.display_name}**\n　{stats_str}")
        embed.add_field(
            name=SLOT_LABELS.get(slot, slot),
            value="\n".join(lines)[:1020],
            inline=False,
        )
    return embed


def _realm_total_for_player(player) -> int:
    return (
        player.body_realm * 9 + player.body_level
        + player.qi_realm * 9 + player.qi_level
        + player.formation_realm * 9 + player.formation_level
    ) // 3


class EquipmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /gear ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="gear", description="Xem trang bị hiện tại")
    async def gear(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return
            equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        await interaction.followup.send(embed=_gear_embed(player.name, equipped))

    # ── /bag ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="bag", description="Xem túi đồ trang bị")
    @app_commands.describe(slot="Lọc theo vị trí (để trống = tất cả)")
    @app_commands.choices(slot=[app_commands.Choice(name="Tất cả", value="all")] + _SLOT_CHOICES)
    async def bag(self, interaction: discord.Interaction, slot: str = "all") -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return
            bag_items = [i for i in (player.item_instances or []) if i.location == "bag"]

        slot_filter = None if slot == "all" else slot
        if slot_filter:
            bag_items = [i for i in bag_items if i.slot == slot_filter]

        await interaction.followup.send(embed=_bag_embed(player.name, bag_items, slot_filter))

    # ── /equip ────────────────────────────────────────────────────────────────

    @app_commands.command(name="equip", description="Trang bị vật phẩm từ túi đồ theo ID")
    @app_commands.describe(instance_id="ID vật phẩm (xem trong /bag)")
    async def equip(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return

            erepo = EquipmentRepository(session)
            try:
                displaced = await erepo.equip(player.id, instance_id)
            except ValueError as e:
                await interaction.followup.send(embed=error_embed(str(e)))
                return

            # Re-fetch to get updated display name
            inst = await erepo.get_instance(instance_id, player.id)

        slot_label = SLOT_LABELS.get(inst.slot, inst.slot) if inst else ""
        if displaced:
            msg = (
                f"✅ Đã trang bị **{inst.display_name}** vào {slot_label}.\n"
                f"↩️ **{displaced.display_name}** trả về túi đồ."
            )
        else:
            msg = f"✅ Đã trang bị **{inst.display_name}** vào {slot_label}."
        await interaction.followup.send(embed=success_embed(msg))

    # ── /unequip ──────────────────────────────────────────────────────────────

    @app_commands.command(name="unequip", description="Tháo trang bị khỏi một vị trí")
    @app_commands.describe(slot="Vị trí trang bị muốn tháo")
    @app_commands.choices(slot=_SLOT_CHOICES)
    async def unequip(self, interaction: discord.Interaction, slot: str) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return

            erepo = EquipmentRepository(session)
            inst = await erepo.unequip(player.id, slot)
            if inst is None:
                slot_label = SLOT_LABELS.get(slot, slot)
                await interaction.followup.send(embed=error_embed(f"{slot_label} đang trống."))
                return
            name = inst.display_name

        slot_label = SLOT_LABELS.get(slot, slot)
        await interaction.followup.send(
            embed=success_embed(f"↩️ Đã tháo **{name}** từ {slot_label} về túi đồ.")
        )

    # ── /forge ────────────────────────────────────────────────────────────────

    @app_commands.command(name="forge", description="Đúc trang bị ngẫu nhiên (tốn Công Đức)")
    @app_commands.describe(slot="Vị trí trang bị muốn đúc")
    @app_commands.choices(slot=_SLOT_CHOICES)
    async def forge(self, interaction: discord.Interaction, slot: str) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return

            realm_total = _realm_total_for_player(player)
            grade = grade_from_realm(realm_total)
            cost = FORGE_COST[grade]

            if player.merit < cost:
                await interaction.followup.send(
                    embed=error_embed(f"Không đủ Công Đức. Cần **{cost:,}**, hiện có **{player.merit:,}**.")
                )
                return

            bases = registry.bases_for_slot(slot)
            if not bases:
                await interaction.followup.send(embed=error_embed(f"Không có vật phẩm nào cho vị trí này."))
                return

            rng = random.Random()
            base_key = rng.choice(bases)["key"]
            # Grade determines affix count: g1→0-1 each, g2→1 each, g3→1+1
            num_pfx = rng.randint(0, 1) if grade == 1 else 1
            num_sfx = rng.randint(0, 1) if grade == 1 else 1
            item_data = generate_item(base_key, grade, rng, num_prefixes=num_pfx, num_suffixes=num_sfx)

            player.merit -= cost
            await prepo.save(player)

            erepo = EquipmentRepository(session)
            inst = await erepo.add_to_bag(player.id, item_data)

        stats_str = format_computed_stats(inst.computed_stats)
        embed = success_embed(
            f"⚒️ Đúc thành công **{inst.display_name}** [{_grade_label(grade)}]\n"
            f"{stats_str}\n\n"
            f"Đã trừ **{cost:,}** Công Đức. Dùng `/bag` để xem, `/equip {inst.id}` để trang bị."
        )
        await interaction.followup.send(embed=embed)

    # ── /item_info ────────────────────────────────────────────────────────────

    @app_commands.command(name="item_info", description="Xem chi tiết một vật phẩm theo ID")
    @app_commands.describe(instance_id="ID vật phẩm (xem trong /bag hoặc /gear)")
    async def item_info(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return
            erepo = EquipmentRepository(session)
            inst = await erepo.get_instance(instance_id, player.id)
            if inst is None:
                await interaction.followup.send(embed=error_embed(f"Không tìm thấy vật phẩm ID `{instance_id}`."))
                return

        slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?")
        embed = base_embed(inst.display_name, color=0xFFD700 if inst.unique_key else 0x88AAFF)
        embed.add_field(name="Vị trí", value=slot_label, inline=True)
        embed.add_field(name="Cấp độ", value=_grade_label(inst.grade), inline=True)
        loc_str = "🟢 Đang trang bị" if inst.location == "equipped" else "📦 Trong túi"
        embed.add_field(name="Trạng thái", value=loc_str, inline=True)

        if inst.unique_key:
            uniq = registry.get_unique(inst.unique_key)
            if uniq and uniq.get("description_vi"):
                embed.add_field(name="✨ Vật phẩm Đặc Biệt", value=f"*{uniq['description_vi']}*", inline=False)

        # Show base implicit
        if inst.base_key:
            base = registry.get_base(inst.base_key)
            if base:
                implicit_str = format_computed_stats(base.get("implicit_stats", {}))
                embed.add_field(name=f"Nền ({base['vi']})", value=implicit_str, inline=False)

        # Show affixes
        for affix_entry in (inst.affixes or []):
            aff = registry.get_affix(affix_entry["key"])
            if aff:
                val = affix_entry["value"]
                stat = affix_entry["stat"]
                val_str = format_stat(stat, val)
                kind = "🔶 Tiền Tố" if affix_entry["type"] == "prefix" else "🔷 Hậu Tố"
                embed.add_field(name=f"{kind} — {aff['vi']}", value=val_str, inline=True)

        # Total stats
        total_str = format_computed_stats(inst.computed_stats)
        embed.add_field(name="📊 Tổng Chỉ Số", value=total_str, inline=False)

        await interaction.followup.send(embed=embed)

    # ── /discard ──────────────────────────────────────────────────────────────

    @app_commands.command(name="discard", description="Hủy vật phẩm trong túi đồ (không thể hoàn tác)")
    @app_commands.describe(instance_id="ID vật phẩm cần hủy")
    async def discard(self, interaction: discord.Interaction, instance_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."))
                return

            erepo = EquipmentRepository(session)
            inst = await erepo.get_instance(instance_id, player.id)
            if inst is None or inst.location != "bag":
                await interaction.followup.send(embed=error_embed("Vật phẩm không tồn tại trong túi đồ."))
                return
            name = inst.display_name
            await erepo.discard(player.id, instance_id)

        await interaction.followup.send(embed=success_embed(f"🗑️ Đã hủy **{name}**."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EquipmentCog(bot))
