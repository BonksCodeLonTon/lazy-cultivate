"""Admin commands."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.embed_builder import error_embed, success_embed


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="sync", description="[Admin] Sync slash commands")
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        # Guild sync is instant; global sync propagates within ~1 hour
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

    @app_commands.command(name="reset_turns", description="[Admin] Reset daily turns for all players")
    @app_commands.default_permissions(administrator=True)
    async def reset_turns(self, interaction: discord.Interaction) -> None:
        # TODO: implement daily turn reset via scheduler
        await interaction.response.send_message(
            embed=success_embed("Reset turns đang được xử lý."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
