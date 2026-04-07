"""Cultivation commands — status with interactive nav, axis buttons for cultivate & breakthrough."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.realms import BODY_REALMS, QI_REALMS, FORMATION_REALMS, realm_label
from src.game.systems.cultivation import (
    can_breakthrough,
    apply_breakthrough,
    get_breakthrough_requirements,
    compute_hp_max,
    compute_mp_max,
    compute_formation_bonuses,
    compute_constitution_bonuses,
    merge_bonuses,
)
from src.game.constants.grades import Grade
from src.game.engine.tick import compute_offline_ticks
from src.utils.embed_builder import base_embed, character_embed, error_embed, success_embed
from src.utils.assets import AXIS_LABELS, AXIS_ICONS

log = logging.getLogger(__name__)

_AXIS_CONFIGS = [
    ("body",      "💪 Luyện Thể"),
    ("qi",        "🔮 Luyện Khí"),
    ("formation", "🔯 Trận Đạo"),
]


# ── Model helpers ─────────────────────────────────────────────────────────────

def _orm_to_model(player):
    from src.game.models.character import Character as CharModel
    from src.game.constants.linh_can import parse_linh_can
    tracker = player.turn_tracker
    return CharModel(
        player_id=player.id,
        discord_id=player.discord_id,
        name=player.name,
        body_realm=player.body_realm,
        body_level=player.body_level,
        qi_realm=player.qi_realm,
        qi_level=player.qi_level,
        formation_realm=player.formation_realm,
        formation_level=player.formation_level,
        constitution_type=player.constitution_type,
        dao_ti_unlocked=player.dao_ti_unlocked,
        merit=player.merit,
        karma_accum=player.karma_accum,
        karma_usable=player.karma_usable,
        primordial_stones=player.primordial_stones,
        hp_current=player.hp_current,
        mp_current=player.mp_current,
        active_formation=player.active_formation,
        main_title=player.main_title,
        sub_title=player.sub_title,
        evil_title=player.evil_title,
        active_axis=player.active_axis,
        body_xp=player.body_xp,
        qi_xp=player.qi_xp,
        formation_xp=player.formation_xp,
        turns_today=tracker.turns_today if tracker else 0,
        bonus_turns_remaining=tracker.bonus_turns_remaining if tracker else 440,
        linh_can=parse_linh_can(player.linh_can or ""),
    )


def _pre_breakthrough_realm(player, axis: str) -> int:
    if axis == "body":
        return player.body_realm
    if axis == "qi":
        return player.qi_realm
    return player.formation_realm


async def _apply_ticks_to_player(player, repo, axis: str) -> dict:
    """Set active axis, compute offline ticks, sync fields, save. Returns tick result."""
    player.active_axis = axis
    tracker = player.turn_tracker
    result: dict = {}

    if tracker and tracker.last_tick_at:
        char = _orm_to_model(player)
        result = compute_offline_ticks(char, tracker.last_tick_at)
        player.merit          = char.merit
        player.karma_accum    = char.karma_accum
        player.karma_usable   = char.karma_usable
        if char.evil_title:
            player.evil_title = char.evil_title
        player.body_xp         = char.body_xp
        player.qi_xp           = char.qi_xp
        player.formation_xp    = char.formation_xp
        player.body_level      = char.body_level
        player.qi_level        = char.qi_level
        player.formation_level = char.formation_level
        if tracker:
            tracker.turns_today           = char.turns_today
            tracker.bonus_turns_remaining = char.bonus_turns_remaining

    if tracker:
        tracker.last_tick_at = datetime.now(timezone.utc)
    await repo.save(player)
    return result


# ── Status embed builder ──────────────────────────────────────────────────────

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
    }
    embed = character_embed(player.name, stats, avatar_url=avatar_url)

    # Linh Căn display
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


# ── Embed builders ────────────────────────────────────────────────────────────

def _cultivate_embed(axis: str, result: dict) -> discord.Embed:
    axis_label = AXIS_LABELS.get(axis, axis)
    axis_icon  = AXIS_ICONS.get(axis, "🌀")
    turns      = result.get("turns", 0)
    merit      = result.get("merit_gained", 0)
    karma      = result.get("karma_gained", 0)
    cult       = result.get("cult_result", {})
    levels_up  = cult.get("levels_gained", 0)

    lines = [
        f"Trục: {axis_icon} **{axis_label}**",
        f"Lượt xử lý: **{turns:,}** lượt",
        f"Công Đức nhận: **+{merit:,}**",
        f"Nghiệp Lực tích lũy: **+{karma:,}**",
    ]
    if levels_up:
        lines.append(f"✨ Cảnh giới tiến: **+{levels_up} cấp**")
    if cult.get("merit_spent"):
        lines.append(f"  *(Tiêu tốn {cult['merit_spent']:,} Công Đức để luyện Trận Đạo)*")
    if cult.get("blocked_by_merit"):
        lines.append("⚠️ Thiếu Công Đức — Trận Đạo dừng tiến cấp.")
    if cult.get("blocked_at_9") and not levels_up:
        lines.append("📌 Đã đạt Cấp 9 — nhấn ⚡ Đột Phá để đột phá.")

    return base_embed("🌀 Tu Luyện", "\n".join(lines), color=0x8B5CF6)


def _breakthrough_overview_embed(player, readiness: dict[str, bool]) -> discord.Embed:
    embed = base_embed("⚡ Đột Phá Cảnh Giới", color=0xF1C40F)
    for axis, (_, label) in zip(("body", "qi", "formation"), _AXIS_CONFIGS):
        rl = {
            "body":      realm_label(BODY_REALMS,      player.body_realm,      player.body_level),
            "qi":        realm_label(QI_REALMS,         player.qi_realm,        player.qi_level),
            "formation": realm_label(FORMATION_REALMS,  player.formation_realm, player.formation_level),
        }[axis]
        status = "✅ Sẵn sàng đột phá" if readiness[axis] else "🔒 Chưa đủ điều kiện"
        embed.add_field(name=label, value=f"{rl}\n{status}", inline=True)
    embed.set_footer(text="Chọn trục để tiến hành đột phá")
    return embed


# ── Views ─────────────────────────────────────────────────────────────────────

class StatusView(discord.ui.View):
    """Main status navigation bar — shown with the /status embed."""

    def __init__(self, discord_id: int) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id

        configs = [
            ("🌀 Tu Luyện",  discord.ButtonStyle.primary,   self._cultivate_cb),
            ("⚡ Đột Phá",   discord.ButtonStyle.success,   self._breakthrough_cb),
            ("⚔️ Chiến Đấu", discord.ButtonStyle.danger,    self._fight_cb),
            ("🗺️ Bí Cảnh",   discord.ButtonStyle.secondary, self._dungeon_cb),
        ]
        for label, style, cb in configs:
            btn = discord.ui.Button(label=label, style=style, row=0)
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

    async def _fight_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        # Lazy import avoids any module-level circularity
        from src.bot.cogs.combat import FightRankView
        embed = base_embed("⚔️ Chọn Hạng Quái", "Chọn hạng quái muốn giao chiến:", color=0xFF6B35)
        view = FightRankView(self._discord_id, back_fn=_show_status)
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
                result = await _apply_ticks_to_player(player, repo, axis)

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
                    inventory_map[inv_item.item_key] = (
                        inventory_map.get(inv_item.item_key, 0) + inv_item.quantity
                    )

                char = _orm_to_model(player)
                ok, reason = can_breakthrough(char, axis, inventory=inventory_map)
                if not ok:
                    await interaction.edit_original_response(
                        embed=error_embed(reason),
                        view=BreakthroughView(self._discord_id, self._readiness, back_fn=self._back_fn),
                    )
                    return

                apply_breakthrough(char, axis, inventory=inventory_map)

                from src.db.repositories.inventory_repo import InventoryRepository
                inv_repo = InventoryRepository(session)
                reqs = get_breakthrough_requirements(axis, _pre_breakthrough_realm(player, axis))
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

                gem_count = 0
                if player.active_formation and player.formations:
                    for f in player.formations:
                        if f.formation_key == player.active_formation:
                            gem_count = len(f.gem_slots)
                            break
                bonuses = merge_bonuses(
                    compute_formation_bonuses(player.active_formation, gem_count),
                    compute_constitution_bonuses(player.constitution_type),
                )
                player.hp_current = compute_hp_max(char, bonuses)
                player.mp_current = compute_mp_max(char, bonuses)
                await repo.save(player)

                new_readiness: dict[str, bool] = {}
                updated_char = _orm_to_model(player)
                for ax in ("body", "qi", "formation"):
                    ready, _ = can_breakthrough(updated_char, ax, inventory=inventory_map)
                    new_readiness[ax] = ready

            realm_labels = {
                "body":      realm_label(BODY_REALMS,      player.body_realm,      player.body_level),
                "qi":        realm_label(QI_REALMS,         player.qi_realm,        player.qi_level),
                "formation": realm_label(FORMATION_REALMS,  player.formation_realm, player.formation_level),
            }
            embed = success_embed(f"Đột phá thành công!\nCảnh giới mới: **{realm_labels[axis]}**")
            embed.title = "⚡ Đột Phá Cảnh Giới"
            if player.dao_ti_unlocked and axis == "body":
                embed.add_field(name="🔓 Mở Khóa", value="**Đạo Thể** — Hệ thống Thể Chất đã mở!", inline=False)

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
            await interaction.response.send_message(
                embed=error_embed("Tên nhân vật phải từ 2–24 ký tự."), ephemeral=True
            )
            return

        async with get_session() as session:
            repo = PlayerRepository(session)
            if await repo.exists(interaction.user.id):
                await interaction.response.send_message(
                    embed=error_embed("Ngươi đã có nhân vật rồi. Dùng `/status` để xem."),
                    ephemeral=True,
                )
                return
            player = await repo.create(discord_id=interaction.user.id, name=name)
            linh_can_raw = player.linh_can or ""

        from src.game.constants.linh_can import LINH_CAN_DATA, parse_linh_can
        linh_can_list = parse_linh_can(linh_can_raw)
        lc_lines = [
            f"{LINH_CAN_DATA[lc]['emoji']} **{LINH_CAN_DATA[lc]['vi']}** — {LINH_CAN_DATA[lc]['description']}"
            for lc in linh_can_list if lc in LINH_CAN_DATA
        ]
        lc_text = "\n".join(lc_lines) if lc_lines else "*(không có)*"

        embed = success_embed(
            f"**{name}** đã bước vào con đường tu tiên!\n\n"
            f"⭐ **Linh Căn thiên phú:**\n{lc_text}\n\n"
            f"Dùng `/status` để xem thông tin nhân vật."
        )
        embed.title = "✨ Chào Mừng Đạo Hữu"
        await interaction.response.send_message(embed=embed)

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
            result = await _apply_ticks_to_player(player, repo, active)

        embed = _cultivate_embed(active, result)
        view = CultivateView(interaction.user.id, active)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="breakthrough", description="Đột phá cảnh giới")
    async def breakthrough(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True
                )
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
        view = BreakthroughView(interaction.user.id, readiness)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CultivationCog(bot))
