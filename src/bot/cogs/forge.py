"""Forge — interactive button flow for crafting equipment."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.grades import Grade
from src.game.systems.forge import (
    QUALITY_LABELS,
    check_forge_requirements,
    describe_recipe,
    forge_equipment,
    get_material_grade,
    get_recipe,
)
from src.utils.embed_builder import base_embed, error_embed

log = logging.getLogger(__name__)

SLOT_VI: dict[str, str] = {
    "weapon":   "Vũ Khí",
    "off_hand": "Phụ Thủ",
    "armor":    "Giáp",
    "helmet":   "Mũ Giáp",
    "glove":    "Găng Tay",
    "belt":     "Đai Lưng",
    "boot":     "Giày",
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
    embed.add_field(name="⚒️ Rèn Trang Bị",      value="Rèn trang bị từ nguyên liệu trong kho.", inline=False)
    embed.add_field(name="📋 Danh Sách Trang Bị", value="Xem các loại trang bị có thể rèn.", inline=False)
    embed.add_field(name="📜 Công Thức Rèn",      value="Xem yêu cầu nguyên liệu và tỷ lệ phẩm chất.", inline=False)
    return embed


def _guard(interaction: discord.Interaction, discord_id: int) -> bool:
    return interaction.user.id == discord_id


# ── Navigation helpers (edit in-place) ───────────────────────────────────────

async def _nav_hub(interaction: discord.Interaction, discord_id: int, hub_back_fn) -> None:
    await interaction.edit_original_response(
        embed=_forge_hub_embed(),
        view=ForgeHubView(discord_id, hub_back_fn),
    )


async def _nav_slot(interaction: discord.Interaction, discord_id: int, hub_back_fn) -> None:
    embed = discord.Embed(
        title="⚒️ Rèn Trang Bị — Chọn Vị Trí Trang Bị",
        description="Chọn vị trí trang bị muốn rèn.",
        color=0xB8860B,
    )
    await interaction.edit_original_response(embed=embed, view=_SlotView(discord_id, hub_back_fn))


async def _nav_base(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    hub_back_fn,
) -> None:
    slot_name = SLOT_VI.get(slot, slot)
    embed = discord.Embed(
        title=f"⚒️ Chọn Loại Trang Bị — {slot_name}",
        description="Chọn loại trang bị muốn rèn.",
        color=0xB8860B,
    )
    await interaction.edit_original_response(embed=embed, view=_BaseView(discord_id, slot, hub_back_fn))


async def _nav_grade(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    hub_back_fn,
) -> None:
    base_data = registry.get_base(base_key)
    embed = discord.Embed(
        title=f"⚒️ Chọn Cấp Rèn — {base_data['vi'] if base_data else base_key}",
        description="Chọn cấp trang bị muốn rèn (1 = thấp nhất, 9 = cao nhất).",
        color=0xB8860B,
    )
    await interaction.edit_original_response(embed=embed, view=_GradeView(discord_id, slot, base_key, hub_back_fn))


async def _nav_confirm(
    interaction: discord.Interaction,
    discord_id: int,
    slot: str,
    base_key: str,
    grade: int,
    hub_back_fn,
) -> None:
    async with get_session() as session:
        player_repo = PlayerRepository(session)
        inv_repo    = InventoryRepository(session)

        player = await player_repo.get_by_discord_id(discord_id)
        if not player:
            await interaction.edit_original_response(
                embed=error_embed("Không tìm thấy nhân vật."), view=None
            )
            return
        char = _player_to_model(player)

        all_items   = await inv_repo.get_all(player.id)
        mats_in_bag = {
            it.item_key: it.quantity
            for it in all_items
            if get_material_grade(it.item_key) is not None
        }

    view  = _ConfirmView(discord_id, slot, base_key, grade, hub_back_fn, char=char, mats_in_bag=mats_in_bag)
    embed = _build_confirm_embed(base_key, grade, char, mats_in_bag)
    await interaction.edit_original_response(embed=embed, view=view)


# ── Confirm embed builder ─────────────────────────────────────────────────────

def _build_confirm_embed(base_key: str, grade: int, char, mats_in_bag: dict[str, int]) -> discord.Embed:
    base_data = registry.get_base(base_key)
    recipe    = get_recipe(grade)
    slot_vi   = SLOT_VI.get(base_data["slot"], base_data["slot"]) if base_data else "?"

    ok, msg, _ = check_forge_requirements(char, grade, mats_in_bag)

    embed = discord.Embed(
        title="⚒️ Xác Nhận Rèn Luyện",
        color=discord.Color.gold() if ok else discord.Color.red(),
    )
    embed.add_field(
        name="Trang Bị",
        value=f"**{base_data['vi']}** ({slot_vi}) · Cấp {grade}",
        inline=False,
    )

    if recipe:
        merit_icon = "✅" if char.merit >= recipe["cost_cong_duc"] else "❌"
        embed.add_field(
            name="Chi Phí Công Đức",
            value=f"{merit_icon} {recipe['cost_cong_duc']:,} ✨ (đang có: {char.merit:,})",
            inline=False,
        )

        mat_lines: list[str] = []
        for i, opt in enumerate(recipe["options"], 1):
            opt_label = f"[PA {i}] " if len(recipe["options"]) > 1 else ""
            for req in opt["materials"]:
                owned = sum(qty for k, qty in mats_in_bag.items() if get_material_grade(k) == req["mat_grade"])
                icon  = "✅" if owned >= req["qty"] else "❌"
                mat_lines.append(f"{icon} {opt_label}{req['qty']}x Linh Thiết Phẩm {req['mat_grade']} (có: {owned})")
        embed.add_field(name="Nguyên Liệu", value="\n".join(mat_lines) or "—", inline=False)

        c = recipe["quality_chances"]
        embed.add_field(
            name="Tỷ Lệ Phẩm Chất",
            value=(
                f"Hoàng **{c['hoan']*100:.0f}%** · "
                f"Huyền **{c['huyen']*100:.0f}%** · "
                f"Địa **{c['dia']*100:.0f}%** · "
                f"Thiên **{c['thien']*100:.0f}%**"
            ),
            inline=False,
        )

    if not ok:
        embed.add_field(name="⚠️ Không Đủ Điều Kiện", value=msg, inline=False)

    return embed


# ── Views ─────────────────────────────────────────────────────────────────────

class ForgeHubView(discord.ui.View):
    """Hub shown from the status page Forge button or /forge command."""

    def __init__(self, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id  = discord_id
        self._back_fn     = back_fn

        configs = [
            ("⚒️ Rèn Trang Bị",      discord.ButtonStyle.primary,   self._craft_cb,  0),
            ("📋 Danh Sách Trang Bị", discord.ButtonStyle.secondary,  self._list_cb,   0),
            ("📜 Công Thức Rèn",      discord.ButtonStyle.secondary,  self._recipe_cb, 0),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return _guard(interaction, self._discord_id)

    async def _craft_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_slot(interaction, self._discord_id, self._back_fn)

    async def _list_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        embed = discord.Embed(title="⚒️ Danh Sách Trang Bị Có Thể Rèn", color=discord.Color.gold())
        for slot, bases in sorted(_bases_by_slot().items()):
            names = ", ".join(f"{b['vi']} (`{b['key']}`)" for b in bases)
            embed.add_field(name=SLOT_VI.get(slot, slot), value=names, inline=False)
        embed.set_footer(text="Dùng /forge recipe <cấp> để xem yêu cầu chi tiết.")

        back_view = discord.ui.View(timeout=120)
        back_btn  = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
        async def _back(inter: discord.Interaction) -> None:
            await inter.response.defer()
            await _nav_hub(inter, self._discord_id, self._back_fn)
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
        await interaction.edit_original_response(embed=embed, view=_RecipeSelectView(self._discord_id, self._back_fn))

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class _SlotView(discord.ui.View):
    """Step 1 — choose an equipment slot."""

    def __init__(self, discord_id: int, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._hub_back_fn = hub_back_fn

        slots = sorted(_bases_by_slot().keys())
        for i, slot in enumerate(slots):
            btn = discord.ui.Button(
                label=SLOT_VI.get(slot, slot),
                style=discord.ButtonStyle.primary,
                row=i // 4,
            )
            btn.callback = self._make_slot_cb(slot)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_slot_cb(self, slot: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            await _nav_base(interaction, self._discord_id, slot, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_hub(interaction, self._discord_id, self._hub_back_fn)


class _BaseView(discord.ui.View):
    """Step 2 — choose a base item within the selected slot."""

    def __init__(self, discord_id: int, slot: str, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._slot        = slot
        self._hub_back_fn = hub_back_fn

        bases = _bases_by_slot().get(slot, [])
        for i, base in enumerate(bases):
            btn = discord.ui.Button(
                label=base["vi"],
                style=discord.ButtonStyle.primary,
                row=i // 4,
            )
            btn.callback = self._make_base_cb(base["key"])
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_base_cb(self, base_key: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            await _nav_grade(interaction, self._discord_id, self._slot, base_key, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_slot(interaction, self._discord_id, self._hub_back_fn)


class _GradeView(discord.ui.View):
    """Step 3 — choose a craft grade (1-9)."""

    def __init__(self, discord_id: int, slot: str, base_key: str, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._slot        = slot
        self._base_key    = base_key
        self._hub_back_fn = hub_back_fn

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
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            await _nav_confirm(interaction, self._discord_id, self._slot, self._base_key, grade, self._hub_back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_base(interaction, self._discord_id, self._slot, self._hub_back_fn)


class _ConfirmView(discord.ui.View):
    """Step 4 — confirm requirements, then execute forge."""

    def __init__(
        self,
        discord_id: int,
        slot: str,
        base_key: str,
        grade: int,
        hub_back_fn,
        *,
        char,
        mats_in_bag: dict[str, int],
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id  = discord_id
        self._slot        = slot
        self._base_key    = base_key
        self._grade       = grade
        self._hub_back_fn = hub_back_fn

        ok, _, _ = check_forge_requirements(char, grade, mats_in_bag)

        confirm_btn = discord.ui.Button(
            label="✅ Xác Nhận Rèn",
            style=discord.ButtonStyle.success,
            disabled=not ok,
            row=0,
        )
        confirm_btn.callback = self._confirm_cb
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label="◀ Hủy", style=discord.ButtonStyle.secondary, row=0)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    async def _confirm_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        async with get_session() as session:
            player_repo = PlayerRepository(session)
            inv_repo    = InventoryRepository(session)
            equip_repo  = EquipmentRepository(session)

            player = await player_repo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Không tìm thấy nhân vật."), view=None)
                return

            char = _player_to_model(player)
            all_items   = await inv_repo.get_all(player.id)
            mats_in_bag = {
                it.item_key: it.quantity
                for it in all_items
                if get_material_grade(it.item_key) is not None
            }

            ok, msg, option = check_forge_requirements(char, self._grade, mats_in_bag)
            if not ok:
                await interaction.edit_original_response(embed=error_embed(msg), view=None)
                return

            # Consume materials
            consumed: list[tuple[str, int]] = []
            for req in option["materials"]:
                mat_grade  = req["mat_grade"]
                qty_needed = req["qty"]
                for mat_key, owned_qty in sorted(mats_in_bag.items()):
                    if get_material_grade(mat_key) != mat_grade or qty_needed <= 0:
                        continue
                    take    = min(owned_qty, qty_needed)
                    removed = await inv_repo.remove_item(player.id, mat_key, Grade(mat_grade), take)
                    if removed:
                        consumed.append((mat_key, take))
                        qty_needed -= take
                    if qty_needed <= 0:
                        break
                if qty_needed > 0:
                    await interaction.edit_original_response(
                        embed=error_embed("Lỗi nội bộ: thiếu nguyên liệu sau kiểm tra."), view=None
                    )
                    return

            result = forge_equipment(char, self._base_key, self._grade, consumed)
            if not result.success:
                await interaction.edit_original_response(embed=error_embed(result.message), view=None)
                return

            # Write mutated merit back to ORM and save
            player.merit = char.merit
            await player_repo.save(player)
            inst = await equip_repo.add_to_bag(player.id, result.item_data)

        quality = result.item_data["quality"]
        embed = discord.Embed(
            title="⚒️ Rèn Luyện Thành Công!",
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
            text=f"ID: {inst.id} | Phẩm: {QUALITY_LABELS[quality]} | Còn {char.merit:,} Công Đức ✨"
        )

        back_view = discord.ui.View(timeout=120)
        back_btn  = discord.ui.Button(label="◀ Về Luyện Công Phường", style=discord.ButtonStyle.secondary)
        async def _to_hub(inter: discord.Interaction) -> None:
            await inter.response.defer()
            await _nav_hub(inter, self._discord_id, self._hub_back_fn)
        back_btn.callback = _to_hub
        back_view.add_item(back_btn)

        await interaction.edit_original_response(embed=embed, view=back_view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_grade(interaction, self._discord_id, self._slot, self._base_key, self._hub_back_fn)


class _RecipeSelectView(discord.ui.View):
    """Grade selector for recipe browsing."""

    def __init__(self, discord_id: int, hub_back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._hub_back_fn = hub_back_fn

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
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            embed = discord.Embed(
                title=f"📜 Công Thức Rèn Cấp {grade}",
                description=describe_recipe(grade),
                color=discord.Color.blue(),
            )
            back_view = discord.ui.View(timeout=120)
            back_btn  = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary)
            async def _back(inter: discord.Interaction) -> None:
                await inter.response.defer()
                await inter.edit_original_response(
                    embed=discord.Embed(
                        title="📜 Công Thức Rèn — Chọn Cấp",
                        description="Nhấn nút để xem công thức rèn theo cấp trang bị.",
                        color=discord.Color.blue(),
                    ),
                    view=_RecipeSelectView(self._discord_id, self._hub_back_fn),
                )
            back_btn.callback = _back
            back_view.add_item(back_btn)
            await interaction.edit_original_response(embed=embed, view=back_view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_hub(interaction, self._discord_id, self._hub_back_fn)


# ── Cog ──────────────────────────────────────────────────────────────────────

class ForgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="forge", description="Mở Luyện Công Phường — rèn trang bị từ nguyên liệu")
    async def forge_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_forge_hub_embed(),
            view=ForgeHubView(interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ForgeCog(bot))
