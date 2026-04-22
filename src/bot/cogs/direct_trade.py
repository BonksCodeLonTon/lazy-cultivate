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
from src.game.constants.grades import Grade
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.data.registry import registry

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


# ── Modals ────────────────────────────────────────────────────────────────────

class _AddInvModal(discord.ui.Modal, title="Thêm Vật Phẩm Vào Giao Dịch"):
    item_key_input = discord.ui.TextInput(
        label="Key vật phẩm (vd: GemKim_1)",
        placeholder="Dùng /inventory để xem key",
        max_length=64,
    )
    qty_input = discord.ui.TextInput(
        label="Số lượng", placeholder="1", max_length=6, default="1",
    )

    def __init__(self, session: _TradeSession, discord_id: int, message: discord.Message) -> None:
        super().__init__()
        self._session = session
        self._discord_id = discord_id
        self._message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        item_key = self.item_key_input.value.strip()
        qty_raw = self.qty_input.value.strip()
        if not qty_raw.isdigit() or int(qty_raw) < 1:
            await interaction.response.send_message(embed=error_embed("Số lượng phải ≥ 1."), ephemeral=True)
            return

        qty = int(qty_raw)
        item_data = registry.get_item(item_key)
        if not item_data:
            await interaction.response.send_message(embed=error_embed(f"Không tìm thấy vật phẩm `{item_key}`."), ephemeral=True)
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if not player:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            irepo = InventoryRepository(session)
            grade = Grade(item_data.get("grade", 1))
            if not await irepo.has_item(player.id, item_key, grade, qty):
                await interaction.response.send_message(
                    embed=error_embed(f"Không đủ **{item_data['vi']}** trong túi (cần {qty})."),
                    ephemeral=True,
                )
                return

        items = self._session.items_for(self._discord_id)
        items.append(_TradeItem("inventory", item_key, grade.value, qty))
        self._session.initiator_confirmed = False
        self._session.target_confirmed = False

        await interaction.response.send_message(
            embed=success_embed(f"Đã thêm **{item_data['vi']} × {qty}** vào giao dịch."),
            ephemeral=True,
        )
        await _refresh_trade_message(self._message, self._session)


class _AddEquipModal(discord.ui.Modal, title="Thêm Trang Bị Vào Giao Dịch"):
    instance_id_input = discord.ui.TextInput(
        label="ID trang bị (vd: 42)",
        placeholder="Dùng /bag để xem ID",
        max_length=10,
    )

    def __init__(self, session: _TradeSession, discord_id: int, message: discord.Message) -> None:
        super().__init__()
        self._session = session
        self._discord_id = discord_id
        self._message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.instance_id_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message(embed=error_embed("ID phải là số nguyên."), ephemeral=True)
            return

        instance_id = int(raw)
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(self._discord_id)
            if not player:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            erepo = EquipmentRepository(session)
            inst = await erepo.get_instance(instance_id, player.id)
            if not inst or inst.location != "bag":
                await interaction.response.send_message(
                    embed=error_embed(f"Trang bị ID `{instance_id}` không có trong túi đồ."),
                    ephemeral=True,
                )
                return
            display = inst.display_name
            grade = inst.grade

        # Check not already in trade
        items = self._session.items_for(self._discord_id)
        if any(i.instance_id == instance_id for i in items):
            await interaction.response.send_message(embed=error_embed("Trang bị này đã có trong giao dịch."), ephemeral=True)
            return

        items.append(_TradeItem("equipment", display, grade, 1, instance_id))
        self._session.initiator_confirmed = False
        self._session.target_confirmed = False

        await interaction.response.send_message(
            embed=success_embed(f"Đã thêm trang bị **{display}** vào giao dịch."),
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
        modal = _AddInvModal(self._session, interaction.user.id, interaction.message)
        await interaction.response.send_modal(modal)

    async def _add_eq_cb(self, interaction: discord.Interaction) -> None:
        if not self._session.has_player(interaction.user.id):
            await interaction.response.send_message("Bạn không thuộc giao dịch này.", ephemeral=True)
            return
        modal = _AddEquipModal(self._session, interaction.user.id, interaction.message)
        await interaction.response.send_modal(modal)

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
            await interaction.response.defer()
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
