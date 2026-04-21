"""Admin commands for development and management."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.utils.embed_builder import error_embed, success_embed


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="sync", description="[Admin] Đồng bộ các lệnh slash commands")
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_count = 0
        if interaction.guild:
            await self.bot.tree.sync(guild=interaction.guild)
            guild_count = 1
        await self.bot.tree.sync()
        await interaction.followup.send(
            embed=success_embed(
                f"Đã đồng bộ slash commands.\n"
                f"• Guild (tức thì): {'✅' if guild_count else '—'}\n"
                f"• Global (tối đa 1 giờ): ✅"
            ),
            ephemeral=True,
        )

    @app_commands.command(name="reset_turns", description="[Admin] Reset lượt tu luyện hàng ngày cho tất cả người chơi")
    @app_commands.default_permissions(administrator=True)
    async def reset_turns(self, interaction: discord.Interaction) -> None:
        """Thực hiện reset turns thủ công."""
        async with get_session() as session:
            from sqlalchemy import text
            await session.execute(text("UPDATE players SET energy = 100"))
            await session.commit()
            
        await interaction.response.send_message(
            embed=success_embed("Đã reset năng lượng (energy) cho toàn bộ server về 100."),
            ephemeral=True,
        )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))