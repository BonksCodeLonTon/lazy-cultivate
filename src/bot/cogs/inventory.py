"""Inventory commands."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.skill import CharacterSkill, MAX_SKILL_SLOTS
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.formation_repo import FormationRepository
from src.game.constants.grades import Grade
from src.game.systems.chest import open_chest
from src.game.systems.inventory import (
    apply_elixir, scroll_skill_type, skill_tier_from_mp,
)
from src.utils import emojis
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.pagination import add_page_controls, page_slice, total_pages

log = logging.getLogger(__name__)

SLOT_VI: dict[str, str] = {
    "weapon":   "Vũ Khí",
    "off_hand": "Phụ Thủ",
    "armor":    "Giáp",
    "helmet":   "Mũ Giáp",
    "glove":    "Găng Tay",
    "belt":     "Đai Lưng",
    "boot":     "Giày",
    "ring":     "Nhẫn",
    "amulet":   "Bùa Hộ Mệnh",
}
QUALITY_LABEL: dict[int, str] = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}

_CATEGORIES: list[tuple[str, str]] = [
    ("forge_material", "Nguyên Liệu Rèn"),
    ("constitution_material", "Nguyên Liệu Thể Chất"),
    ("gem",            "Ngọc"),
    ("scroll",         "Ngọc Giản"),
    ("pill",           "Đan Dược"),
    ("chest",          "Rương"),
    ("special",        "Đặc Biệt"),
    ("equipment",      "Trang Bị"),
]


def _category_emoji(cat: str) -> str:
    return emojis.ITEM_TYPE_EMOJI.get(cat, "❓")


def _item_display(item_key: str, grade: int, quantity: int) -> str:
    item = registry.get_item(item_key)
    name = item["vi"] if item else item_key
    t_emoji = emojis.for_item(item) if item else "❓"
    g_emoji = emojis.for_grade(grade)
    return f"{t_emoji}{g_emoji} **{name}** × {quantity}"


def _build_hub_embed(inv_items: list, equip_bag: list) -> discord.Embed:
    embed = base_embed("🎒 Túi Đồ", "Chọn danh mục để xem chi tiết.", color=0x95A5A6)
    counts: dict[str, int] = {}
    for it in inv_items:
        item_data = registry.get_item(it.item_key)
        cat = item_data.get("type", "?") if item_data else "?"
        counts[cat] = counts.get(cat, 0) + 1
    lines = []
    for cat, label in _CATEGORIES:
        emoji = _category_emoji(cat)
        if cat == "equipment":
            lines.append(f"{emoji} **{label}**: {len(equip_bag)} món")
        else:
            lines.append(f"{emoji} **{label}**: {counts.get(cat, 0)} loại")
    embed.add_field(name="Tổng quan", value="\n".join(lines), inline=False)
    return embed


# Items per page in the category view. Each rendered line contains a
# custom emoji code (``<:name:12345>``) plus the item's display name —
# scroll names with full emoji codes can hit ~100 chars, so a page of 15
# stays well under Discord's 4096-char description cap and the 1024-char
# field cap if the listing has to fall back to a field.
CATEGORY_PAGE_SIZE = 15

# Discord embed description cap — we trim the rendered list defensively
# in case a future item spec produces unusually long names.
_DESC_LIMIT = 4000


def _build_category_embed(
    cat: str,
    label: str,
    emoji: str,
    inv_items: list,
    equip_bag: list,
    page: int = 0,
) -> discord.Embed:
    if cat == "equipment":
        return _build_equip_embed(equip_bag)
    filtered = [it for it in inv_items if (registry.get_item(it.item_key) or {}).get("type") == cat]
    embed = discord.Embed(title=f"🎒 Túi Đồ — {emoji} {label}", color=0x95A5A6)
    if not filtered:
        embed.description = "Không có vật phẩm."
        return embed
    sorted_items = sorted(filtered, key=lambda x: (x.grade, x.item_key))
    pages = total_pages(len(sorted_items), per_page=CATEGORY_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    visible = page_slice(sorted_items, page, per_page=CATEGORY_PAGE_SIZE)
    lines = [_item_display(it.item_key, it.grade, it.quantity) for it in visible]

    header = f"**Tổng: {len(filtered)} loại**"
    if pages > 1:
        header += f" · Trang {page + 1}/{pages}"
    body = "\n".join(lines) or "—"
    description = f"{header}\n\n{body}"
    if len(description) > _DESC_LIMIT:
        description = description[: _DESC_LIMIT - 3] + "..."
    embed.description = description
    return embed


# Item types eligible for the /discard slash command and the inventory-UI
# "🗑️ Vứt bỏ" button. Equipment is excluded because gear lives in a separate
# table and has its own salvage flow — we don't want a typo on a Thiên-grade
# weapon to permanently nuke it through this path.
DISCARDABLE_ITEM_TYPES: frozenset[str] = frozenset({
    "pill", "scroll", "special",
    "forge_material", "constitution_material",
    "gem", "chest", "material",
})


class CategoryView(discord.ui.View):
    """Single-category bag view with prev/next pagination + return to hub.

    Wraps a single category page from ``InventoryView`` so a player whose
    Ngọc Giản (or any other category) bag overflows 20 items can still
    page through the full list.
    """

    def __init__(
        self,
        discord_id: int,
        cat: str,
        label: str,
        emoji: str,
        inv_items: list,
        equip_bag: list,
        player_name: str,
        back_fn,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._cat = cat
        self._label = label
        self._emoji = emoji
        self._inv_items = inv_items
        self._equip_bag = equip_bag
        self._player_name = player_name
        self._back_fn = back_fn
        filtered = [
            it for it in inv_items
            if (registry.get_item(it.item_key) or {}).get("type") == cat
        ]
        pages = total_pages(len(filtered), per_page=CATEGORY_PAGE_SIZE)
        self._page = max(0, min(page, pages - 1))

        hub_btn = discord.ui.Button(
            label="📊 Tổng Quan", style=discord.ButtonStyle.primary, row=0,
        )
        hub_btn.callback = self._hub_cb
        self.add_item(hub_btn)

        # Discard control — only attached when the current category holds
        # disposable items (consumables, materials, gems, …). The handler
        # opens a Select listing the page's items so the user can pick which
        # stack to drop without having to type the item key by hand.
        if cat in DISCARDABLE_ITEM_TYPES and filtered:
            discard_btn = discord.ui.Button(
                label="🗑️ Vứt bỏ",
                style=discord.ButtonStyle.danger,
                row=0,
            )
            discard_btn.callback = self._discard_cb
            self.add_item(discard_btn)

        add_page_controls(
            self,
            page=self._page,
            total=len(filtered),
            on_change=self._on_page_change,
            per_page=CATEGORY_PAGE_SIZE,
            row=1,
        )

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        embed = _build_category_embed(
            self._cat, self._label, self._emoji,
            self._inv_items, self._equip_bag, page=new_page,
        )
        view = CategoryView(
            self._discord_id, self._cat, self._label, self._emoji,
            self._inv_items, self._equip_bag, self._player_name,
            self._back_fn, page=new_page,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _hub_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        embed = _build_hub_embed(self._inv_items, self._equip_bag)
        view = InventoryView(
            self._discord_id, self._inv_items, self._equip_bag,
            self._player_name, back_fn=self._back_fn,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _discard_cb(self, interaction: discord.Interaction) -> None:
        """Open a Select listing the visible items so the player can pick
        which stack to drop. The follow-up modal then asks for quantity
        and performs the atomic deletion.
        """
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message(
                "Đây không phải túi đồ của bạn.", ephemeral=True,
            )
            return

        filtered = [
            it for it in self._inv_items
            if (registry.get_item(it.item_key) or {}).get("type") == self._cat
        ]
        sorted_items = sorted(filtered, key=lambda x: (x.grade, x.item_key))
        visible = page_slice(sorted_items, self._page, per_page=CATEGORY_PAGE_SIZE)
        if not visible:
            await interaction.response.send_message(
                embed=error_embed("Không có vật phẩm nào trên trang để vứt."),
                ephemeral=True,
            )
            return

        view = DiscardSelectView(
            discord_id=self._discord_id,
            cat=self._cat,
            label=self._label,
            emoji=self._emoji,
            inv_items=self._inv_items,
            equip_bag=self._equip_bag,
            player_name=self._player_name,
            back_fn=self._back_fn,
            page=self._page,
            page_items=visible,
        )
        await interaction.response.send_message(
            embed=base_embed(
                title="🗑️ Chọn vật phẩm muốn vứt",
                description=(
                    "Chọn vật phẩm trong danh sách bên dưới. "
                    "Sau đó nhập số lượng cần vứt."
                ),
            ),
            view=view,
            ephemeral=True,
        )


class DiscardSelectView(discord.ui.View):
    """Ephemeral Select prompt: pick which stack to discard, then a modal
    asks for the quantity. After the modal submits the atomic remove, the
    user's main inventory view is refreshed inline.
    """

    def __init__(
        self,
        discord_id: int,
        cat: str,
        label: str,
        emoji: str,
        inv_items: list,
        equip_bag: list,
        player_name: str,
        back_fn,
        page: int,
        page_items: list,
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._cat = cat
        self._label = label
        self._emoji = emoji
        self._inv_items = inv_items
        self._equip_bag = equip_bag
        self._player_name = player_name
        self._back_fn = back_fn
        self._page = page

        options: list[discord.SelectOption] = []
        # ``payload`` per option encodes (item_key, grade, owned) so the
        # quantity modal can validate against the stack the user actually
        # picked rather than re-querying.
        self._payload: dict[str, tuple[str, int, int]] = {}
        for inst in page_items[:25]:
            item_def = registry.get_item(inst.item_key) or {}
            vi = item_def.get("vi", inst.item_key)
            qual = QUALITY_LABEL.get(inst.grade, str(inst.grade))
            value = f"{inst.item_key}|{inst.grade}"
            options.append(discord.SelectOption(
                label=f"[{qual}] {vi}"[:100],
                description=f"Hiện có: {inst.quantity:,}"[:100],
                value=value,
            ))
            self._payload[value] = (inst.item_key, inst.grade, int(inst.quantity))

        self._select = discord.ui.Select(
            placeholder="Chọn vật phẩm...",
            min_values=1, max_values=1,
            options=options,
            row=0,
        )
        self._select.callback = self._on_pick
        self.add_item(self._select)

    async def _on_pick(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return
        value = self._select.values[0]
        item_key, grade_int, owned = self._payload[value]
        item_def = registry.get_item(item_key) or {}
        vi = item_def.get("vi", item_key)
        modal = DiscardQuantityModal(
            discord_id=self._discord_id,
            cat=self._cat,
            label=self._label,
            emoji=self._emoji,
            inv_items=self._inv_items,
            equip_bag=self._equip_bag,
            player_name=self._player_name,
            back_fn=self._back_fn,
            page=self._page,
            item_key=item_key,
            grade=Grade(grade_int),
            item_vi=vi,
            owned=owned,
        )
        await interaction.response.send_modal(modal)


class DiscardQuantityModal(discord.ui.Modal, title="Vứt vật phẩm"):
    quantity = discord.ui.TextInput(
        label="Số lượng cần vứt",
        placeholder="Nhập số nguyên ≥ 1",
        required=True,
        max_length=12,
    )

    def __init__(
        self,
        discord_id: int,
        cat: str,
        label: str,
        emoji: str,
        inv_items: list,
        equip_bag: list,
        player_name: str,
        back_fn,
        page: int,
        item_key: str,
        grade: Grade,
        item_vi: str,
        owned: int,
    ) -> None:
        super().__init__()
        self._discord_id = discord_id
        self._cat = cat
        self._label = label
        self._emoji = emoji
        self._inv_items = inv_items
        self._equip_bag = equip_bag
        self._player_name = player_name
        self._back_fn = back_fn
        self._page = page
        self._item_key = item_key
        self._grade = grade
        self._item_vi = item_vi
        self._owned = owned
        self.quantity.placeholder = f"1 – {owned:,} (hiện có)"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message(
                "Đây không phải cửa sổ của bạn.", ephemeral=True,
            )
            return

        raw = (self.quantity.value or "").strip().replace(",", "").replace(".", "")
        if not raw.isdigit():
            await interaction.response.send_message(
                embed=error_embed("Vui lòng nhập số nguyên ≥ 1."), ephemeral=True,
            )
            return
        amount = int(raw)
        if amount < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True,
            )
            return
        if amount > self._owned:
            await interaction.response.send_message(
                embed=error_embed(
                    f"Bạn chỉ có **{self._owned:,}× {self._item_vi}** "
                    f"(yêu cầu {amount:,})."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            irepo = InventoryRepository(session)
            ok = await irepo.try_remove_item(
                player.id, self._item_key, self._grade, amount,
            )
            if not ok:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Không đủ **{self._item_vi}** (cần {amount:,})."
                    ),
                    ephemeral=True,
                )
                return
            # Reload inventory + equipment so the refreshed CategoryView
            # mirrors the deletion (and any other concurrent change since
            # the bag was first opened).
            fresh_inv = await irepo.get_all(player.id)
            fresh_equip = await EquipmentRepository(session).get_bag(player.id)

        await interaction.followup.send(
            embed=success_embed(
                f"🗑️ Đã vứt **{self._item_vi} × {amount:,}**."
            ),
            ephemeral=True,
        )

        # Repaint the category page on the original message so the stack
        # count updates immediately. Edit-by-token via ``followup`` isn't
        # straightforward across a modal boundary, so we open a fresh view
        # the next time the user re-opens the category. The success
        # followup above is the immediate feedback.
        try:
            embed = _build_category_embed(
                self._cat, self._label, self._emoji,
                fresh_inv, fresh_equip, page=self._page,
            )
            view = CategoryView(
                self._discord_id, self._cat, self._label, self._emoji,
                fresh_inv, fresh_equip, self._player_name,
                self._back_fn, page=self._page,
            )
            await interaction.edit_original_response(embed=embed, view=view)
        except discord.HTTPException:
            # Original message may have been dismissed already — non-fatal.
            pass


def _build_equip_embed(equip_bag: list) -> discord.Embed:
    embed = discord.Embed(title="🎒 Túi Đồ — 🗡️ Trang Bị", color=0x95A5A6)
    if not equip_bag:
        embed.description = "Không có trang bị trong túi."
        return embed
    by_slot: dict[str, list] = {}
    for inst in equip_bag:
        by_slot.setdefault(inst.slot, []).append(inst)
    for slot, insts in sorted(by_slot.items()):
        lines = [
            f"• [{QUALITY_LABEL.get(inst.grade, str(inst.grade))}] **{inst.display_name}** `ID:{inst.id}`"
            for inst in insts[:10]
        ]
        embed.add_field(name=SLOT_VI.get(slot, slot), value="\n".join(lines), inline=True)
    embed.set_footer(text=f"Tổng: {len(equip_bag)} trang bị trong túi")
    return embed


class InventoryView(discord.ui.View):
    def __init__(
        self,
        discord_id: int,
        inv_items: list,
        equip_bag: list,
        player_name: str = "",
        back_fn=None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id  = discord_id
        self._inv_items   = inv_items
        self._equip_bag   = equip_bag
        self._player_name = player_name
        self._back_fn     = back_fn

        for i, (cat, label) in enumerate(_CATEGORIES):
            emoji = _category_emoji(cat)
            # Discord parses custom emoji (``<:name:id>``) only inside the
            # ``emoji=`` arg of UI components, never inside the label string.
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                row=i // 4,
                emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
            )
            btn.callback = self._make_equipment_cb() if cat == "equipment" else self._make_cb(cat, label, emoji)
            self.add_item(btn)

        overview_btn = discord.ui.Button(label="📊 Tổng Quan", style=discord.ButtonStyle.primary, row=1)
        overview_btn.callback = self._overview_cb
        self.add_item(overview_btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _make_cb(self, cat: str, label: str, emoji: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()

            if cat == "chest":
                # Chest category swaps in an interactive open-chest hub —
                # reload from DB so the list reflects any changes since
                # ``/inventory`` was first opened (other commands may have
                # added or consumed chests in the meantime).
                async with get_session() as session:
                    player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
                    if player is None:
                        await interaction.edit_original_response(
                            embed=error_embed("Chưa có nhân vật."), view=None,
                        )
                        return
                    inv_items = await InventoryRepository(session).get_all(player.id)
                    equip_bag = await EquipmentRepository(session).get_bag(player.id)
                embed = _build_chest_embed(inv_items)
                view = ChestHubView(
                    self._discord_id, inv_items, equip_bag,
                    self._player_name, self._back_fn,
                )
                await interaction.edit_original_response(embed=embed, view=view)
                return

            embed = _build_category_embed(cat, label, emoji, self._inv_items, self._equip_bag)
            view = CategoryView(
                self._discord_id, cat, label, emoji,
                self._inv_items, self._equip_bag, self._player_name,
                self._back_fn,
            )
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    def _make_equipment_cb(self):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()

            from src.bot.cogs.equipment import EquipBagView, _equip_bag_embed

            # Reload fresh bag + equipped data so we always show current state.
            # The equip view now renders the currently-equipped piece per slot
            # alongside bag items, so both lists need to come from the DB.
            async with get_session() as session:
                from src.db.repositories.player_repo import PlayerRepository as PR
                player = await PR(session).get_by_discord_id(interaction.user.id)
                erepo = EquipmentRepository(session)
                equip_bag = await erepo.get_bag(player.id)
                equipped = await erepo.get_equipped(player.id)
                player_name = player.name if player else self._player_name

            async def back_to_inventory(inter: discord.Interaction) -> None:
                async with get_session() as s:
                    from src.db.repositories.player_repo import PlayerRepository as PR2
                    p = await PR2(s).get_by_discord_id(inter.user.id)
                    fresh_inv = await InventoryRepository(s).get_all(p.id)
                    fresh_equip = await EquipmentRepository(s).get_bag(p.id)
                embed = _build_hub_embed(fresh_inv, fresh_equip)
                view = InventoryView(
                    self._discord_id, fresh_inv, fresh_equip,
                    p.name, back_fn=self._back_fn,
                )
                await inter.edit_original_response(embed=embed, view=view)

            embed = _equip_bag_embed(player_name, equip_bag, equipped)
            view = EquipBagView(
                self._discord_id, player_name, equip_bag, equipped,
                back_fn=back_to_inventory,
            )
            await interaction.edit_original_response(embed=embed, view=view)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)

    async def _overview_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.edit_original_response(embed=_build_hub_embed(self._inv_items, self._equip_bag))


# ── Chest opening UI ──────────────────────────────────────────────────────────

# Cap on a single bulk-open click. Endgame players can hoard hundreds of
# chests; we still allow ~999 per click so they can clear a stockpile in
# a few presses, but the cap keeps the rolled-loot summary embed legible
# and bounds CPU per interaction.
_BULK_OPEN_HARD_CAP: int = 999


@dataclass
class _BulkOpenResult:
    """Aggregated outcome of opening N chests in a single transaction."""

    opened: int                                   # actually-opened count
    requested: int                                # what the user asked for
    error: str | None                             # reason for early stop
    aggregated_loot: dict[str, int] = field(default_factory=dict)
    # Equipment from world-boss realm chests — each entry already persisted
    # via EquipmentRepository.add_to_bag, so this is rendering data only.
    equipment_drops: list[dict] = field(default_factory=list)


def _build_chest_embed(
    inv_items: list,
    selected: tuple[str, int] | None = None,
) -> discord.Embed:
    """Render the chest-list embed with the active selection highlighted."""
    chests = [
        it for it in inv_items
        if (registry.get_item(it.item_key) or {}).get("type") == "chest"
    ]
    embed = discord.Embed(title="🎁 Rương", color=0xC0392B)
    if not chests:
        embed.description = (
            "Không có rương trong túi.\n\n"
            "Săn bí cảnh, đánh bại world boss, hoặc mua tại Phường Thị để có rương."
        )
        return embed
    chests.sort(key=lambda x: (x.grade, x.item_key))
    lines = []
    for it in chests:
        item = registry.get_item(it.item_key)
        name = item["vi"] if item else it.item_key
        marker = "▶" if selected == (it.item_key, it.grade) else "•"
        quality = QUALITY_LABEL.get(it.grade, str(it.grade))
        lines.append(f"{marker} **{name}** [{quality}] × {it.quantity}")
    desc = "\n".join(lines)
    if len(desc) > _DESC_LIMIT:
        desc = desc[: _DESC_LIMIT - 3] + "..."
    embed.description = desc
    if selected:
        sel_item = registry.get_item(selected[0])
        sel_name = sel_item["vi"] if sel_item else selected[0]
        embed.set_footer(text=f"Đã chọn: {sel_name} — bấm 'Mở' để mở rương.")
    else:
        embed.set_footer(text="Chọn rương từ dropdown để mở.")
    return embed


def _build_open_result_embed(
    chest_key: str,
    grade: int,
    result: _BulkOpenResult,
) -> discord.Embed:
    """Render the embed shown after a bulk open completes."""
    chest = registry.get_item(chest_key)
    name = chest["vi"] if chest else chest_key
    if result.opened == 0:
        return error_embed(result.error or "Không thể mở rương.")

    embed = discord.Embed(
        title=f"🎁 Mở **{name}** × {result.opened}",
        color=0xF1C40F,
    )
    if not result.aggregated_loot and not result.equipment_drops:
        embed.description = "Rương trống — không nhận được vật phẩm nào."
        return embed

    lines = []
    for item_key, qty in sorted(result.aggregated_loot.items()):
        item = registry.get_item(item_key)
        item_name = item["vi"] if item else item_key
        emoji = emojis.for_item(item) if item else "❓"
        lines.append(f"{emoji} **{item_name}** × {qty}")
    for eq in result.equipment_drops:
        eq_name = eq.get("display_name", "???")
        eq_grade = QUALITY_LABEL.get(eq.get("grade", 1), str(eq.get("grade", 1)))
        lines.append(f"⚔️ **{eq_name}** *(Phẩm {eq_grade})*")

    desc = "\n".join(lines)
    if len(desc) > _DESC_LIMIT:
        desc = desc[: _DESC_LIMIT - 3] + "..."
    embed.description = desc
    if result.error and result.opened < result.requested:
        embed.set_footer(text=f"⚠️ Dừng sau {result.opened} rương: {result.error}")
    return embed


async def _do_bulk_open_chest(
    interaction: discord.Interaction,
    chest_key: str,
    grade: int,
    quantity: int,
) -> _BulkOpenResult:
    """Open up to ``quantity`` chests, aggregate loot, persist atomically.

    Each ``open_chest`` call is a pure, in-memory roll — only the final
    chest decrement and loot ``add_item`` calls hit the DB, all inside a
    single ``get_session`` transaction. Stops early if a roll fails (for
    example, an unknown chest key) and reports how many were opened plus
    the cumulative loot in :class:`_BulkOpenResult`.
    """
    quantity = max(1, min(int(quantity), _BULK_OPEN_HARD_CAP))

    async with get_session() as session:
        prepo = PlayerRepository(session)
        irepo = InventoryRepository(session)
        eqrepo = EquipmentRepository(session)
        player = await prepo.get_by_discord_id(interaction.user.id)
        if player is None:
            return _BulkOpenResult(0, quantity, "Không tìm thấy nhân vật.")

        existing = await irepo.get_item(player.id, chest_key, Grade(grade))
        if existing is None or existing.quantity < 1:
            return _BulkOpenResult(0, quantity, "Không có rương này trong túi.")

        to_open = min(quantity, int(existing.quantity))

        merged: dict[str, int] = {}
        equipment_drops: list[dict] = []
        opened = 0
        last_err: str | None = None
        for _ in range(to_open):
            res = open_chest(chest_key)
            if not res.ok:
                last_err = res.message
                break
            for drop in res.loot:
                merged[drop["item_key"]] = (
                    merged.get(drop["item_key"], 0) + int(drop["quantity"])
                )
            equipment_drops.extend(res.equipment)
            opened += 1

        if opened == 0:
            return _BulkOpenResult(0, quantity, last_err or "Mở rương thất bại.")

        ok = await irepo.remove_item(player.id, chest_key, Grade(grade), opened)
        if not ok:
            return _BulkOpenResult(0, quantity, "Lỗi nội bộ: không thể trừ rương.")

        for eq in equipment_drops:
            await eqrepo.add_to_bag(player.id, eq)

        from src.game.engine.item_generator import generate_unique, is_unique_key

        inventory_merged: dict[str, int] = {}
        for item_key, qty in merged.items():
            if is_unique_key(item_key):
                for _ in range(max(1, int(qty))):
                    eq_data = generate_unique(item_key)
                    await eqrepo.add_to_bag(player.id, eq_data)
                    equipment_drops.append(eq_data)
                continue
            item_data = registry.get_item(item_key)
            grade_val = item_data.get("grade", 1) if item_data else 1
            await irepo.add_item(player.id, item_key, Grade(grade_val), qty)
            inventory_merged[item_key] = qty

    return _BulkOpenResult(opened, quantity, last_err, inventory_merged, equipment_drops)


class ChestSelect(discord.ui.Select):
    """Dropdown listing every owned chest stack.

    Picking a chest re-renders the parent ``ChestHubView`` with the
    selected key cached so the quantity buttons enable and the embed's
    "đã chọn" footer points at the right chest.
    """

    def __init__(
        self,
        discord_id: int,
        chests: list,
        view_owner: "ChestHubView",
    ) -> None:
        self._discord_id = discord_id
        self._view_owner = view_owner
        options: list[discord.SelectOption] = []
        for it in chests[:25]:
            item = registry.get_item(it.item_key)
            name = item["vi"] if item else it.item_key
            quality = QUALITY_LABEL.get(it.grade, str(it.grade))
            options.append(discord.SelectOption(
                label=f"{name} [{quality}] × {it.quantity}"[:100],
                value=f"{it.item_key}|{it.grade}",
                default=(view_owner._selected == (it.item_key, it.grade)),
            ))
        if not options:
            options = [discord.SelectOption(label="(không có rương)", value="__none__")]
        super().__init__(placeholder="Chọn rương để mở…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        if self.values[0] == "__none__":
            await interaction.response.defer()
            return
        await interaction.response.defer()
        key, grade_str = self.values[0].split("|")
        grade = int(grade_str)
        embed = _build_chest_embed(self._view_owner._inv_items, selected=(key, grade))
        view = ChestHubView(
            self._view_owner._discord_id,
            self._view_owner._inv_items,
            self._view_owner._equip_bag,
            self._view_owner._player_name,
            self._view_owner._back_fn,
            selected=(key, grade),
        )
        await interaction.edit_original_response(embed=embed, view=view)


class ChestQuantityModal(discord.ui.Modal, title="Số Lượng Mở Rương"):
    qty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Số lượng rương",
        placeholder="vd: 25",
        max_length=4,
    )

    def __init__(self, on_submit, max_qty: int) -> None:
        super().__init__()
        self._on_submit = on_submit
        self._max_qty = max_qty
        self.qty_input.placeholder = f"Tối đa: {min(max_qty, _BULK_OPEN_HARD_CAP)}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.qty_input.value.strip().replace(",", "")
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải là số nguyên dương."),
                ephemeral=True,
            )
            return
        qty = min(int(raw), self._max_qty, _BULK_OPEN_HARD_CAP)
        await self._on_submit(interaction, qty)


class ChestHubView(discord.ui.View):
    """Interactive chest-opening hub.

    Built with a chest-select dropdown plus quantity preset buttons (1 /
    5 / 10 / "tất cả") and a custom-amount modal. Each open routes
    through :func:`_do_bulk_open_chest`, which keeps the entire
    consume-and-grant cycle inside a single DB transaction.
    """

    def __init__(
        self,
        discord_id: int,
        inv_items: list,
        equip_bag: list,
        player_name: str,
        back_fn,
        selected: tuple[str, int] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._inv_items = inv_items
        self._equip_bag = equip_bag
        self._player_name = player_name
        self._back_fn = back_fn
        self._selected = selected

        chests = [
            it for it in inv_items
            if (registry.get_item(it.item_key) or {}).get("type") == "chest"
        ]
        chests.sort(key=lambda x: (x.grade, x.item_key))
        self._chests = chests

        if chests:
            self.add_item(ChestSelect(discord_id, chests, view_owner=self))

        owned = self._selected_owned()
        button_disabled = (selected is None) or (owned < 1)

        for qty in self._quantity_presets(owned):
            label = f"Mở {qty}" if qty < owned else f"Mở tất cả ({qty})"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                row=1,
                disabled=button_disabled,
            )
            btn.callback = self._make_open_cb(qty)
            self.add_item(btn)

        custom_btn = discord.ui.Button(
            label="Số khác…",
            style=discord.ButtonStyle.primary,
            row=2,
            disabled=button_disabled,
        )
        custom_btn.callback = self._open_modal
        self.add_item(custom_btn)

        hub_btn = discord.ui.Button(
            label="📊 Tổng Quan",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        hub_btn.callback = self._hub_cb
        self.add_item(hub_btn)

    def _selected_owned(self) -> int:
        if self._selected is None:
            return 0
        for it in self._chests:
            if (it.item_key, it.grade) == self._selected:
                return int(it.quantity)
        return 0

    def _quantity_presets(self, owned: int) -> list[int]:
        """Quantity buttons to display.

        Always shows 1 (disabled when nothing owned so the row stays
        consistent), 5 / 10 if affordable, and ``owned`` as the "all"
        option when it isn't already a preset. Capped to 4 buttons so
        the row stays under Discord's 5-component limit.
        """
        if owned <= 0:
            return [1]
        result: list[int] = []
        for q in (1, 5, 10):
            if q <= owned and q not in result:
                result.append(q)
        if owned not in result and len(result) < 4:
            result.append(owned)
        return result

    def _make_open_cb(self, quantity: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            await self._open_and_render(interaction, quantity)
        return _cb

    async def _open_modal(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        if self._selected is None:
            await interaction.response.send_message(
                embed=error_embed("Hãy chọn rương trước."), ephemeral=True,
            )
            return

        owned = self._selected_owned()

        async def _on_submit(modal_inter: discord.Interaction, qty: int) -> None:
            await modal_inter.response.defer()
            await self._open_and_render(modal_inter, qty)

        await interaction.response.send_modal(
            ChestQuantityModal(_on_submit, max_qty=owned)
        )

    async def _open_and_render(
        self, interaction: discord.Interaction, quantity: int
    ) -> None:
        if self._selected is None:
            return
        chest_key, grade = self._selected
        result = await _do_bulk_open_chest(interaction, chest_key, grade, quantity)

        # Refresh inventory so the next render shows updated stacks.
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            inv_items: list = []
            equip_bag: list = []
            if player is not None:
                inv_items = await InventoryRepository(session).get_all(player.id)
                equip_bag = await EquipmentRepository(session).get_bag(player.id)

        # Drop the selection if the chest stack ran out so the dropdown
        # default doesn't point at a phantom row.
        new_owned = sum(
            int(it.quantity) for it in inv_items
            if it.item_key == chest_key and it.grade == grade
        )
        new_selected = self._selected if new_owned > 0 else None

        embed = _build_open_result_embed(chest_key, grade, result)
        view = ChestHubView(
            self._discord_id, inv_items, equip_bag,
            self._player_name, self._back_fn,
            selected=new_selected,
        )
        await interaction.edit_original_response(embed=embed, view=view)

    async def _hub_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        async with get_session() as session:
            player = await PlayerRepository(session).get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(
                    embed=error_embed("Chưa có nhân vật."), view=None,
                )
                return
            inv_items = await InventoryRepository(session).get_all(player.id)
            equip_bag = await EquipmentRepository(session).get_bag(player.id)
        embed = _build_hub_embed(inv_items, equip_bag)
        view = InventoryView(
            self._discord_id, inv_items, equip_bag,
            self._player_name, back_fn=self._back_fn,
        )
        await interaction.edit_original_response(embed=embed, view=view)


class DiscardConfirmView(discord.ui.View):
    """Confirm/cancel prompt for ``/discard``. Disposable items only — gear
    has its own salvage flow elsewhere. Blocks input from anyone but the
    invoking user so a public ephemeral leak still couldn't be hijacked.
    """

    def __init__(
        self,
        discord_id: int,
        item_key: str,
        grade: Grade,
        quantity: int,
        item_vi: str,
    ) -> None:
        super().__init__(timeout=60)
        self._discord_id = discord_id
        self._item_key = item_key
        self._grade = grade
        self._quantity = quantity
        self._item_vi = item_vi

    @discord.ui.button(label="🗑️ Vứt bỏ", style=discord.ButtonStyle.danger)
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
            ok = await irepo.try_remove_item(
                player.id, self._item_key, self._grade, self._quantity,
            )

        if not ok:
            await interaction.response.edit_message(
                embed=error_embed(
                    f"Không đủ **{self._item_vi}** trong túi đồ "
                    f"(cần {self._quantity:,})."
                ),
                view=None,
            )
            return

        await interaction.response.edit_message(
            embed=success_embed(
                f"🗑️ Đã vứt **{self._item_vi} × {self._quantity:,}**."
            ),
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
            embed=base_embed(title="Đã huỷ", description="Không vật phẩm nào bị vứt."),
            view=None,
        )


class InventoryCog(commands.Cog, name="Inventory"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="inventory", description="Xem túi đồ")
    async def inventory(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            irepo      = InventoryRepository(session)
            equip_repo = EquipmentRepository(session)
            inv_items  = await irepo.get_all(player.id)
            equip_bag  = await equip_repo.get_bag(player.id)

        view  = InventoryView(interaction.user.id, inv_items, equip_bag, player.name)
        embed = _build_hub_embed(inv_items, equip_bag)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="use", description="Sử dụng đan dược / vật phẩm")
    @app_commands.describe(item_key="Key của vật phẩm (vd: DanHoiHPSmall)", quantity="Số lượng dùng")
    async def use(self, interaction: discord.Interaction, item_key: str, quantity: int = 1) -> None:
        if quantity < 1:
            await interaction.response.send_message(embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True)
            return

        item_data = registry.get_item(item_key)
        if not item_data:
            await interaction.response.send_message(
                embed=error_embed(f"Không tìm thấy vật phẩm `{item_key}`."), ephemeral=True
            )
            return

        # ``apply_elixir`` only knows about HP/MP/karma item-key substrings —
        # cultivation pills go through alchemy.consume_pill, not /use.
        # Gate on category=="elixir" (soft tag preserved across the type
        # merge) plus the legacy "special" bucket.
        is_legacy_elixir = (
            item_data.get("type") == "pill"
            and item_data.get("category") == "elixir"
        )
        if not (is_legacy_elixir or item_data.get("type") == "special"):
            await interaction.response.send_message(
                embed=error_embed("Chỉ có thể sử dụng Đan Dược hoặc vật phẩm đặc biệt."), ephemeral=True
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            irepo = InventoryRepository(session)
            grade = Grade(item_data.get("grade", 1))
            if not await irepo.has_item(player.id, item_key, grade, quantity):
                await interaction.response.send_message(
                    embed=error_embed(f"Không đủ **{item_data['vi']}** trong túi đồ."), ephemeral=True
                )
                return

            effects = apply_elixir(player, item_key, quantity)
            await irepo.remove_item(player.id, item_key, grade, quantity)
            await prepo.save(player)

        embed = success_embed(
            f"Sử dụng **{item_data['vi']} × {quantity}**\n" + "\n".join(effects)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="discard",
        description="Vứt bỏ vật phẩm không cần dùng (đan, ngọc giản, …)",
    )
    @app_commands.describe(
        item_key="Key của vật phẩm (vd: DanHoiHPSmall)",
        quantity="Số lượng cần vứt (≥1, mặc định 1)",
    )
    async def discard(
        self,
        interaction: discord.Interaction,
        item_key: str,
        quantity: int = 1,
    ) -> None:
        if quantity < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True,
            )
            return

        item_data = registry.get_item(item_key)
        if not item_data:
            await interaction.response.send_message(
                embed=error_embed(f"Không tìm thấy vật phẩm `{item_key}`."),
                ephemeral=True,
            )
            return

        # Equipment lives in a separate table and has its own salvage
        # flow; keep it out of this path so a typo on a Thiên-grade weapon
        # can't nuke gear. ``DISCARDABLE_ITEM_TYPES`` is the shared
        # whitelist used by the /discard slash command and the inventory-
        # UI "🗑️ Vứt bỏ" button — keep both in sync.
        item_type = item_data.get("type") or item_data.get("category", "")
        if item_type not in DISCARDABLE_ITEM_TYPES:
            await interaction.response.send_message(
                embed=error_embed(
                    "Chỉ có thể vứt đan dược / ngọc giản / nguyên liệu / "
                    "ngọc / rương / đặc biệt. Trang bị dùng hệ thống "
                    "phân giải riêng."
                ),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return

            irepo = InventoryRepository(session)
            grade = Grade(item_data.get("grade", 1))
            if not await irepo.has_item(player.id, item_key, grade, quantity):
                await interaction.response.send_message(
                    embed=error_embed(
                        f"Không đủ **{item_data['vi']}** trong túi đồ "
                        f"(cần {quantity:,})."
                    ),
                    ephemeral=True,
                )
                return

        # Two-step UX: show a confirmation prompt before any DB mutation so
        # a wrong quantity doesn't permanently destroy stack contents.
        view = DiscardConfirmView(
            discord_id=interaction.user.id,
            item_key=item_key,
            grade=grade,
            quantity=quantity,
            item_vi=item_data.get("vi", item_key),
        )
        embed = base_embed(
            title="🗑️ Xác nhận vứt vật phẩm",
            description=(
                f"Bạn có chắc muốn **vứt bỏ** "
                f"**{item_data.get('vi', item_key)} × {quantity:,}**?\n\n"
                f"_(Hành động này không thể hoàn tác.)_"
            ),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="formation", description="Đổi trận pháp đang dùng")
    @app_commands.describe(formation_key="Key trận pháp (vd: NhatNguyenKim)")
    async def formation(self, interaction: discord.Interaction, formation_key: str) -> None:
        form_data = registry.get_formation(formation_key)
        if not form_data:
            available = ", ".join(registry.formations.keys())
            await interaction.response.send_message(
                embed=error_embed(f"Trận pháp không hợp lệ.\nCác trận có sẵn: `{available}`"),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            frepo = FormationRepository(session)
            await frepo.get_or_create(player.id, formation_key)
            # Legacy CLI: single-formation assignment. The new formation hub
            # UI is where multi-slot Trận Tu manages multiple slots.
            player.active_formation = formation_key
            await prepo.save(player)

        embed = success_embed(
            f"Đã kích hoạt **{form_data['vi']}**!\n"
            f"Trận cũ sẽ bị khóa nhưng giữ nguyên tiến độ khi quay lại."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="inlay", description="Khảm ngọc vào trận pháp")
    @app_commands.describe(slot_index="Vị trí slot (0-9)", gem_key="Key ngọc (vd: GemKim_1)")
    async def inlay(self, interaction: discord.Interaction, slot_index: int, gem_key: str) -> None:
        from src.db.models.formation import FORMATION_GEM_SLOTS
        if not 0 <= slot_index < FORMATION_GEM_SLOTS:
            await interaction.response.send_message(
                embed=error_embed(f"Slot phải từ 0–{FORMATION_GEM_SLOTS - 1}."), ephemeral=True
            )
            return

        gem_data = registry.get_item(gem_key)
        if not gem_data or gem_data.get("type") != "gem":
            await interaction.response.send_message(
                embed=error_embed(f"`{gem_key}` không phải ngọc khảm."), ephemeral=True
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            from src.game.systems.cultivation import get_active_formations
            active_keys = get_active_formations(player.active_formation)
            if not active_keys:
                await interaction.response.send_message(
                    embed=error_embed("Chưa chọn trận pháp. Dùng `/formation <key>` trước."), ephemeral=True
                )
                return
            # CLI inlay targets the FIRST active slot. Multi-slot management
            # (pick which formation to inlay into) is in the formation hub UI.
            target_formation = active_keys[0]

            irepo = InventoryRepository(session)
            grade = Grade(gem_data.get("grade", 1))
            if not await irepo.has_item(player.id, gem_key, grade):
                await interaction.response.send_message(
                    embed=error_embed(f"Không có **{gem_data['vi']}** trong túi đồ."), ephemeral=True
                )
                return

            frepo = FormationRepository(session)
            formation = await frepo.inlay_gem(player.id, target_formation, slot_index, gem_key)
            await irepo.remove_item(player.id, gem_key, grade, 1)

        filled = len(formation.gem_slots)
        thresholds = [1, 3, 5, 7, 10]
        next_threshold = next((t for t in thresholds if t > filled), None)

        embed = success_embed(
            f"Khảm **{gem_data['vi']}** vào slot **{slot_index}** của trận pháp!\n"
            f"Tổng ngọc đã khảm: **{filled}/{FORMATION_GEM_SLOTS}**\n"
            + (f"Ngưỡng tiếp theo: **{next_threshold}** ngọc" if next_threshold else f"✨ Đã đạt **{FORMATION_GEM_SLOTS}/{FORMATION_GEM_SLOTS}** — tối đa!")
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="learn", description="Học kỹ năng từ Ngọc Giản")
    @app_commands.describe(
        scroll_key="Key Ngọc Giản (vd: Scroll_SkillAtkKim1)",
        skill_key="Key kỹ năng muốn học (vd: SkillAtkKim1)",
        slot="Slot trang bị (0–5)",
    )
    async def learn(
        self, interaction: discord.Interaction, scroll_key: str, skill_key: str, slot: int = -1
    ) -> None:
        if slot != -1 and not (0 <= slot < MAX_SKILL_SLOTS):
            await interaction.response.send_message(
                embed=error_embed(f"Slot phải từ 0–{MAX_SKILL_SLOTS - 1}."), ephemeral=True
            )
            return

        scroll_data = registry.get_item(scroll_key)
        if not scroll_data or scroll_data.get("type") != "scroll":
            await interaction.response.send_message(
                embed=error_embed(f"`{scroll_key}` không phải Ngọc Giản."), ephemeral=True
            )
            return

        skill_data = registry.get_skill(skill_key)
        if not skill_data:
            await interaction.response.send_message(
                embed=error_embed(f"Kỹ năng `{skill_key}` không tồn tại."), ephemeral=True
            )
            return

        # Validate scroll type vs skill category
        allowed_types = scroll_skill_type(scroll_key)
        if skill_data.get("category") not in allowed_types:
            await interaction.response.send_message(
                embed=error_embed(
                    f"Ngọc Giản **{scroll_data['vi']}** không phù hợp với kỹ năng **{skill_data['vi']}**.\n"
                    f"Loại kỹ năng được học: `{', '.join(allowed_types)}`"
                ),
                ephemeral=True,
            )
            return

        # Validate scroll grade vs skill tier
        scroll_grade = scroll_data.get("grade", 1)
        skill_tier = skill_tier_from_mp(skill_data)
        if scroll_grade < skill_tier:
            from src.game.constants.grades import GRADE_LABELS, Grade as G
            scroll_label = GRADE_LABELS.get(G(scroll_grade), (str(scroll_grade),))[0]
            await interaction.response.send_message(
                embed=error_embed(
                    f"Ngọc Giản **{scroll_label}** không đủ phẩm để học kỹ năng này.\n"
                    f"Cần phẩm **{skill_tier}** trở lên."
                ),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            # Validate Linh Căn element compatibility
            from src.game.constants.linh_can import parse_linh_can, LINH_CAN_DATA
            skill_element = skill_data.get("element")
            if skill_element is not None:
                player_linh_can = parse_linh_can(player.linh_can or "")
                if skill_element not in player_linh_can:
                    elem_vi = LINH_CAN_DATA.get(skill_element, {}).get("vi", skill_element)
                    elem_emoji = LINH_CAN_DATA.get(skill_element, {}).get("emoji", "")
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"Kỹ năng **{skill_data['vi']}** yêu cầu Linh Căn "
                            f"**{elem_emoji} {elem_vi}**.\n"
                            f"Linh Căn của bạn không phù hợp để học kỹ năng này."
                        ),
                        ephemeral=True,
                    )
                    return

            # Check scroll in inventory
            scroll_grade_enum = Grade(scroll_grade)
            irepo = InventoryRepository(session)
            if not await irepo.has_item(player.id, scroll_key, scroll_grade_enum):
                await interaction.response.send_message(
                    embed=error_embed(f"Không có **{scroll_data['vi']}** trong túi đồ."), ephemeral=True
                )
                return

            # Check if skill already learned
            existing = await session.execute(
                select(CharacterSkill).where(
                    CharacterSkill.player_id == player.id,
                    CharacterSkill.skill_key == skill_key,
                )
            )
            if existing.scalar_one_or_none():
                await interaction.response.send_message(
                    embed=error_embed(f"Đã học kỹ năng **{skill_data['vi']}** rồi."), ephemeral=True
                )
                return

            # Determine slot
            used_slots_result = await session.execute(
                select(CharacterSkill.slot_index).where(CharacterSkill.player_id == player.id)
            )
            used_slots = set(row[0] for row in used_slots_result.fetchall())

            # Formation skills: open-ended slot bar (≥ MAX_SKILL_SLOTS), capped
            # only by total MP reservation. Pre-check the cap before assigning.
            is_formation = skill_data.get("category") == "formation"
            if is_formation:
                from src.game.systems.skills import (
                    formation_reservation_would_exceed_cap, next_formation_slot,
                )
                exceeds, projected = formation_reservation_would_exceed_cap(player, skill_key)
                if exceeds:
                    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"Không đủ Linh Khí để Trấn Trận **{skill_data['vi']}**.\n"
                            f"Sau khi trang bị: **{projected * 100:.1f}%** MP bị trấn — "
                            f"vượt mức tối đa **{FORMATION_MAX_RESERVE_PCT * 100:.0f}%**."
                        ),
                        ephemeral=True,
                    )
                    return
                target_slot = next_formation_slot(player)
            elif slot == -1:
                free = next((i for i in range(MAX_SKILL_SLOTS) if i not in used_slots), None)
                if free is None:
                    await interaction.response.send_message(
                        embed=error_embed(f"Đã đầy {MAX_SKILL_SLOTS} slot kỹ năng. Chỉ định slot để ghi đè."),
                        ephemeral=True,
                    )
                    return
                target_slot = free
            else:
                target_slot = slot

            # If slot occupied, remove old skill
            if target_slot in used_slots:
                old = await session.execute(
                    select(CharacterSkill).where(
                        CharacterSkill.player_id == player.id,
                        CharacterSkill.slot_index == target_slot,
                    )
                )
                old_skill = old.scalar_one_or_none()
                if old_skill:
                    await session.delete(old_skill)
                    await session.flush()

            new_skill = CharacterSkill(
                player_id=player.id,
                skill_key=skill_key,
                slot_index=target_slot,
            )
            session.add(new_skill)
            await irepo.remove_item(player.id, scroll_key, scroll_grade_enum, 1)

        type_labels = {
            "attack":    "Công Kích",
            "defense":   "Phòng Thủ",
            "movement":  "Thân Pháp",
            "passive":   "Bị Động",
            "formation": "Trận Pháp",
        }
        type_label = type_labels.get(skill_data.get("category", ""), skill_data.get("category", ""))
        embed = success_embed(
            f"Học **{skill_data['vi']}** thành công!\n"
            f"Loại: **{type_label}** | Slot: **{target_slot}**\n"
            f"MP: **{skill_data.get('mp_cost', 0)}** | DMG: **{skill_data.get('base_dmg', 0)}** | CD: {skill_data.get('cooldown', 1)}t"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InventoryCog(bot))
