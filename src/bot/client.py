"""Discord bot client."""
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

log = logging.getLogger(__name__)

# Set GUILD_ID env var for instant command sync during development.
_DEV_GUILD_ID: int | None = int(os.getenv("GUILD_ID", "0")) or None

COGS = [
    "src.bot.cogs.cultivation",
    "src.bot.cogs.combat",
    "src.bot.cogs.equipment",
    "src.bot.cogs.dungeon",
    "src.bot.cogs.inventory",
    "src.bot.cogs.shop",
    "src.bot.cogs.trade",
    "src.bot.cogs.admin",
]


class CultivationBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self) -> None:
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as e:
                log.error("Failed to load cog %s: %s", cog, e)
        if _DEV_GUILD_ID:
            guild = discord.Object(id=_DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Dev guild sync complete (guild_id=%s)", _DEV_GUILD_ID)
        await self.tree.sync()
        log.info("Global command sync complete")

    async def on_ready(self) -> None:
        log.info("Bot ready: %s (ID: %s)", self.user, self.user.id if self.user else "?")
