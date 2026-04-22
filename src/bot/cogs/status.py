"""Status command — character overview with interactive navigation."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.realms import BODY_REALMS, QI_REALMS, FORMATION_REALMS, realm_label
from src.game.systems.cultivation import (
    can_breakthrough,
    compute_hp_max,
    compute_mp_max,
    compute_formation_bonuses,
    compute_constitution_bonuses,
    merge_bonuses,
)
from src.utils.embed_builder import character_embed, error_embed
from src.bot.cogs.cultivation import (
    _orm_to_model,
    _apply_ticks_to_player,
    _cultivate_embed,
    _breakthrough_overview_embed,
    CultivateView,
    BreakthroughView,
)
from src.game.systems.cultivation import compute_atk, compute_matk, compute_def_stat
from src.game.engine.equipment import compute_equipment_stats

log = logging.getLogger(__name__)


def _make_status_embed(player, avatar_url: str | None = None) -> discord.Embed:
    from src.game.constants.linh_can import LINH_CAN_DATA, compute_linh_can_bonuses, parse_linh_can
    char = _orm_to_model(player)
    gem_count = 0
    if player.active_formation and player.formations:
        for f in player.formations:
            if f.formation_key == player.active_formation:
                gem_count = len(f.gem_slots)
                break

    linh_can_list = parse_linh_can(player.linh_can or "")
    lc_bonuses = compute_linh_can_bonuses(linh_can_list)
    bonuses = merge_bonuses(
        compute_formation_bonuses(player.active_formation, gem_count),
        compute_constitution_bonuses(player.constitution_type),
        lc_bonuses,
    )

    from src.data.registry import registry as gr
    const_data = gr.get_constitution(player.constitution_type)
    form_data  = gr.get_formation(player.active_formation) if player.active_formation else None

    spd_base = 10 + bonuses.get("spd_bonus", 0)
    spd_final = round(spd_base * (1.0 + bonuses.get("spd_pct", 0.0)))

    # Equipment stats (item_instances is eagerly loaded by PlayerRepository)
    equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
    equip_stats = compute_equipment_stats(equipped)

    base_atk  = compute_atk(char, bonuses)
    base_matk = compute_matk(char, bonuses)
    base_def  = compute_def_stat(char, bonuses)

    stats = {
        "hp_current": player.hp_current,
        "hp_max":     compute_hp_max(char, bonuses),
        "mp_current": player.mp_current,
        "mp_max":     compute_mp_max(char, bonuses),
        "spd":        spd_final,
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
        # Combat stats
        "atk":              base_atk  + int(equip_stats.get("atk", 0)),
        "matk":             base_matk + int(equip_stats.get("matk", 0)),
        "def_stat":         base_def  + int(equip_stats.get("def_stat", 0)),
        "crit_rating":      char.stats.crit_rating     + bonuses.get("crit_rating", 0)     + int(equip_stats.get("crit_rating", 0)),
        "crit_dmg_rating":  char.stats.crit_dmg_rating + bonuses.get("crit_dmg_rating", 0) + int(equip_stats.get("crit_dmg_rating", 0)),
        "evasion_rating":   char.stats.evasion_rating  + bonuses.get("evasion_rating", 0)  + int(equip_stats.get("evasion_rating", 0)),
        "crit_res_rating":  char.stats.crit_res_rating + bonuses.get("crit_res_rating", 0) + int(equip_stats.get("crit_res_rating", 0)),
        "final_dmg_bonus":  char.stats.final_dmg_bonus + bonuses.get("final_dmg_bonus", 0.0) + equip_stats.get("final_dmg_bonus", 0.0),
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
            ("🌀 Tu Luyện",           discord.ButtonStyle.secondary, self._cultivate_cb,     0),
            ("⚡ Đột Phá",            discord.ButtonStyle.secondary, self._breakthrough_cb,  0),
            ("🗺️ Bí Cảnh",            discord.ButtonStyle.secondary, self._dungeon_cb,       0),
            ("🎯 Kỹ Năng",            discord.ButtonStyle.secondary, self._skills_cb,        1),
            ("📚 Tàng Kinh Các",      discord.ButtonStyle.secondary, self._tang_kinh_cac_cb, 1),
            ("🎒 Túi Đồ",             discord.ButtonStyle.secondary, self._inventory_cb,     1),
            ("⚒️ Luyện Công Phường",  discord.ButtonStyle.secondary, self._forge_cb,         2),
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
            char = _orm_to_model(player)
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

        from src.bot.cogs.combat import _build_skills_embed_view
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

        from src.bot.cogs.combat import _build_skilllist
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
