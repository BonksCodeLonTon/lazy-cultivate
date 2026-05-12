"""Direct P2P trade between two players (no fee)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.repositories.equipment_repo import EquipmentRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.grades import Grade, GRADE_LABELS
from src.game.engine.equipment import format_computed_stats
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.pagination import PAGE_SIZE, add_page_controls, page_slice, total_pages
from src.utils.discord_safe import safe_defer
from src.utils.search import SearchModal, matches
from src.data.registry import registry


def _registry_item_name(key: str) -> str:
    """Resolve a Vietnamese display name for an inventory item_key."""
    data = registry.get_item(key)
    return data["vi"] if data else key

_TRADE_TIMEOUT = 300  # seconds


@dataclass
class _TradeItem:
    kind: str          # "inventory" or "equipment"
    item_key: str      # for inventory: item_key; for equipment: display_name
    grade: int = 1
    quantity: int = 1
    instance_id: int | None = None


@dataclass
class _TradeSession:
    initiator_id: int
    target_id: int
    initiator_items: list[_TradeItem] = field(default_factory=list)
    target_items: list[_TradeItem] = field(default_factory=list)
    initiator_confirmed: bool = False
    target_confirmed: bool = False
    accepted: bool = False
    started_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() - self.started_at > _TRADE_TIMEOUT

    def has_player(self, discord_id: int) -> bool:
        return discord_id in (self.initiator_id, self.target_id)

    def is_initiator(self, discord_id: int) -> bool:
        return discord_id == self.initiator_id

    def items_for(self, discord_id: int) -> list[_TradeItem]:
        return self.initiator_items if self.is_initiator(discord_id) else self.target_items

    def set_confirmed(self, discord_id: int, val: bool) -> None:
        if self.is_initiator(discord_id):
            self.initiator_confirmed = val
        else:
            self.target_confirmed = val

    def both_confirmed(self) -> bool:
        return self.initiator_confirmed and self.target_confirmed


# key: (min_id, max_id) → session
_sessions: dict[tuple[int, int], _TradeSession] = {}


def _session_key(a: int, b: int) -> tuple[int, int]:
    return (min(a, b), max(a, b))


def _find_session(discord_id: int) -> _TradeSession | None:
    for key, sess in list(_sessions.items()):
        if sess.has_player(discord_id):
            if sess.is_expired():
                del _sessions[key]
                return None
            return sess
    return None


def _remove_session(sess: _TradeSession) -> None:
    key = _session_key(sess.initiator_id, sess.target_id)
    _sessions.pop(key, None)


# ── Pickers ───────────────────────────────────────────────────────────────────
# The previous flow used text-input modals where the player had to type the
# item key / equipment ID manually. Both keys and IDs were hidden in
# /inventory output and easy to mistype, so the flow now opens an ephemeral
# picker that lists the player's actual stock. Discord caps each Select at
# 25 options, so the pickers paginate via the shared ``add_page_controls``
# helper.


def _grade_label(grade: int) -> str:
    try:
        lbl = GRADE_LABELS.get(Grade(grade))
    except ValueError:
        return str(grade)
    return lbl[0] if lbl else str(grade)


class _QtyModal(discord.ui.Modal, title="Số Lượng"):
    qty_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Số lượng", placeholder="vd: 10", max_length=6, default="1",
    )

    def __init__(self, on_submit) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.qty_input.value.strip()
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message(
                embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True
            )
            return
        await self._on_submit(interaction, int(raw))


class _AddInvSelectView(discord.ui.View):
    """Paginated picker for inventory items to add to a direct trade.

    ``inv_items`` is the raw list of InventoryItem rows. The select
    encodes ``item_key|grade`` so we recover the exact bucket the player
    selected — important because the same item key can exist at multiple
    grade rows after the world-boss rework that stamps drops by boss tier.
    """

    def __init__(
        self, session: _TradeSession, discord_id: int,
        message: discord.Message, inv_items: list,
        page: int = 0, search: str = "",
    ) -> None:
        super().__init__(timeout=300)
        self._session = session
        self._discord_id = discord_id
        self._message = message
        self._inv_items = inv_items
        self._search = search
        self._filtered = [
            inv for inv in inv_items
            if matches(search, _registry_item_name(inv.item_key))
        ]
        pages = max(1, total_pages(len(self._filtered), per_page=PAGE_SIZE))
        self._page = max(0, min(page, pages - 1))
        self._build()

    def _build(self) -> None:
        visible = page_slice(self._filtered, self._page, per_page=PAGE_SIZE)
        if visible:
            options = []
            for inv in visible:
                name = _registry_item_name(inv.item_key)
                options.append(discord.SelectOption(
                    label=f"{name} × {inv.quantity}"[:100],
                    description=f"Phẩm {_grade_label(inv.grade)}"[:100],
                    value=f"{inv.item_key}|{inv.grade}",
                ))
        else:
            options = [discord.SelectOption(label="(không có kết quả)", value="__noop")]
        pages = total_pages(len(self._filtered), per_page=PAGE_SIZE)
        prefix = f"🔎 [{self._search[:20]}] " if self._search else ""
        placeholder = f"{prefix}Chọn vật phẩm..."
        if pages > 1:
            placeholder = f"{prefix}Chọn vật phẩm... (Trang {self._page + 1}/{pages})"

        sel = discord.ui.Select(
            placeholder=placeholder, options=options, min_values=1, max_values=1, row=0,
            disabled=not visible,
        )
        sel.callback = self._sel_cb
        self.add_item(sel)

        search_btn = discord.ui.Button(
            label=f"🔎 Tìm: {self._search[:18]}" if self._search else "🔎 Tìm Kiếm",
            style=discord.ButtonStyle.primary, row=1,
        )
        search_btn.callback = self._search_cb
        self.add_item(search_btn)
        if self._search:
            clear_btn = discord.ui.Button(
                label="✖ Xoá Lọc", style=discord.ButtonStyle.secondary, row=1,
            )
            clear_btn.callback = self._clear_cb
            self.add_item(clear_btn)

        add_page_controls(
            self, page=self._page, total=len(self._filtered),
            on_change=self._on_page_change, row=2,
        )

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        view = _AddInvSelectView(
            self._session, self._discord_id, self._message,
            self._inv_items, page=new_page, search=self._search,
        )
        await interaction.response.edit_message(view=view)

    async def _search_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return

        async def _on_query(inter: discord.Interaction, query: str) -> None:
            view = _AddInvSelectView(
                self._session, self._discord_id, self._message,
                self._inv_items, page=0, search=query,
            )
            await inter.response.edit_message(view=view)

        await interaction.response.send_modal(
            SearchModal("Tìm Vật Phẩm", _on_query, default=self._search)
        )

    async def _clear_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        view = _AddInvSelectView(
            self._session, self._discord_id, self._message,
            self._inv_items, page=0, search="",
        )
        await interaction.response.edit_message(view=view)

    async def _sel_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        val = interaction.data["values"][0]
        if val == "__noop":
            await interaction.response.defer()
            return
        item_key, grade_str = val.split("|")
        grade_val = int(grade_str)
        item_data = registry.get_item(item_key)
        item_name = item_data["vi"] if item_data else item_key

        session_obj = self._session
        message = self._message
        discord_id = self._discord_id

        async def _on_qty(inter: discord.Interaction, qty: int) -> None:
            async with get_session() as db_session:
                prepo = PlayerRepository(db_session)
                player = await prepo.get_by_discord_id(discord_id)
                if not player:
                    await inter.response.send_message(
                        embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                    )
                    return
                irepo = InventoryRepository(db_session)
                grade = Grade(grade_val)
                if not await irepo.has_item(player.id, item_key, grade, qty):
                    await inter.response.send_message(
                        embed=error_embed(
                            f"Không đủ **{item_name}** ({_grade_label(grade_val)}) "
                            f"trong túi (cần {qty})."
                        ),
                        ephemeral=True,
                    )
                    return

            items = session_obj.items_for(discord_id)
            items.append(_TradeItem("inventory", item_key, grade_val, qty))
            session_obj.initiator_confirmed = False
            session_obj.target_confirmed = False

            await inter.response.send_message(
                embed=success_embed(
                    f"Đã thêm **{item_name} × {qty}** "
                    f"({_grade_label(grade_val)}) vào giao dịch."
                ),
                ephemeral=True,
            )
            await _refresh_trade_message(message, session_obj)

        await interaction.response.send_modal(_QtyModal(_on_qty))


class _AddEquipSelectView(discord.ui.View):
    """Paginated picker for equipment instances to add to a direct trade."""

    def __init__(
        self, session: _TradeSession, discord_id: int,
        message: discord.Message, bag_items: list,
        page: int = 0, search: str = "",
    ) -> None:
        super().__init__(timeout=300)
        self._session = session
        self._discord_id = discord_id
        self._message = message
        self._bag_items = bag_items
        self._bag_lookup = {str(i.id): i for i in bag_items}
        self._search = search
        self._filtered = [
            i for i in bag_items if matches(search, i.display_name)
        ]
        pages = max(1, total_pages(len(self._filtered), per_page=PAGE_SIZE))
        self._page = max(0, min(page, pages - 1))
        self._build()

    def _build(self) -> None:
        visible = page_slice(self._filtered, self._page, per_page=PAGE_SIZE)
        if visible:
            options = [
                discord.SelectOption(
                    label=f"[ID:{i.id}] {i.display_name}"[:100],
                    description=(format_computed_stats(i.computed_stats) or "—")[:100],
                    value=str(i.id),
                )
                for i in visible
            ]
        else:
            options = [discord.SelectOption(label="(không có kết quả)", value="__noop")]
        pages = total_pages(len(self._filtered), per_page=PAGE_SIZE)
        prefix = f"🔎 [{self._search[:20]}] " if self._search else ""
        placeholder = f"{prefix}Chọn trang bị..."
        if pages > 1:
            placeholder = f"{prefix}Chọn trang bị... (Trang {self._page + 1}/{pages})"

        sel = discord.ui.Select(
            placeholder=placeholder, options=options, min_values=1, max_values=1, row=0,
            disabled=not visible,
        )
        sel.callback = self._sel_cb
        self.add_item(sel)

        search_btn = discord.ui.Button(
            label=f"🔎 Tìm: {self._search[:18]}" if self._search else "🔎 Tìm Kiếm",
            style=discord.ButtonStyle.primary, row=1,
        )
        search_btn.callback = self._search_cb
        self.add_item(search_btn)
        if self._search:
            clear_btn = discord.ui.Button(
                label="✖ Xoá Lọc", style=discord.ButtonStyle.secondary, row=1,
            )
            clear_btn.callback = self._clear_cb
            self.add_item(clear_btn)

        add_page_controls(
            self, page=self._page, total=len(self._filtered),
            on_change=self._on_page_change, row=2,
        )

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        view = _AddEquipSelectView(
            self._session, self._discord_id, self._message,
            self._bag_items, page=new_page, search=self._search,
        )
        await interaction.response.edit_message(view=view)

    async def _search_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return

        async def _on_query(inter: discord.Interaction, query: str) -> None:
            view = _AddEquipSelectView(
                self._session, self._discord_id, self._message,
                self._bag_items, page=0, search=query,
            )
            await inter.response.edit_message(view=view)

        await interaction.response.send_modal(
            SearchModal("Tìm Trang Bị", _on_query, default=self._search)
        )

    async def _clear_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        view = _AddEquipSelectView(
            self._session, self._discord_id, self._message,
            self._bag_items, page=0, search="",
        )
        await interaction.response.edit_message(view=view)

    async def _sel_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        val = interaction.data["values"][0]
        if val == "__noop":
            await interaction.response.defer()
            return
        instance_id = int(val)
        inst = self._bag_lookup.get(str(instance_id))
        if inst is None:
            await interaction.response.send_message(
                embed=error_embed("Không tìm thấy trang bị."), ephemeral=True,
            )
            return

        items = self._session.items_for(self._discord_id)
        if any(i.instance_id == instance_id for i in items):
            await interaction.response.send_message(
                embed=error_embed("Trang bị này đã có trong giao dịch."), ephemeral=True,
            )
            return

        # Re-validate against the DB — the bag snapshot was loaded when the
        # picker was opened and a parallel auction listing or trade could
        # have moved the instance out of the bag in the meantime.
        async with get_session() as db_session:
            prepo = PlayerRepository(db_session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if not player:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            erepo = EquipmentRepository(db_session)
            real_inst = await erepo.get_instance(instance_id, player.id)
            if not real_inst or real_inst.location != "bag":
                await interaction.response.send_message(
                    embed=error_embed("Trang bị này không còn trong túi đồ."), ephemeral=True,
                )
                return

        items.append(_TradeItem("equipment", inst.display_name, inst.grade, 1, instance_id))
        self._session.initiator_confirmed = False
        self._session.target_confirmed = False

        await interaction.response.send_message(
            embed=success_embed(
                f"Đã thêm trang bị **{inst.display_name}** vào giao dịch."
            ),
            ephemeral=True,
        )
        await _refresh_trade_message(self._message, self._session)


# ── Trade embed & view ────────────────────────────────────────────────────────

def _trade_embed(session: _TradeSession, initiator_name: str, target_name: str) -> discord.Embed:
    status = "⏳ Chờ Chấp Nhận" if not session.accepted else "🔄 Đang Giao Dịch"
    embed = base_embed(f"🤝 Giao Dịch Trực Tiếp — {status}", color=0x9B59B6)
    embed.description = (
        f"**{initiator_name}** ↔ **{target_name}**\n"
        "Không có phí • Cả hai cần xác nhận để hoàn tất"
    )

    def _fmt_items(items: list[_TradeItem]) -> str:
        if not items:
            return "*— Chưa có vật phẩm —*"
        lines = []
        for it in items:
            if it.kind == "equipment":
                lines.append(f"⚔️ **{it.item_key}** (ID: {it.instance_id})")
            else:
                item_data = registry.get_item(it.item_key)
                name = item_data["vi"] if item_data else it.item_key
                lines.append(f"🎒 **{name}** × {it.quantity}")
        return "\n".join(lines)

    i_confirm = "✅" if session.initiator_confirmed else "⬜"
    t_confirm = "✅" if session.target_confirmed else "⬜"

    embed.add_field(
        name=f"{i_confirm} {initiator_name}",
        value=_fmt_items(session.initiator_items),
        inline=True,
    )
    embed.add_field(
        name=f"{t_confirm} {target_name}",
        value=_fmt_items(session.target_items),
        inline=True,
    )
    if session.accepted:
        embed.set_footer(text="Thêm vật phẩm bằng nút Add • Nhấn Xác Nhận khi sẵn sàng")
    else:
        embed.set_footer(text=f"Chờ {target_name} chấp nhận giao dịch...")
    return embed


class TradeView(discord.ui.View):
    def __init__(
        self,
        session: _TradeSession,
        initiator_id: int,
        target_id: int,
        initiator_name: str,
        target_name: str,
    ) -> None:
        super().__init__(timeout=_TRADE_TIMEOUT)
        self._session = session
        self._initiator_id = initiator_id
        self._target_id = target_id
        self._initiator_name = initiator_name
        self._target_name = target_name

        if not session.accepted:
            accept_btn = discord.ui.Button(label="✅ Chấp Nhận", style=discord.ButtonStyle.success, row=0)
            accept_btn.callback = self._accept_cb
            self.add_item(accept_btn)

            decline_btn = discord.ui.Button(label="❌ Từ Chối", style=discord.ButtonStyle.danger, row=0)
            decline_btn.callback = self._cancel_cb
            self.add_item(decline_btn)
        else:
            add_inv = discord.ui.Button(label="🎒 Thêm Vật Phẩm", style=discord.ButtonStyle.primary, row=0)
            add_inv.callback = self._add_inv_cb
            self.add_item(add_inv)

            add_eq = discord.ui.Button(label="⚔️ Thêm Trang Bị", style=discord.ButtonStyle.primary, row=0)
            add_eq.callback = self._add_eq_cb
            self.add_item(add_eq)

            remove_btn = discord.ui.Button(label="🗑️ Xóa Tất Cả", style=discord.ButtonStyle.secondary, row=0)
            remove_btn.callback = self._remove_cb
            self.add_item(remove_btn)

            confirm_btn = discord.ui.Button(label="✅ Xác Nhận", style=discord.ButtonStyle.success, row=1)
            confirm_btn.callback = self._confirm_cb
            self.add_item(confirm_btn)

            cancel_btn = discord.ui.Button(label="❌ Hủy Giao Dịch", style=discord.ButtonStyle.danger, row=1)
            cancel_btn.callback = self._cancel_cb
            self.add_item(cancel_btn)

    async def _accept_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._target_id:
            await interaction.response.send_message("Chỉ người được mời mới có thể chấp nhận.", ephemeral=True)
            return
        self._session.accepted = True
        embed = _trade_embed(self._session, self._initiator_name, self._target_name)
        view = TradeView(self._session, self._initiator_id, self._target_id, self._initiator_name, self._target_name)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _add_inv_cb(self, interaction: discord.Interaction) -> None:
        if not self._session.has_player(interaction.user.id):
            await interaction.response.send_message("Bạn không thuộc giao dịch này.", ephemeral=True)
            return
        async with get_session() as db_session:
            prepo = PlayerRepository(db_session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            irepo = InventoryRepository(db_session)
            inv_items = await irepo.get_all(player.id)

        # Filter out zero-stock rows (defensive — repo cleans on remove,
        # but a stale cache or manual DB edit could leave one behind and
        # rendering a "× 0" option in the picker is just confusing).
        inv_items = [i for i in inv_items if i.quantity > 0]
        if not inv_items:
            await interaction.response.send_message(
                embed=error_embed("Túi đồ thường trống."), ephemeral=True,
            )
            return
        view = _AddInvSelectView(
            self._session, interaction.user.id, interaction.message, inv_items,
        )
        embed = base_embed("🎒 Chọn Vật Phẩm Để Trao", color=0x9B59B6)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _add_eq_cb(self, interaction: discord.Interaction) -> None:
        if not self._session.has_player(interaction.user.id):
            await interaction.response.send_message("Bạn không thuộc giao dịch này.", ephemeral=True)
            return
        async with get_session() as db_session:
            prepo = PlayerRepository(db_session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if not player:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            erepo = EquipmentRepository(db_session)
            bag_items = await erepo.get_bag(player.id)

        bag_items = [i for i in bag_items if i.location == "bag"]
        # Hide instances already added to this trade so a player can't
        # double-stage the same gear, and so the picker matches what's
        # actually still tradeable.
        already = {
            i.instance_id for i in self._session.items_for(interaction.user.id)
            if i.instance_id is not None
        }
        bag_items = [i for i in bag_items if i.id not in already]
        if not bag_items:
            await interaction.response.send_message(
                embed=error_embed("Không có trang bị nào trong túi (hoặc tất cả đã thêm vào giao dịch)."),
                ephemeral=True,
            )
            return
        view = _AddEquipSelectView(
            self._session, interaction.user.id, interaction.message, bag_items,
        )
        embed = base_embed("⚔️ Chọn Trang Bị Để Trao", color=0x9B59B6)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _remove_cb(self, interaction: discord.Interaction) -> None:
        if not self._session.has_player(interaction.user.id):
            await interaction.response.send_message("Bạn không thuộc giao dịch này.", ephemeral=True)
            return
        items = self._session.items_for(interaction.user.id)
        items.clear()
        self._session.initiator_confirmed = False
        self._session.target_confirmed = False
        await _refresh_trade_message(interaction.message, self._session)
        await interaction.response.send_message(embed=success_embed("Đã xóa tất cả vật phẩm của bạn."), ephemeral=True)

    async def _confirm_cb(self, interaction: discord.Interaction) -> None:
        if not self._session.has_player(interaction.user.id):
            await interaction.response.send_message("Bạn không thuộc giao dịch này.", ephemeral=True)
            return
        self._session.set_confirmed(interaction.user.id, True)

        if self._session.both_confirmed():
            if not await safe_defer(interaction):
                return
            result = await _execute_trade(self._session)
            _remove_session(self._session)
            embed = base_embed("🤝 Giao Dịch Hoàn Tất" if result is None else "❌ Giao Dịch Thất Bại", color=0x2ECC71 if result is None else 0xE74C3C)
            embed.description = result or "✅ Trao đổi thành công! Vật phẩm đã được chuyển."
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await _refresh_trade_message(interaction.message, self._session)
            name = self._initiator_name if self._session.is_initiator(interaction.user.id) else self._target_name
            await interaction.response.send_message(embed=success_embed(f"✅ **{name}** đã xác nhận. Chờ đối phương..."), ephemeral=True)

    async def _cancel_cb(self, interaction: discord.Interaction) -> None:
        if not self._session.has_player(interaction.user.id):
            await interaction.response.send_message("Bạn không thuộc giao dịch này.", ephemeral=True)
            return
        _remove_session(self._session)
        embed = base_embed("❌ Giao Dịch Bị Hủy", color=0xE74C3C)
        embed.description = f"Giao dịch đã bị hủy bởi {interaction.user.display_name}."
        await interaction.response.edit_message(embed=embed, view=None)


async def _refresh_trade_message(
    message: discord.Message, session: _TradeSession,
) -> None:
    try:
        initiator = message.guild.get_member(session.initiator_id)
        target = message.guild.get_member(session.target_id)
        i_name = initiator.display_name if initiator else str(session.initiator_id)
        t_name = target.display_name if target else str(session.target_id)
        embed = _trade_embed(session, i_name, t_name)
        view = TradeView(session, session.initiator_id, session.target_id, i_name, t_name)
        await message.edit(embed=embed, view=view)
    except Exception:
        pass


async def _execute_trade(session: _TradeSession) -> str | None:
    """Execute the trade. Returns an error string or None on success."""
    async with get_session() as db_session:
        prepo = PlayerRepository(db_session)
        initiator = await prepo.get_by_discord_id(session.initiator_id)
        target = await prepo.get_by_discord_id(session.target_id)
        if not initiator or not target:
            return "❌ Không tìm thấy người chơi."

        irepo = InventoryRepository(db_session)
        erepo = EquipmentRepository(db_session)

        # Validate all items still exist
        for item in session.initiator_items:
            if item.kind == "inventory":
                if not await irepo.has_item(initiator.id, item.item_key, Grade(item.grade), item.quantity):
                    return f"❌ **{initiator.name}** không đủ **{item.item_key}** × {item.quantity}."
            else:
                inst = await erepo.get_instance(item.instance_id, initiator.id)
                if not inst or inst.location != "bag":
                    return f"❌ **{initiator.name}** không có trang bị ID `{item.instance_id}` trong túi."

        for item in session.target_items:
            if item.kind == "inventory":
                if not await irepo.has_item(target.id, item.item_key, Grade(item.grade), item.quantity):
                    return f"❌ **{target.name}** không đủ **{item.item_key}** × {item.quantity}."
            else:
                inst = await erepo.get_instance(item.instance_id, target.id)
                if not inst or inst.location != "bag":
                    return f"❌ **{target.name}** không có trang bị ID `{item.instance_id}` trong túi."

        # Execute: initiator → target
        for item in session.initiator_items:
            if item.kind == "inventory":
                grade = Grade(item.grade)
                await irepo.remove_item(initiator.id, item.item_key, grade, item.quantity)
                await irepo.add_item(target.id, item.item_key, grade, item.quantity)
            else:
                inst = await erepo.get_instance(item.instance_id, initiator.id)
                inst.player_id = target.id
                await db_session.flush()

        # Execute: target → initiator
        for item in session.target_items:
            if item.kind == "inventory":
                grade = Grade(item.grade)
                await irepo.remove_item(target.id, item.item_key, grade, item.quantity)
                await irepo.add_item(initiator.id, item.item_key, grade, item.quantity)
            else:
                inst = await erepo.get_instance(item.instance_id, target.id)
                inst.player_id = initiator.id
                await db_session.flush()

    return None


# ── Cog ───────────────────────────────────────────────────────────────────────

class DirectTradeCog(commands.Cog, name="DirectTrade"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="trade", description="Giao dịch trực tiếp với người chơi khác (không phí)")
    @app_commands.describe(target="Người chơi muốn giao dịch")
    async def trade(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if target.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("Không thể giao dịch với chính mình."), ephemeral=True
            )
            return
        if target.bot:
            await interaction.response.send_message(
                embed=error_embed("Không thể giao dịch với bot."), ephemeral=True
            )
            return

        # Check if either player is in an active trade
        if _find_session(interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed("Bạn đang trong một giao dịch khác. Hủy trước khi tạo mới."),
                ephemeral=True,
            )
            return
        if _find_session(target.id):
            await interaction.response.send_message(
                embed=error_embed(f"**{target.display_name}** đang trong một giao dịch khác."),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            initiator_player = await prepo.get_by_discord_id(interaction.user.id)
            target_player = await prepo.get_by_discord_id(target.id)

        if not initiator_player:
            await interaction.response.send_message(embed=error_embed("Bạn chưa có nhân vật."), ephemeral=True)
            return
        if not target_player:
            await interaction.response.send_message(
                embed=error_embed(f"**{target.display_name}** chưa có nhân vật."), ephemeral=True
            )
            return

        sess = _TradeSession(initiator_id=interaction.user.id, target_id=target.id)
        key = _session_key(interaction.user.id, target.id)
        _sessions[key] = sess

        embed = _trade_embed(sess, interaction.user.display_name, target.display_name)
        view = TradeView(sess, interaction.user.id, target.id, interaction.user.display_name, target.display_name)

        await interaction.response.send_message(
            content=f"{target.mention} — **{interaction.user.display_name}** muốn giao dịch với bạn!",
            embed=embed,
            view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DirectTradeCog(bot))
