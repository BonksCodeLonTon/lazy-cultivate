"""Luyện Đan — interactive button flow for alchemy (herbs → recipes → pills)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.player import Player
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.grades import Grade
from src.game.engine.quality import QUALITY_LABELS
from src.game.systems.alchemy import (
    AlchemyResult,
    PillEffect,
    check_requirements,
    consume_pill,
    craft_pill,
    get_recipe,
)
from src.game.systems import sect_missions
from src.game.systems.pill_buffs import is_buff_pill
from src.game.systems.sect import attach_sect_buffs
from src.utils import emojis
from src.utils.discord_safe import safe_defer
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.pagination import PAGE_SIZE, add_page_controls, page_slice, total_pages

log = logging.getLogger(__name__)


QUALITY_COLORS: dict[str, discord.Color] = {
    "hoan":  discord.Color.from_str("#B8860B"),
    "huyen": discord.Color.from_str("#8A2BE2"),
    "dia":   discord.Color.from_str("#1A5276"),
    "thien": discord.Color.from_str("#C0392B"),
}

_QUALITY_LABEL_VI: dict[int, str] = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}
_QUALITY_KEY_BY_TIER: dict[int, str] = {1: "hoan", 2: "huyen", 3: "dia", 4: "thien"}

# Hard cap on a single bulk consume click. Endgame realms target ~1000
# pills, but each iteration is a real DB hit — keep the per-click cost
# bounded and let the player click again for more.
_BULK_CONSUME_HARD_CAP: int = 999

# Hard cap on a single bulk craft click. Each iteration consumes a fresh
# set of herbs + Công Đức, so even a "luyện max" power-user click is
# bounded server-side. Higher than 50 starts producing slow Discord
# responses — the simulation loop is cheap (in-memory), but the inventory
# decrement and embed render still scale with N quality buckets.
_BULK_CRAFT_HARD_CAP: int = 50

# Herbs always stack in inventory at this single grade slot — their
# intrinsic grade (1-6) is a template attribute, not per-row state. Yêu thú
# materials share the ``herb`` type, so this covers both herb drops and
# yêu thú drops.
INGREDIENT_GRADE = Grade.HOANG


def _guard(interaction: discord.Interaction, discord_id: int) -> bool:
    return interaction.user.id == discord_id


async def _ingredient_map(player_id: int) -> dict[str, int]:
    """Return {item_key: total_qty} for every herb (alchemy ingredient) in inventory."""
    async with get_session() as session:
        inv_repo = InventoryRepository(session)
        rows = await inv_repo.get_all(player_id)
    result: dict[str, int] = {}
    for row in rows:
        item = registry.get_item(row.item_key)
        if item and item.get("type") == "herb":
            result[row.item_key] = result.get(row.item_key, 0) + row.quantity
    return result


async def _owned_furnace_keys(player_id: int) -> list[str]:
    """Return the list of furnace item_keys the player currently owns."""
    async with get_session() as session:
        inv_repo = InventoryRepository(session)
        rows = await inv_repo.get_all(player_id)
    keys: list[str] = []
    for row in rows:
        item = registry.get_item(row.item_key)
        if item and item.get("type") == "furnace":
            keys.append(row.item_key)
    return keys


def _owned_furnaces_from_player(player) -> list[str]:
    """Synchronous variant used when the player + inventory are already loaded."""
    out: list[str] = []
    for row in player.inventory:
        item = registry.get_item(row.item_key)
        if item and item.get("type") == "furnace":
            out.append(row.item_key)
    return out


def _resolve_default_furnace(
    preferred_key: str | None,
    owned_furnaces: list[str],
    required_tier: int,
) -> str | None:
    """Pick the dropdown's default key.

    Honours the player's stored preference when (a) they still own that
    furnace and (b) it qualifies for the recipe's required tier. Falls back
    to the auto-picked best so a stale preference can never lock the player
    out of a recipe they're currently eligible for.
    """
    from src.game.systems.alchemy import _pick_best_furnace

    if preferred_key and preferred_key in owned_furnaces:
        f = registry.get_furnace(preferred_key)
        if f and int(f.get("furnace_tier", 0)) >= required_tier:
            return preferred_key
    best = _pick_best_furnace(owned_furnaces, required_tier)
    return best["key"] if best else None


async def _pills_in_bag(player_id: int) -> list[tuple[str, int, int]]:
    """Return list of (item_key, grade_as_quality_tier, quantity) for owned pills."""
    async with get_session() as session:
        inv_repo = InventoryRepository(session)
        rows = await inv_repo.get_all(player_id)
    out: list[tuple[str, int, int]] = []
    for row in rows:
        item = registry.get_item(row.item_key)
        if item and item.get("type") == "pill":
            out.append((row.item_key, row.grade, row.quantity))
    out.sort(key=lambda t: (t[0], -t[1]))
    return out


async def _alchemy_hub_embed(player: Player) -> discord.Embed:
    """Build the Luyện Đan hub embed with summary counts."""
    herbs = sum(1 for r in player.inventory
                if registry.get_item(r.item_key) and registry.get_item(r.item_key).get("type") == "herb")
    pills = sum(1 for r in player.inventory
                if registry.get_item(r.item_key) and registry.get_item(r.item_key).get("type") == "pill")
    unlocked = len(registry.pill_recipes_for_realm(player.qi_realm))
    total_recipes = len(registry.pill_recipes)

    furnace_keys = _owned_furnaces_from_player(player)
    if furnace_keys:
        lines = []
        for k in furnace_keys:
            f = registry.get_furnace(k)
            if not f:
                continue
            tag = "✦" if f.get("is_unique") else "•"
            lines.append(f"{tag} {f['vi']} (Cấp {f.get('furnace_tier', 1)})")
        furnace_display = "\n".join(lines) if lines else "*(không có)*"
    else:
        furnace_display = "*(chưa có — mua Đan Lô tại Phường Thị)*"

    embed = base_embed(
        "⚗️ Luyện Đan",
        "Luyện chế linh đan từ thảo dược (bao gồm tinh hoa từ yêu thú).",
        color=0x2E7D32,
    )
    from src.game.systems.toxicity import (
        cult_speed_penalty, final_dmg_penalty, hp_regen_multiplier,
        tier_label, TOXICITY_FULL,
    )
    dd = int(player.dan_doc or 0)
    cult_pen = cult_speed_penalty(dd)
    dmg_pen = final_dmg_penalty(dd)
    regen_mult = hp_regen_multiplier(dd)
    if dd > 0:
        tox_value = (
            f"☠️ {dd:,} / {TOXICITY_FULL:,}  ·  **{tier_label(dd)}**\n"
            f"−{cult_pen:.0%} EXP tu luyện  ·  −{dmg_pen:.0%} sát thương "
            f"·  ×{regen_mult:.2f} hồi HP"
        )
    else:
        tox_value = f"☠️ 0 / {TOXICITY_FULL:,}  ·  **{tier_label(dd)}**"

    embed.add_field(name="Công Đức",     value=f"{emojis.for_currency('merit')} {player.merit:,}", inline=True)
    embed.add_field(name="Đan Độc",      value=tox_value, inline=False)
    embed.add_field(name="Đan Phương",   value=f"📜 {unlocked}/{total_recipes} mở khoá", inline=True)
    embed.add_field(name="Thảo Dược",    value=f"🌿 {herbs} loại", inline=True)
    embed.add_field(name="Đan Dược",     value=f"💊 {pills} loại", inline=True)
    embed.add_field(name="🔥 Đan Lô Sở Hữu", value=furnace_display, inline=False)
    return embed


# ── Main Hub View ───────────────────────────────────────────────────────────

class AlchemyHubView(discord.ui.View):
    def __init__(self, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._back_fn = back_fn

        recipes_btn = discord.ui.Button(label="📜 Đan Phương", style=discord.ButtonStyle.primary, row=0)
        recipes_btn.callback = self._open_recipes
        self.add_item(recipes_btn)

        pills_btn = discord.ui.Button(label="💊 Đan Dược", style=discord.ButtonStyle.success, row=0)
        pills_btn.callback = self._open_pills
        self.add_item(pills_btn)

        herbs_btn = discord.ui.Button(label="🌿 Thảo Dược", style=discord.ButtonStyle.secondary, row=0)
        herbs_btn.callback = self._open_herbs
        self.add_item(herbs_btn)

        discard_furnace_btn = discord.ui.Button(
            label="🗑️ Bỏ Đan Lô", style=discord.ButtonStyle.danger, row=1,
        )
        discard_furnace_btn.callback = self._open_discard_furnace
        self.add_item(discard_furnace_btn)

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    async def _open_recipes(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            qi_realm = player.qi_realm

        embed = _recipe_list_embed(qi_realm)
        view = RecipeGradeView(self._discord_id, qi_realm, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _open_pills(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            pills = await _pills_in_bag(player.id)

        embed = _pill_bag_embed(pills)
        view = PillBagView(self._discord_id, pills, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _open_herbs(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            herbs = await _ingredient_map(player.id)

        embed = _herb_bag_embed(herbs)
        view = _BackOnlyView(self._discord_id, lambda i: _nav_hub(i, self._discord_id, self._back_fn))
        await interaction.edit_original_response(embed=embed, view=view)

    async def _open_discard_furnace(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            owned = _owned_furnaces_from_player(player)

        if not owned:
            await interaction.response.send_message(
                embed=error_embed("Bạn chưa sở hữu Đan Lô nào để bỏ."),
                ephemeral=True,
            )
            return

        view = FurnaceDiscardSelectView(self._discord_id, owned)
        await interaction.response.send_message(
            embed=base_embed(
                "🗑️ Bỏ Đan Lô",
                "Chọn Đan Lô muốn bỏ. Hành động này **không thể hoàn tác**.\n"
                "Nếu bỏ Đan Lô đang được dùng làm mặc định, hệ thống sẽ tự "
                "chọn lại lò khác cho lần luyện đan tiếp theo.",
                color=0xC0392B,
            ),
            view=view,
            ephemeral=True,
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)


# ── Furnace discard flow ────────────────────────────────────────────────────

class FurnaceDiscardSelectView(discord.ui.View):
    """Ephemeral picker — choose one owned furnace, then confirm."""

    def __init__(self, discord_id: int, owned_keys: list[str]) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self.add_item(_FurnaceDiscardSelect(discord_id, owned_keys))


class _FurnaceDiscardSelect(discord.ui.Select):
    def __init__(self, discord_id: int, owned_keys: list[str]) -> None:
        self._discord_id = discord_id
        options: list[discord.SelectOption] = []
        for key in owned_keys[:25]:
            f = registry.get_furnace(key)
            if not f:
                continue
            tag = "✦" if f.get("is_unique") else "•"
            tier = int(f.get("furnace_tier", 1))
            label = f"{tag} {f['vi']}"[:100]
            options.append(discord.SelectOption(
                label=label, value=key,
                description=f"Cấp {tier}"[:100],
            ))
        if not options:
            options = [discord.SelectOption(label="(không có)", value="__none__")]
        super().__init__(placeholder="🔥 Chọn Đan Lô để bỏ…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        picked = self.values[0]
        if picked == "__none__":
            if not await safe_defer(interaction):
                return
            return
        f = registry.get_furnace(picked)
        if not f:
            await interaction.response.edit_message(
                embed=error_embed("Đan Lô không hợp lệ."), view=None,
            )
            return
        view = FurnaceDiscardConfirmView(self._discord_id, picked, f["vi"])
        await interaction.response.edit_message(
            embed=base_embed(
                "🗑️ Xác Nhận Bỏ Đan Lô",
                f"Bạn có chắc muốn **bỏ** **{f['vi']}** (Cấp "
                f"{int(f.get('furnace_tier', 1))})?\n\n"
                "_(Hành động này không thể hoàn tác.)_",
                color=0xC0392B,
            ),
            view=view,
        )


class FurnaceDiscardConfirmView(discord.ui.View):
    """Confirm/cancel prompt for furnace removal. Removes one inventory row
    and clears ``preferred_furnace_key`` if it pointed to the dropped lò.
    """

    def __init__(self, discord_id: int, furnace_key: str, furnace_vi: str) -> None:
        super().__init__(timeout=60)
        self._discord_id = discord_id
        self._furnace_key = furnace_key
        self._furnace_vi = furnace_vi

    @discord.ui.button(label="🗑️ Bỏ", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.edit_message(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return

            irepo = InventoryRepository(session)
            f = registry.get_item(self._furnace_key) or {}
            grade = Grade(f.get("grade", 1))
            ok = await irepo.try_remove_item(
                player.id, self._furnace_key, grade, 1,
            )

            if ok and player.preferred_furnace_key == self._furnace_key:
                player.preferred_furnace_key = None
                await prepo.save(player)

        if not ok:
            await interaction.response.edit_message(
                embed=error_embed(
                    f"Không tìm thấy **{self._furnace_vi}** trong túi đồ."
                ),
                view=None,
            )
            return

        await interaction.response.edit_message(
            embed=success_embed(f"🗑️ Đã bỏ **{self._furnace_vi}**."),
            view=None,
        )

    @discord.ui.button(label="Huỷ", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=base_embed("Đã huỷ", "Đan Lô của bạn vẫn còn nguyên."),
            view=None,
        )


async def _nav_hub(interaction: discord.Interaction, discord_id: int, back_fn) -> None:
    """Re-render the alchemy hub in place."""
    async with get_session() as session:
        player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
        if not player:
            await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        embed = await _alchemy_hub_embed(player)
    view = AlchemyHubView(discord_id, back_fn=back_fn)
    await interaction.edit_original_response(embed=embed, view=view)


# ── Recipes ─────────────────────────────────────────────────────────────────

def _recipe_list_embed(qi_realm: int) -> discord.Embed:
    embed = base_embed(
        "📜 Đan Phương",
        "Chọn cấp đan phương để xem chi tiết. Đan phương mở khoá theo cảnh giới Luyện Khí.",
        color=0x2E7D32,
    )
    counts: dict[int, tuple[int, int]] = {}
    for r in registry.pill_recipes.values():
        grade = int(r.get("grade", 1))
        total_u, unlocked_u = counts.get(grade, (0, 0))
        total_u += 1
        if r.get("min_qi_realm", 0) <= qi_realm:
            unlocked_u += 1
        counts[grade] = (total_u, unlocked_u)
    lines = []
    for g in sorted(counts):
        total_u, unlocked_u = counts[g]
        icon = "✅" if unlocked_u == total_u else ("🔒" if unlocked_u == 0 else "🟡")
        lines.append(f"{icon} **Cấp {g}** — {unlocked_u}/{total_u} mở khoá")
    embed.description = (embed.description or "") + "\n\n" + "\n".join(lines)
    return embed


class RecipeGradeView(discord.ui.View):
    """9 buttons for selecting recipe grade, then drills into RecipeListView."""

    def __init__(self, discord_id: int, qi_realm: int, back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._qi_realm = qi_realm
        self._back_fn = back_fn

        present_grades = sorted({int(r.get("grade", 1)) for r in registry.pill_recipes.values()})
        for grade in present_grades:
            locked = grade - 1 > qi_realm
            style = discord.ButtonStyle.secondary if locked else discord.ButtonStyle.primary
            label = f"{'🔒 ' if locked else ''}Cấp {grade}"
            btn = discord.ui.Button(label=label, style=style, row=(grade - 1) // 5)
            btn.callback = self._make_cb(grade)
            self.add_item(btn)

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_cb(self, grade: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            embed = _recipe_grade_embed(grade, self._qi_realm)
            view = RecipeListView(self._discord_id, grade, self._qi_realm, self._back_fn)
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_hub(interaction, self._discord_id, self._back_fn)


def _recipe_grade_embed(grade: int, qi_realm: int) -> discord.Embed:
    pool = [r for r in registry.pill_recipes.values() if int(r.get("grade", 1)) == grade]
    unlocked = sum(1 for r in pool if r.get("min_qi_realm", 0) <= qi_realm)
    return base_embed(
        f"📜 Đan Phương — Cấp {grade}",
        f"Đã mở khoá **{unlocked}/{len(pool)}** đan phương. Chọn một đan phương để xem chi tiết và luyện chế.",
        color=0x2E7D32,
    )


class RecipeListView(discord.ui.View):
    def __init__(self, discord_id: int, grade: int, qi_realm: int, back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._grade = grade
        self._qi_realm = qi_realm
        self._back_fn = back_fn
        self.add_item(RecipeSelect(discord_id, grade, qi_realm, back_fn))

        back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        embed = _recipe_list_embed(self._qi_realm)
        view = RecipeGradeView(self._discord_id, self._qi_realm, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)


class RecipeSelect(discord.ui.Select):
    def __init__(self, discord_id: int, grade: int, qi_realm: int, back_fn) -> None:
        self._discord_id = discord_id
        self._grade = grade
        self._qi_realm = qi_realm
        self._back_fn = back_fn

        pool = [r for r in registry.pill_recipes.values() if int(r.get("grade", 1)) == grade]
        pool.sort(key=lambda r: r.get("vi", ""))
        options: list[discord.SelectOption] = []
        for r in pool[:25]:
            locked = r.get("min_qi_realm", 0) > qi_realm
            pill = registry.get_pill(r["output_pill"])
            desc = pill["vi"] if pill else r["output_pill"]
            options.append(discord.SelectOption(
                label=r["vi"][:100],
                value=r["key"],
                description=f"→ {desc[:80]}",
                emoji="🔒" if locked else "⚗️",
            ))
        if not options:
            options = [discord.SelectOption(label="(Không có)", value="__none__")]
        super().__init__(placeholder="Chọn đan phương...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        recipe_key = self.values[0]
        if recipe_key == "__none__":
            if not await safe_defer(interaction):
                return
            return
        if not await safe_defer(interaction):
            return
        # Load owned furnaces so the detail view can offer a picker. Default
        # selection is the player's stored preference when it's still valid,
        # otherwise the auto-picked "best" furnace — so users who don't want
        # to fiddle just hit Luyện and go.
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
        owned_furnaces = _owned_furnaces_from_player(player) if player else []
        preferred_key = getattr(player, "preferred_furnace_key", None) if player else None
        recipe = get_recipe(recipe_key)
        required_tier = int((recipe or {}).get("furnace_tier", 1))
        default_key = _resolve_default_furnace(preferred_key, owned_furnaces, required_tier)

        embed = await _recipe_detail_embed(interaction, recipe_key)
        view = RecipeDetailView(
            self._discord_id, recipe_key, self._grade, self._qi_realm, self._back_fn,
            owned_furnaces=owned_furnaces,
            required_tier=required_tier,
            selected_furnace_key=default_key,
        )
        await interaction.edit_original_response(embed=embed, view=view)


async def _recipe_detail_embed(interaction: discord.Interaction, recipe_key: str) -> discord.Embed:
    from src.game.systems.alchemy import apply_furnace_bonus

    recipe = get_recipe(recipe_key)
    if not recipe:
        return error_embed("Đan phương không tồn tại.")
    pill = registry.get_pill(recipe["output_pill"])

    async with get_session() as session:
        player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
        bag = await _ingredient_map(player.id) if player else {}
        furnace_keys = _owned_furnaces_from_player(player) if player else []
        preferred_key = getattr(player, "preferred_furnace_key", None) if player else None

    embed = base_embed(recipe["vi"], f"Đan phương cấp **{recipe['grade']}**", color=0x2E7D32)

    if pill:
        embed.add_field(
            name="💊 Đan Dược",
            value=f"**{pill['vi']}** — Hiệu ứng: {pill.get('effect_vi','?')}\n"
                  f"☠️ Đan độc: {pill.get('dan_doc', 0)}",
            inline=False,
        )

    # Ingredients with current stock
    ing_lines = []
    for slot in recipe.get("ingredients", []):
        role = {"chu": "Chủ Dược", "phu": "Phụ Dược", "dan": "Dẫn Dược"}.get(slot["role"], slot["role"])
        option_lines = []
        for opt in slot["options"]:
            item = registry.get_item(opt["key"])
            name = item["vi"] if item else opt["key"]
            owned = bag.get(opt["key"], 0)
            ok = owned >= opt["qty"]
            option_lines.append(f"{'✅' if ok else '❌'} {name} ({owned}/{opt['qty']})")
        ing_lines.append(f"**{role}**: " + " / ".join(option_lines))
    embed.add_field(name="🌿 Nguyên Liệu", value="\n".join(ing_lines) or "*(không có)*", inline=False)

    embed.add_field(name="Chi Phí", value=f"{emojis.for_currency('merit')} {recipe.get('cost_cong_duc', 0):,} Công Đức", inline=True)

    required_tier = int(recipe.get("furnace_tier", 1))
    chosen_key = _resolve_default_furnace(preferred_key, furnace_keys, required_tier)
    chosen_furnace = registry.get_furnace(chosen_key) if chosen_key else None
    if chosen_furnace is not None:
        bonus = chosen_furnace.get("quality_bonus") or {}
        badge = "✦ " if chosen_furnace.get("is_unique") else ""
        bonus_suffix = ""
        if bonus:
            parts = [f"+{v:.0%} {k.capitalize()}" for k, v in bonus.items()]
            bonus_suffix = "\n🎯 " + ", ".join(parts)
        furnace_value = f"✅ {badge}**{chosen_furnace['vi']}** (Cấp {chosen_furnace['furnace_tier']}){bonus_suffix}"
    else:
        furnace_value = f"❌ Cần Đan Lô **Cấp {required_tier}**"
    embed.add_field(name="🔥 Đan Lô", value=furnace_value, inline=True)

    chances = recipe.get("quality_chances", {})
    if chances:
        effective = apply_furnace_bonus(chances, chosen_furnace)
        total = sum(effective.values())
        if total > 0:
            norm = {k: v / total for k, v in effective.items()}
        else:
            norm = effective
        embed.add_field(
            name="Tỷ Lệ Phẩm Chất",
            value="Hoàng {:.0%} • Huyền {:.0%} • Địa {:.0%} • Thiên {:.0%}".format(
                norm.get("hoan", 0), norm.get("huyen", 0),
                norm.get("dia", 0), norm.get("thien", 0),
            ),
            inline=False,
        )
    return embed


class _FurnaceSelect(discord.ui.Select):
    """Furnace picker that lives inside ``RecipeDetailView``.

    Lists every owned furnace with its tier and quality bonuses; updates
    ``view_owner._selected_furnace_key`` on each pick. Furnaces below the
    recipe's tier are still listed (marked ❌) so the player can see why
    they don't qualify; the system layer will reject them at craft time.
    """

    def __init__(
        self,
        *,
        discord_id: int,
        view_owner: "RecipeDetailView",
        owned_furnaces: list[str],
        required_tier: int,
        selected_key: str | None,
    ) -> None:
        self._discord_id = discord_id
        self._view_owner = view_owner

        options: list[discord.SelectOption] = []
        for key in owned_furnaces[:25]:
            f = registry.get_furnace(key)
            if not f:
                continue
            tier = int(f.get("furnace_tier", 1))
            qualifies = tier >= required_tier
            tag = "✦" if f.get("is_unique") else "•"
            bonus = f.get("quality_bonus") or {}
            if bonus:
                bonus_str = ", ".join(f"+{int(v*100)}% {k}" for k, v in bonus.items())
                desc = f"Cấp {tier} · {bonus_str}"
            else:
                desc = f"Cấp {tier}"
            label = f"{tag} {f['vi']}"
            if not qualifies:
                label = "❌ " + label
            options.append(discord.SelectOption(
                label=label[:100],
                value=key,
                description=desc[:100],
                default=(key == selected_key),
            ))
        if not options:
            options = [discord.SelectOption(label="(không có Đan Lô)", value="__none__")]
        super().__init__(placeholder="🔥 Chọn Đan Lô…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        picked = self.values[0]
        new_key = None if picked == "__none__" else picked
        self._view_owner._selected_furnace_key = new_key

        # Sticky preference: persist the user's choice so the next recipe
        # detail view defaults to it. We commit on every pick (cheap — one
        # column update) so the preference survives bot restarts.
        if new_key is not None:
            async with get_session() as session:
                repo = PlayerRepository(session)
                player = await repo.get_by_discord_id(interaction.user.id)
                if player and player.preferred_furnace_key != new_key:
                    player.preferred_furnace_key = new_key
                    await repo.save(player)

        if not await safe_defer(interaction):
            return
class RecipeDetailView(discord.ui.View):
    def __init__(
        self,
        discord_id: int,
        recipe_key: str,
        grade: int,
        qi_realm: int,
        back_fn,
        *,
        owned_furnaces: list[str] | None = None,
        required_tier: int = 1,
        selected_furnace_key: str | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._recipe_key = recipe_key
        self._grade = grade
        self._qi_realm = qi_realm
        self._back_fn = back_fn
        self._owned_furnaces: list[str] = list(owned_furnaces or [])
        self._required_tier = required_tier
        self._selected_furnace_key: str | None = selected_furnace_key

        # Bulk-craft preset row — ×1 stays first for muscle-memory parity
        # with the old single-craft button. ×5/×10 cover the common case
        # (mid-tier grinding), and "Số khác…" opens a modal up to the
        # _BULK_CRAFT_HARD_CAP. Each button uses the same _do_craft path
        # so partial-success on resource exhaustion is handled uniformly.
        for qty in (1, 5, 10):
            label = "⚗️ Luyện" if qty == 1 else f"⚗️ Luyện ×{qty}"
            btn = discord.ui.Button(
                label=label, style=discord.ButtonStyle.success, row=0,
            )
            btn.callback = self._make_craft_cb(qty)
            self.add_item(btn)

        custom_btn = discord.ui.Button(
            label="⚗️ Số khác…", style=discord.ButtonStyle.primary, row=0,
        )
        custom_btn.callback = self._open_qty_modal
        self.add_item(custom_btn)

        # Show a furnace picker when the player has more than one owned
        # furnace — single furnace = no decision to make.
        if len(self._owned_furnaces) >= 2:
            self.add_item(_FurnaceSelect(
                discord_id=discord_id,
                view_owner=self,
                owned_furnaces=self._owned_furnaces,
                required_tier=required_tier,
                selected_key=selected_furnace_key,
            ))

        back_btn = discord.ui.Button(
            label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=2,
        )
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_craft_cb(self, qty: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            await self._run_craft(interaction, qty)
        return _cb

    async def _open_qty_modal(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        modal = BulkCraftQuantityModal(self._on_modal_submit)
        await interaction.response.send_modal(modal)

    async def _on_modal_submit(
        self, interaction: discord.Interaction, qty: int,
    ) -> None:
        if not await safe_defer(interaction):
            return
        await self._run_craft(interaction, qty)

    async def _run_craft(self, interaction: discord.Interaction, qty: int) -> None:
        result = await _do_craft(
            interaction, self._recipe_key,
            selected_furnace_key=self._selected_furnace_key,
            quantity=qty,
        )
        if not result.success:
            await interaction.edit_original_response(
                embed=error_embed(result.message), view=self._done_view(),
            )
            return
        embed = _craft_result_embed(result)
        await interaction.edit_original_response(embed=embed, view=self._done_view())

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        embed = _recipe_grade_embed(self._grade, self._qi_realm)
        view = RecipeListView(self._discord_id, self._grade, self._qi_realm, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    def _done_view(self) -> discord.ui.View:
        """Compact post-craft action row: Luyện tiếp (re-renders the
        recipe detail with all bulk buttons) + ◀ Về Luyện Đan."""
        view = discord.ui.View(timeout=180)
        again_btn = discord.ui.Button(label="⚗️ Luyện tiếp", style=discord.ButtonStyle.success, row=0)

        async def _again(i: discord.Interaction) -> None:
            if not _guard(i, self._discord_id):
                await i.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(i):
                return
            embed = await _recipe_detail_embed(i, self._recipe_key)
            await i.edit_original_response(embed=embed, view=self)

        again_btn.callback = _again
        view.add_item(again_btn)

        hub_btn = discord.ui.Button(label="◀ Về Luyện Đan", style=discord.ButtonStyle.secondary, row=0)

        async def _to_hub(i: discord.Interaction) -> None:
            if not _guard(i, self._discord_id):
                await i.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(i):
                return
            await _nav_hub(i, self._discord_id, self._back_fn)

        hub_btn.callback = _to_hub
        view.add_item(hub_btn)
        return view


class BulkCraftQuantityModal(discord.ui.Modal, title="Số Lượng Luyện"):
    """Custom-quantity entry for the ⚗️ Số khác… button. Caps to
    ``_BULK_CRAFT_HARD_CAP`` server-side; values above the cap are
    clamped silently rather than rejected so a "999" typo still does
    something useful.
    """

    qty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Số lượng đan dược",
        placeholder=f"vd: 25 (tối đa {_BULK_CRAFT_HARD_CAP})",
        max_length=3,
    )

    def __init__(self, on_submit) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.qty_input.value.strip().replace(",", "")
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải là số nguyên dương."),
                ephemeral=True,
            )
            return
        qty = min(int(raw), _BULK_CRAFT_HARD_CAP)
        await self._on_submit(interaction, qty)


@dataclass
class BulkCraftResult:
    """Aggregated outcome from one bulk-craft click.

    ``quality_counts`` keys are quality tiers (1=Hoàng, 2=Huyền, 3=Địa,
    4=Thiên). ``last_failure`` is the message from the iteration that
    stopped the loop (insufficient herbs / merit / furnace) — surfaced so
    the user sees *why* a request for ×10 produced fewer pills.
    """

    success: bool
    message: str
    pill_key: str | None = None
    quality_counts: dict[int, int] = field(default_factory=dict)
    consumed_totals: dict[str, int] = field(default_factory=dict)
    total_cost: int = 0
    total_dan_doc: int = 0
    crafted: int = 0
    requested: int = 0
    furnace_key: str | None = None
    last_failure: str | None = None


async def _do_craft(
    interaction: discord.Interaction,
    recipe_key: str,
    *,
    selected_furnace_key: str | None = None,
    quantity: int = 1,
) -> BulkCraftResult:
    """Validate, deduct ingredients/merit, craft N pills, add to inventory.

    ``quantity=1`` matches the original single-craft contract. Larger
    values loop ``craft_pill`` against a freshly loaded in-memory snapshot
    of the player's bag and merit, applying inventory mutations in one
    DB pass at the end. Stops early on the first iteration that fails
    (insufficient resources / under-tier furnace) so a partial bulk craft
    is reported honestly instead of silently truncated.

    If ``selected_furnace_key`` is provided, every iteration is
    constrained to that single furnace. When ``None`` the system layer
    picks the best owned furnace each iteration (which can flip if a
    unique furnace is consumed mid-bulk — currently impossible since
    furnaces aren't consumed, but the contract holds).
    """
    quantity = max(1, min(int(quantity), _BULK_CRAFT_HARD_CAP))
    requested = quantity

    async with get_session() as session:
        player_repo = PlayerRepository(session)
        inv_repo = InventoryRepository(session)

        player = await player_repo.get_by_discord_id(interaction.user.id)
        if not player:
            return BulkCraftResult(False, "Không tìm thấy nhân vật.", requested=requested)

        char = _player_to_model(player)
        # Tông Môn Luyện Đan Phòng — quality tilt read inside craft_pill.
        await attach_sect_buffs(session, char)

        bag: dict[str, int] = {}
        owned_furnace_keys: list[str] = []
        for row in player.inventory:
            item = registry.get_item(row.item_key)
            if not item:
                continue
            t = item.get("type")
            if t == "herb":
                bag[row.item_key] = bag.get(row.item_key, 0) + row.quantity
            elif t == "furnace":
                owned_furnace_keys.append(row.item_key)

        # Honour the user's pick if one was supplied. Verify the player
        # still owns it (inventory might have changed since the picker
        # rendered) before passing through.
        if selected_furnace_key:
            if selected_furnace_key not in owned_furnace_keys:
                return BulkCraftResult(
                    False, "Đan Lô đã chọn không còn trong túi.", requested=requested,
                )
            furnace_keys: list[str] = [selected_furnace_key]
        else:
            furnace_keys = owned_furnace_keys

        # ── Loop crafts on the in-memory bag/char snapshot ────────────
        quality_counts: dict[int, int] = {}
        consumed_totals: dict[str, int] = {}
        total_cost = 0
        total_dan_doc = 0
        crafted = 0
        last_failure: str | None = None
        last_furnace_key: str | None = None
        last_pill_key: str | None = None

        for _ in range(quantity):
            iter_result = craft_pill(char, recipe_key, bag, furnace_keys)
            if not iter_result.success:
                last_failure = iter_result.message
                break
            crafted += 1
            for pick in iter_result.consumed:
                bag[pick.key] = bag.get(pick.key, 0) - pick.qty
                consumed_totals[pick.key] = consumed_totals.get(pick.key, 0) + pick.qty
            tier = int(iter_result.quality_tier)
            quality_counts[tier] = quality_counts.get(tier, 0) + 1
            total_cost += int(iter_result.cost_cong_duc)
            total_dan_doc += int(iter_result.dan_doc_delta)
            last_furnace_key = iter_result.furnace_key
            last_pill_key = iter_result.pill_key

        if crafted == 0:
            # No iteration succeeded — return the first-iteration failure
            # verbatim so the user sees the validation error from craft_pill.
            return BulkCraftResult(
                False,
                last_failure or "Không thể luyện đan.",
                requested=requested,
            )

        # ── Apply DB mutations once (one merit save, one pill add per
        # quality bucket, one ingredient remove per item key) ─────────
        for key, qty in consumed_totals.items():
            ok = await inv_repo.remove_any_grade(player.id, key, qty)
            if not ok:
                ing = registry.get_item(key) or {}
                name = ing.get("vi", key)
                return BulkCraftResult(
                    False, f"Lỗi nội bộ: thiếu {name}.", requested=requested,
                )

        # Persist merit deduction (char.merit was decremented N times).
        player.merit = char.merit

        # Add pills to inventory bucketed by quality tier.
        if last_pill_key:
            for tier, count in quality_counts.items():
                if count > 0:
                    await inv_repo.add_item(
                        player.id, last_pill_key, Grade(tier), count,
                    )

        await player_repo.save(player)

        # Sect daily mission credit — one tick per pill crafted. No-op for
        # sect-less players, internally failure-proof.
        await sect_missions.record_event(
            session, player.id, sect_missions.EVENT_ALCHEMY_CRAFT, crafted
        )

    return BulkCraftResult(
        success=True,
        message="",
        pill_key=last_pill_key,
        quality_counts=quality_counts,
        consumed_totals=consumed_totals,
        total_cost=total_cost,
        total_dan_doc=total_dan_doc,
        crafted=crafted,
        requested=requested,
        furnace_key=last_furnace_key,
        last_failure=last_failure if crafted < requested else None,
    )


def _craft_result_embed(result: BulkCraftResult) -> discord.Embed:
    """Render a bulk-craft summary. Single crafts (crafted=1) collapse to
    the simple success layout; multi-craft adds a per-quality breakdown
    and a partial-success footer when applicable.
    """
    pill = registry.get_pill(result.pill_key or "")
    # Tint the embed by the highest-quality pill produced — Thiên if any
    # rolled, otherwise the next tier down. Falls back to Hoàng if the
    # quality_counts map is empty (shouldn't happen on success path).
    top_tier = max(result.quality_counts) if result.quality_counts else 1
    quality_key = _QUALITY_KEY_BY_TIER.get(top_tier, "hoan")
    color = QUALITY_COLORS.get(quality_key, discord.Color.green())

    if result.crafted == 1 and pill:
        # Single-craft fast path — match the original message style.
        only_tier = next(iter(result.quality_counts))
        quality_label = QUALITY_LABELS.get(_QUALITY_KEY_BY_TIER[only_tier], "?")
        title = "⚗️ Luyện Đan Thành Công!"
        description = (
            f"✅ **{pill['vi']}** [Cấp {pill.get('grade', '?')} — "
            f"{quality_label} Phẩm]"
        )
    else:
        title = f"⚗️ Luyện Đan ×{result.crafted} Thành Công!"
        description = (
            f"Sản xuất **{result.crafted}/{result.requested}** viên "
            f"**{pill['vi'] if pill else result.pill_key}**."
        )

    embed = discord.Embed(title=title, description=description, color=color)

    if result.quality_counts:
        # Render every tier produced, sorted high → low so Thiên shows first.
        breakdown = []
        for tier in sorted(result.quality_counts, reverse=True):
            label = _QUALITY_LABEL_VI.get(tier, "?")
            breakdown.append(f"**{label}**: {result.quality_counts[tier]}")
        embed.add_field(
            name="📦 Phẩm Chất", value=" · ".join(breakdown), inline=False,
        )

    if pill:
        embed.add_field(name="Hiệu ứng", value=pill.get("effect_vi", "?"), inline=True)
    embed.add_field(name="☠️ Đan độc tổng", value=str(result.total_dan_doc), inline=True)
    embed.add_field(name="✨ Chi phí", value=f"−{result.total_cost:,}", inline=True)

    if result.furnace_key:
        furnace = registry.get_furnace(result.furnace_key)
        if furnace:
            tag = "✦ " if furnace.get("is_unique") else ""
            embed.add_field(
                name="🔥 Đan Lô", value=f"{tag}{furnace['vi']}", inline=True,
            )

    if result.consumed_totals:
        consumed_text = ", ".join(
            f"{(registry.get_item(k) or {}).get('vi', k)}×{q}"
            for k, q in result.consumed_totals.items()
        )
        embed.add_field(name="🌿 Nguyên liệu đã dùng", value=consumed_text, inline=False)

    if result.last_failure and result.crafted < result.requested:
        # Surface why the bulk run stopped short — most often "không đủ
        # nguyên liệu" or "không đủ Công Đức".
        embed.set_footer(
            text=f"Dừng ở {result.crafted}/{result.requested}: {result.last_failure}",
        )

    return embed


# ── Pill bag ────────────────────────────────────────────────────────────────

def _pill_bag_embed(pills: list[tuple[str, int, int]], page: int = 0) -> discord.Embed:
    embed = base_embed(
        "💊 Đan Dược",
        "Chọn đan dược để sử dụng. Phẩm chất cao sẽ nâng cao hiệu quả và giảm đan độc tích lũy.",
        color=0x27AE60,
    )
    if not pills:
        embed.description += "\n\n*(Túi rỗng — hãy luyện vài viên đan trước.)*"
        return embed
    visible = page_slice(pills, page, per_page=PAGE_SIZE)
    lines = []
    for key, grade, qty in visible:
        item = registry.get_item(key)
        name = item["vi"] if item else key
        quality = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}.get(grade, "?")
        lines.append(f"• **{name}** — {quality} Phẩm ×{qty}")
    embed.description += "\n\n" + "\n".join(lines)
    pages = total_pages(len(pills), per_page=PAGE_SIZE)
    if pages > 1:
        embed.set_footer(text=f"Trang {page + 1}/{pages} · Tổng {len(pills)} loại đan dược")
    return embed


class PillBagView(discord.ui.View):
    def __init__(
        self,
        discord_id: int,
        pills: list[tuple[str, int, int]],
        back_fn,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._pills = pills
        self._back_fn = back_fn
        pages = total_pages(len(pills), per_page=PAGE_SIZE)
        self._page = max(0, min(page, pages - 1))
        self.add_item(PillSelect(discord_id, pills, back_fn, page=self._page))

        back_btn = discord.ui.Button(label="◀ Về Luyện Đan", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

        add_page_controls(
            self,
            page=self._page,
            total=len(pills),
            on_change=self._on_page_change,
            row=2,
        )

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        view = PillBagView(self._discord_id, self._pills, self._back_fn, page=new_page)
        embed = _pill_bag_embed(self._pills, page=new_page)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _nav_hub(interaction, self._discord_id, self._back_fn)


class PillSelect(discord.ui.Select):
    def __init__(
        self,
        discord_id: int,
        pills: list[tuple[str, int, int]],
        back_fn,
        page: int = 0,
    ) -> None:
        self._discord_id = discord_id
        self._back_fn = back_fn
        self._all_pills = pills
        self._page = page

        options: list[discord.SelectOption] = []
        for key, grade, qty in page_slice(pills, page, per_page=PAGE_SIZE):
            item = registry.get_item(key)
            name = item["vi"] if item else key
            quality = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}.get(grade, "?")
            options.append(discord.SelectOption(
                label=f"{name} [{quality}] ×{qty}"[:100],
                value=f"{key}|{grade}",
                description=(item.get("effect_vi", "") if item else "")[:80],
            ))
        if not options:
            options = [discord.SelectOption(label="(Không có đan dược)", value="__none__")]
        super().__init__(placeholder="Chọn đan dược để sử dụng...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if self.values[0] == "__none__":
            if not await safe_defer(interaction):
                return
            return
        if not await safe_defer(interaction):
            return
        key, grade_str = self.values[0].split("|")
        grade = int(grade_str)

        # Pull the latest owned quantity for this pill — list snapshot may
        # be stale if multiple sessions touched inventory. Fall back to the
        # cached list value if we somehow can't find the row.
        owned = next(
            (q for k, g, q in self._all_pills if k == key and g == grade),
            0,
        )

        embed = _pill_detail_embed(key, grade, owned)
        view = PillDetailView(
            self._discord_id,
            key,
            grade,
            owned,
            self._all_pills,
            self._back_fn,
            page=self._page,
        )
        await interaction.edit_original_response(embed=embed, view=view)


def _pill_detail_embed(
    pill_key: str,
    grade: int,
    quantity_owned: int,
) -> discord.Embed:
    """Per-pill embed shown before the bulk-quantity buttons.

    Surfaces the effect description, owned count, and per-pill toxicity so
    the player can decide how many to consume. Quality (Hoàng→Thiên) is
    derived from ``grade`` (the inventory bucket), not the recipe grade.
    """
    pill = registry.get_pill(pill_key)
    quality_key = _QUALITY_KEY_BY_TIER.get(grade, "hoan")
    quality_vi = _QUALITY_LABEL_VI.get(grade, "?")
    color = QUALITY_COLORS.get(quality_key, discord.Color.green())

    if not pill:
        return error_embed(f"Không tìm thấy đan dược '{pill_key}'.")

    embed = discord.Embed(
        title=f"💊 {pill['vi']} ({quality_vi} Phẩm)",
        description=pill.get("effect_vi", ""),
        color=color,
    )
    embed.add_field(name="📦 Số lượng", value=f"×{quantity_owned}", inline=True)
    embed.add_field(name="📋 Phẩm cấp", value=f"Cấp {pill.get('grade', '?')}", inline=True)
    base_doc = int(pill.get("dan_doc", 0))
    reduction = 1.0 - (grade - 1) * 0.2
    per_pill_doc = max(0, int(round(base_doc * reduction)))
    embed.add_field(name="☠️ Đan độc / viên", value=f"{per_pill_doc}", inline=True)
    embed.set_footer(text="Chọn số lượng dùng. Đan dược không thể hấp thu sẽ tự dừng.")
    return embed


class PillQuantityModal(discord.ui.Modal, title="Số Lượng Sử Dụng"):
    qty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Số lượng đan dược",
        placeholder="vd: 25",
        max_length=4,
    )

    def __init__(self, on_submit, max_qty: int) -> None:
        super().__init__()
        self._on_submit = on_submit
        self._max_qty = max_qty
        self.qty_input.placeholder = f"Tối đa: {min(max_qty, _BULK_CONSUME_HARD_CAP)}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.qty_input.value.strip().replace(",", "")
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải là số nguyên dương."),
                ephemeral=True,
            )
            return
        qty = min(int(raw), self._max_qty, _BULK_CONSUME_HARD_CAP)
        await self._on_submit(interaction, qty)


class PillDetailView(discord.ui.View):
    """Quantity-picker shown after a pill is selected from the bag.

    Exposes presets (1 / 5 / 10 / all) and a modal for arbitrary amounts.
    Each click triggers a bulk consume via :func:`_do_consume`; the result
    embed is rendered inline and the back button returns to the pill bag
    (preserving the previous page so the user keeps their place).
    """

    def __init__(
        self,
        discord_id: int,
        pill_key: str,
        grade: int,
        quantity_owned: int,
        all_pills: list[tuple[str, int, int]],
        back_fn,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._pill_key = pill_key
        self._grade = grade
        self._owned = quantity_owned
        self._all_pills = all_pills
        self._back_fn = back_fn
        self._page = page

        for qty in self._quantity_presets():
            label = f"Dùng {qty}" if qty < quantity_owned else f"Dùng tất cả ({qty})"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                row=0,
                disabled=quantity_owned < 1,
            )
            btn.callback = self._make_consume_cb(qty)
            self.add_item(btn)

        custom_btn = discord.ui.Button(
            label="Số khác…",
            style=discord.ButtonStyle.primary,
            row=1,
            disabled=quantity_owned < 1,
        )
        custom_btn.callback = self._open_modal
        self.add_item(custom_btn)

        back_btn = discord.ui.Button(
            label="◀ Quay lại",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _quantity_presets(self) -> list[int]:
        """Preset quantities to render as buttons.

        Always includes 1 (when affordable), then 5/10 if the player owns
        that many, then ``self._owned`` as the "all" option when it's not
        already a preset. Caps at 4 buttons so the row stays under
        Discord's 5-component-per-row limit.
        """
        result: list[int] = []
        for q in (1, 5, 10):
            if q <= self._owned and q not in result:
                result.append(q)
        if self._owned > 0 and self._owned not in result and len(result) < 4:
            result.append(self._owned)
        return result or ([1] if self._owned >= 1 else [])

    def _make_consume_cb(self, quantity: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, self._discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            await self._consume_and_render(interaction, quantity)
        return _cb

    async def _open_modal(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return

        async def _on_submit(modal_inter: discord.Interaction, qty: int) -> None:
            if not await safe_defer(modal_inter):
                return
            await self._consume_and_render(modal_inter, qty)

        await interaction.response.send_modal(
            PillQuantityModal(_on_submit, max_qty=self._owned)
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            pills = await _pills_in_bag(player.id) if player else []
        embed = _pill_bag_embed(pills, page=self._page)
        view = PillBagView(self._discord_id, pills, self._back_fn, page=self._page)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _consume_and_render(
        self, interaction: discord.Interaction, quantity: int
    ) -> None:
        effect, consumed = await _do_consume(
            interaction, self._pill_key, self._grade, quantity
        )

        # Refresh inventory snapshot so the next render reflects the new
        # count (or hides the pill if the stack ran out).
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            pills = await _pills_in_bag(player.id) if player else []

        if not effect.applied and consumed == 0:
            embed = base_embed(
                "💊 Sử dụng Đan Dược",
                effect.message,
                color=0xC0392B,
            )
        else:
            embed = base_embed(
                "💊 Sử dụng Đan Dược",
                effect.message,
                color=0x27AE60,
            )

        # If the player still owns this pill, stay on the detail view so
        # they can keep pressing quantity buttons. Otherwise drop back to
        # the bag (the pill is gone from the dropdown options).
        new_owned = next(
            (q for k, g, q in pills if k == self._pill_key and g == self._grade),
            0,
        )
        if new_owned > 0:
            view = PillDetailView(
                self._discord_id,
                self._pill_key,
                self._grade,
                new_owned,
                pills,
                self._back_fn,
                page=self._page,
            )
        else:
            view = PillBagView(self._discord_id, pills, self._back_fn, page=self._page)
        await interaction.edit_original_response(embed=embed, view=view)


async def _do_consume(
    interaction: discord.Interaction,
    pill_key: str,
    grade: int,
    quantity: int = 1,
) -> tuple[PillEffect, int]:
    """Consume up to ``quantity`` pills of ``(pill_key, grade)``.

    Returns ``(effect, consumed_count)`` where ``effect.message`` is a
    human-readable summary aggregating per-pill effects. Stops early if
    a single consume is refused (combat-buff cap, grade-gated, or stock
    runs out) and includes the refusal reason in the summary so the
    player learns *why* the bulk run stopped short.
    """
    quantity = max(1, min(int(quantity), _BULK_CONSUME_HARD_CAP))

    async with get_session() as session:
        player_repo = PlayerRepository(session)
        inv_repo = InventoryRepository(session)

        player = await player_repo.get_by_discord_id(interaction.user.id)
        if not player:
            return PillEffect(False, "Không tìm thấy nhân vật."), 0

        # Single inventory lookup — cap requested quantity to what's
        # actually in the bag and decrement after the consume loop in one
        # call. Avoids N round-trips through SQLAlchemy when bulk-using.
        existing = await inv_repo.get_item(player.id, pill_key, Grade(grade))
        if not existing or existing.quantity < 1:
            return PillEffect(False, "Không có đan dược này trong túi."), 0
        available = int(existing.quantity)
        to_attempt = min(quantity, available)

        char = _player_to_model(player)
        pill = registry.get_pill(pill_key)
        effect_key = pill.get("effect_key", "") if pill else ""
        is_buff = is_buff_pill(effect_key)

        consumed = 0
        last_effect: PillEffect | None = None
        last_refusal: str | None = None
        total_dan_doc = 0
        total_merit = 0
        total_qi_xp = 0
        total_body_xp = 0
        total_heal = 0
        last_buff_increment: str | None = None

        for _ in range(to_attempt):
            effect = consume_pill(char, pill_key, grade)
            if not effect.applied:
                last_refusal = effect.message
                break
            consumed += 1
            last_effect = effect
            total_dan_doc += effect.dan_doc_delta
            total_merit += effect.merit_delta
            total_qi_xp += effect.qi_xp_delta
            total_body_xp += effect.body_xp_delta
            total_heal += effect.heal_delta
            if effect.pill_buff_increment:
                last_buff_increment = effect.pill_buff_increment

        if consumed == 0:
            # No pills consumed — surface the refusal so the player knows
            # why (grade-gated, buff cap reached, etc.).
            return PillEffect(False, last_refusal or "Không thể dùng đan dược."), 0

        ok = await inv_repo.remove_item(player.id, pill_key, Grade(grade), consumed)
        if not ok:
            # Should not happen: we capped to ``available`` above. If we
            # land here the row was modified mid-flight; fail safe.
            return PillEffect(False, "Lỗi nội bộ: không thể trừ đan dược."), 0

        # Persist aggregated deltas in one save.
        player.dan_doc = max(0, int(player.dan_doc or 0) + total_dan_doc)
        if total_qi_xp:
            player.qi_xp = int(player.qi_xp or 0) + total_qi_xp
        if total_body_xp:
            player.body_xp = int(player.body_xp or 0) + total_body_xp
        # Re-derive bậc from the new XP — pill XP otherwise leaves
        # ``*_level`` stale, which strands the player at "overflow XP, can't
        # progress" because gates like ``check_needs_tribulation`` and the
        # status embed's progress bar both read the level column.
        if total_body_xp or total_qi_xp:
            from src.game.constants.realms import get_level_from_exp, get_realm
            if total_body_xp:
                body_realm = get_realm("body", player.body_realm)
                if body_realm is not None:
                    player.body_level = get_level_from_exp(player.body_xp, body_realm)
            if total_qi_xp:
                qi_realm = get_realm("qi", player.qi_realm)
                if qi_realm is not None:
                    player.qi_level = get_level_from_exp(player.qi_xp, qi_realm)
        if total_merit:
            from src.game.systems.merit import grant_merit
            grant_merit(player, int(total_merit))
        if total_heal and player.hp_current > 0:
            player.hp_current = player.hp_current + total_heal
        if last_buff_increment:
            from src.game.systems.pill_buffs import encode_counts
            player.pill_buff_counts = encode_counts(char.pill_buff_counts)

        await player_repo.save(player)

    summary_message = _format_bulk_consume_message(
        pill_key=pill_key,
        grade=grade,
        consumed=consumed,
        requested=quantity,
        is_buff=is_buff,
        last_effect=last_effect,
        total_dan_doc=total_dan_doc,
        total_qi_xp=total_qi_xp,
        total_body_xp=total_body_xp,
        total_heal=total_heal,
        total_merit=total_merit,
        refusal=last_refusal,
    )

    return (
        PillEffect(
            applied=True,
            message=summary_message,
            dan_doc_delta=total_dan_doc,
            merit_delta=total_merit,
            qi_xp_delta=total_qi_xp,
            body_xp_delta=total_body_xp,
            heal_delta=total_heal,
            pill_buff_increment=last_buff_increment,
        ),
        consumed,
    )


def _format_bulk_consume_message(
    *,
    pill_key: str,
    grade: int,
    consumed: int,
    requested: int,
    is_buff: bool,
    last_effect: PillEffect | None,
    total_dan_doc: int,
    total_qi_xp: int,
    total_body_xp: int,
    total_heal: int,
    total_merit: int,
    refusal: str | None,
) -> str:
    """Produce the user-facing summary for a bulk consume run."""
    if consumed == 1 and last_effect is not None:
        msg = last_effect.message
    elif is_buff and last_effect is not None:
        # The last successful buff message already shows the cumulative
        # stat and the new ``count/cap`` — most informative summary line.
        msg = f"✨ Dùng **{consumed}** viên — " + last_effect.message
    else:
        pill = registry.get_pill(pill_key)
        name = pill["vi"] if pill else pill_key
        quality = _QUALITY_LABEL_VI.get(grade, "?")
        parts: list[str] = []
        if total_qi_xp:
            parts.append(f"+{total_qi_xp:,} EXP Luyện Khí")
        if total_body_xp:
            parts.append(f"+{total_body_xp:,} EXP Luyện Thể")
        if total_heal:
            parts.append(f"+{total_heal:,} HP")
        if total_merit:
            parts.append(f"+{total_merit:,} Công Đức")
        if total_dan_doc > 0:
            parts.append(f"☠️ +{total_dan_doc} Đan Độc")
        elif total_dan_doc < 0:
            parts.append(f"☠️ −{abs(total_dan_doc)} Đan Độc")
        body = ", ".join(parts) if parts else "Hiệu ứng đã áp dụng"
        msg = f"✨ Dùng **{consumed}** viên **{name}** ({quality} Phẩm) — {body}"

    if refusal and consumed < requested:
        msg += f"\n\n⚠️ Dừng sau {consumed} viên: {refusal}"

    return msg


# ── Herb bag ────────────────────────────────────────────────────────────────

def _herb_bag_embed(herbs: dict[str, int]) -> discord.Embed:
    embed = base_embed(
        "🌿 Thảo Dược",
        "Nguyên liệu luyện đan trong túi — bao gồm thảo dược rớt từ "
        "**Dược Viên** và tinh hoa yêu thú rớt từ **Bí Cảnh Thường**.",
        color=0x16A085,
    )
    if not herbs:
        embed.description += "\n\n*(Túi rỗng.)*"
        return embed

    # Group by grade only — yêu thú materials were merged into the
    # ``herb`` type and now sit alongside regular thảo dược.
    lines_by_grade: dict[int, list[str]] = {}
    for key, qty in sorted(herbs.items()):
        item = registry.get_item(key)
        if not item:
            continue
        grade = int(item.get("grade", 1))
        lines_by_grade.setdefault(grade, []).append(f"• {item['vi']} ×{qty}")

    for grade in sorted(lines_by_grade):
        embed.add_field(
            name=f"🌿 Thảo Dược phẩm {grade}",
            value="\n".join(lines_by_grade[grade][:20]),
            inline=False,
        )
    return embed


class _BackOnlyView(discord.ui.View):
    def __init__(self, discord_id: int, back_cb) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=0)

        async def _cb(interaction: discord.Interaction) -> None:
            if not _guard(interaction, discord_id):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if not await safe_defer(interaction):
                return
            await back_cb(interaction)

        btn.callback = _cb
        self.add_item(btn)


# ── Cog ─────────────────────────────────────────────────────────────────────

class AlchemyCog(commands.Cog, name="Alchemy"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="luyendan", description="Luyện đan — chế đan từ thảo dược")
    async def luyendan(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` để bắt đầu."),
                    ephemeral=True,
                )
                return
            embed = await _alchemy_hub_embed(player)

        view = AlchemyHubView(interaction.user.id, back_fn=None)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AlchemyCog(bot))
