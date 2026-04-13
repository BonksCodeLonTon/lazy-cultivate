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
    @app_commands.command(name="cheat_trib", description="[Admin] Đưa nhân vật vào trạng thái sẵn sàng đột phá để test")
    @app_commands.describe(axis="Chọn đường tu luyện muốn test")
    @app_commands.choices(axis=[
            app_commands.Choice(name="Luyện Khí (Qi)", value="qi"),
            app_commands.Choice(name="Luyện Thể (Body)", value="body"),
            app_commands.Choice(name="Trận Pháp (Formation)", value="formation"),
        ])
    @app_commands.default_permissions(administrator=True)
    async def cheat_trib(self, interaction: discord.Interaction, axis: str) -> None:
        """Lệnh hỗ trợ đưa nhân vật đạt đủ mọi điều kiện của hàm can_breakthrough."""
        await interaction.response.defer(ephemeral=True)
        
        async with get_session() as session:
            repo = PlayerRepository(session)
            inv_repo = InventoryRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            
            if not player:
                await interaction.followup.send(embed=error_embed("Bạn chưa tạo nhân vật."), ephemeral=True)
                return

            from src.game.constants.realms import LEVELS_PER_REALM
            from src.game.systems.cultivation import get_breakthrough_requirements
            
            level_attr = "formation_level" if axis == "formation" else f"{axis}_level"
            setattr(player, level_attr, LEVELS_PER_REALM)
            
            xp_attr = f"{axis}_xp"
            setattr(player, xp_attr, 9999) 

            current_realm_idx = getattr(player, "formation_realm" if axis == "formation" else f"{axis}_realm")
            reqs = get_breakthrough_requirements(axis, current_realm_idx)
            
            response_details = []

            if reqs.get("item_key") and reqs.get("quantity"):
                item_key = reqs["item_key"]
                qty = reqs["quantity"]
                
                from src.game.constants.grades import Grade
                
                await inv_repo.add_item(
                    player_id=player.id, 
                    item_key=item_key,
                    grade=Grade.HOANG,
                    quantity=qty
                )
                # -------------------------------------
                
                response_details.append(f"• Vật phẩm: Đã tặng {qty}x `{item_key}`")

            if reqs.get("merit_cost", 0) > 0:
                cost = reqs["merit_cost"]
                player.merit += cost + 1000
                response_details.append(f"• Công đức: Đã thêm {cost + 1000:,}")

            await repo.save(player)
            
            await session.commit()
            
            details_str = "\n".join(response_details)
            msg = (
                f"**Chế độ Test Đột Phá:**\n"
                f"• Đường tu luyện: `{axis.upper()}`\n"
                f"• Cấp độ: Đã ép lên **Cấp {LEVELS_PER_REALM}**\n"
                f"{details_str}\n"
            )
            await interaction.followup.send(embed=success_embed(msg), ephemeral=True)

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