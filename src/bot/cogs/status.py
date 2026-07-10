"""Status command — character overview with interactive navigation."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.systems.cultivation_service import apply_offline_ticks
from src.game.systems.dungeon import best_axis_realm, compute_realm_total
from src.game.systems.status import build_status_snapshot
from src.utils import emojis
from src.utils.discord_safe import safe_defer
from src.utils.embed_builder import character_embed, error_embed
from src.bot.cogs.cultivation import (
    _cultivate_embed,
    CultivateView,
)

log = logging.getLogger(__name__)


def _make_status_embed(
    player, avatar_url: str | None = None, sect_tag: str | None = None
) -> discord.Embed:
    from src.game.constants.linh_can import LINH_CAN_DATA
    from src.game.systems.the_chat import get_constitutions
    from src.data.registry import registry

    stats, linh_can_list = build_status_snapshot(player)
    # Tông Môn tag prefixes the title — membership identity at a glance.
    display_name = f"[{sect_tag}] {player.name}" if sect_tag else player.name
    embed = character_embed(display_name, stats, avatar_url=avatar_url)

    if linh_can_list:
        # One-liner per linh-can — name + emoji only. Full description lives
        # in the Linh Căn Bảng button. Trims long descriptions out of the hub.
        lc_parts = [
            f"{lc['emoji']} **{lc['vi']}**"
            for lc_key in linh_can_list
            if (lc := LINH_CAN_DATA.get(lc_key))
        ]
        if lc_parts:
            embed.add_field(name="⭐ Linh Căn", value=" · ".join(lc_parts), inline=False)

    equipped = get_constitutions(player.constitution_type)
    if equipped:
        # Compact one-line summary per equipped Thể Chất. Full bonus details
        # are surfaced via /stats and the Thể Chất Bảng button so the hub
        # doesn't need a 20-line bonus dump per constitution.
        const_lines: list[str] = []
        for key in equipped:
            const_data = registry.get_constitution(key)
            if not const_data:
                continue
            rarity = const_data.get("rarity", "common")
            const_lines.append(
                f"{emojis.for_rarity(rarity)} **{const_data['vi']}**"
            )
        if const_lines:
            embed.add_field(
                name="🧬 Thể Chất Đang Trang Bị",
                value="\n".join(const_lines)
                + "\n*Dùng `/stats` để xem chi tiết chỉ số chiến đấu.*",
                inline=False,
            )

    return embed


async def _show_status(interaction: discord.Interaction) -> None:
    """Rebuild status embed and edit the current message. Called after defer()."""
    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(interaction.user.id)
        sect_tag = None
        if player is not None:
            from src.db.repositories.sect_repo import SectRepository
            sect_tag = await SectRepository(session).get_tag_for_player(player.id)

    if player is None:
        await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
        return

    embed = _make_status_embed(player, interaction.user.display_avatar.url, sect_tag=sect_tag)
    await interaction.edit_original_response(embed=embed, view=StatusView(interaction.user.id))


class StatusView(discord.ui.View):
    """Main status navigation bar — shown with the /status embed."""

    def __init__(self, discord_id: int) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id

        configs = [
            ("🌀 Tu Luyện",          discord.ButtonStyle.secondary, self._cultivate_cb,     0),
            ("🗺️ Bí Cảnh",           discord.ButtonStyle.secondary, self._dungeon_cb,       0),
            ("🌌 Boss Thế Giới",     discord.ButtonStyle.secondary, self._world_boss_cb,    0),
            ("🏯 Thí Luyện Đài",     discord.ButtonStyle.secondary, self._arena_cb,         0),
            ("🔯 Trận Pháp",         discord.ButtonStyle.secondary, self._formation_cb,     1),
            ("🎯 Kỹ Năng",           discord.ButtonStyle.secondary, self._skills_cb,        1),
            ("🧬 Thể Chất Bảng",     discord.ButtonStyle.secondary, self._the_chat_cb,      1),
            ("🌿 Linh Căn Bảng",     discord.ButtonStyle.secondary, self._linh_can_cb,      1),
            ("🎒 Túi Đồ",            discord.ButtonStyle.secondary, self._inventory_cb,     2),
            ("⚒️ Thiên Công Phường", discord.ButtonStyle.secondary, self._forge_cb,         2),
            ("⚗️ Luyện Đan",         discord.ButtonStyle.secondary, self._alchemy_cb,       2),
            ("🏯 Tông Môn",          discord.ButtonStyle.secondary, self._tongmon_cb,       2),
            ("🏪 Phường Thị",        discord.ButtonStyle.secondary, self._shop_cb,          3),
            ("🏮 Đấu Thương Các",    discord.ButtonStyle.secondary, self._market_cb,        3),
            ("📖 Cẩm Nang",          discord.ButtonStyle.primary,   self._handbook_cb,      3),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _tongmon_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        # Lazy import — tong_mon also renders back into other screens; keeping
        # the import here avoids any load-order coupling between the two cogs.
        from src.bot.cogs.tong_mon import _render_sect_hub
        await _render_sect_hub(interaction, self._discord_id)

    async def _cultivate_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            active = player.active_axis or "qi"
            result = await apply_offline_ticks(player, repo, active)

        embed = _cultivate_embed(active, result)
        view = CultivateView(self._discord_id, active, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _inventory_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
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
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            # Dungeons gate by the player's STRONGEST axis — Thể/Khí/Trận
            # Tu alike qualify once any one axis reaches the dungeon's realm.
            player_best_realm = best_axis_realm(player)
            player_realm_total = compute_realm_total(player)

        from src.bot.cogs.dungeon import DungeonTypeSelectView, _dungeon_type_embed
        embed = _dungeon_type_embed()
        view = DungeonTypeSelectView(
            self._discord_id, player_best_realm, player_realm_total, back_fn=_show_status,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _alchemy_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.alchemy import AlchemyHubView, _alchemy_hub_embed
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            embed = await _alchemy_hub_embed(player)

        view = AlchemyHubView(self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _world_boss_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.world_boss import _refresh_hub
        await _refresh_hub(interaction, self._discord_id, back_fn=_show_status)

    async def _arena_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        # Reuse the panel embed + view directly. The standalone /thi_luyen_dai
        # entry uses ``_open_panel`` which goes through ``response.send_message``
        # / ``response.edit_message`` — neither works once we've deferred from
        # the status hub, so we render via ``edit_original_response`` here.
        # ``back_fn=_show_status`` so the panel's ◀ Trở về button rebuilds
        # the status hub in place.
        from src.bot.cogs.arena import ArenaPanelView, _panel_embed
        await interaction.edit_original_response(
            embed=_panel_embed(),
            view=ArenaPanelView(self._discord_id, back_fn=_show_status),
        )

    async def _formation_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.formation import _render_hub as _render_formation_hub
        await _render_formation_hub(interaction, self._discord_id, back_fn=_show_status)

    async def _skills_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
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

    async def _the_chat_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.constitution import render_the_chat_hub
        await render_the_chat_hub(interaction, self._discord_id, back_fn=_show_status)

    async def _linh_can_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.linh_can import render_linh_can_hub
        await render_linh_can_hub(interaction, self._discord_id, back_fn=_show_status)

    async def _forge_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.forge import ForgeHubView, _forge_hub_embed
        embed = _forge_hub_embed()
        view = ForgeHubView(self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _shop_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.shop import ShopView, _shop_embed
        embed = _shop_embed("fixed")
        view = ShopView("fixed", self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _market_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.trade import MarketHubView, _hub_embed
        embed = _hub_embed()
        view = MarketHubView(self._discord_id, back_fn=_show_status)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _handbook_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        from src.bot.cogs.handbook import render_handbook_hub
        await render_handbook_hub(interaction, self._discord_id, back_fn=_show_status)


# ── /stats — full combat-stats dump ───────────────────────────────────────────

# Rating-derived display formulas mirror the live combat math (% = R / (R+3000)).
_RATING_DENOM = 3000.0


def _r2pct(rating: int) -> float:
    """Standard rating → percentage formula, capped at 99% so display never
    misleads with a 100% read on a 'soft' rating stat."""
    rating = max(0, int(rating))
    return min(0.99, rating / (rating + _RATING_DENOM))


def _crit_dmg_mult(rating: int) -> float:
    """Display multiplier for crit-damage rating: 1.5× base + softcap bonus."""
    return 1.5 + _r2pct(rating)


def _make_full_stats_embed(player) -> discord.Embed:
    """Render every meaningful combat stat the player carries.

    Built from ``compute_combat_stats`` so equipment / formation / linh-can /
    constitution bonuses are all baked in — same pipeline combat uses.
    """
    from src.utils import emojis as _emojis
    from src.utils.assets import ELEMENT_NAMES_VI
    from src.game.engine.equipment import compute_equipment_stats
    from src.game.systems.character_stats import (
        active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
    )

    char = _player_to_model(player)
    gem_keys = active_formation_gem_keys(player)
    gem_map = active_formation_gem_map(player)
    equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
    cs = compute_combat_stats(
        char, gem_count=len(gem_keys),
        equip_stats=compute_equipment_stats(equipped),
        gem_keys=gem_keys, gem_keys_by_formation=gem_map,
    )

    embed = discord.Embed(
        title=f"📊 Chỉ Số Chiến Đấu — {player.name}",
        color=0x5865F2,
        description="Tổng hợp chỉ số sau khi cộng dồn trang bị, trận pháp, "
                    "thể chất và linh căn.",
    )

    # ── Vitals ──────────────────────────────────────────────────────────────
    embed.add_field(
        name="❤️ Sinh Lực / Linh Lực / Tốc Độ",
        value=(
            f"❤️ **{cs.hp_max:,}** HP  ·  💙 **{cs.mp_max:,}** MP  ·  ⚡ **{cs.spd}** SPD"
            + (f"\n🔒 Trấn Trận: −{cs.mp_reserved:,} MP ({cs.mp_reserve_pct * 100:.1f}%)"
               if cs.mp_reserved else "")
        ),
        inline=False,
    )

    # ── Offense ─────────────────────────────────────────────────────────────
    crit_pct = _r2pct(cs.crit_rating)
    crit_dmg_mult = _crit_dmg_mult(cs.crit_dmg_rating)
    offense_lines = [
        f"⚔️ **{cs.atk:,}** Công  ·  🔮 **{cs.matk:,}** Pháp Công",
        f"💥 Bạo Kích **{crit_pct * 100:.2f}%** "
        f"(rating {cs.crit_rating:,})  ·  ×**{crit_dmg_mult:.2f}** ST khi bạo "
        f"(rating {cs.crit_dmg_rating:,})",
        f"⚡ Tăng ST cuối **+{cs.final_dmg_bonus * 100:.1f}%**"
        + (f"  ·  🗡️ ST Chuẩn **+{cs.true_dmg_pct * 100:.1f}%**"
           if cs.true_dmg_pct else "")
        + (f"  ·  🩸 Hút Máu **{cs.life_steal_pct * 100:.1f}%**"
           if cs.life_steal_pct else ""),
    ]
    if cs.crit_dmg_rating_to_dmg_pct:
        flat_floor = int(cs.crit_dmg_rating * cs.crit_dmg_rating_to_dmg_pct)
        offense_lines.append(
            f"💥 Hủy Diệt Hóa Hình: **{cs.crit_dmg_rating_to_dmg_pct * 100:.0f}%** "
            f"× Bạo Kích DMG Rating → +**{flat_floor:,}** ST cuối/đòn"
        )
    if cs.multi_strike_pct:
        offense_lines.append(
            f"✨ Liên Kích **{cs.multi_strike_pct * 100:.1f}%** "
            f"(ST liên kích {cs.multi_strike_dmg_pct * 100:.0f}% gốc)"
        )
    if cs.turn_steal_pct:
        offense_lines.append(f"⏩ Cướp Lượt **{cs.turn_steal_pct * 100:.1f}%**")
    embed.add_field(name="⚔️ Tấn Công", value="\n".join(offense_lines), inline=False)

    # ── Defense ─────────────────────────────────────────────────────────────
    eva_pct = _r2pct(cs.evasion_rating)
    cres_pct = _r2pct(cs.crit_res_rating)
    defense_lines = [
        f"🛡️ **{cs.def_stat:,}** Phòng Thủ  ·  "
        f"🌀 Né Tránh **{eva_pct * 100:.2f}%** (rating {cs.evasion_rating:,})  ·  "
        f"🛡️ Kháng Bạo **{cres_pct * 100:.2f}%** (rating {cs.crit_res_rating:,})",
        f"🛡️ Giảm ST cuối **{cs.final_dmg_reduce * 100:.1f}%**"
        + (f"  ·  🪬 Miễn Debuff **{cs.debuff_immune_pct * 100:.0f}%**"
           if cs.debuff_immune_pct else ""),
    ]
    if cs.shield_max_base or cs.shield_max_flat or cs.shield_max_pct:
        defense_lines.append(
            f"🛡️ Khiên: gốc **{cs.shield_max_base:,}** + cộng thêm "
            f"**{cs.shield_max_flat:,}** + **{cs.shield_max_pct * 100:.0f}%** HP"
        )
    if cs.thorn_pct:
        defense_lines.append(f"🌵 Phản Đòn **{cs.thorn_pct * 100:.1f}%**")
    if cs.reflect_pct:
        defense_lines.append(f"🪞 Phản ST **{cs.reflect_pct * 100:.1f}%**")
    embed.add_field(name="🛡️ Phòng Ngự", value="\n".join(defense_lines), inline=False)

    # ── Regen ───────────────────────────────────────────────────────────────
    regen_lines = []
    if cs.hp_regen_pct or cs.hp_regen_flat:
        regen_lines.append(
            f"💚 Hồi HP "
            f"{cs.hp_regen_pct * 100:.2f}% / lượt + {cs.hp_regen_flat:,} flat"
        )
    if cs.mp_regen_pct or cs.mp_regen_flat:
        regen_lines.append(
            f"💧 Hồi MP "
            f"{cs.mp_regen_pct * 100:.2f}% / lượt + {cs.mp_regen_flat:,} flat"
        )
    if cs.shield_regen_pct or cs.shield_regen_flat:
        regen_lines.append(
            f"🛡️ Hồi Khiên "
            f"{cs.shield_regen_pct * 100:.2f}% / lượt + {cs.shield_regen_flat:,} flat"
        )
    if cs.heal_pct:
        regen_lines.append(f"✨ Trị Liệu +**{cs.heal_pct * 100:.0f}%**")
    if cs.cooldown_reduce:
        regen_lines.append(f"⏱️ Giảm Hồi Chiêu **{cs.cooldown_reduce * 100:.0f}%**")
    if regen_lines:
        embed.add_field(name="💚 Hồi Phục & Hồi Chiêu", value="\n".join(regen_lines), inline=False)

    # ── Elemental damage / penetration / resistance ─────────────────────────
    elem_dmg = cs.element_dmg_bonus or {}
    elem_pen = cs.element_pen or {}
    res_map = {k: v for k, v in cs.resistances.items() if v}
    elem_lines = []
    for elem, vi in ELEMENT_NAMES_VI.items():
        emoji = _emojis.for_element(elem)
        d = elem_dmg.get(elem, 0.0)
        p = elem_pen.get(elem, 0.0)
        r = res_map.get(elem, 0.0)
        if not (d or p or r):
            continue
        parts = []
        if d:
            parts.append(f"ST +**{d * 100:.0f}%**")
        if p:
            parts.append(f"Xuyên **{p * 100:.0f}%**")
        if r:
            parts.append(f"Kháng **{r * 100:.0f}%**")
        elem_lines.append(f"{emoji} {vi}: {' · '.join(parts)}")
    if elem_lines:
        embed.add_field(name="🌈 Nguyên Tố", value="\n".join(elem_lines), inline=False)

    # ── DoT amplifiers ──────────────────────────────────────────────────────
    dot_lines = []
    if cs.dot_dmg_bonus:
        dot_lines.append(f"💢 ST DoT chung +**{cs.dot_dmg_bonus * 100:.0f}%**")
    _DOT_KIND_LABELS = (
        ("burn",   "🔥 ST Thiêu Đốt"),
        ("bleed",  "🩸 ST Chảy Máu"),
        ("poison", "☠️ ST Độc"),
    )
    for _kind, _label in _DOT_KIND_LABELS:
        _v = cs.dot_dmg_bonus_by_kind.get(_kind, 0.0)
        if _v:
            dot_lines.append(f"{_label} +**{_v * 100:.0f}%**")
    if dot_lines:
        embed.add_field(name="💢 Sát Thương Theo Thời Gian", value="\n".join(dot_lines), inline=False)

    # ── Proc-rate summary (only emit when non-zero so unrelated builds stay tidy) ──
    proc_rows: list[tuple[str, float]] = [
        ("🔥 Thiêu Đốt khi đánh",      cs.burn_on_hit_pct),
        ("🩸 Chảy Máu khi đánh",        cs.bleed_on_hit_pct),
        ("⚡ Sốc Điện khi đánh",        cs.shock_on_hit_pct),
        ("🎯 Ấn Phong khi đánh",        cs.mark_on_hit_pct),
        ("💫 Choáng khi đánh",          cs.stun_on_hit_pct),
        ("🐢 Làm Chậm khi đánh",        cs.slow_on_hit_pct),
        ("🤐 Câm Lặng khi bạo",         cs.silence_on_crit_pct),
        ("🚫 Giảm Hồi khi đánh",        cs.heal_reduce_on_hit_pct),
        ("✨ Thanh Tẩy mỗi lượt",       cs.cleanse_on_turn_pct),
        ("💀 Hồn Phệ khi đánh",         cs.soul_drain_on_hit_pct),
        ("💠 Cướp Chỉ Số khi đánh",     cs.stat_steal_on_hit_pct),
    ]
    proc_lines = [f"{label} **{pct * 100:.1f}%**" for label, pct in proc_rows if pct]
    if cs.paralysis_on_crit:
        proc_lines.append("⚡ Tê Liệt khi Bạo Kích")
    if cs.freeze_on_skill_chance:
        proc_lines.append(
            f"🧊 Đóng Băng khi tung kỹ năng **{cs.freeze_on_skill_chance * 100:.1f}%**"
        )
    if proc_lines:
        embed.add_field(name="🎲 Cơ Hội Kích Hoạt", value="\n".join(proc_lines), inline=False)

    embed.set_footer(text="Mỗi giá trị đã bao gồm trang bị, trận pháp, thể chất và linh căn.")
    return embed


class StatusCog(commands.Cog, name="Status"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="status", description="Xem thông tin nhân vật")
    async def status(self, interaction: discord.Interaction) -> None:
        # `/status` does a player lookup before responding, so a slow DB query
        # can let the 3-second token expire — guard the defer to avoid a
        # NotFound crash bubbling up to the command tree.
        if not await safe_defer(interaction, ephemeral=True):
            return

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            sect_tag = None
            if player is not None:
                from src.db.repositories.sect_repo import SectRepository
                sect_tag = await SectRepository(session).get_tag_for_player(player.id)

        if player is None:
            await interaction.followup.send(
                embed=error_embed("Chưa có nhân vật. Dùng `/register <tên>` để bắt đầu."),
                ephemeral=True,
            )
            return

        embed = _make_status_embed(player, interaction.user.display_avatar.url, sect_tag=sect_tag)
        view = StatusView(interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="stats",
        description="Xem chi tiết toàn bộ chỉ số chiến đấu (atk/matk/spd/crit/élem/...)",
    )
    async def stats(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
        if player is None:
            await interaction.followup.send(
                embed=error_embed("Chưa có nhân vật. Dùng `/register <tên>` để bắt đầu."),
                ephemeral=True,
            )
            return
        embed = _make_full_stats_embed(player)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusCog(bot))
