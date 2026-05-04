"""Shop commands — interactive UI (Đạo Thương & Quỷ Thị)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.game.constants.grades import Grade
from src.game.systems.economy import (
    get_fixed_shop, get_rotating_shop, get_dark_market, get_skill_scroll_shop,
    purchase, ShopSlot,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _rotating_seed() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 10000 + now.month * 100 + now.day + (now.hour // 6)


def _dark_seed() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 10000 + now.month * 100 + now.day + (now.hour // 4)


# ── Display helpers ───────────────────────────────────────────────────────────

def _item_name(item_key: str) -> str:
    item = registry.get_item(item_key)
    return item["vi"] if item else item_key


def _grade_emoji(grade: int) -> str:
    return {1: "🟢", 2: "🟣", 3: "🟡", 4: "🔴"}.get(grade, "⚪")


def _currency_emoji(currency: str) -> str:
    return {"merit": "✨", "karma_usable": "☯️", "primordial_stones": "💎"}.get(currency, "💰")


def _get_slots(section: str) -> list[ShopSlot]:
    if section == "fixed":
        return get_fixed_shop()
    if section == "rotating":
        return get_rotating_shop(_rotating_seed())
    if section == "tang_kinh_cac":
        return get_skill_scroll_shop()
    fixed_slot, rotating = get_dark_market(_dark_seed())
    return [fixed_slot] + rotating


def _shop_embed(section: str) -> discord.Embed:
    slots = _get_slots(section)
    cfg = {
        "fixed":    ("🏪 Đạo Thương — Gian Cố Định",          0xFFD700, None),
        "rotating": ("🔄 Đạo Thương — Gian Luân Chuyển",       0x9B59B6, "Đổi hàng mỗi 6 giờ"),
        "dark":     ("☯️ Quỷ Thị — Chợ Đen",                  0x2C2F33, "Đổi hàng mỗi 4 giờ • Dùng Nghiệp Lực Khả Dụng"),
    }
    title, color, footer = cfg[section]
    embed = base_embed(title, color=color)
    for i, slot in enumerate(slots):
        name = _item_name(slot.item_key)
        is_dark_fixed = (section == "dark" and i == 0)
        prefix = "🔒 " if is_dark_fixed else f"`{i + 1:02d}` "
        embed.add_field(
            name=f"{prefix}{_grade_emoji(slot.grade)} {name}",
            value=f"{_currency_emoji(slot.currency)} **{slot.price:,}**",
            inline=True,
        )
    if footer:
        embed.set_footer(text=footer)
    return embed


# ── Tàng Kinh Các tab — paginated, filterable scroll catalog ─────────────────
# The scroll catalog can hit 80+ entries (one per learnable grade-1/2 skill),
# so we can't render it with the flat ShopItemSelect (Discord's 25-option cap).
# Instead, this section uses the same filter+pagination pattern as /skilllist.

_TKC_PAGE_SIZE = 25  # matches Discord SelectMenu cap

_TKC_TYPE_BUTTONS = [
    (None,        "🌐 Tất Cả",   discord.ButtonStyle.secondary),
    ("attack",    "⚔️ Công",     discord.ButtonStyle.danger),
    ("defense",   "🛡️ Thủ",      discord.ButtonStyle.primary),
    ("movement",  "🏃 Thân",     discord.ButtonStyle.success),
    ("formation", "🌀 Trận",     discord.ButtonStyle.secondary),
]

_TKC_ELEM_OPTIONS = [
    ("",      "— Tất Cả Nguyên Tố —"),
    ("kim",   "🪙 Kim"),
    ("moc",   "🌿 Mộc"),
    ("thuy",  "💧 Thủy"),
    ("hoa",   "🔥 Hỏa"),
    ("tho",   "🪨 Thổ"),
    ("loi",   "⚡ Lôi"),
    ("phong", "🌬️ Phong"),
    ("am",    "🌑 Âm"),
    ("quang", "☀️ Quang"),
]

_TKC_TYPE_LABELS = {
    "attack": "Công", "defense": "Thủ", "movement": "Thân",
    "passive": "Bị Động", "formation": "Trận",
}


def _filtered_skill_scrolls(category: str | None, element: str | None) -> list[ShopSlot]:
    out: list[ShopSlot] = []
    for slot in get_skill_scroll_shop():
        skill_key = slot.item_key.removeprefix("Scroll_")
        skill = registry.get_skill(skill_key)
        if skill is None:
            continue
        if category and skill.get("category") != category:
            continue
        if element and skill.get("element") != element:
            continue
        out.append(slot)
    return out


def _build_tkc_embed_view(
    discord_id: int,
    category: str | None = None,
    element: str | None = None,
    page: int = 0,
    back_fn=None,
) -> tuple[discord.Embed, "TangKinhCacView"]:
    slots = _filtered_skill_scrolls(category, element)
    total = len(slots)
    total_pages = max(1, (total + _TKC_PAGE_SIZE - 1) // _TKC_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_slots = slots[page * _TKC_PAGE_SIZE: (page + 1) * _TKC_PAGE_SIZE]

    title = "🌌 Đạo Thương — Tàng Kinh Các"
    parts = []
    if category:
        parts.append(_TKC_TYPE_LABELS.get(category, category))
    if element:
        parts.append(next((l for v, l in _TKC_ELEM_OPTIONS[1:] if v == element), element))
    if parts:
        title += f" [{' · '.join(parts)}]"

    if not page_slots:
        embed = base_embed(title, "Không có ngọc giản phù hợp với bộ lọc.", color=0xFFD700)
    else:
        lines: list[str] = []
        for slot in page_slots:
            skill_key = slot.item_key.removeprefix("Scroll_")
            skill = registry.get_skill(skill_key) or {}
            scroll_item = registry.get_item(slot.item_key) or {}
            name = scroll_item.get("vi", slot.item_key)
            realm = skill.get("realm", "?")
            mp = skill.get("mp_cost", 0)
            dmg = skill.get("base_dmg", 0)
            cd = skill.get("cooldown", 1)
            lines.append(
                f"{_grade_emoji(slot.grade)} **{name}**\n"
                f"  Cảnh giới **{realm}** · MP **{mp}** · DMG **{dmg}** · CD **{cd}t** · ✨ **{slot.price:,}**"
            )
        embed = base_embed(title, "\n".join(lines), color=0xFFD700)

    embed.set_footer(text=f"Trang {page + 1}/{total_pages} • {total} ngọc giản • Chọn từ menu để mua")

    view = TangKinhCacView(
        discord_id=discord_id,
        category=category,
        element=element,
        page=page,
        total_pages=total_pages,
        page_slots=page_slots,
        back_fn=back_fn,
    )
    return embed, view


class TangKinhCacView(discord.ui.View):
    """Filterable, paginated scroll shop. Tabs match ShopView so users can move between sections."""

    def __init__(
        self,
        discord_id: int,
        category: str | None,
        element: str | None,
        page: int,
        total_pages: int,
        page_slots: list[ShopSlot],
        back_fn=None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._category = category
        self._element = element
        self._page = page
        self._total_pages = total_pages
        self._page_slots = page_slots
        self._back_fn = back_fn

        # Row 0: shop section tabs
        for tab_id, tab_label in [
            ("fixed",         "📚 Cố Định"),
            ("rotating",      "🔄 Luân Chuyển"),
            ("dark",          "☯️ Quỷ Thị"),
            ("tang_kinh_cac", "🌌 Tàng Kinh"),
        ]:
            style = discord.ButtonStyle.primary if tab_id == "tang_kinh_cac" else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=tab_label, style=style, row=0)
            btn.callback = self._make_tab_cb(tab_id)
            self.add_item(btn)

        # Row 1: type filter buttons (5 of them — fits one row)
        for cat, label, style in _TKC_TYPE_BUTTONS:
            active = discord.ButtonStyle.primary if cat == category else style
            btn = discord.ui.Button(label=label, style=active, row=1)
            btn.callback = self._make_type_cb(cat)
            self.add_item(btn)

        # Row 2: element filter Select (its own row — Selects fill the row)
        elem_select = discord.ui.Select(
            placeholder="🌍 Lọc nguyên tố…",
            options=[
                discord.SelectOption(
                    label=label,
                    value=val or "__all__",
                    default=(val == (element or "")),
                )
                for val, label in _TKC_ELEM_OPTIONS
            ],
            row=2,
        )
        elem_select.callback = self._element_cb
        self.add_item(elem_select)

        # Row 3: scroll picker Select (purchase entry)
        if page_slots:
            options = []
            for i, slot in enumerate(page_slots):
                skill_key = slot.item_key.removeprefix("Scroll_")
                skill = registry.get_skill(skill_key) or {}
                scroll_item = registry.get_item(slot.item_key) or {}
                name = scroll_item.get("vi", slot.item_key)[:100]
                desc = f"✨ {slot.price:,} • Cảnh giới {skill.get('realm', '?')}"[:100]
                options.append(discord.SelectOption(
                    label=name,
                    description=desc,
                    value=str(i),
                    emoji=_grade_emoji(slot.grade),
                ))
            buy_select = discord.ui.Select(
                placeholder="🛒 Chọn ngọc giản để mua…",
                options=options,
                row=3,
            )
            buy_select.callback = self._buy_cb
            self.add_item(buy_select)

        # Row 4: pagination + back
        prev_btn = discord.ui.Button(
            label="◀ Trước", style=discord.ButtonStyle.secondary,
            disabled=(page == 0), row=4,
        )
        prev_btn.callback = self._prev_cb
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Sau ▶", style=discord.ButtonStyle.secondary,
            disabled=(page >= total_pages - 1), row=4,
        )
        next_btn.callback = self._next_cb
        self.add_item(next_btn)

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=4)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    def _make_tab_cb(self, tab_id: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if tab_id == "tang_kinh_cac":
                embed, view = _build_tkc_embed_view(self._discord_id, back_fn=self._back_fn)
            else:
                embed = _shop_embed(tab_id)
                view = ShopView(tab_id, self._discord_id, self._back_fn)
            await interaction.response.edit_message(embed=embed, view=view)
        return _cb

    def _make_type_cb(self, cat: str | None):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            embed, view = _build_tkc_embed_view(
                self._discord_id, category=cat, element=self._element,
                page=0, back_fn=self._back_fn,
            )
            await interaction.response.edit_message(embed=embed, view=view)
        return _cb

    async def _element_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        raw = interaction.data["values"][0]
        new_elem = None if raw == "__all__" else raw
        embed, view = _build_tkc_embed_view(
            self._discord_id, category=self._category, element=new_elem,
            page=0, back_fn=self._back_fn,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed, view = _build_tkc_embed_view(
            self._discord_id, category=self._category, element=self._element,
            page=self._page - 1, back_fn=self._back_fn,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _next_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed, view = _build_tkc_embed_view(
            self._discord_id, category=self._category, element=self._element,
            page=self._page + 1, back_fn=self._back_fn,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)

    async def _buy_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        idx = int(interaction.data["values"][0])
        slot = self._page_slots[idx]
        embed = base_embed(
            f"🛒 {_grade_emoji(slot.grade)} {_item_name(slot.item_key)}",
            f"Giá: {_currency_emoji(slot.currency)} **{slot.price:,}** / vật phẩm\n"
            f"Chọn số lượng muốn mua:",
            color=0x57F287,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=ShopBuyView("tang_kinh_cac", slot, self._discord_id),
        )


# ── UI components ─────────────────────────────────────────────────────────────

class ShopItemSelect(discord.ui.Select):
    def __init__(self, section: str, discord_id: int) -> None:
        self._section = section
        self._discord_id = discord_id
        self._slots = _get_slots(section)

        options: list[discord.SelectOption] = []
        for i, slot in enumerate(self._slots[:25]):
            name = _item_name(slot.item_key)
            is_dark_fixed = (section == "dark" and i == 0)
            label = f"{'🔒 ' if is_dark_fixed else f'{i + 1}. '}{name}"[:100]
            options.append(discord.SelectOption(
                label=label,
                description=f"{_currency_emoji(slot.currency)} {slot.price:,}",
                value=str(i),
                emoji=_grade_emoji(slot.grade),
            ))

        super().__init__(placeholder="Chọn vật phẩm muốn mua...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        idx = int(self.values[0])
        slot = self._slots[idx]
        embed = base_embed(
            f"🛒 {_grade_emoji(slot.grade)} {_item_name(slot.item_key)}",
            f"Giá: {_currency_emoji(slot.currency)} **{slot.price:,}** / vật phẩm\n"
            f"Chọn số lượng muốn mua:",
            color=0x57F287,
        )
        await interaction.response.edit_message(embed=embed, view=ShopBuyView(self._section, slot, self._discord_id))


class ShopView(discord.ui.View):
    def __init__(self, section: str, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._back_fn = back_fn

        for tab_id, tab_label in [
            ("fixed",         "📚 Cố Định"),
            ("rotating",      "🔄 Luân Chuyển"),
            ("dark",          "☯️ Quỷ Thị"),
            ("tang_kinh_cac", "🌌 Tàng Kinh"),
        ]:
            style = discord.ButtonStyle.primary if tab_id == section else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=tab_label, style=style, row=0)
            btn.callback = self._make_tab_cb(tab_id)
            self.add_item(btn)

        self.add_item(ShopItemSelect(section, discord_id))

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
            back.callback = self._back_cb
            self.add_item(back)

    def _make_tab_cb(self, tab_id: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            if tab_id == "tang_kinh_cac":
                embed, view = _build_tkc_embed_view(self._discord_id, back_fn=self._back_fn)
            else:
                embed = _shop_embed(tab_id)
                view = ShopView(tab_id, self._discord_id, self._back_fn)
            await interaction.response.edit_message(embed=embed, view=view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class ShopBuyView(discord.ui.View):
    def __init__(self, section: str, slot: ShopSlot, discord_id: int) -> None:
        super().__init__(timeout=120)
        self._section = section
        self._slot = slot
        self._discord_id = discord_id

        for qty in (1, 5, 10):
            btn = discord.ui.Button(label=f"× {qty}", style=discord.ButtonStyle.success, row=0)
            btn.callback = self._make_buy_cb(qty)
            self.add_item(btn)

        back = discord.ui.Button(label="◀ Quay Lại", style=discord.ButtonStyle.secondary, row=0)
        back.callback = self._back_cb
        self.add_item(back)

    def _make_buy_cb(self, quantity: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()

            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                result = purchase(player, self._slot, quantity)
                if not result.ok:
                    await interaction.edit_original_response(embed=error_embed(result.message), view=None)
                    return

                irepo = InventoryRepository(session)
                await irepo.add_item(player.id, self._slot.item_key, Grade(self._slot.grade), quantity)
                await prepo.save(player)

            embed = success_embed(
                f"Mua **{_item_name(self._slot.item_key)} × {quantity}** thành công!\n"
                f"Tiêu: {_currency_emoji(self._slot.currency)} **{self._slot.price * quantity:,}**"
            )
            embed.title = "✅ Mua Thành Công"
            await interaction.edit_original_response(embed=embed, view=_ShopBackView(self._section, self._discord_id))
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed, view = _section_landing(self._section, self._discord_id)
        await interaction.response.edit_message(embed=embed, view=view)


class _ShopBackView(discord.ui.View):
    def __init__(self, section: str, discord_id: int) -> None:
        super().__init__(timeout=120)
        self._section = section
        self._discord_id = discord_id
        btn = discord.ui.Button(label="🏪 Tiếp Tục Mua", style=discord.ButtonStyle.primary, row=0)
        btn.callback = self._cb
        self.add_item(btn)

    async def _cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed, view = _section_landing(self._section, self._discord_id)
        await interaction.response.edit_message(embed=embed, view=view)


def _section_landing(section: str, discord_id: int) -> tuple[discord.Embed, discord.ui.View]:
    """Return the (embed, view) pair for a section's landing page."""
    if section == "tang_kinh_cac":
        return _build_tkc_embed_view(discord_id)
    return _shop_embed(section), ShopView(section, discord_id)


# ── Cog ───────────────────────────────────────────────────────────────────────

class ShopCog(commands.Cog, name="Shop"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="shop", description="Mở Đạo Thương & Quỷ Thị")
    async def shop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        async with get_session() as session:
            prepo = PlayerRepository(session)
            if not await prepo.exists(interaction.user.id):
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                    ephemeral=True,
                )
                return

        section = "fixed"
        await interaction.followup.send(
            embed=_shop_embed(section),
            view=ShopView(section, interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
