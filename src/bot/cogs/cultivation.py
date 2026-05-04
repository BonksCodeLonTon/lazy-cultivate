"""Cultivation commands — register, cultivate, breakthrough."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.realms import realm_label
from src.game.systems.cultivation import (
    can_breakthrough,
    apply_breakthrough,
    get_breakthrough_requirements,
    study_formation_with_merit,
)
from src.game.systems.cultivation_service import (
    apply_offline_ticks,
    pre_breakthrough_realm,
)
from src.game.constants.grades import Grade
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.assets import AXIS_LABELS, AXIS_ICONS

log = logging.getLogger(__name__)

_AXIS_CONFIGS = [
    ("body",      "💪 Luyện Thể"),
    ("qi",        "🔮 Luyện Khí"),
    ("formation", "🔯 Trận Đạo"),
]

# ── Embed builders ────────────────────────────────────────────────────────────

def _cultivate_embed(axis: str, result: dict) -> discord.Embed:
    axis_label  = AXIS_LABELS.get(axis, axis)
    axis_icon   = AXIS_ICONS.get(axis, "🌀")
    turns       = result.get("turns", 0)
    merit       = result.get("merit_gained", 0)
    karma       = result.get("karma_gained", 0)
    cult        = result.get("cult_result", {})
    exp_gained  = cult.get("exp_gained", 0)
    levels_up   = cult.get("levels_gained", 0)
    ready_trib  = cult.get("is_ready_for_tribulation", False)
    cap_reached = result.get("cap_reached", False)

    lines = [
        f"Hướng: {axis_icon} **{axis_label}**",
        f"Lượt xử lý: **{turns:,}** lượt",
        f"Công Đức nhận: **+{merit:,}**",
        f"Nghiệp Lực tích lũy: **+{karma:,}**",
    ]
    if exp_gained:
        lines.append(f"📘 EXP tu luyện: **+{exp_gained:,}**")
    if levels_up:
        lines.append(f"✨ Cảnh giới tiến: **+{levels_up} cấp**")
    if axis == "formation" and turns > 0 and exp_gained == 0:
        lines.append("ℹ️ *Trận Đạo chỉ tiến bằng Công Đức — dùng `/study_formation`.*")
    if turns == 0:
        if cap_reached:
            lines.append("⏳ *Đã đạt giới hạn **1440 lượt/ngày** — quay lại ngày mai.*")
        else:
            lines.append("⏳ *Chưa đủ thời gian tích lũy — chờ ít nhất 1 phút giữa các lần.*")
    if ready_trib:
        lines.append("⚡ Linh khí đã đủ — có thể **Độ Kiếp**!")

    return base_embed("🌀 Tu Luyện", "\n".join(lines), color=0x8B5CF6)


def _breakthrough_overview_embed(player, readiness: dict[str, bool]) -> discord.Embed:
    embed = base_embed("⚡ Đột Phá Cảnh Giới", color=0xF1C40F)
    for axis, (_, label) in zip(("body", "qi", "formation"), _AXIS_CONFIGS):
        rl = {
            "body":      realm_label("body",      player.body_realm,      player.body_xp),
            "qi":        realm_label("qi",        player.qi_realm,        player.qi_xp),
            "formation": realm_label("formation", player.formation_realm, player.formation_xp),
        }[axis]
        status = "✅ Sẵn sàng đột phá" if readiness[axis] else "🔒 Chưa đủ điều kiện"
        embed.add_field(name=label, value=f"{rl}\n{status}", inline=True)
    embed.set_footer(text="Chọn hướng để tiến hành đột phá")
    return embed


# ── Views ─────────────────────────────────────────────────────────────────────

class CultivateView(discord.ui.View):
    def __init__(self, discord_id: int, active_axis: str, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._back_fn = back_fn

        for axis_id, axis_label in _AXIS_CONFIGS:
            style = discord.ButtonStyle.primary if axis_id == active_axis else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=axis_label, style=style, row=0)
            btn.callback = self._make_cb(axis_id)
            self.add_item(btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _make_cb(self, axis: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            await interaction.response.defer()

            async with get_session() as session:
                repo = PlayerRepository(session)
                player = await repo.get_by_discord_id(interaction.user.id)

                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                char = _player_to_model(player)

                from src.game.systems.tribulation import TribulationManager
                trib_manager = TribulationManager()

                if trib_manager.check_needs_tribulation(char, axis):
                    await interaction.edit_original_response(
                        embed=error_embed(
                            "⚡ Bạn đã đạt cực hạn cảnh giới!\n"
                            "Hãy **đột phá (Thiên Kiếp)** để tiếp tục tu luyện."
                        ),
                        view=CultivateView(self._discord_id, axis, back_fn=self._back_fn)
                    )
                    return

                result = await apply_offline_ticks(player, repo, axis)
                
                await session.commit()

            char = _player_to_model(player)
            from src.game.systems.tribulation import TribulationManager
            if TribulationManager().check_needs_tribulation(char, axis):
                await interaction.edit_original_response(
                    embed=error_embed("⚡ Đã đạt cực hạn, hãy Đột Phá để tiếp tục!"),
                    view=CultivateView(self._discord_id, axis, back_fn=self._back_fn)
                )
                return

            embed = _cultivate_embed(axis, result)
            new_view = CultivateView(self._discord_id, axis, back_fn=self._back_fn)
            await interaction.edit_original_response(embed=embed, view=new_view)

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class BreakthroughView(discord.ui.View):
    def __init__(self, discord_id: int, readiness: dict[str, bool], back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._readiness = readiness
        self._back_fn = back_fn

        for axis_id, axis_label in _AXIS_CONFIGS:
            style = discord.ButtonStyle.success if readiness[axis_id] else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=axis_label, style=style, row=0)
            btn.callback = self._make_cb(axis_id)
            self.add_item(btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _make_cb(self, axis: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            await interaction.response.defer()

            async with get_session() as session:
                repo = PlayerRepository(session)
                player = await repo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                inventory_map: dict[str, int] = {}
                for inv_item in player.inventory:
                    inventory_map[inv_item.item_key] = inventory_map.get(inv_item.item_key, 0) + inv_item.quantity

                char = _player_to_model(player)

                ok, reason = can_breakthrough(char, axis, inventory=inventory_map)
                if not ok:
                    await interaction.edit_original_response(
                        embed=error_embed(reason),
                        view=BreakthroughView(self._discord_id, self._readiness, back_fn=self._back_fn),
                    )
                    return

                from src.game.systems.tribulation import TribulationManager
                trib_manager = TribulationManager()

                if trib_manager.check_needs_tribulation(char, axis):
                    equipped_skill_keys = [s.skill_key for s in player.skills]

                    from src.game.systems.character_stats import (
                        active_formation_gem_keys, active_formation_gem_map,
                    )
                    trib_gem_keys = active_formation_gem_keys(player)
                    trib_gem_map = active_formation_gem_map(player)
                    gem_count = len(trib_gem_keys)

                    from src.game.engine.equipment import compute_equipment_stats
                    equipped_items = [i for i in (player.item_instances or []) if i.location == "equipped"]
                    equip_stats = compute_equipment_stats(equipped_items)

                    trib_result = await trib_manager.run_tribulation(
                        interaction,
                        char,
                        axis,
                        equipped_skill_keys,
                        gem_count,
                        equip_stats=equip_stats,
                        gem_keys=trib_gem_keys,
                        gem_keys_by_formation=trib_gem_map,
                    )

                    if not trib_result.success:
                        player.hp_current = 1

                        if trib_result.cultivation_lost:
                            current_lv = getattr(player, f"{axis}_level")
                            setattr(player, f"{axis}_level", max(1, current_lv - 1))

                        await repo.save(player)
                        return

                    realm_name = realm_label(
                        axis,
                        getattr(player, f"{axis}_realm"),
                        getattr(player, f"{axis}_xp"),
                    )
                    await interaction.followup.send(f"**{player.name}** đã vượt qua Thiên Kiếp của cảnh giới **{realm_name}**!")

                from src.db.repositories.inventory_repo import InventoryRepository
                inv_repo = InventoryRepository(session)

                old_realm_idx = pre_breakthrough_realm(player, axis)
                reqs = get_breakthrough_requirements(axis, old_realm_idx)

                apply_breakthrough(char, axis, inventory=inventory_map)

                if reqs["item_key"] and reqs["quantity"]:
                    await inv_repo.remove_item(player.id, reqs["item_key"], Grade.HOANG, reqs["quantity"])

                player.body_realm      = char.body_realm
                player.body_level      = char.body_level
                player.body_xp         = char.body_xp
                player.qi_realm        = char.qi_realm
                player.qi_level        = char.qi_level
                player.qi_xp           = char.qi_xp
                player.formation_realm = char.formation_realm
                player.formation_level = char.formation_level
                player.formation_xp    = char.formation_xp
                player.merit           = char.merit
                player.dao_ti_unlocked = char.dao_ti_unlocked

                char = _player_to_model(player)

                from src.game.systems.character_stats import (
                    active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
                )
                post_gem_keys = active_formation_gem_keys(player)
                post_gem_map = active_formation_gem_map(player)
                post_cs = compute_combat_stats(
                    char, gem_count=len(post_gem_keys),
                    gem_keys=post_gem_keys, gem_keys_by_formation=post_gem_map,
                    learned_skill_keys=[s.skill_key for s in (player.skills or [])],
                )

                player.hp_current = post_cs.hp_max
                player.mp_current = post_cs.mp_max

                await repo.save(player)

                new_readiness = {}
                for ax in ("body", "qi", "formation"):
                    ready, _ = can_breakthrough(char, ax, inventory=inventory_map)
                    new_readiness[ax] = ready

                realm_name = realm_label(
                    axis,
                    getattr(player, f"{axis}_realm"),
                    getattr(player, f"{axis}_xp"),
                )

                embed = success_embed(f"Chúc mừng bạn đã đột phá lên **{realm_name}**!")
                if player.dao_ti_unlocked and axis == "body":
                    embed.add_field(name="✨ Thông báo", value="**Đạo Thể** của bạn đã thức tỉnh!")

                await interaction.edit_original_response(
                    embed=embed,
                    view=BreakthroughView(self._discord_id, new_readiness, back_fn=self._back_fn),
                )

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


# ── Cog ───────────────────────────────────────────────────────────────────────

class CultivationCog(commands.Cog, name="Cultivation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="register", description="Tạo nhân vật tu tiên")
    @app_commands.describe(name="Đạo hiệu của bạn")
    async def register(self, interaction: discord.Interaction, name: str) -> None:
        if len(name) < 2 or len(name) > 24:
            return await interaction.response.send_message(embed=error_embed("Tên từ 2–24 ký tự."), ephemeral=True)

        async with get_session() as session:
            repo = PlayerRepository(session)
            if await repo.exists(interaction.user.id):
                return await interaction.response.send_message(embed=error_embed("Ngươi đã có nhân vật rồi."), ephemeral=True)
            
            player = await repo.create(discord_id=interaction.user.id, name=name)
            await session.commit()
            rolled_const = player.constitution_type
            rolled_linh_can = player.linh_can

        from src.data.registry import registry
        const_data = registry.get_constitution(rolled_const) or {}
        rarity_labels = {
            "common": "Phổ Thông",
            "uncommon": "Hiếm",
            "rare": "Quý",
            "epic": "Sử Thi",
            "legendary": "Truyền Thuyết",
        }
        rarity = const_data.get("rarity", "common")
        rarity_vi = rarity_labels.get(rarity, rarity)

        embed = success_embed(
            f"**{name}** đã bước vào con đường tu tiên!\n"
            f"🌿 Linh Căn: **{rolled_linh_can or '(không)'}**\n"
            f"🧬 Thể Chất sơ khởi: **{const_data.get('vi', rolled_const)}** "
            f"(*{rarity_vi}*)\n"
            f"Dùng `/status` để xem chi tiết."
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="cultivate", description="Tu luyện — áp dụng AFK ticks")
    async def cultivate(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                    ephemeral=True,
                )
                return

            active = player.active_axis or "qi"
            result = await apply_offline_ticks(player, repo, active)

        embed = _cultivate_embed(active, result)
        view = CultivateView(interaction.user.id, active)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="study_formation", description="Dùng Công Đức tăng Trận Đạo")
    @app_commands.describe(merits="Số lượng Công Đức")
    async def study_formation(self, interaction: discord.Interaction, merits: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            repo = PlayerRepository(session)
            player_orm = await repo.get_by_discord_id(interaction.user.id)
            if not player_orm: return await interaction.followup.send(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
            
            char = _player_to_model(player_orm)
            res = study_formation_with_merit(char, merits)
            if not res["success"]: return await interaction.followup.send(embed=error_embed(res["error"]), ephemeral=True)
            
            player_orm.merit = char.merit
            player_orm.formation_xp = char.formation_xp
            player_orm.formation_level = char.formation_level
            await session.commit()
            
            await interaction.followup.send(embed=success_embed(f"Tiêu {merits:,} Công Đức -> +{res['exp_gained']:,} EXP Trận Đạo."))

    @app_commands.command(name="breakthrough", description="Độ Kiếp")
    async def breakthrough(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            repo = PlayerRepository(session)
            player_orm = await repo.get_by_discord_id(interaction.user.id)
            if not player_orm: return
        
            
            char = _player_to_model(player_orm)
            axis = char.active_axis
            
            inventory_map = {inv.item_key: inv.quantity for inv in player_orm.inventory}

            readiness: dict[str, bool] = {}
            for ax in ("body", "qi", "formation"):
                ok, _ = can_breakthrough(char, ax, inventory=inventory_map)
                readiness[ax] = ok

            from src.game.systems.tribulation import TribulationManager
            manager = TribulationManager()
            skill_keys = [s.skill_key for s in player_orm.skills]
            result = await manager.run_tribulation(interaction, char, axis, skill_keys)
            
            if result.success:
                apply_breakthrough(char, axis, inventory=inventory_map)
                
                player_orm.update_from_model(char)
                
                for inv_item in player_orm.inventory:
                    if inv_item.item_key in inventory_map:
                        inv_item.quantity = inventory_map[inv_item.item_key]
                
                await session.commit()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CultivationCog(bot))
