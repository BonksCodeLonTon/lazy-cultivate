"""Phân Giải Trang Bị — interactive recycle / bulk-recycle UI.

Mirrors the existing ``EquipBagView`` shape: slot filter dropdown, a
multi-select listing bag equipment, an action button, and pagination
when the bag overflows Discord's 25-option Select cap. Selected items
are deleted from the bag in one transaction and one random forge
material per item is added back to the inventory via
``recycle.recycle_equipment``.
"""
from __future__ import annotations

import logging
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.grades import Grade
from src.game.engine.equipment import SLOT_LABELS, SLOT_ORDER, format_computed_stats
from src.game.systems.recycle import get_recycle_material_grade, recycle_equipment
from src.utils import emojis
from src.utils.embed_builder import base_embed, error_embed
from src.utils.pagination import PAGE_SIZE, add_page_controls, page_slice, total_pages

log = logging.getLogger(__name__)


def _grade_label(grade: int) -> str:
    return f"{emojis.for_grade(grade)} G{grade}"


# ── Embed ────────────────────────────────────────────────────────────────────


def _recycle_embed(
    player_name: str,
    bag_items: list,
    slot_filter: str | None = None,
    result_msg: str = "",
) -> discord.Embed:
    """Render the recycle picker — bag items grouped by slot with a preview
    of the forge_material grade each one would yield."""
    title = f"♻️ Phân Giải Trang Bị — {player_name}"
    if slot_filter:
        title += f" ({SLOT_LABELS.get(slot_filter, slot_filter)})"
    embed = base_embed(
        title,
        "Chọn 1 hoặc nhiều trang bị trong túi để phân giải. "
        "Mỗi món trả về **1 vật liệu rèn ngẫu nhiên** theo cấp:\n"
        "• Cấp 1-2 → Vật Liệu Phẩm 1\n"
        "• Cấp 3-4 → Vật Liệu Phẩm 2\n"
        "• Cấp 5-6 → Vật Liệu Phẩm 3\n"
        "• Cấp 7   → Vật Liệu Phẩm 4\n"
        "• Cấp 8   → Vật Liệu Phẩm 5\n"
        "• Cấp 9   → Vật Liệu Phẩm 6",
        color=0x16A085,
    )
    if result_msg:
        embed.description = result_msg + "\n\n" + (embed.description or "")

    by_slot: dict[str, list] = {}
    for inst in bag_items:
        by_slot.setdefault(inst.slot or "unknown", []).append(inst)

    rendered_any = False
    for slot in SLOT_ORDER:
        if slot_filter and slot != slot_filter:
            continue
        insts = by_slot.get(slot, [])
        if not insts:
            continue
        rendered_any = True
        lines = []
        for inst in insts[:8]:  # truncate per-slot to keep embed under field limit
            mat_grade = get_recycle_material_grade(int(inst.grade))
            stats_str = format_computed_stats(inst.computed_stats)
            lines.append(
                f"`#{inst.id}` {_grade_label(inst.grade)} **{inst.display_name}** "
                f"→ Phẩm {mat_grade}\n　{stats_str}"
            )
        if len(insts) > 8:
            lines.append(f"*… và {len(insts) - 8} món khác (chọn dropdown để xem)*")
        embed.add_field(
            name=SLOT_LABELS.get(slot, slot),
            value="\n".join(lines)[:1020],
            inline=False,
        )

    if not rendered_any:
        empty = (
            "*Không có trang bị cho vị trí này.*"
            if slot_filter
            else "*Túi đồ trống — không có gì để phân giải.*"
        )
        embed.description = (embed.description or "") + "\n\n" + empty
    return embed


# ── View ─────────────────────────────────────────────────────────────────────


def _make_recycle_options(
    bag_items: list, page: int = 0,
) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for inst in page_slice(bag_items, page, per_page=PAGE_SIZE):
        slot_label = SLOT_LABELS.get(inst.slot or "", inst.slot or "?")
        mat_grade = get_recycle_material_grade(int(inst.grade))
        options.append(discord.SelectOption(
            label=f"{inst.display_name}"[:100],
            description=f"{slot_label} · {_grade_label(inst.grade)} · → Phẩm {mat_grade}"[:100],
            value=str(inst.id),
        ))
    return options


