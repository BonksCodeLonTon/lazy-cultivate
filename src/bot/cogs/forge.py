"""Forge commands — craft equipment from materials."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.grades import Grade
from src.game.systems.forge import (
    QUALITY_LABELS,
    check_forge_requirements,
    describe_recipe,
    forge_equipment,
    get_material_grade,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

# Slot display names
SLOT_VI: dict[str, str] = {
    "weapon":   "Vũ Khí",
    "off_hand": "Phụ Thủ",
    "armor":    "Giáp",
    "helmet":   "Mũ Giáp",
    "glove":    "Găng Tay",
    "belt":     "Đai Lưng",
    "ring":     "Nhẫn",
    "amulet":   "Bùa Hộ Mệnh",
}

QUALITY_COLORS: dict[str, discord.Color] = {
    "hoan":  discord.Color.from_str("#B8860B"),
    "huyen": discord.Color.from_str("#8A2BE2"),
    "dia":   discord.Color.from_str("#1A5276"),
    "thien": discord.Color.from_str("#C0392B"),
}


def _bases_by_slot() -> dict[str, list[dict]]:
    """Group all base types by slot."""
    grouped: dict[str, list[dict]] = {}
    for b in registry.bases.values():
        grouped.setdefault(b["slot"], []).append(b)
    return grouped


def _forge_hub_embed() -> discord.Embed:
    embed = base_embed(
        "⚒️ Luyện Công Phường",
        "Chào mừng đến Luyện Công Phường! Chọn chức năng bên dưới.",
        color=0xB8860B,
    )
    embed.add_field(
        name="📋 Danh Sách Trang Bị",
        value="Xem các loại trang bị có thể rèn theo vị trí.",
        inline=False,
    )
    embed.add_field(
        name="📜 Công Thức Rèn",
        value="Xem yêu cầu nguyên liệu và tỷ lệ phẩm chất theo cấp.",
        inline=False,
    )
    return embed


class ForgeHubView(discord.ui.View):
    """Hub view shown when navigating to the forge from the status page."""

    def __init__(self, discord_id: int, back_fn) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._back_fn = back_fn

        configs = [
            ("📋 Danh Sách Trang Bị", discord.ButtonStyle.secondary, self._list_cb,   0),
            ("📜 Công Thức Rèn",      discord.ButtonStyle.secondary, self._recipe_cb, 0),
            ("◀ Trở về",              discord.ButtonStyle.secondary, self._back_cb,   1),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _list_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        embed = discord.Embed(
            title="⚒️ Danh Sách Trang Bị Có Thể Rèn",
            color=discord.Color.gold(),
        )
        for slot, bases in sorted(_bases_by_slot().items()):
            names = ", ".join(f"`{b['key']}`  {b['vi']}" for b in bases)
            embed.add_field(name=SLOT_VI.get(slot, slot), value=names, inline=False)
        embed.set_footer(text="Dùng /forge recipe <cấp> để xem yêu cầu chi tiết.")

        back_view = discord.ui.View(timeout=120)
        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
        async def _back(inter: discord.Interaction) -> None:
            await inter.response.defer()
            hub_embed = _forge_hub_embed()
            hub_view = ForgeHubView(self._discord_id, self._back_fn)
            await inter.edit_original_response(embed=hub_embed, view=hub_view)
        back_btn.callback = _back
        back_view.add_item(back_btn)
        await interaction.edit_original_response(embed=embed, view=back_view)

    async def _recipe_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        embed = discord.Embed(
            title="📜 Công Thức Rèn — Chọn Cấp",
            description="Nhấn nút để xem công thức rèn theo cấp trang bị.",
            color=discord.Color.blue(),
        )
        view = _ForgeRecipeSelectView(self._discord_id, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class _ForgeRecipeSelectView(discord.ui.View):
    """Grade selector for recipe browsing — grades 1-9 shown as buttons."""

    def __init__(self, discord_id: int, back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._back_fn = back_fn

        for grade in range(1, 10):
            btn = discord.ui.Button(
                label=f"Cấp {grade}",
                style=discord.ButtonStyle.primary,
                row=(grade - 1) // 5,
            )
            btn.callback = self._make_grade_cb(grade)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_grade_cb(self, grade: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            text = describe_recipe(grade)
            embed = discord.Embed(
                title=f"📜 Công Thức Rèn Cấp {grade}",
                description=text,
                color=discord.Color.blue(),
            )
            back_view = discord.ui.View(timeout=120)
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
            async def _back(inter: discord.Interaction) -> None:
                await inter.response.defer()
                sel_embed = discord.Embed(
                    title="📜 Công Thức Rèn — Chọn Cấp",
                    description="Nhấn nút để xem công thức rèn theo cấp trang bị.",
                    color=discord.Color.blue(),
                )
                await inter.edit_original_response(embed=sel_embed, view=_ForgeRecipeSelectView(self._discord_id, self._back_fn))
            back_btn.callback = _back
            back_view.add_item(back_btn)
            await interaction.edit_original_response(embed=embed, view=back_view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        hub_embed = _forge_hub_embed()
        hub_view = ForgeHubView(self._discord_id, self._back_fn)
        await interaction.edit_original_response(embed=hub_embed, view=hub_view)


class ForgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    forge = app_commands.Group(name="forge", description="Rèn luyện trang bị từ nguyên liệu")

    # ── /forge list ──────────────────────────────────────────────────────────

    @forge.command(name="list", description="Xem danh sách loại trang bị có thể rèn")
    async def forge_list(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="⚒️ Danh Sách Trang Bị Có Thể Rèn",
            color=discord.Color.gold(),
        )
        for slot, bases in sorted(_bases_by_slot().items()):
            names = ", ".join(f"`{b['key']}`  {b['vi']}" for b in bases)
            embed.add_field(name=SLOT_VI.get(slot, slot), value=names, inline=False)
        embed.set_footer(text="Dùng /forge recipe <cấp> để xem yêu cầu nguyên liệu.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /forge recipe ─────────────────────────────────────────────────────────

    @forge.command(name="recipe", description="Xem công thức rèn trang bị theo cấp (1-9)")
    @app_commands.describe(grade="Cấp trang bị muốn rèn (1-9)")
    async def forge_recipe(
        self,
        interaction: discord.Interaction,
        grade: app_commands.Range[int, 1, 9],
    ) -> None:
        text = describe_recipe(grade)
        embed = discord.Embed(
            title=f"📜 Công Thức Rèn Cấp {grade}",
            description=text,
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /forge craft ──────────────────────────────────────────────────────────

    @forge.command(name="craft", description="Rèn trang bị từ nguyên liệu trong túi đồ")
    @app_commands.describe(
        base="Key loại trang bị (xem /forge list)",
        grade="Cấp trang bị muốn rèn (1-9)",
    )
    async def forge_craft(
        self,
        interaction: discord.Interaction,
        base: str,
        grade: app_commands.Range[int, 1, 9],
    ) -> None:
        if not registry.get_base(base):
            await interaction.response.send_message(
                embed=error_embed(f"Không tìm thấy loại trang bị `{base}`. "
                                  "Dùng `/forge list` để xem danh sách."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            player_repo = PlayerRepository(session)
            inv_repo = InventoryRepository(session)
            equip_repo = EquipmentRepository(session)

            char = await player_repo.get_character(interaction.user.id)
            if not char:
                await interaction.followup.send(
                    embed=error_embed("Bạn chưa đăng ký. Dùng `/start` để bắt đầu."),
                    ephemeral=True,
                )
                return

            # Build a grade-keyed map of what forge materials the player has
            all_items = await inv_repo.get_all(char.player_id)
            mats_in_bag: dict[str, int] = {
                it.item_key: it.quantity
                for it in all_items
                if get_material_grade(it.item_key) is not None
            }

            ok, msg, option = check_forge_requirements(char, grade, mats_in_bag)
            if not ok:
                await interaction.followup.send(
                    embed=error_embed(msg), ephemeral=True
                )
                return

            # Deduct materials — consume the cheapest satisfying materials first
            consumed: list[tuple[str, int]] = []
            for req in option["materials"]:
                mat_grade = req["mat_grade"]
                qty_needed = req["qty"]
                for mat_key, owned_qty in sorted(mats_in_bag.items()):
                    if get_material_grade(mat_key) != mat_grade or qty_needed <= 0:
                        continue
                    take = min(owned_qty, qty_needed)
                    removed = await inv_repo.remove_item(
                        char.player_id, mat_key, Grade(mat_grade), take
                    )
                    if removed:
                        consumed.append((mat_key, take))
                        qty_needed -= take
                    if qty_needed <= 0:
                        break
                if qty_needed > 0:
                    # Shouldn't reach here after validation, but guard anyway
                    await interaction.followup.send(
                        embed=error_embed("Lỗi nội bộ: thiếu nguyên liệu sau kiểm tra."),
                        ephemeral=True,
                    )
                    return

            result = forge_equipment(char, base, grade, consumed)
            if not result.success:
                await interaction.followup.send(
                    embed=error_embed(result.message), ephemeral=True
                )
                return

            # Persist updated Công Đức and new item
            await player_repo.save_character(char)
            inst = await equip_repo.add_to_bag(char.player_id, result.item_data)

            quality = result.item_data["quality"]
            embed = discord.Embed(
                title="⚒️ Rèn Luyện Thành Công",
                description=result.message,
                color=QUALITY_COLORS[quality],
            )
            embed.add_field(
                name="Chỉ Số Cơ Bản",
                value="\n".join(
                    f"• {k}: **{v:.3f}**" if isinstance(v, float) and v < 1 else f"• {k}: **{int(v)}**"
                    for k, v in result.item_data["implicit_stats"].items()
                ),
                inline=True,
            )
            if result.item_data["affixes"]:
                embed.add_field(
                    name="Thuộc Tính",
                    value="\n".join(
                        f"• {a['key']} → {a['value']:.3f}"
                        if isinstance(a["value"], float) and a["value"] < 1
                        else f"• {a['key']} → {int(a['value'])}"
                        for a in result.item_data["affixes"]
                    ),
                    inline=True,
                )
            embed.set_footer(
                text=f"ID: {inst.id} | Phẩm: {QUALITY_LABELS[quality]} | "
                     f"Còn {char.merit:,} Công Đức ✨"
            )
            await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ForgeCog(bot))
