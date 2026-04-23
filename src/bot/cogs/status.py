"""Status command — character overview with interactive navigation."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.realms import BODY_REALMS, QI_REALMS, FORMATION_REALMS, realm_label
from src.game.systems.cultivation import can_breakthrough
from src.game.systems.character_stats import compute_combat_stats
from src.game.engine.equipment import compute_equipment_stats
from src.utils.embed_builder import character_embed, error_embed
from src.bot.cogs.cultivation import (
    _apply_ticks_to_player,
    _cultivate_embed,
    _breakthrough_overview_embed,
    CultivateView,
    BreakthroughView,
)

log = logging.getLogger(__name__)


def _make_status_embed(player, avatar_url: str | None = None) -> discord.Embed:
    from src.game.constants.linh_can import LINH_CAN_DATA, parse_linh_can

    char = _player_to_model(player)
    from src.game.systems.character_stats import active_formation_gem_keys
    gem_keys = active_formation_gem_keys(player)
    gem_count = len(gem_keys)

    linh_can_list = parse_linh_can(player.linh_can or "")

    equipped_instances = [i for i in (player.item_instances or []) if i.location == "equipped"]
    equip_stats = compute_equipment_stats(equipped_instances)

    cs = compute_combat_stats(char, gem_count=gem_count, equip_stats=equip_stats, gem_keys=gem_keys)

    from src.data.registry import registry as gr
    const_data = gr.get_constitution(player.constitution_type)
    form_data  = gr.get_formation(player.active_formation) if player.active_formation else None

    # Equipped items: slot → display_name
    equipped_by_slot: dict[str, str] = {
        inst.slot: inst.display_name
        for inst in equipped_instances
    }

    stats = {
        "hp_current": player.hp_current,
        "hp_max":     cs.hp_max,
        "mp_current": player.mp_current,
        "mp_max":     cs.mp_max,
        "spd":        cs.spd,
        "body_realm":      player.body_realm,
        "body_level":      player.body_level,
        "body_xp":         player.body_xp,
        "qi_realm":        player.qi_realm,
        "qi_level":        player.qi_level,
        "qi_xp":           player.qi_xp,
        "formation_realm": player.formation_realm,
        "formation_level": player.formation_level,
        "formation_xp":    player.formation_xp,
        "active_axis":     player.active_axis,
        "body_realm_label":      realm_label(BODY_REALMS,      player.body_realm,      player.body_level),
        "qi_realm_label":        realm_label(QI_REALMS,         player.qi_realm,        player.qi_level),
        "formation_realm_label": realm_label(FORMATION_REALMS,  player.formation_realm, player.formation_level),
        "merit":             player.merit,
        "karma_accum":       player.karma_accum,
        "primordial_stones": player.primordial_stones,
        "constitution":      const_data["vi"] if const_data else player.constitution_type,
        "active_formation":  form_data["vi"] if form_data else None,
        "gem_count":         gem_count,
        "mp_reserved":       cs.mp_reserved,
        "mp_reserve_pct":    cs.mp_reserve_pct,
        # Combat stats (already merged with equipment)
        "atk":             cs.atk,
        "matk":            cs.matk,
        "def_stat":        cs.def_stat,
        "crit_rating":     cs.crit_rating,
        "crit_dmg_rating": cs.crit_dmg_rating,
        "evasion_rating":  cs.evasion_rating,
        "crit_res_rating": cs.crit_res_rating,
        "final_dmg_bonus": cs.final_dmg_bonus,
        # Elemental resistances (only pass non-zero entries)
        "resistances": {k: v for k, v in cs.resistances.items() if v > 0.0},
        # Equipped gear by slot
        "equipped_by_slot": equipped_by_slot,
    }

    embed = character_embed(player.name, stats, avatar_url=avatar_url)

    if linh_can_list:
        lc_parts = []
        for lc_key in linh_can_list:
            lc = LINH_CAN_DATA.get(lc_key)
            if lc:
                lc_parts.append(f"{lc['emoji']} **{lc['vi']}** — {lc['description']}")
        embed.add_field(name="⭐ Linh Căn", value="\n".join(lc_parts), inline=False)

    return embed


async def _show_status(interaction: discord.Interaction) -> None:
    """Rebuild status embed and edit the current message. Called after defer()."""
    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(interaction.user.id)

    if player is None:
        await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
        return

    embed = _make_status_embed(player, interaction.user.display_avatar.url)
    await interaction.edit_original_response(embed=embed, view=StatusView(interaction.user.id))


class StatusView(discord.ui.View):
    """Main status navigation bar — shown with the /status embed."""

    def __init__(self, discord_id: int) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id

        configs = [
            ("🌀 Tu Luyện",          discord.ButtonStyle.secondary, self._cultivate_cb,     0),
            ("⚡ Đột Phá",           discord.ButtonStyle.secondary, self._breakthrough_cb,  0),
            ("🗺️ Bí Cảnh",           discord.ButtonStyle.secondary, self._dungeon_cb,       0),
            ("🌌 Boss Thế Giới",     discord.ButtonStyle.secondary, self._world_boss_cb,    0),
            ("🎯 Kỹ Năng",           discord.ButtonStyle.secondary, self._skills_cb,        1),
            ("🎒 Túi Đồ",            discord.ButtonStyle.secondary, self._inventory_cb,     1),
            ("📚 Tàng Kinh Các",     discord.ButtonStyle.secondary, self._tang_kinh_cac_cb, 1),
            ("⚒️ Thiên Công Phường", discord.ButtonStyle.secondary, self._forge_cb,         2),
            ("🏪 Phường Thị",        discord.ButtonStyle.secondary, self._shop_cb,          2),
            ("🏮 Đấu Thương Các",    discord.ButtonStyle.secondary, self._market_cb,        2),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _cultivate_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            active = player.active_axis or "qi"
            result = await _apply_ticks_to_player(player, repo, active)

        embed = _cultivate_embed(active, result)
        view = CultivateView(self._discord_id, active, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _breakthrough_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
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
                inventory_map[inv_item.item_key] = (
                    inventory_map.get(inv_item.item_key, 0) + inv_item.quantity
                )
            char = _player_to_model(player)
            readiness: dict[str, bool] = {}
            for ax in ("body", "qi", "formation"):
                ok, _ = can_breakthrough(char, ax, inventory=inventory_map)
                readiness[ax] = ok

        embed = _breakthrough_overview_embed(player, readiness)
        view = BreakthroughView(self._discord_id, readiness, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _inventory_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        from src.bot.cogs.inventory import InventoryView, _build_hub_embed
        from src.db.repositories.equipment_repo import EquipmentRepository
        from src.db.repositories.inventory_repo import InventoryRepository

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            inv_items = await InventoryRepository(session).get_all(player.id)
            equip_bag = await EquipmentRepository(session).get_bag(player.id)

        embed = _build_hub_embed(inv_items, equip_bag)
        view = InventoryView(self._discord_id, inv_items, equip_bag, player.name, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _dungeon_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            qi_realm = player.qi_realm

        from src.bot.cogs.dungeon import DungeonListView, _dungeon_list_embed
        embed = _dungeon_list_embed(qi_realm)
        view = DungeonListView(self._discord_id, qi_realm, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _world_boss_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        from src.bot.cogs.world_boss import _refresh_hub
        await _refresh_hub(interaction, self._discord_id, back_fn=_show_status)

    async def _skills_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            equipped = [
                type("S", (), {"slot_index": s.slot_index, "skill_key": s.skill_key})()
                for s in sorted(player.skills or [], key=lambda x: x.slot_index)
            ]

        from src.bot.cogs.skills import _build_skills_embed_view
        embed, view = _build_skills_embed_view(equipped, self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _tang_kinh_cac_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        from src.game.constants.linh_can import parse_linh_can
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            linh_can = parse_linh_can(player.linh_can or "")

        from src.bot.cogs.skills import _build_skilllist
        embed, view = _build_skilllist(
            discord_id=self._discord_id,
            back_fn=_show_status,
            linh_can=linh_can,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _forge_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        from src.bot.cogs.forge import ForgeHubView, _forge_hub_embed
        embed = _forge_hub_embed()
        view = ForgeHubView(self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _shop_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        from src.bot.cogs.shop import ShopView, _shop_embed
        embed = _shop_embed("fixed")
        view = ShopView("fixed", self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _market_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()

        from src.bot.cogs.trade import MarketHubView, _hub_embed
        embed = _hub_embed()
        view = MarketHubView(self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)


class StatusCog(commands.Cog, name="Status"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="status", description="Xem thông tin nhân vật")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)

        if player is None:
            await interaction.followup.send(
                embed=error_embed("Chưa có nhân vật. Dùng `/register <tên>` để bắt đầu."),
                ephemeral=True,
            )
            return

        embed = _make_status_embed(player, interaction.user.display_avatar.url)
        view = StatusView(interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusCog(bot))
