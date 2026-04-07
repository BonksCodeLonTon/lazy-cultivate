"""P2P marketplace commands."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.market import MarketListing
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.market_repo import MarketRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.currencies import MARKET_LISTING_HOURS, MARKET_MAX_LISTINGS, TRADE_FEE_RATE
from src.game.constants.grades import Grade, GRADE_CURRENCY, GRADE_LABELS
from src.utils.embed_builder import base_embed, error_embed, success_embed

GRADE_FILTER: dict[str, int] = {
    "hoang": 1, "huyen": 2, "dia": 3, "thien": 4,
}
GRADE_EMOJI = {1: "🟡", 2: "🟣", 3: "🟢", 4: "🔴"}
CURRENCY_EMOJI = {"merit": "✨", "primordial_stones": "💎"}


def _item_name(key: str) -> str:
    item = registry.get_item(key)
    return item["vi"] if item else key


def _grade_label(grade: int) -> str:
    label = GRADE_LABELS.get(Grade(grade))
    return label[0] if label else str(grade)


class TradeCog(commands.Cog, name="Trade"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /market ───────────────────────────────────────────────────────────
    @app_commands.command(name="market", description="Xem chợ P2P")
    @app_commands.describe(grade="Lọc theo phẩm: hoang / huyen / dia / thien")
    async def market(self, interaction: discord.Interaction, grade: str | None = None) -> None:
        grade_filter: Grade | None = None
        if grade:
            g = grade.lower().strip()
            if g not in GRADE_FILTER:
                await interaction.response.send_message(
                    embed=error_embed("Phẩm không hợp lệ. Chọn: hoang / huyen / dia / thien."),
                    ephemeral=True,
                )
                return
            grade_filter = Grade(GRADE_FILTER[g])

        async with get_session() as session:
            mrepo = MarketRepository(session)
            listings = await mrepo.browse(grade=grade_filter, limit=20)

        if not listings:
            desc = "Chợ P2P trống." if not grade else f"Không có hàng phẩm **{grade.upper()}**."
            await interaction.response.send_message(
                embed=base_embed("🏮 Chợ P2P", desc), ephemeral=True
            )
            return

        now = datetime.now(timezone.utc)
        embed = base_embed("🏮 Chợ P2P — Đấu Thương Các", color=0xE74C3C)
        for listing in listings:
            cur = listing.currency_type
            cur_emoji = CURRENCY_EMOJI.get(cur, "💰")
            grade_emoji = GRADE_EMOJI.get(listing.grade, "⚪")
            name = _item_name(listing.item_key)
            hours_left = max(0, int((listing.expires_at - now).total_seconds() / 3600))
            fee = int(listing.shop_ref_price * TRADE_FEE_RATE * listing.quantity)
            embed.add_field(
                name=f"`#{listing.id}` {grade_emoji} {name} × {listing.quantity}",
                value=(
                    f"{cur_emoji} **{listing.price:,}** + phí {fee:,}\n"
                    f"*Tổng: {listing.buyer_total():,} | Còn {hours_left}h*"
                ),
                inline=True,
            )

        embed.set_footer(text="Dùng /market_buy <id> để mua • /market_list để đăng bán")
        await interaction.response.send_message(embed=embed)

    # ── /market_list ──────────────────────────────────────────────────────
    @app_commands.command(name="market_list", description="Đăng bán vật phẩm trên chợ P2P")
    @app_commands.describe(
        item_key="Key vật phẩm (vd: GemKim_1)",
        quantity="Số lượng",
        price="Giá bán (không gồm phí)",
    )
    async def market_list(
        self,
        interaction: discord.Interaction,
        item_key: str,
        quantity: int,
        price: int,
    ) -> None:
        if quantity < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True
            )
            return
        if price < 1:
            await interaction.response.send_message(
                embed=error_embed("Giá phải ≥ 1."), ephemeral=True
            )
            return

        item_data = registry.get_item(item_key)
        if not item_data:
            await interaction.response.send_message(
                embed=error_embed(f"Không tìm thấy vật phẩm `{item_key}`."), ephemeral=True
            )
            return

        grade = Grade(item_data.get("grade", 1))
        currency = GRADE_CURRENCY[grade]

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True
                )
                return

            mrepo = MarketRepository(session)
            can_list, msg = await mrepo.can_create_listing(player.id)
            if not can_list:
                await interaction.response.send_message(
                    embed=error_embed(msg), ephemeral=True
                )
                return

            irepo = InventoryRepository(session)
            if not await irepo.has_item(player.id, item_key, grade, quantity):
                await interaction.response.send_message(
                    embed=error_embed(f"Không đủ **{item_data['vi']}** trong túi đồ."), ephemeral=True
                )
                return

            # Deduct from inventory and create listing
            shop_ref = item_data.get("shop_price_merit", price)
            expires = datetime.now(timezone.utc) + timedelta(hours=MARKET_LISTING_HOURS)
            listing = MarketListing(
                seller_id=player.id,
                item_key=item_key,
                grade=grade.value,
                quantity=quantity,
                price=price,
                shop_ref_price=shop_ref,
                currency_type=currency,
                expires_at=expires,
            )
            await irepo.remove_item(player.id, item_key, grade, quantity)
            await mrepo.create(listing)

        fee = int(shop_ref * TRADE_FEE_RATE * quantity)
        grade_emoji = GRADE_EMOJI.get(grade.value, "⚪")
        cur_emoji = CURRENCY_EMOJI.get(currency, "💰")
        embed = success_embed(
            f"Đăng bán **{item_data['vi']} × {quantity}** thành công!\n"
            f"{grade_emoji} Phẩm: **{_grade_label(grade.value)}** | "
            f"Tiền tệ: {cur_emoji} **{currency}**\n"
            f"Giá: **{price:,}** + Phí **{fee:,}** → Người mua trả **{price + fee:,}**\n"
            f"Hết hạn sau **{MARKET_LISTING_HOURS}h** | Tối đa {MARKET_MAX_LISTINGS} đơn"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /market_buy ───────────────────────────────────────────────────────
    @app_commands.command(name="market_buy", description="Mua vật phẩm từ chợ P2P")
    @app_commands.describe(listing_id="ID đơn hàng (từ /market)")
    async def market_buy(self, interaction: discord.Interaction, listing_id: int) -> None:
        async with get_session() as session:
            mrepo = MarketRepository(session)
            listing = await mrepo.get_by_id(listing_id)

            if listing is None:
                await interaction.response.send_message(
                    embed=error_embed(f"Không tìm thấy đơn hàng `#{listing_id}`."), ephemeral=True
                )
                return

            now = datetime.now(timezone.utc)
            if listing.is_expired(now):
                await interaction.response.send_message(
                    embed=error_embed("Đơn hàng này đã hết hạn."), ephemeral=True
                )
                return

            prepo = PlayerRepository(session)
            buyer = await prepo.get_by_discord_id(interaction.user.id)
            if buyer is None:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True
                )
                return

            if buyer.id == listing.seller_id:
                await interaction.response.send_message(
                    embed=error_embed("Không thể mua hàng của chính mình."), ephemeral=True
                )
                return

            total = listing.buyer_total()
            cur = listing.currency_type

            # Check buyer funds
            if cur == "merit":
                if buyer.merit < total:
                    await interaction.response.send_message(
                        embed=error_embed(f"Không đủ ✨ Công Đức. Cần **{total:,}**, có **{buyer.merit:,}**."),
                        ephemeral=True,
                    )
                    return
                buyer.merit -= total
            elif cur == "primordial_stones":
                if buyer.primordial_stones < total:
                    await interaction.response.send_message(
                        embed=error_embed(f"Không đủ 💎 Hỗn Nguyên Thạch. Cần **{total:,}**, có **{buyer.primordial_stones:,}**."),
                        ephemeral=True,
                    )
                    return
                buyer.primordial_stones -= total

            # Pay seller (price only — fee is burned)
            seller = await prepo.get_by_id(listing.seller_id)
            if seller:
                if cur == "merit":
                    seller.merit = min(seller.merit + listing.price, 10_000_000)
                elif cur == "primordial_stones":
                    seller.primordial_stones = min(seller.primordial_stones + listing.price, 10_000_000)
                await prepo.save(seller)

            # Give item to buyer
            grade = Grade(listing.grade)
            irepo = InventoryRepository(session)
            await irepo.add_item(buyer.id, listing.item_key, grade, listing.quantity)
            await prepo.save(buyer)
            await mrepo.delete(listing)

        item_name = _item_name(listing.item_key)
        fee = listing.buyer_total() - listing.price
        grade_emoji = GRADE_EMOJI.get(listing.grade, "⚪")
        cur_emoji = CURRENCY_EMOJI.get(listing.currency_type, "💰")
        embed = success_embed(
            f"Mua **{item_name} × {listing.quantity}** thành công!\n"
            f"{grade_emoji} Phẩm: **{_grade_label(listing.grade)}**\n"
            f"Đã thanh toán: {cur_emoji} **{listing.price:,}** + Phí **{fee:,}** = **{total:,}**"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /my_listings ──────────────────────────────────────────────────────
    @app_commands.command(name="my_listings", description="Xem đơn hàng đang niêm yết của bạn")
    async def my_listings(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True
                )
                return

            mrepo = MarketRepository(session)
            listings = await mrepo.get_active_by_seller(player.id)

        if not listings:
            await interaction.response.send_message(
                embed=base_embed("📋 Đơn Hàng Của Tôi", "Không có đơn hàng đang niêm yết."),
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        embed = base_embed("📋 Đơn Hàng Của Tôi", f"**{len(listings)}/{MARKET_MAX_LISTINGS}** đơn đang hoạt động", color=0x3498DB)
        for listing in listings:
            hours_left = max(0, int((listing.expires_at - now).total_seconds() / 3600))
            name = _item_name(listing.item_key)
            grade_emoji = GRADE_EMOJI.get(listing.grade, "⚪")
            cur_emoji = CURRENCY_EMOJI.get(listing.currency_type, "💰")
            embed.add_field(
                name=f"`#{listing.id}` {grade_emoji} {name} × {listing.quantity}",
                value=f"{cur_emoji} **{listing.price:,}** | Còn {hours_left}h",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradeCog(bot))
