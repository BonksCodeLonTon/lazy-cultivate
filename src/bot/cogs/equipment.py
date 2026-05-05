"""Equipment commands — equip, unequip, view gear and bag."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.engine.equipment import SLOT_LABELS, SLOT_ORDER, STAT_LABELS, compute_equipment_stats, format_computed_stats, format_stat
from src.utils import emojis
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

_SLOT_CHOICES = [app_commands.Choice(name=SLOT_LABELS[s], value=s) for s in SLOT_ORDER]


def _grade_label(grade: int) -> str:
    return f"{emojis.for_grade(grade)} G{grade}"


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


def _bag_embed(
    player_name: str,
    bag_items: list,
    equipped: list,
    slot_filter: str | None,
) -> discord.Embed:
    """Render bag items grouped by slot, with the currently-equipped item in
    that slot shown as a header line so players can compare before swapping.

    ``equipped`` should be the player's currently-equipped instances (location
    == "equipped"); ``bag_items`` should be in-bag items only. Both are passed
    in so the caller can re-fetch once and reuse for the view buttons.
    """
    title = f"🎒 Túi Đồ — {player_name}"
    if slot_filter:
        title += f" ({SLOT_LABELS.get(slot_filter, slot_filter)})"
    embed = base_embed(title, color=0x4488FF)

    by_slot_bag: dict[str, list] = {}
    for inst in bag_items:
        s = inst.slot or "unknown"
        by_slot_bag.setdefault(s, []).append(inst)
    by_slot_eq: dict[str, object] = {i.slot: i for i in equipped if i.slot}

    rendered_any = False
    for slot in SLOT_ORDER:
        if slot_filter and slot != slot_filter:
            continue
        insts = by_slot_bag.get(slot, [])
        eq_inst = by_slot_eq.get(slot)
        if not insts and not eq_inst:
            continue
        rendered_any = True

        # Header — the currently-equipped piece in this slot, or an "empty"
        # marker so the player can see at a glance what they'd be swapping.
        if eq_inst:
            eq_stats = format_computed_stats(eq_inst.computed_stats)
            header = (
                f"🟢 **Đang trang bị:** `ID:{eq_inst.id}` "
                f"{_grade_label(eq_inst.grade)} **{eq_inst.display_name}**\n"
                f"　{eq_stats}"
            )
        else:
            header = "⚪ *Đang trang bị: — Trống —*"

        # Bag entries below.
        if insts:
            bag_lines = []
            for inst in insts:
                stats_str = format_computed_stats(inst.computed_stats)
                bag_lines.append(
                    f"`ID:{inst.id}` {_grade_label(inst.grade)} "
                    f"**{inst.display_name}**\n　{stats_str}"
                )
            bag_block = "\n".join(bag_lines)
        else:
            bag_block = "*— Không có vật phẩm trong túi —*"

        body = f"{header}\n\n📦 **Trong túi:**\n{bag_block}"
        embed.add_field(
            name=SLOT_LABELS.get(slot, slot),
            value=body[:1020],
            inline=False,
        )

    if not rendered_any:
        embed.description = (
            "*Không có vật phẩm cho vị trí này.*"
            if slot_filter
            else "*Túi đồ trống. Dùng `/forge` để tạo trang bị.*"
        )
    return embed


class BagView(discord.ui.View):
    """Interactive bag view — slot filter + Equip/Unequip dropdowns.

    The view rebuilds itself after every action so the caller doesn't need to
    refetch the embed manually. Discord's hard 25-option-per-Select cap is
    respected by trimming long item lists; the underlying bag is still reachable
    via `/bag` with a slot filter when overflow happens.
    """

    def __init__(
        self,
        discord_id: int,
        player_name: str,
        bag_items: list,
        equipped: list,
        slot_filter: str | None,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._player_name = player_name
        self._slot_filter = slot_filter

        # ── Row 0: slot filter dropdown ──────────────────────────────────
        slot_select = discord.ui.Select(
            placeholder=(
                f"📂 Lọc: {SLOT_LABELS.get(slot_filter, slot_filter)}"
                if slot_filter
                else "📂 Lọc theo vị trí…"
            ),
            options=[
                discord.SelectOption(label="Tất cả", value="__all__", emoji="📚"),
                *[
                    discord.SelectOption(
                        label=SLOT_LABELS[s].split(" ", 1)[-1],   # strip emoji prefix
                        value=s,
                        emoji=SLOT_LABELS[s].split(" ", 1)[0],
                        default=(s == slot_filter),
                    )
                    for s in SLOT_ORDER
                ],
            ],
            row=0,
        )
        slot_select.callback = self._make_filter_cb()
        self.add_item(slot_select)

        # ── Row 1: Equip dropdown — bag items in the current filter ─────
        if slot_filter:
            visible_bag = [i for i in bag_items if i.slot == slot_filter]
        else:
            visible_bag = list(bag_items)
        if visible_bag:
            options = []
            for inst in visible_bag[:25]:
                slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?")
                options.append(discord.SelectOption(
                    label=f"{inst.display_name}"[:100],
                    value=str(inst.id),
                    description=f"{slot_label} · {_grade_label(inst.grade)} · ID {inst.id}"[:100],
                ))
            equip_select = discord.ui.Select(
                placeholder=f"⚔️ Trang bị từ túi… ({len(visible_bag)} món)",
                options=options,
                row=1,
            )
            equip_select.callback = self._make_equip_cb()
            self.add_item(equip_select)

        # ── Row 2: Unequip dropdown — currently equipped in current filter ─
        if slot_filter:
            visible_eq = [i for i in equipped if i.slot == slot_filter]
        else:
            visible_eq = list(equipped)
        if visible_eq:
            options = []
            for inst in visible_eq[:25]:
                slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?")
                options.append(discord.SelectOption(
                    label=f"{inst.display_name}"[:100],
                    value=inst.slot,
                    description=f"{slot_label} · {_grade_label(inst.grade)}"[:100],
                ))
            unequip_select = discord.ui.Select(
                placeholder=f"↩️ Tháo trang bị… ({len(visible_eq)} món)",
                options=options,
                row=2,
            )
            unequip_select.callback = self._make_unequip_cb()
            self.add_item(unequip_select)

    # ── Callbacks ────────────────────────────────────────────────────────

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _refresh(self, interaction: discord.Interaction, slot_filter: str | None, *, status: str | None = None) -> None:
        """Re-fetch bag + equipped and re-render the view inline."""
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            erepo = EquipmentRepository(session)
            equipped = await erepo.get_equipped(player.id)
            bag = await erepo.get_bag(player.id)
        embed = _bag_embed(self._player_name, bag, equipped, slot_filter)
        if status:
            embed.description = status
        view = BagView(self._discord_id, self._player_name, bag, equipped, slot_filter)
        await interaction.edit_original_response(embed=embed, view=view)

    def _make_filter_cb(self):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            value = interaction.data["values"][0]
            new_filter: str | None = None if value == "__all__" else value
            await self._refresh(interaction, new_filter)
        return _cb

    def _make_equip_cb(self):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            instance_id = int(interaction.data["values"][0])
            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(self._discord_id)
                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return
                erepo = EquipmentRepository(session)
                try:
                    displaced = await erepo.equip(player.id, instance_id)
                except ValueError as e:
                    await self._refresh(interaction, self._slot_filter, status=f"❌ {e}")
                    return
                inst = await erepo.get_instance(instance_id, player.id)
            slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?") if inst else ""
            if displaced:
                returned = ", ".join(d.display_name for d in displaced)
                msg = (
                    f"✅ Đã trang bị **{inst.display_name}** vào {slot_label}. "
                    f"↩️ {returned} trả về túi đồ."
                )
            else:
                msg = f"✅ Đã trang bị **{inst.display_name}** vào {slot_label}."
            await self._refresh(interaction, self._slot_filter, status=msg)
        return _cb

    def _make_unequip_cb(self):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            slot = interaction.data["values"][0]
            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(self._discord_id)
                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return
                erepo = EquipmentRepository(session)
                inst = await erepo.unequip(player.id, slot)
            slot_label = SLOT_LABELS.get(slot, slot)
            msg = (
                f"↩️ Đã tháo **{inst.display_name}** từ {slot_label} về túi đồ."
                if inst else f"⚪ {slot_label} đang trống."
            )
            await self._refresh(interaction, self._slot_filter, status=msg)
        return _cb



class GearView(discord.ui.View):
    """Shows equipped gear with per-slot unequip buttons."""

    def __init__(self, discord_id: int, player_name: str, equipped: list) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._player_name = player_name

        by_slot = {i.slot: i for i in equipped}
        row, col = 0, 0
        for slot in SLOT_ORDER:
            if slot not in by_slot:
                continue
            if col >= 5:
                row += 1
                col = 0
            btn = discord.ui.Button(
                label=f"↩️ {SLOT_LABELS[slot]}",
                style=discord.ButtonStyle.secondary,
                row=row,
            )
            btn.callback = self._make_cb(slot)
            self.add_item(btn)
            col += 1

    def _make_cb(self, slot: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if not player:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return
                erepo = EquipmentRepository(session)
                inst = await erepo.unequip(player.id, slot)
                equipped = await erepo.get_equipped(player.id)

            slot_label = SLOT_LABELS.get(slot, slot)
            msg = f"↩️ Đã tháo **{inst.display_name}** từ {slot_label} về túi đồ." if inst else f"{slot_label} đang trống."
            embed = _gear_embed(self._player_name, equipped)
            embed.description = msg
            await interaction.edit_original_response(embed=embed, view=GearView(self._discord_id, self._player_name, equipped))
        return _cb


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
        embed = _gear_embed(player.name, equipped)
        view = GearView(interaction.user.id, player.name, equipped) if equipped else None
        await interaction.followup.send(embed=embed, view=view)

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
            erepo = EquipmentRepository(session)
            equipped = await erepo.get_equipped(player.id)
            bag_items = await erepo.get_bag(player.id)

        slot_filter = None if slot == "all" else slot
        embed = _bag_embed(player.name, bag_items, equipped, slot_filter)
        view = BagView(interaction.user.id, player.name, bag_items, equipped, slot_filter)
        await interaction.followup.send(embed=embed, view=view)

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
            returned_names = ", ".join(d.display_name for d in displaced)
            msg = (
                f"✅ Đã trang bị **{inst.display_name}** vào {slot_label}.\n"
                f"↩️ {returned_names} trả về túi đồ."
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


def _equip_bag_embed(
    player_name: str,
    bag_items: list,
    equipped: list,
    slot_filter: str | None = None,
    result_msg: str = "",
) -> discord.Embed:
    """Embed showing bag equipment grouped by slot, with the currently-equipped
    piece in that slot rendered as a header line so the player can compare
    before swapping.

    ``equipped`` is the player's currently-equipped instances (location ==
    ``equipped``). When ``slot_filter`` is set, only that slot is rendered.
    """
    title = f"🎒 Túi Trang Bị — {player_name}"
    if slot_filter:
        title += f" ({SLOT_LABELS.get(slot_filter, slot_filter)})"
    embed = base_embed(title, color=0x4488FF)
    if result_msg:
        embed.description = result_msg

    by_slot_bag: dict[str, list] = {}
    for inst in bag_items:
        by_slot_bag.setdefault(inst.slot or "unknown", []).append(inst)
    by_slot_eq: dict[str, object] = {i.slot: i for i in equipped if i.slot}

    rendered_any = False
    for slot in SLOT_ORDER:
        if slot_filter and slot != slot_filter:
            continue
        insts = by_slot_bag.get(slot, [])
        eq_inst = by_slot_eq.get(slot)
        if not insts and not eq_inst:
            continue
        rendered_any = True

        if eq_inst:
            eq_stats = format_computed_stats(eq_inst.computed_stats)
            header = (
                f"🟢 **Đang trang bị:** `#{eq_inst.id}` "
                f"{_grade_label(eq_inst.grade)} **{eq_inst.display_name}**\n"
                f"　{eq_stats}"
            )
        else:
            header = "⚪ *Đang trang bị: — Trống —*"

        if insts:
            bag_lines = []
            for inst in insts:
                stats_str = format_computed_stats(inst.computed_stats)
                bag_lines.append(
                    f"`#{inst.id}` {_grade_label(inst.grade)} "
                    f"**{inst.display_name}**\n　{stats_str}"
                )
            bag_block = "\n".join(bag_lines)
        else:
            bag_block = "*— Không có vật phẩm trong túi —*"

        body = f"{header}\n\n📦 **Trong túi:**\n{bag_block}"
        embed.add_field(
            name=SLOT_LABELS.get(slot, slot),
            value=body[:1020],
            inline=False,
        )

    if not rendered_any:
        empty_note = (
            "*Không có vật phẩm cho vị trí này.*"
            if slot_filter
            else "*Túi trang bị trống. Dùng `/forge craft` để rèn trang bị.*"
        )
        embed.description = (embed.description or "") + ("\n" if embed.description else "") + empty_note
    else:
        embed.set_footer(text="Dùng menu bên dưới để Trang bị / Tháo / Lọc theo vị trí.")
    return embed


def _make_equip_options(bag_items: list) -> list[discord.SelectOption]:
    options = []
    for inst in bag_items[:25]:
        slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?")
        stats = inst.computed_stats or {}
        desc = " | ".join(
            f"{STAT_LABELS.get(k, k)} {int(v)}" if v >= 1 else f"{STAT_LABELS.get(k, k)} {v:.3f}"
            for k, v in list(stats.items())[:3]
        ) or "—"
        options.append(discord.SelectOption(
            label=f"{slot_label} {inst.display_name}"[:100],
            description=desc[:100],
            value=str(inst.id),
        ))
    return options


class EquipBagView(discord.ui.View):
    """Shown from /inventory → "Trang Bị" — lists bag equipment alongside the
    currently-equipped item per slot, and exposes Equip / Unequip dropdowns
    plus a slot filter.

    The view rebuilds itself after every action so the caller doesn't need to
    refetch state. Discord's hard 25-options/Select cap is respected — the
    slot filter is the player's escape valve when their bag overflows.
    """

    def __init__(
        self,
        discord_id: int,
        player_name: str,
        bag_items: list,
        equipped: list,
        back_fn,
        slot_filter: str | None = None,
        result_msg: str = "",
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id  = discord_id
        self._player_name = player_name
        self._bag_items   = bag_items
        self._equipped    = equipped
        self._back_fn     = back_fn
        self._slot_filter = slot_filter

        # ── Row 0: slot filter dropdown ──────────────────────────────────
        slot_options = [
            discord.SelectOption(
                label="Tất cả", value="__all__", emoji="📚",
                default=(slot_filter is None),
            ),
        ]
        for s in SLOT_ORDER:
            full = SLOT_LABELS[s]
            parts = full.split(" ", 1)
            emoji_part, name_part = (parts[0], parts[1]) if len(parts) == 2 else ("", full)
            slot_options.append(discord.SelectOption(
                label=name_part, value=s, emoji=emoji_part or None,
                default=(s == slot_filter),
            ))
        slot_select = discord.ui.Select(
            placeholder=(
                f"📂 Lọc: {SLOT_LABELS.get(slot_filter, slot_filter)}"
                if slot_filter else "📂 Lọc theo vị trí…"
            ),
            options=slot_options,
            row=0,
        )
        slot_select.callback = self._filter_cb
        self.add_item(slot_select)

        # ── Row 1: Equip dropdown — bag items in current filter ─────────
        if slot_filter:
            visible_bag = [i for i in bag_items if i.slot == slot_filter]
        else:
            visible_bag = list(bag_items)
        if visible_bag:
            equip_select = discord.ui.Select(
                placeholder=f"⚔️ Trang bị từ túi… ({len(visible_bag)} món)",
                options=_make_equip_options(visible_bag),
                min_values=1, max_values=1,
                row=1,
            )
            equip_select.callback = self._equip_cb
            self.add_item(equip_select)

        # ── Row 2: Unequip dropdown — currently equipped in current filter ─
        if slot_filter:
            visible_eq = [i for i in equipped if i.slot == slot_filter]
        else:
            visible_eq = list(equipped)
        if visible_eq:
            options = []
            for inst in visible_eq[:25]:
                slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?")
                options.append(discord.SelectOption(
                    label=inst.display_name[:100],
                    value=inst.slot,
                    description=f"{slot_label} · {_grade_label(inst.grade)}"[:100],
                ))
            unequip_select = discord.ui.Select(
                placeholder=f"↩️ Tháo trang bị… ({len(visible_eq)} món)",
                options=options,
                min_values=1, max_values=1,
                row=2,
            )
            unequip_select.callback = self._unequip_cb
            self.add_item(unequip_select)

        # ── Row 3: Back button ───────────────────────────────────────────
        back_btn = discord.ui.Button(
            label="◀ Trở về", style=discord.ButtonStyle.secondary, row=3,
        )
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _refresh(
        self,
        interaction: discord.Interaction,
        slot_filter: str | None,
        result_msg: str = "",
    ) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            erepo = EquipmentRepository(session)
            equipped = await erepo.get_equipped(player.id)
            bag = await erepo.get_bag(player.id)
        embed = _equip_bag_embed(self._player_name, bag, equipped, slot_filter, result_msg=result_msg)
        view = EquipBagView(
            self._discord_id, self._player_name, bag, equipped,
            self._back_fn, slot_filter=slot_filter,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _filter_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        value = interaction.data["values"][0]
        new_filter: str | None = None if value == "__all__" else value
        await self._refresh(interaction, new_filter)

    async def _equip_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        instance_id = int(interaction.data["values"][0])

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            erepo = EquipmentRepository(session)
            try:
                displaced = await erepo.equip(player.id, instance_id)
            except ValueError as e:
                await self._refresh(interaction, self._slot_filter, result_msg=f"❌ {e}")
                return
            inst = await erepo.get_instance(instance_id, player.id)

        slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "")
        msg = f"✅ Đã trang bị **{inst.display_name}** vào {slot_label}."
        if displaced:
            returned = ", ".join(d.display_name for d in displaced)
            msg += f"\n↩️ {returned} trả về túi đồ."
        await self._refresh(interaction, self._slot_filter, result_msg=msg)

    async def _unequip_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        slot = interaction.data["values"][0]

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            erepo = EquipmentRepository(session)
            inst = await erepo.unequip(player.id, slot)

        slot_label = SLOT_LABELS.get(slot, slot)
        msg = (
            f"↩️ Đã tháo **{inst.display_name}** từ {slot_label} về túi đồ."
            if inst else f"⚪ {slot_label} đang trống."
        )
        await self._refresh(interaction, self._slot_filter, result_msg=msg)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EquipmentCog(bot))
