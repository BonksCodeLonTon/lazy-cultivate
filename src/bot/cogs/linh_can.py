"""Linh Căn (Spiritual Root) commands — view, unlock, and upgrade.

The cap rule (per-element level ≤ Luyện Khí realm + 1) and the cost tables
both live in ``src/game/systems/linh_can.py`` — this cog is a thin Discord
adapter that loads the player, calls the system, and renders the result.

Module-level ``render_linh_can_hub`` is the entry point used by the status
page so the same UI is reachable both from ``/linh_can`` and the status
button bar.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.linh_can import (
    ALL_LINH_CAN, LINH_CAN_DATA, LINH_CAN_MAX_LEVEL,
    get_threshold_unlocks,
)
from src.game.systems.linh_can import (
    LinhCanError, get_levels, player_max_level,
    unlock_cost, unlock_linh_can, upgrade_cost, upgrade_linh_can,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed


_ELEMENT_CHOICES = [
    Choice(name=f"{LINH_CAN_DATA[k]['emoji']} {LINH_CAN_DATA[k]['vi']}", value=k)
    for k in ALL_LINH_CAN
]

# Type alias for the back-callback the status page hands us.
BackFn = Callable[[discord.Interaction], Awaitable[None]]


# ── Embed helpers (module-level so other cogs can reuse) ────────────────────

def _format_cost(cost) -> str:
    """Render LinhCanCost as a multi-line string for embed fields."""
    if cost.is_free():
        return "(miễn phí)"
    lines: list[str] = []
    if cost.merit > 0:
        lines.append(f"💰 Công Đức: **{cost.merit:,}**")
    for item_key, qty in cost.materials.items():
        item = registry.get_item(item_key)
        name = item["vi"] if item else item_key
        lines.append(f"• **{name}** ×{qty}  `{item_key}`")
    return "\n".join(lines)


def _format_levels_block(levels: dict[str, int], cap: int) -> str:
    """Compact summary line: every Linh Căn the player owns + their level."""
    if not levels:
        return "_(Chưa khai mở Linh Căn nào)_"
    parts: list[str] = []
    for elem in ALL_LINH_CAN:
        if elem in levels:
            data = LINH_CAN_DATA[elem]
            lv = levels[elem]
            cap_marker = " 🔒" if lv >= cap else ""
            parts.append(f"{data['emoji']} {data['vi']} **Lv{lv}**{cap_marker}")
    return " · ".join(parts)


def _format_thresholds_block(element: str, level: int) -> str:
    """List each unlocked threshold effect for the element at the given level."""
    unlocks = get_threshold_unlocks(element, level)
    if not unlocks:
        return "_(Chưa mở mốc hiệu ứng nào)_"
    return "\n".join(f"✦ {label}" for label in unlocks)


def build_overview_embed(
    player_name: str, levels: dict[str, int], cap: int,
) -> discord.Embed:
    from src.game.constants.linh_can import (
        LINH_CAN_BREADTH_MIN_LEVEL, LINH_CAN_BREADTH_MAX_MULT,
        linh_can_breadth_multiplier,
    )

    embed = base_embed(
        f"🌿 Linh Căn — {player_name}",
        f"Giới hạn cấp theo cảnh giới Luyện Khí: **Lv{cap}/{LINH_CAN_MAX_LEVEL}**",
        color=0x9B59B6,
    )
    embed.add_field(
        name="Đang sở hữu",
        value=_format_levels_block(levels, cap),
        inline=False,
    )

    missing = [k for k in ALL_LINH_CAN if k not in levels]
    if missing:
        missing_str = " · ".join(
            f"{LINH_CAN_DATA[k]['emoji']} {LINH_CAN_DATA[k]['vi']}"
            for k in missing
        )
        embed.add_field(name="Chưa khai mở", value=missing_str, inline=False)

    # Khí Tu breadth multiplier preview — visible to everyone so non-Khí-Tu
    # players see what they'd earn if they pivot the qi axis. The actual
    # bonus only fires when ``is_khi_tu`` (qi > body and qi > formation).
    qualifying = sum(1 for lvl in levels.values() if lvl >= LINH_CAN_BREADTH_MIN_LEVEL)
    mult = linh_can_breadth_multiplier(levels)
    embed.add_field(
        name="🌌 Khí Tu — Cộng Hưởng Linh Căn",
        value=(
            f"Linh Căn ≥ Lv{LINH_CAN_BREADTH_MIN_LEVEL}: **{qualifying}/9**\n"
            f"Hệ số khuếch đại passive: **×{mult:.2f}** "
            f"(trần ×{LINH_CAN_BREADTH_MAX_MULT:.2f}).\n"
            "_Chỉ kích hoạt khi Luyện Khí > Luyện Thể & Trận Đạo._"
        ),
        inline=False,
    )

    embed.set_footer(
        text="Nhấn nút bên dưới để xem chi tiết, khai mở hoặc nâng cấp Linh Căn."
    )
    return embed


def build_detail_embed(
    player_name: str,
    element: str,
    levels: dict[str, int],
    cap: int,
) -> discord.Embed:
    data = LINH_CAN_DATA[element]
    current = levels.get(element, 0)
    owned = current > 0
    title = f"{data['emoji']} {data['vi']} Linh Căn — {player_name}"
    desc_parts = [f"_{data['description']}_"]
    if owned:
        desc_parts.append(f"Cấp hiện tại: **Lv{current}** (giới hạn Lv{cap})")
    else:
        desc_parts.append("**Chưa khai mở** — bấm 🔓 Khai Mở bên dưới.")
    embed = base_embed(title, "\n".join(desc_parts), color=0x9B59B6)

    embed.add_field(
        name="Hiệu ứng cơ bản",
        value=data.get("effect_desc", "—"),
        inline=False,
    )

    embed.add_field(
        name="📜 Nguồn nguyên liệu",
        value=(
            f"🌌 **{data['vi']} Linh Mạch Bí Cảnh** — bí cảnh hệ "
            f"{data['emoji']} {data['vi']} là nguồn rớt chính.\n"
            "🏮 Có thể đăng bán hoặc tìm mua tại **Đấu Thương Các**.\n"
            "🤝 Trao đổi trực tiếp qua `/trade`.\n"
            "⚠️ Tỉ lệ rớt giảm dần theo cảnh giới Luyện Khí."
        ),
        inline=False,
    )

    if owned:
        embed.add_field(
            name=f"Mốc hiệu ứng đã mở (Lv{current})",
            value=_format_thresholds_block(element, current),
            inline=False,
        )

    # Cost preview — unlock or next upgrade
    if not owned:
        try:
            cost = unlock_cost(element, existing_count=len(levels))
            embed.add_field(name="Chi phí khai mở", value=_format_cost(cost), inline=False)
        except LinhCanError as exc:
            embed.add_field(name="Chi phí khai mở", value=str(exc), inline=False)
    elif current < cap:
        try:
            cost = upgrade_cost(element, current + 1)
            embed.add_field(
                name=f"Chi phí nâng Lv{current} → Lv{current + 1}",
                value=_format_cost(cost),
                inline=False,
            )
        except LinhCanError as exc:
            embed.add_field(name="Chi phí nâng cấp", value=str(exc), inline=False)
    else:
        embed.add_field(
            name="Đã đạt giới hạn",
            value=(
                f"Cấp Lv{current} là tối đa cho cảnh giới Luyện Khí hiện tại.\n"
                f"Đột phá Luyện Khí lên cấp cao hơn để mở Lv tiếp theo."
            ),
            inline=False,
        )

    return embed


# ── Hub view (entry point used by /linh_can and the status page) ────────────

async def render_linh_can_hub(
    interaction: discord.Interaction,
    discord_id: int,
    *,
    back_fn: BackFn | None = None,
) -> None:
    """Refresh the hub embed + view in the current message.

    Caller must have already deferred (we use ``edit_original_response``).
    Pass ``back_fn`` from the status cog to enable the ◀ Quay Lại button.
    """
    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(discord_id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật."), view=None,
            )
            return
        levels = get_levels(player)
        cap = player_max_level(player)
        player_name = player.name

    embed = build_overview_embed(player_name, levels, cap)
    view = LinhCanHubView(discord_id, levels, cap, back_fn=back_fn)
    await interaction.edit_original_response(embed=embed, view=view)


class LinhCanHubView(discord.ui.View):
    """Hub: 9 element buttons (one per Linh Căn) + back to status."""

    def __init__(
        self,
        discord_id: int,
        levels: dict[str, int],
        cap: int,
        back_fn: BackFn | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._levels = levels
        self._cap = cap
        self._back_fn = back_fn

        # 9 element buttons spread across rows 0-2 (3 per row), then row 3
        # is reserved for the back button.
        for i, elem in enumerate(ALL_LINH_CAN):
            data = LINH_CAN_DATA[elem]
            owned_lv = levels.get(elem, 0)
            label = (
                f"{data['emoji']} {data['vi']} Lv{owned_lv}"
                if owned_lv else f"{data['emoji']} {data['vi']}"
            )
            style = (
                discord.ButtonStyle.success if owned_lv else discord.ButtonStyle.secondary
            )
            btn = discord.ui.Button(label=label, style=style, row=i // 3)
            btn.callback = self._make_element_cb(elem)
            self.add_item(btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(
                label="◀ Quay Lại", style=discord.ButtonStyle.danger, row=3,
            )
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    def _make_element_cb(self, element: str):
        async def cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message(
                    "Đây không phải cửa sổ của bạn.", ephemeral=True,
                )
                return
            await interaction.response.defer()
            await _render_detail(
                interaction, self._discord_id, element, back_fn=self._back_fn,
            )
        return cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        if self._back_fn is not None:
            await self._back_fn(interaction)


# ── Detail view (one specific element, with action buttons) ────────────────

async def _render_detail(
    interaction: discord.Interaction,
    discord_id: int,
    element: str,
    *,
    back_fn: BackFn | None = None,
) -> None:
    """Refresh the detail embed + action view for one element."""
    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(discord_id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật."), view=None,
            )
            return
        levels = get_levels(player)
        cap = player_max_level(player)
        player_name = player.name

    embed = build_detail_embed(player_name, element, levels, cap)
    view = LinhCanDetailView(
        discord_id, element, levels, cap, back_fn=back_fn,
    )
    await interaction.edit_original_response(embed=embed, view=view)


class LinhCanDetailView(discord.ui.View):
    """Detail screen: 🔓 Khai Mở / ⬆️ Nâng Cấp + ◀ Quay Lại to hub."""

    def __init__(
        self,
        discord_id: int,
        element: str,
        levels: dict[str, int],
        cap: int,
        back_fn: BackFn | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._element = element
        self._levels = levels
        self._cap = cap
        self._back_fn = back_fn

        current = levels.get(element, 0)
        if current == 0:
            unlock_btn = discord.ui.Button(
                label="🔓 Khai Mở", style=discord.ButtonStyle.success, row=0,
            )
            unlock_btn.callback = self._unlock_cb
            self.add_item(unlock_btn)
        elif current < cap:
            upgrade_btn = discord.ui.Button(
                label=f"⬆️ Nâng Lv{current} → Lv{current + 1}",
                style=discord.ButtonStyle.primary, row=0,
            )
            upgrade_btn.callback = self._upgrade_cb
            self.add_item(upgrade_btn)
        else:
            capped_btn = discord.ui.Button(
                label=f"🔒 Đã đạt giới hạn Lv{current}",
                style=discord.ButtonStyle.secondary, row=0, disabled=True,
            )
            self.add_item(capped_btn)

        hub_btn = discord.ui.Button(
            label="◀ Về Linh Căn Bảng", style=discord.ButtonStyle.secondary, row=1,
        )
        hub_btn.callback = self._hub_cb
        self.add_item(hub_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _unlock_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            try:
                result = await unlock_linh_can(session, player, self._element)
            except LinhCanError as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return
            await session.commit()

        data = LINH_CAN_DATA[result["element"]]
        consumed = "\n".join(f"• `{k}` ×{v}" for k, v in result["consumed"].items())
        await interaction.followup.send(
            embed=success_embed(
                f"{data['emoji']} Đã khai mở **{data['vi']}** Linh Căn (Lv{result['level']}).\n\n"
                f"💰 Công Đức tiêu hao: **{result['spent_merit']:,}**\n{consumed}\n\n"
                f"_Hiệu ứng nền: {data['description']}_"
            ),
            ephemeral=True,
        )
        await _render_detail(
            interaction, self._discord_id, self._element, back_fn=self._back_fn,
        )

    async def _upgrade_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(self._discord_id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            try:
                result = await upgrade_linh_can(session, player, self._element)
            except LinhCanError as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return
            await session.commit()

        data = LINH_CAN_DATA[result["element"]]
        new_level = result["level"]
        consumed = "\n".join(f"• `{k}` ×{v}" for k, v in result["consumed"].items())
        unlocks = get_threshold_unlocks(result["element"], new_level)
        new_unlocks = [line for line in unlocks if line.startswith(f"Lv{new_level} ")]
        new_unlocks_block = (
            "\n".join(f"✨ {line}" for line in new_unlocks)
            if new_unlocks else "_(Không mở thêm mốc hiệu ứng mới)_"
        )
        await interaction.followup.send(
            embed=success_embed(
                f"{data['emoji']} **{data['vi']}** Linh Căn — đã đạt **Lv{new_level}**!\n\n"
                f"💰 Công Đức tiêu hao: **{result['spent_merit']:,}**\n{consumed}\n\n"
                f"{new_unlocks_block}"
            ),
            ephemeral=True,
        )
        await _render_detail(
            interaction, self._discord_id, self._element, back_fn=self._back_fn,
        )

    async def _hub_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.defer()
        await render_linh_can_hub(interaction, self._discord_id, back_fn=self._back_fn)


# ── Slash commands (kept for direct CLI use) ───────────────────────────────

class LinhCanCog(commands.Cog, name="LinhCan"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="linh_can",
        description="Mở bảng Linh Căn — xem, khai mở, nâng cấp",
    )
    async def linh_can(self, interaction: discord.Interaction) -> None:
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
            levels = get_levels(player)
            cap = player_max_level(player)
            player_name = player.name

        embed = build_overview_embed(player_name, levels, cap)
        view = LinhCanHubView(interaction.user.id, levels, cap, back_fn=None)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="linh_can_unlock",
        description="Khai mở một Linh Căn mới (cần nguyên liệu khai mở)",
    )
    @app_commands.describe(element="Linh Căn muốn khai mở")
    @app_commands.choices(element=_ELEMENT_CHOICES)
    async def linh_can_unlock(
        self,
        interaction: discord.Interaction,
        element: Choice[str],
    ) -> None:
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
            try:
                result = await unlock_linh_can(session, player, element.value)
            except LinhCanError as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return
            await session.commit()

        data = LINH_CAN_DATA[result["element"]]
        consumed = "\n".join(f"• `{k}` ×{v}" for k, v in result["consumed"].items())
        await interaction.followup.send(
            embed=success_embed(
                f"{data['emoji']} Đã khai mở **{data['vi']}** Linh Căn (Lv{result['level']}).\n\n"
                f"💰 Công Đức tiêu hao: **{result['spent_merit']:,}**\n{consumed}\n\n"
                f"_Hiệu ứng nền: {data['description']}_"
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="linh_can_upgrade",
        description="Nâng cấp một Linh Căn đã sở hữu lên 1 cấp",
    )
    @app_commands.describe(element="Linh Căn muốn nâng cấp")
    @app_commands.choices(element=_ELEMENT_CHOICES)
    async def linh_can_upgrade(
        self,
        interaction: discord.Interaction,
        element: Choice[str],
    ) -> None:
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
            try:
                result = await upgrade_linh_can(session, player, element.value)
            except LinhCanError as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return
            await session.commit()

        data = LINH_CAN_DATA[result["element"]]
        new_level = result["level"]
        consumed = "\n".join(f"• `{k}` ×{v}" for k, v in result["consumed"].items())
        unlocks = get_threshold_unlocks(result["element"], new_level)
        new_unlocks = [line for line in unlocks if line.startswith(f"Lv{new_level} ")]
        new_unlocks_block = (
            "\n".join(f"✨ {line}" for line in new_unlocks)
            if new_unlocks else "_(Không mở thêm mốc hiệu ứng mới)_"
        )
        await interaction.followup.send(
            embed=success_embed(
                f"{data['emoji']} **{data['vi']}** Linh Căn — đã đạt **Lv{new_level}**!\n\n"
                f"💰 Công Đức tiêu hao: **{result['spent_merit']:,}**\n{consumed}\n\n"
                f"{new_unlocks_block}"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinhCanCog(bot))
