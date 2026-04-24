"""Luyện Đan — interactive button flow for alchemy (herbs → recipes → pills)."""
from __future__ import annotations

import logging
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
from src.utils.embed_builder import base_embed, error_embed

log = logging.getLogger(__name__)


QUALITY_COLORS: dict[str, discord.Color] = {
    "hoan":  discord.Color.from_str("#B8860B"),
    "huyen": discord.Color.from_str("#8A2BE2"),
    "dia":   discord.Color.from_str("#1A5276"),
    "thien": discord.Color.from_str("#C0392B"),
}

# Herbs/yeu_thu always stack in inventory at this single grade slot —
# their intrinsic grade (1-6) is a template attribute, not per-row state.
INGREDIENT_GRADE = Grade.HOANG


def _guard(interaction: discord.Interaction, discord_id: int) -> bool:
    return interaction.user.id == discord_id


async def _ingredient_map(player_id: int) -> dict[str, int]:
    """Return {item_key: total_qty} for all herb/yeu_thu rows in inventory."""
    async with get_session() as session:
        inv_repo = InventoryRepository(session)
        rows = await inv_repo.get_all(player_id)
    result: dict[str, int] = {}
    for row in rows:
        item = registry.get_item(row.item_key)
        if item and item.get("type") in ("herb", "yeu_thu"):
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
    yeu_thu = sum(1 for r in player.inventory
                  if registry.get_item(r.item_key) and registry.get_item(r.item_key).get("type") == "yeu_thu")
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
        "Luyện chế linh đan từ thảo dược và nguyên liệu yêu thú.",
        color=0x2E7D32,
    )
    embed.add_field(name="Công Đức",     value=f"✨ {player.merit:,}", inline=True)
    embed.add_field(name="Đan Độc",      value=f"☠️ {player.dan_doc:,}", inline=True)
    embed.add_field(name="Đan Phương",   value=f"📜 {unlocked}/{total_recipes} mở khoá", inline=True)
    embed.add_field(name="Thảo Dược",    value=f"🌿 {herbs} loại", inline=True)
    embed.add_field(name="Nguyên Liệu",  value=f"🩸 {yeu_thu} loại", inline=True)
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

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    async def _open_recipes(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
        await interaction.response.defer()
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
        await interaction.response.defer()
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            herbs = await _ingredient_map(player.id)

        embed = _herb_bag_embed(herbs)
        view = _BackOnlyView(self._discord_id, lambda i: _nav_hub(i, self._discord_id, self._back_fn))
        await interaction.edit_original_response(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


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
            await interaction.response.defer()
            embed = _recipe_grade_embed(grade, self._qi_realm)
            view = RecipeListView(self._discord_id, grade, self._qi_realm, self._back_fn)
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
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
        await interaction.response.defer()
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
            await interaction.response.defer()
            return
        await interaction.response.defer()
        embed = await _recipe_detail_embed(interaction, recipe_key)
        view = RecipeDetailView(self._discord_id, recipe_key, self._grade, self._qi_realm, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)


async def _recipe_detail_embed(interaction: discord.Interaction, recipe_key: str) -> discord.Embed:
    from src.game.systems.alchemy import _pick_best_furnace, apply_furnace_bonus

    recipe = get_recipe(recipe_key)
    if not recipe:
        return error_embed("Đan phương không tồn tại.")
    pill = registry.get_pill(recipe["output_pill"])

    async with get_session() as session:
        player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
        bag = await _ingredient_map(player.id) if player else {}
        furnace_keys = _owned_furnaces_from_player(player) if player else []

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

    embed.add_field(name="Chi Phí", value=f"✨ {recipe.get('cost_cong_duc', 0):,} Công Đức", inline=True)

    required_tier = int(recipe.get("furnace_tier", 1))
    chosen_furnace = _pick_best_furnace(furnace_keys, required_tier)
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


class RecipeDetailView(discord.ui.View):
    def __init__(self, discord_id: int, recipe_key: str, grade: int, qi_realm: int, back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._recipe_key = recipe_key
        self._grade = grade
        self._qi_realm = qi_realm
        self._back_fn = back_fn

    @discord.ui.button(label="⚗️ Luyện", style=discord.ButtonStyle.success, row=0)
    async def craft_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        result = await _do_craft(interaction, self._recipe_key)
        if not result.success:
            await interaction.edit_original_response(embed=error_embed(result.message), view=self._done_view())
            return
        embed = _craft_result_embed(result)
        await interaction.edit_original_response(embed=embed, view=self._done_view())

    @discord.ui.button(label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        embed = _recipe_grade_embed(self._grade, self._qi_realm)
        view = RecipeListView(self._discord_id, self._grade, self._qi_realm, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    def _done_view(self) -> discord.ui.View:
        view = discord.ui.View(timeout=180)
        again_btn = discord.ui.Button(label="⚗️ Luyện tiếp", style=discord.ButtonStyle.success, row=0)

        async def _again(i: discord.Interaction) -> None:
            if not _guard(i, self._discord_id):
                await i.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await i.response.defer()
            embed = await _recipe_detail_embed(i, self._recipe_key)
            await i.edit_original_response(embed=embed, view=self)

        again_btn.callback = _again
        view.add_item(again_btn)

        hub_btn = discord.ui.Button(label="◀ Về Luyện Đan", style=discord.ButtonStyle.secondary, row=0)

        async def _to_hub(i: discord.Interaction) -> None:
            if not _guard(i, self._discord_id):
                await i.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await i.response.defer()
            await _nav_hub(i, self._discord_id, self._back_fn)

        hub_btn.callback = _to_hub
        view.add_item(hub_btn)
        return view


async def _do_craft(interaction: discord.Interaction, recipe_key: str) -> AlchemyResult:
    """Validate, deduct ingredients/merit, craft pill, and add to inventory."""
    async with get_session() as session:
        player_repo = PlayerRepository(session)
        inv_repo = InventoryRepository(session)

        player = await player_repo.get_by_discord_id(interaction.user.id)
        if not player:
            return AlchemyResult(False, "Không tìm thấy nhân vật.")

        char = _player_to_model(player)

        bag: dict[str, int] = {}
        furnace_keys: list[str] = []
        for row in player.inventory:
            item = registry.get_item(row.item_key)
            if not item:
                continue
            t = item.get("type")
            if t in ("herb", "yeu_thu"):
                bag[row.item_key] = bag.get(row.item_key, 0) + row.quantity
            elif t == "furnace":
                furnace_keys.append(row.item_key)

        result = craft_pill(char, recipe_key, bag, furnace_keys)
        if not result.success:
            return result

        # Deduct ingredients
        for pick in result.consumed:
            ok = await inv_repo.remove_item(player.id, pick.key, INGREDIENT_GRADE, pick.qty)
            if not ok:
                # Should not happen — we validated above. Be defensive.
                return AlchemyResult(False, f"Lỗi nội bộ: thiếu {pick.key}.")

        # Persist merit deduction
        player.merit = char.merit

        # Add pill to inventory keyed by quality tier.
        pill_grade = Grade(result.quality_tier)
        await inv_repo.add_item(player.id, result.pill_key, pill_grade, 1)

        await player_repo.save(player)

    return result


def _craft_result_embed(result: AlchemyResult) -> discord.Embed:
    pill = registry.get_pill(result.pill_key or "")
    color = QUALITY_COLORS.get(result.quality or "hoan", discord.Color.green())
    embed = discord.Embed(title="⚗️ Luyện Đan Thành Công!", description=result.message, color=color)
    if pill:
        embed.add_field(name="Hiệu ứng", value=pill.get("effect_vi", "?"), inline=True)
        embed.add_field(name="Đan độc", value=str(result.dan_doc_delta), inline=True)
    embed.add_field(name="Chi phí", value=f"✨ −{result.cost_cong_duc:,}", inline=True)
    if result.furnace_key:
        furnace = registry.get_furnace(result.furnace_key)
        if furnace:
            tag = "✦ " if furnace.get("is_unique") else ""
            embed.add_field(name="🔥 Đan Lô", value=f"{tag}{furnace['vi']}", inline=True)
    consumed_text = ", ".join(
        f"{registry.get_item(c.key)['vi'] if registry.get_item(c.key) else c.key}×{c.qty}"
        for c in result.consumed
    )
    if consumed_text:
        embed.add_field(name="Nguyên liệu đã dùng", value=consumed_text, inline=False)
    return embed


# ── Pill bag ────────────────────────────────────────────────────────────────

def _pill_bag_embed(pills: list[tuple[str, int, int]]) -> discord.Embed:
    embed = base_embed(
        "💊 Đan Dược",
        "Chọn đan dược để sử dụng. Phẩm chất cao sẽ nâng cao hiệu quả và giảm đan độc tích lũy.",
        color=0x27AE60,
    )
    if not pills:
        embed.description += "\n\n*(Túi rỗng — hãy luyện vài viên đan trước.)*"
        return embed
    lines = []
    for key, grade, qty in pills[:20]:
        item = registry.get_item(key)
        name = item["vi"] if item else key
        quality = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}.get(grade, "?")
        lines.append(f"• **{name}** — {quality} Phẩm ×{qty}")
    embed.description += "\n\n" + "\n".join(lines)
    if len(pills) > 20:
        embed.set_footer(text=f"(+{len(pills) - 20} đan dược khác — hiển thị 20 hàng đầu)")
    return embed


class PillBagView(discord.ui.View):
    def __init__(self, discord_id: int, pills: list[tuple[str, int, int]], back_fn) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._back_fn = back_fn
        self.add_item(PillSelect(discord_id, pills, back_fn))

        back_btn = discord.ui.Button(label="◀ Về Luyện Đan", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not _guard(interaction, self._discord_id):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _nav_hub(interaction, self._discord_id, self._back_fn)


class PillSelect(discord.ui.Select):
    def __init__(self, discord_id: int, pills: list[tuple[str, int, int]], back_fn) -> None:
        self._discord_id = discord_id
        self._back_fn = back_fn

        options: list[discord.SelectOption] = []
        for key, grade, qty in pills[:25]:
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
            await interaction.response.defer()
            return
        await interaction.response.defer()
        key, grade_str = self.values[0].split("|")
        grade = int(grade_str)
        effect = await _do_consume(interaction, key, grade)

        embed = base_embed(
            "💊 Sử dụng Đan Dược",
            effect.message if effect.applied else (effect.message or "Thất bại."),
            color=0x27AE60 if effect.applied else 0xC0392B,
        )

        # Refresh pill bag for the back view
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            pills = await _pills_in_bag(player.id) if player else []
        view = PillBagView(self._discord_id, pills, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)


async def _do_consume(interaction: discord.Interaction, pill_key: str, grade: int) -> PillEffect:
    async with get_session() as session:
        player_repo = PlayerRepository(session)
        inv_repo = InventoryRepository(session)

        player = await player_repo.get_by_discord_id(interaction.user.id)
        if not player:
            return PillEffect(False, "Không tìm thấy nhân vật.")

        ok = await inv_repo.remove_item(player.id, pill_key, Grade(grade), 1)
        if not ok:
            return PillEffect(False, "Không có đan dược này trong túi.")

        char = _player_to_model(player)
        effect = consume_pill(char, pill_key, grade)

        # Persist effects back to player row
        player.dan_doc = max(0, int(player.dan_doc or 0) + effect.dan_doc_delta)
        if effect.qi_xp_delta:
            player.qi_xp = int(player.qi_xp or 0) + effect.qi_xp_delta
        if effect.body_xp_delta:
            player.body_xp = int(player.body_xp or 0) + effect.body_xp_delta
        if effect.heal_delta and player.hp_current > 0:
            player.hp_current = player.hp_current + effect.heal_delta

        await player_repo.save(player)

    return effect


# ── Herb bag ────────────────────────────────────────────────────────────────

def _herb_bag_embed(herbs: dict[str, int]) -> discord.Embed:
    embed = base_embed(
        "🌿 Thảo Dược & Nguyên Liệu",
        "Nguyên liệu luyện đan trong túi. Thảo dược rớt từ **Dược Viên**; "
        "nguyên liệu yêu thú rớt từ yêu thú tại **Bí Cảnh Thường**.",
        color=0x16A085,
    )
    if not herbs:
        embed.description += "\n\n*(Túi rỗng.)*"
        return embed

    # Group by type then grade
    herb_lines_by_grade: dict[int, list[str]] = {}
    yeu_lines_by_grade: dict[int, list[str]] = {}
    for key, qty in sorted(herbs.items()):
        item = registry.get_item(key)
        if not item:
            continue
        grade = int(item.get("grade", 1))
        line = f"• {item['vi']} ×{qty}"
        if item.get("type") == "herb":
            herb_lines_by_grade.setdefault(grade, []).append(line)
        else:
            yeu_lines_by_grade.setdefault(grade, []).append(line)

    for grade in sorted(herb_lines_by_grade):
        embed.add_field(
            name=f"🌿 Thảo Dược phẩm {grade}",
            value="\n".join(herb_lines_by_grade[grade][:20]),
            inline=False,
        )
    for grade in sorted(yeu_lines_by_grade):
        embed.add_field(
            name=f"🩸 Nguyên liệu yêu thú phẩm {grade}",
            value="\n".join(yeu_lines_by_grade[grade][:20]),
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
            await interaction.response.defer()
            await back_cb(interaction)

        btn.callback = _cb
        self.add_item(btn)


# ── Cog ─────────────────────────────────────────────────────────────────────

class AlchemyCog(commands.Cog, name="Alchemy"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="luyendan", description="Luyện đan — chế đan từ thảo dược")
    async def luyendan(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
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
