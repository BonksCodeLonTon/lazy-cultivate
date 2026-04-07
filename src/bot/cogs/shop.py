"""Shop commands — interactive UI (Đạo Thương & Quỷ Thị)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.game.constants.grades import Grade
from src.game.systems.economy import (
    get_fixed_shop, get_rotating_shop, get_dark_market, purchase, ShopSlot,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _rotating_seed() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 10000 + now.month * 100 + now.day + (now.hour // 6)


def _dark_seed() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 10000 + now.month * 100 + now.day + (now.hour // 4)


# ── Display helpers ───────────────────────────────────────────────────────────

def _item_name(item_key: str) -> str:
    item = registry.get_item(item_key)
    return item["vi"] if item else item_key


def _grade_emoji(grade: int) -> str:
    return {1: "🟡", 2: "🟣", 3: "🟢", 4: "🔴"}.get(grade, "⚪")


def _currency_emoji(currency: str) -> str:
    return {"merit": "✨", "karma_usable": "☯️", "primordial_stones": "💎"}.get(currency, "💰")


def _get_slots(section: str) -> list[ShopSlot]:
    if section == "fixed":
        return get_fixed_shop()
    if section == "rotating":
        return get_rotating_shop(_rotating_seed())
    fixed_slot, rotating = get_dark_market(_dark_seed())
    return [fixed_slot] + rotating


def _shop_embed(section: str) -> discord.Embed:
    slots = _get_slots(section)
    cfg = {
        "fixed":    ("🏪 Đạo Thương — Gian Cố Định",          0xFFD700, None),
        "rotating": ("🔄 Đạo Thương — Gian Luân Chuyển",       0x9B59B6, "Đổi hàng mỗi 6 giờ"),
        "dark":     ("☯️ Quỷ Thị — Chợ Đen",                  0x2C2F33, "Đổi hàng mỗi 4 giờ • Dùng Nghiệp Lực Khả Dụng"),
    }
    title, color, footer = cfg[section]
    embed = base_embed(title, color=color)
    for i, slot in enumerate(slots):
        name = _item_name(slot.item_key)
        is_dark_fixed = (section == "dark" and i == 0)
        prefix = "🔒 " if is_dark_fixed else f"`{i + 1:02d}` "
        embed.add_field(
            name=f"{prefix}{_grade_emoji(slot.grade)} {name}",
            value=f"{_currency_emoji(slot.currency)} **{slot.price:,}**",
            inline=True,
        )
    if footer:
        embed.set_footer(text=footer)
    return embed


# ── UI components ─────────────────────────────────────────────────────────────

class ShopItemSelect(discord.ui.Select):
    def __init__(self, section: str, discord_id: int) -> None:
        self._section = section
        self._discord_id = discord_id
        self._slots = _get_slots(section)

        options: list[discord.SelectOption] = []
        for i, slot in enumerate(self._slots[:25]):
            name = _item_name(slot.item_key)
            is_dark_fixed = (section == "dark" and i == 0)
            label = f"{'🔒 ' if is_dark_fixed else f'{i + 1}. '}{name}"[:100]
            options.append(discord.SelectOption(
                label=label,
                description=f"{_currency_emoji(slot.currency)} {slot.price:,}",
                value=str(i),
                emoji=_grade_emoji(slot.grade),
            ))

        super().__init__(placeholder="Chọn vật phẩm muốn mua...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        idx = int(self.values[0])
        slot = self._slots[idx]
        embed = base_embed(
            f"🛒 {_grade_emoji(slot.grade)} {_item_name(slot.item_key)}",
            f"Giá: {_currency_emoji(slot.currency)} **{slot.price:,}** / vật phẩm\n"
            f"Chọn số lượng muốn mua:",
            color=0x57F287,
        )
        await interaction.response.edit_message(embed=embed, view=ShopBuyView(self._section, slot, self._discord_id))


class ShopView(discord.ui.View):
    def __init__(self, section: str, discord_id: int) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id

        for tab_id, tab_label in [
            ("fixed",    "📚 Cố Định"),
            ("rotating", "🔄 Luân Chuyển"),
            ("dark",     "☯️ Quỷ Thị"),
        ]:
            style = discord.ButtonStyle.primary if tab_id == section else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=tab_label, style=style, row=0)
            btn.callback = self._make_tab_cb(tab_id)
            self.add_item(btn)

        self.add_item(ShopItemSelect(section, discord_id))

    def _make_tab_cb(self, tab_id: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.edit_message(
                embed=_shop_embed(tab_id),
                view=ShopView(tab_id, self._discord_id),
            )
        return _cb


class ShopBuyView(discord.ui.View):
    def __init__(self, section: str, slot: ShopSlot, discord_id: int) -> None:
        super().__init__(timeout=120)
        self._section = section
        self._slot = slot
        self._discord_id = discord_id

        for qty in (1, 5, 10):
            btn = discord.ui.Button(label=f"× {qty}", style=discord.ButtonStyle.success, row=0)
            btn.callback = self._make_buy_cb(qty)
            self.add_item(btn)

        back = discord.ui.Button(label="◀ Quay Lại", style=discord.ButtonStyle.secondary, row=0)
        back.callback = self._back_cb
        self.add_item(back)

    def _make_buy_cb(self, quantity: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()

            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                result = purchase(player, self._slot, quantity)
                if not result.ok:
                    await interaction.edit_original_response(embed=error_embed(result.message), view=None)
                    return

                irepo = InventoryRepository(session)
                await irepo.add_item(player.id, self._slot.item_key, Grade(self._slot.grade), quantity)
                await prepo.save(player)

            embed = success_embed(
                f"Mua **{_item_name(self._slot.item_key)} × {quantity}** thành công!\n"
                f"Tiêu: {_currency_emoji(self._slot.currency)} **{self._slot.price * quantity:,}**"
            )
            embed.title = "✅ Mua Thành Công"
            await interaction.edit_original_response(embed=embed, view=_ShopBackView(self._section, self._discord_id))
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_shop_embed(self._section), view=ShopView(self._section, self._discord_id))


class _ShopBackView(discord.ui.View):
    def __init__(self, section: str, discord_id: int) -> None:
        super().__init__(timeout=120)
        self._section = section
        self._discord_id = discord_id
        btn = discord.ui.Button(label="🏪 Tiếp Tục Mua", style=discord.ButtonStyle.primary, row=0)
        btn.callback = self._cb
        self.add_item(btn)

    async def _cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_shop_embed(self._section), view=ShopView(self._section, self._discord_id))


# ── Cog ───────────────────────────────────────────────────────────────────────

class ShopCog(commands.Cog, name="Shop"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="shop", description="Mở Đạo Thương & Quỷ Thị")
    async def shop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            prepo = PlayerRepository(session)
            if not await prepo.exists(interaction.user.id):
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                    ephemeral=True,
                )
                return

        section = "fixed"
        await interaction.followup.send(
            embed=_shop_embed(section),
            view=ShopView(section, interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
