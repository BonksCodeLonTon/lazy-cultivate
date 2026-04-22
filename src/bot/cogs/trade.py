"""P2P marketplace — interactive market UI."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.market import MarketListing
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.market_repo import MarketRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.currencies import MARKET_LISTING_HOURS, MARKET_MAX_LISTINGS, TRADE_FEE_RATE
from src.game.constants.grades import Grade, GRADE_LABELS
from src.game.engine.equipment import format_computed_stats
from src.utils.embed_builder import base_embed, error_embed, success_embed

_GRADE_EMOJI = {1: "⚪", 2: "🟢", 3: "🔵", 4: "🟡"}
_PAGE_SIZE = 5


def _item_name(key: str) -> str:
    item = registry.get_item(key)
    return item["vi"] if item else key


def _grade_label(grade: int) -> str:
    lbl = GRADE_LABELS.get(Grade(grade))
    return lbl[0] if lbl else str(grade)


def _fee(listing: MarketListing) -> int:
    return listing.buyer_total() - listing.price


# ── Embed builders ────────────────────────────────────────────────────────────

def _hub_embed() -> discord.Embed:
    embed = base_embed("🏮 Đấu Thương Các", color=0xE74C3C)
    fee_pct = int(TRADE_FEE_RATE * 100)
    embed.description = (
        "Chợ người chơi — mua bán vật phẩm & trang bị với ✨ Công Đức.\n"
        f"Thuế **{fee_pct}%** trên giá tham khảo | Hạn hàng **{MARKET_LISTING_HOURS}h** | Tối đa **{MARKET_MAX_LISTINGS}** đơn/người"
    )
    return embed


def _browse_embed(listings: list[MarketListing], page: int, total: int, viewer_id: int) -> discord.Embed:
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    embed = base_embed(
        f"🏮 Đấu Thương Các — Trang {page + 1}/{total_pages}",
        color=0xE74C3C,
    )
    if not listings:
        embed.description = "*Không có hàng nào.* Hãy đăng bán trước!"
        return embed

    now = datetime.now(timezone.utc)
    for i, listing in enumerate(listings, 1):
        hours_left = max(0, int((listing.expires_at - now).total_seconds() / 3600))
        fee = _fee(listing)
        grade_e = _GRADE_EMOJI.get(listing.grade, "⚪")

        if listing.listing_type == "equipment" and listing.instance_id:
            name = listing.item_key or "Trang Bị"
            line2 = f"Cấp: **{_grade_label(listing.grade)}**"
        else:
            name = f"{_item_name(listing.item_key or '')} × {listing.quantity}"
            line2 = f"Phẩm: **{_grade_label(listing.grade)}**"

        embed.add_field(
            name=f"`#{listing.id}` {grade_e} {name}",
            value=(
                f"✨ **{listing.price:,}** + phí {fee:,} = **{listing.buyer_total():,}**\n"
                f"{line2} | Còn {hours_left}h"
            ),
            inline=True,
        )
    embed.set_footer(text="Nhấn nút Buy #N để mua • /my_listings để quản lý đơn của bạn")
    return embed


def _my_listings_embed(listings: list[MarketListing]) -> discord.Embed:
    embed = base_embed(
        f"📋 Đơn Hàng Của Tôi ({len(listings)}/{MARKET_MAX_LISTINGS})",
        color=0x3498DB,
    )
    if not listings:
        embed.description = "Không có đơn hàng đang niêm yết."
        return embed

    now = datetime.now(timezone.utc)
    for listing in listings:
        hours_left = max(0, int((listing.expires_at - now).total_seconds() / 3600))
        grade_e = _GRADE_EMOJI.get(listing.grade, "⚪")
        if listing.listing_type == "equipment":
            name = listing.item_key or "Trang Bị"
        else:
            name = f"{_item_name(listing.item_key or '')} × {listing.quantity}"
        embed.add_field(
            name=f"`#{listing.id}` {grade_e} {name}",
            value=f"✨ **{listing.price:,}** | Còn {hours_left}h",
            inline=False,
        )
    return embed


# ── Modals ────────────────────────────────────────────────────────────────────

class PriceModal(discord.ui.Modal, title="Đặt Giá Niêm Yết"):
    price_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Giá (✨ Công Đức)",
        placeholder="Nhập số nguyên dương, vd: 50000",
        max_length=10,
    )

    def __init__(self, on_submit) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.price_input.value.strip().replace(",", "")
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Giá phải là số nguyên dương."), ephemeral=True
            )
            return
        await self._on_submit(interaction, int(raw))


class InvQtyPriceModal(discord.ui.Modal, title="Số Lượng & Giá Niêm Yết"):
    qty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Số lượng", placeholder="vd: 10", max_length=6,
    )
    price_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Giá tổng (✨ Công Đức)", placeholder="vd: 50000", max_length=10,
    )

    def __init__(self, on_submit) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        qty_raw = self.qty_input.value.strip()
        price_raw = self.price_input.value.strip().replace(",", "")
        if not qty_raw.isdigit() or int(qty_raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True
            )
            return
        if not price_raw.isdigit() or int(price_raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Giá phải là số nguyên dương."), ephemeral=True
            )
            return
        await self._on_submit(interaction, int(qty_raw), int(price_raw))


# ── Views ─────────────────────────────────────────────────────────────────────

class MarketHubView(discord.ui.View):
    def __init__(self, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._back_fn = back_fn

        configs = [
            ("🔍 Duyệt Chợ",   discord.ButtonStyle.primary,   self._browse_cb,      0),
            ("📤 Đăng Bán",     discord.ButtonStyle.success,   self._list_cb,        0),
            ("📋 Đơn Của Tôi",  discord.ButtonStyle.secondary, self._my_listings_cb, 0),
        ]
        for label, style, cb, row in configs:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = cb
            self.add_item(btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _browse_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        async with get_session() as session:
            mrepo = MarketRepository(session)
            listings = await mrepo.browse(limit=_PAGE_SIZE, offset=0)
            total = await mrepo.count_browse()
        embed = _browse_embed(listings, 0, total, self._discord_id)
        view = MarketBrowseView(self._discord_id, page=0, total=total, listings=listings, back_fn=self._hub_back)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _list_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            irepo = InventoryRepository(session)
            inv_items = await irepo.get_all(player.id)
            erepo = EquipmentRepository(session)
            bag_items = await erepo.get_bag(player.id)
            mrepo = MarketRepository(session)
            can_list, msg = await mrepo.can_create_listing(player.id)

        if not can_list:
            await interaction.edit_original_response(embed=error_embed(msg), view=MarketHubView(self._discord_id, self._back_fn))
            return

        embed = base_embed("📤 Đăng Bán — Chọn Loại Vật Phẩm", color=0x27AE60)
        embed.description = "Chọn **Vật Phẩm Thường** (từ túi đồ) hoặc **Trang Bị** (từ túi trang bị)."
        view = MarketListMenuView(self._discord_id, inv_items, bag_items, back_fn=self._hub_back)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _my_listings_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            mrepo = MarketRepository(session)
            listings = await mrepo.get_active_by_seller(player.id)

        embed = _my_listings_embed(listings)
        view = MarketMyListingsView(self._discord_id, listings, back_fn=self._hub_back)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _hub_back(self, interaction: discord.Interaction) -> None:
        embed = _hub_embed()
        await interaction.edit_original_response(embed=embed, view=MarketHubView(self._discord_id, self._back_fn))

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        if self._back_fn:
            await self._back_fn(interaction)


class MarketBrowseView(discord.ui.View):
    def __init__(
        self, discord_id: int, page: int, total: int,
        listings: list[MarketListing], back_fn=None,
    ) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._page = page
        self._total = total
        self._listings = listings
        self._back_fn = back_fn

        total_pages = max(1, math.ceil(total / _PAGE_SIZE))

        prev = discord.ui.Button(label="◀ Trước", style=discord.ButtonStyle.secondary,
                                 disabled=(page == 0), row=0)
        prev.callback = self._prev_cb
        self.add_item(prev)

        nxt = discord.ui.Button(label="Sau ▶", style=discord.ButtonStyle.secondary,
                                disabled=(page >= total_pages - 1), row=0)
        nxt.callback = self._next_cb
        self.add_item(nxt)

        for i, listing in enumerate(listings, 1):
            btn = discord.ui.Button(label=f"Buy #{i}", style=discord.ButtonStyle.success, row=1)
            btn.callback = self._make_buy_cb(listing.id)
            self.add_item(btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Chợ", style=discord.ButtonStyle.secondary, row=2)
            back.callback = self._back_cb
            self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _reload(self, interaction: discord.Interaction, page: int) -> None:
        async with get_session() as session:
            mrepo = MarketRepository(session)
            listings = await mrepo.browse(limit=_PAGE_SIZE, offset=page * _PAGE_SIZE)
            total = await mrepo.count_browse()
        embed = _browse_embed(listings, page, total, self._discord_id)
        view = MarketBrowseView(self._discord_id, page, total, listings, self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)

    async def _prev_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._reload(interaction, max(0, self._page - 1))

    async def _next_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        total_pages = max(1, math.ceil(self._total / _PAGE_SIZE))
        await self._reload(interaction, min(self._page + 1, total_pages - 1))

    def _make_buy_cb(self, listing_id: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            result_msg = await _execute_buy(interaction.user.id, listing_id)
            # Reload browse after purchase
            async with get_session() as session:
                mrepo = MarketRepository(session)
                listings = await mrepo.browse(limit=_PAGE_SIZE, offset=self._page * _PAGE_SIZE)
                total = await mrepo.count_browse()
            embed = _browse_embed(listings, self._page, total, self._discord_id)
            embed.description = result_msg
            view = MarketBrowseView(self._discord_id, self._page, total, listings, self._back_fn)
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        if self._back_fn:
            await self._back_fn(interaction)


class MarketListMenuView(discord.ui.View):
    def __init__(self, discord_id: int, inv_items: list, bag_items: list, back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._inv_items = inv_items
        self._bag_items = bag_items
        self._back_fn = back_fn

        inv_btn = discord.ui.Button(label="🎒 Vật Phẩm Thường", style=discord.ButtonStyle.primary, row=0)
        inv_btn.callback = self._inv_cb
        self.add_item(inv_btn)

        gear_btn = discord.ui.Button(label="⚔️ Trang Bị", style=discord.ButtonStyle.primary, row=0)
        gear_btn.callback = self._gear_cb
        self.add_item(gear_btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Chợ", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _inv_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not self._inv_items:
            await interaction.response.send_message(
                embed=error_embed("Túi đồ thường trống."), ephemeral=True
            )
            return
        options = [
            discord.SelectOption(
                label=f"{_item_name(i.item_key)} × {i.quantity}"[:100],
                description=f"Phẩm {_grade_label(i.grade)}"[:100],
                value=f"{i.item_key}|{i.grade}",
            )
            for i in self._inv_items[:25]
        ]
        embed = base_embed("🎒 Chọn Vật Phẩm Để Đăng Bán", color=0x27AE60)
        await interaction.response.edit_message(
            embed=embed,
            view=_InvSelectView(self._discord_id, options, self._back_fn),
        )

    async def _gear_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        bag = [i for i in self._bag_items if i.location == "bag"]
        if not bag:
            await interaction.response.send_message(
                embed=error_embed("Túi trang bị trống."), ephemeral=True
            )
            return
        options = [
            discord.SelectOption(
                label=f"[ID:{i.id}] {i.display_name}"[:100],
                description=(format_computed_stats(i.computed_stats) or "—")[:100],
                value=str(i.id),
            )
            for i in bag[:25]
        ]
        embed = base_embed("⚔️ Chọn Trang Bị Để Đăng Bán", color=0x27AE60)
        await interaction.response.edit_message(
            embed=embed,
            view=_GearSelectView(self._discord_id, options, bag, self._back_fn),
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        if self._back_fn:
            await self._back_fn(interaction)


class _InvSelectView(discord.ui.View):
    def __init__(self, discord_id: int, options: list, back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._selected_key: str | None = None
        self._selected_grade: int | None = None
        self._back_fn = back_fn

        sel = discord.ui.Select(
            placeholder="Chọn vật phẩm...", options=options, min_values=1, max_values=1, row=0
        )
        sel.callback = self._sel_cb
        self.add_item(sel)

        if back_fn:
            back = discord.ui.Button(label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _sel_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        val = interaction.data["values"][0]
        item_key, grade_str = val.split("|")
        self._selected_key = item_key
        self._selected_grade = int(grade_str)
        item_data = registry.get_item(item_key)
        item_name = item_data["vi"] if item_data else item_key

        async def _on_submit(inter: discord.Interaction, qty: int, price: int) -> None:
            await _do_list_inventory(inter, item_key, int(grade_str), qty, price, self._back_fn)

        await interaction.response.send_modal(InvQtyPriceModal(_on_submit))

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        if self._back_fn:
            await self._back_fn(interaction)


class _GearSelectView(discord.ui.View):
    def __init__(self, discord_id: int, options: list, bag_items: list, back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._bag_items = {str(i.id): i for i in bag_items}
        self._back_fn = back_fn

        sel = discord.ui.Select(
            placeholder="Chọn trang bị...", options=options, min_values=1, max_values=1, row=0
        )
        sel.callback = self._sel_cb
        self.add_item(sel)

        if back_fn:
            back = discord.ui.Button(label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _sel_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        instance_id = int(interaction.data["values"][0])
        inst = self._bag_items.get(str(instance_id))
        if not inst:
            await interaction.response.send_message(embed=error_embed("Không tìm thấy trang bị."), ephemeral=True)
            return

        async def _on_submit(inter: discord.Interaction, price: int) -> None:
            await _do_list_equipment(inter, instance_id, inst, price, self._back_fn)

        await interaction.response.send_modal(PriceModal(_on_submit))

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        if self._back_fn:
            await self._back_fn(interaction)


class MarketMyListingsView(discord.ui.View):
    def __init__(self, discord_id: int, listings: list[MarketListing], back_fn=None) -> None:
        super().__init__(timeout=300)
        self._discord_id = discord_id
        self._back_fn = back_fn

        for i, listing in enumerate(listings[:5]):
            lbl = f"Hủy #{listing.id}"
            btn = discord.ui.Button(label=lbl, style=discord.ButtonStyle.danger, row=i // 3)
            btn.callback = self._make_cancel_cb(listing.id)
            self.add_item(btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Chợ", style=discord.ButtonStyle.secondary, row=2)
            back.callback = self._back_cb
            self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    def _make_cancel_cb(self, listing_id: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            result_msg = await _execute_cancel(interaction.user.id, listing_id)

            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if not player:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return
                mrepo = MarketRepository(session)
                listings = await mrepo.get_active_by_seller(player.id)

            embed = _my_listings_embed(listings)
            embed.description = result_msg
            view = MarketMyListingsView(self._discord_id, listings, self._back_fn)
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        if self._back_fn:
            await self._back_fn(interaction)


# ── Business logic helpers ────────────────────────────────────────────────────

async def _reply(interaction: discord.Interaction, **kwargs) -> None:
    """Send a response or followup depending on whether interaction is already acknowledged."""
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def _do_list_inventory(
    interaction: discord.Interaction,
    item_key: str,
    grade_val: int,
    quantity: int,
    price: int,
    back_fn=None,
) -> None:
    grade = Grade(grade_val)
    item_data = registry.get_item(item_key)
    if not item_data:
        await _reply(interaction, embed=error_embed(f"Vật phẩm `{item_key}` không tồn tại."), ephemeral=True)
        return

    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(interaction.user.id)
        if not player:
            await _reply(interaction, embed=error_embed("Chưa có nhân vật."), ephemeral=True)
            return

        mrepo = MarketRepository(session)
        can_list, msg = await mrepo.can_create_listing(player.id)
        if not can_list:
            await _reply(interaction, embed=error_embed(msg), ephemeral=True)
            return

        irepo = InventoryRepository(session)
        if not await irepo.has_item(player.id, item_key, grade, quantity):
            await _reply(
                interaction,
                embed=error_embed(f"Không đủ **{item_data['vi']}** trong túi (cần {quantity})."),
                ephemeral=True,
            )
            return

        shop_ref = item_data.get("shop_price_merit", price)
        listing = MarketListing(
            seller_id=player.id,
            listing_type="inventory",
            item_key=item_key,
            grade=grade_val,
            quantity=quantity,
            price=price,
            shop_ref_price=shop_ref,
            currency_type="merit",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=MARKET_LISTING_HOURS),
        )
        await irepo.remove_item(player.id, item_key, grade, quantity)
        await mrepo.create(listing)

    fee = int(item_data.get("shop_price_merit", price) * TRADE_FEE_RATE * quantity)
    embed = success_embed(
        f"✅ Đã đăng bán **{item_data['vi']} × {quantity}**!\n"
        f"✨ Giá: **{price:,}** + Phí người mua: **{fee:,}** | Hết hạn sau {MARKET_LISTING_HOURS}h"
    )
    await _reply(interaction, embed=embed, ephemeral=True)


async def _do_list_equipment(
    interaction: discord.Interaction,
    instance_id: int,
    inst,
    price: int,
    back_fn=None,
) -> None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(interaction.user.id)
        if not player:
            await _reply(interaction, embed=error_embed("Chưa có nhân vật."), ephemeral=True)
            return

        mrepo = MarketRepository(session)
        can_list, msg = await mrepo.can_create_listing(player.id)
        if not can_list:
            await _reply(interaction, embed=error_embed(msg), ephemeral=True)
            return

        erepo = EquipmentRepository(session)
        real_inst = await erepo.get_instance(instance_id, player.id)
        if not real_inst or real_inst.location != "bag":
            await _reply(
                interaction,
                embed=error_embed("Trang bị không có trong túi đồ."), ephemeral=True
            )
            return

        # Lock item in market location
        real_inst.location = "market"
        await session.flush()

        listing = MarketListing(
            seller_id=player.id,
            listing_type="equipment",
            item_key=real_inst.display_name,
            grade=real_inst.grade,
            quantity=1,
            instance_id=instance_id,
            price=price,
            shop_ref_price=price,
            currency_type="merit",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=MARKET_LISTING_HOURS),
        )
        await mrepo.create(listing)

    fee = int(price * TRADE_FEE_RATE)
    embed = success_embed(
        f"✅ Đã đăng bán **{real_inst.display_name}**!\n"
        f"✨ Giá: **{price:,}** + Phí người mua: **{fee:,}** | Hết hạn sau {MARKET_LISTING_HOURS}h"
    )
    await _reply(interaction, embed=embed, ephemeral=True)


async def _execute_buy(discord_user_id: int, listing_id: int) -> str:
    """Execute a market purchase. Returns a result message string."""
    async with get_session() as session:
        mrepo = MarketRepository(session)
        listing = await mrepo.get_by_id(listing_id)
        if not listing:
            return "❌ Đơn hàng không tồn tại."
        if listing.is_expired(datetime.now(timezone.utc)):
            return "❌ Đơn hàng đã hết hạn."

        prepo = PlayerRepository(session)
        buyer = await prepo.get_by_discord_id(discord_user_id)
        if not buyer:
            return "❌ Chưa có nhân vật."
        if buyer.id == listing.seller_id:
            return "❌ Không thể mua hàng của chính mình."

        total = listing.buyer_total()
        if buyer.merit < total:
            return f"❌ Không đủ ✨ Công Đức. Cần **{total:,}**, có **{buyer.merit:,}**."

        buyer.merit -= total

        seller = await prepo.get_by_id(listing.seller_id)
        if seller:
            seller.merit = min(seller.merit + listing.price, 10_000_000)
            await prepo.save(seller)

        if listing.listing_type == "equipment" and listing.instance_id:
            erepo = EquipmentRepository(session)
            inst = await erepo.get_by_id(listing.instance_id)
            if inst:
                inst.player_id = buyer.id
                inst.location = "bag"
                await session.flush()
            item_name = listing.item_key or "Trang Bị"
        else:
            irepo = InventoryRepository(session)
            grade = Grade(listing.grade)
            await irepo.add_item(buyer.id, listing.item_key, grade, listing.quantity)
            item_name = f"{_item_name(listing.item_key or '')} × {listing.quantity}"

        await prepo.save(buyer)
        await mrepo.delete(listing)

    fee = listing.buyer_total() - listing.price
    return (
        f"✅ Đã mua **{item_name}**!\n"
        f"✨ Thanh toán: **{listing.price:,}** + Phí: **{fee:,}** = **{total:,}**"
    )


async def _execute_cancel(discord_user_id: int, listing_id: int) -> str:
    """Cancel a listing and return items to seller."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_user_id)
        if not player:
            return "❌ Chưa có nhân vật."

        mrepo = MarketRepository(session)
        listing = await mrepo.get_by_id(listing_id)
        if not listing or listing.seller_id != player.id:
            return "❌ Đơn hàng không tồn tại hoặc không thuộc về bạn."

        if listing.listing_type == "equipment" and listing.instance_id:
            erepo = EquipmentRepository(session)
            inst = await erepo.get_by_id(listing.instance_id)
            if inst:
                inst.location = "bag"
                await session.flush()
            item_name = listing.item_key or "Trang Bị"
        else:
            irepo = InventoryRepository(session)
            grade = Grade(listing.grade)
            await irepo.add_item(player.id, listing.item_key, grade, listing.quantity)
            item_name = f"{_item_name(listing.item_key or '')} × {listing.quantity}"

        await mrepo.delete(listing)

    return f"↩️ Đã hủy đơn và trả lại **{item_name}** vào túi đồ."


# ── Cog ───────────────────────────────────────────────────────────────────────

class TradeCog(commands.Cog, name="Trade"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="market", description="Mở chợ người chơi P2P")
    async def market(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = _hub_embed()
        view = MarketHubView(interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="my_listings", description="Xem đơn hàng đang niêm yết của bạn")
    async def my_listings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            mrepo = MarketRepository(session)
            listings = await mrepo.get_active_by_seller(player.id)

        embed = _my_listings_embed(listings)
        view = MarketMyListingsView(interaction.user.id, listings)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradeCog(bot))