class RecycleView(discord.ui.View):
    """Bag-equipment recycle UI.

    - Row 0: slot filter dropdown (re-renders the view scoped to one slot).
    - Row 1: multi-select picker (up to 25 picks per Discord cap).
    - Row 2: "Phân Giải" action button + back button.
    - Row 3: pagination (only shown when the visible list overflows).

    The view rebuilds itself after every action — the caller never has to
    refetch state. State per instance is just the current filter, page,
    and the latest "selected ids" snapshot from the picker (not persisted
    across page flips, mirroring how the equip bag handles it).
    """

    def __init__(
        self,
        discord_id: int,
        player_name: str,
        bag_items: list,
        slot_filter: str | None = None,
        page: int = 0,
        selected_ids: list[int] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id  = discord_id
        self._player_name = player_name
        self._bag_items   = bag_items
        self._slot_filter = slot_filter
        self._selected_ids = list(selected_ids or [])

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

        # ── Row 1: multi-select recycle picker ───────────────────────────
        if slot_filter:
            visible = [i for i in bag_items if i.slot == slot_filter]
        else:
            visible = list(bag_items)

        bag_pages = total_pages(len(visible), per_page=PAGE_SIZE)
        self._page = max(0, min(page, bag_pages - 1))

        if visible:
            placeholder = f"♻️ Chọn món để phân giải… ({len(visible)} món)"
            if bag_pages > 1:
                placeholder = f"♻️ Phân giải (Trang {self._page + 1}/{bag_pages})"
            options = _make_recycle_options(visible, page=self._page)
            picker = discord.ui.Select(
                placeholder=placeholder,
                options=options,
                min_values=1,
                max_values=min(len(options), PAGE_SIZE),
                row=1,
            )
            picker.callback = self._pick_cb
            self.add_item(picker)

        # ── Row 2: Action + Back buttons ─────────────────────────────────
        recycle_btn = discord.ui.Button(
            label=(
                f"♻️ Phân Giải {len(self._selected_ids)} món"
                if self._selected_ids else "♻️ Phân Giải"
            ),
            style=discord.ButtonStyle.danger,
            disabled=not self._selected_ids,
            row=2,
        )
        recycle_btn.callback = self._recycle_cb
        self.add_item(recycle_btn)

        clear_btn = discord.ui.Button(
            label="✖ Bỏ chọn",
            style=discord.ButtonStyle.secondary,
            disabled=not self._selected_ids,
            row=2,
        )
        clear_btn.callback = self._clear_cb
        self.add_item(clear_btn)

        # ── Row 3: pagination ────────────────────────────────────────────
        add_page_controls(
            self,
            page=self._page,
            total=len(visible),
            on_change=self._on_page_change,
            row=3,
        )

    # ── Callbacks ────────────────────────────────────────────────────────

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _refresh(
        self,
        interaction: discord.Interaction,
        slot_filter: str | None,
        result_msg: str = "",
        page: int = 0,
        selected_ids: list[int] | None = None,
    ) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return
            erepo = EquipmentRepository(session)
            bag = await erepo.get_bag(player.id)
        # Drop any selected_ids that no longer exist (e.g. just recycled).
        live_ids = {b.id for b in bag}
        kept = [i for i in (selected_ids or []) if i in live_ids]
        embed = _recycle_embed(
            self._player_name, bag, slot_filter, result_msg=result_msg,
        )
        view = RecycleView(
            self._discord_id, self._player_name, bag,
            slot_filter=slot_filter, page=page, selected_ids=kept,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _on_page_change(
        self, interaction: discord.Interaction, new_page: int,
    ) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        # Page flips clear the picker selection (Discord re-renders the
        # Select options anyway, so prior values become invalid).
        await self._refresh(
            interaction, self._slot_filter, page=new_page, selected_ids=[],
        )

    async def _filter_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        value = interaction.data["values"][0]
        new_filter: str | None = None if value == "__all__" else value
        await self._refresh(interaction, new_filter, selected_ids=[])

    async def _pick_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            new_ids = [int(v) for v in interaction.data["values"]]
        except (KeyError, ValueError):
            new_ids = []
        await self._refresh(
            interaction, self._slot_filter, page=self._page, selected_ids=new_ids,
        )

    async def _clear_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        await self._refresh(
            interaction, self._slot_filter, page=self._page, selected_ids=[],
        )

    async def _recycle_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()

        if not self._selected_ids:
            await self._refresh(
                interaction, self._slot_filter, page=self._page,
                result_msg="❌ Chưa chọn món nào để phân giải.",
                selected_ids=[],
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return
            erepo = EquipmentRepository(session)
            irepo = InventoryRepository(session)

            # Walk every selected id, re-validating ownership + bag location
            # against the live row before deleting. Keep recycled items in
            # ``done`` and surface anything that drifted (e.g. the user
            # equipped one between picker render and click) in ``skipped``.
            recycled: list[tuple[str, int, str]] = []  # (display_name, item_grade, mat_key)
            skipped: list[int] = []
            for inst_id in self._selected_ids:
                inst = await erepo.get_instance(inst_id, player.id)
                if inst is None or inst.location != "bag":
                    skipped.append(inst_id)
                    continue
                mat_key = recycle_equipment(int(inst.grade))
                if mat_key is None:
                    skipped.append(inst_id)
                    continue
                mat = registry.get_item(mat_key) or {}
                mat_grade = int(mat.get("grade", 1))
                await irepo.add_item(player.id, mat_key, Grade(mat_grade), 1)
                await erepo.discard(player.id, inst_id)
                recycled.append((inst.display_name, int(inst.grade), mat_key))

        # Build a one-line summary that bundles duplicate material drops so
        # bulk recycling 10 items doesn't print 10 lines of the same key.
        if recycled:
            counts: Counter[str] = Counter(r[2] for r in recycled)
            mat_lines = []
            for key, qty in counts.most_common():
                mat = registry.get_item(key) or {}
                mat_lines.append(f"• **{mat.get('vi', key)}** ×{qty}")
            msg = (
                f"♻️ Đã phân giải **{len(recycled)}** trang bị, nhận:\n"
                + "\n".join(mat_lines)
            )
        else:
            msg = "❌ Không thể phân giải trang bị nào (đã trang bị hoặc đã biến mất)."
        if skipped:
            msg += f"\n⚠️ Bỏ qua {len(skipped)} món không hợp lệ."

        await self._refresh(
            interaction, self._slot_filter, page=0, result_msg=msg, selected_ids=[],
        )


# ── Cog ──────────────────────────────────────────────────────────────────────


class RecycleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="recycle",
        description="Phân giải trang bị trong túi → vật liệu rèn ngẫu nhiên",
    )
    async def recycle_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật."),
                )
                return
            erepo = EquipmentRepository(session)
            bag = await erepo.get_bag(player.id)

        embed = _recycle_embed(player.name, bag)
        view = RecycleView(interaction.user.id, player.name, bag)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecycleCog(bot))
