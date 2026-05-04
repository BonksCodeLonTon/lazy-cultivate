"""Inventory commands."""
from __future__ import annotations

import logging

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
from src.game.systems.inventory import (
    apply_elixir, scroll_skill_type, skill_tier_from_mp,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

GRADE_EMOJI = {1: "⚪", 2: "🟢", 3: "🔵", 4: "🟡"}

TYPE_EMOJI = {
    "forge_material": "🔨", "material": "🪨", "gem": "💠", "scroll": "📜",
    "chest": "📦", "elixir": "⚗️", "special": "⭐", "artifact": "🗡️",
}
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

_CATEGORIES: list[tuple[str, str, str]] = [
    ("🔨", "forge_material",  "Nguyên Liệu Rèn"),
    ("🪨", "material",  "Nguyên Liệu"),
    ("💠", "gem",       "Ngọc"),
    ("📜", "scroll",    "Ngọc Giản"),
    ("⚗️", "elixir",   "Đan Dược"),
    ("📦", "chest",     "Rương"),
    ("⭐", "special",   "Đặc Biệt"),
    ("🗡️", "equipment", "Trang Bị"),
]


def _item_display(item_key: str, grade: int, quantity: int) -> str:
    item = registry.get_item(item_key)
    name = item["vi"] if item else item_key
    t_emoji = TYPE_EMOJI.get(item.get("type", ""), "❓") if item else "❓"
    g_emoji = GRADE_EMOJI.get(grade, "⚪")
    return f"{t_emoji}{g_emoji} **{name}** × {quantity}"


def _build_hub_embed(inv_items: list, equip_bag: list) -> discord.Embed:
    embed = base_embed("🎒 Túi Đồ", "Chọn danh mục để xem chi tiết.", color=0x95A5A6)
    counts: dict[str, int] = {}
    for it in inv_items:
        item_data = registry.get_item(it.item_key)
        cat = item_data.get("type", "?") if item_data else "?"
        counts[cat] = counts.get(cat, 0) + 1
    lines = []
    for emoji, cat, label in _CATEGORIES:
        if cat == "equipment":
            lines.append(f"{emoji} **{label}**: {len(equip_bag)} món")
        else:
            lines.append(f"{emoji} **{label}**: {counts.get(cat, 0)} loại")
    embed.add_field(name="Tổng quan", value="\n".join(lines), inline=False)
    return embed


def _build_category_embed(cat: str, label: str, emoji: str, inv_items: list, equip_bag: list) -> discord.Embed:
    if cat == "equipment":
        return _build_equip_embed(equip_bag)
    filtered = [it for it in inv_items if (registry.get_item(it.item_key) or {}).get("type") == cat]
    embed = discord.Embed(title=f"🎒 Túi Đồ — {emoji} {label}", color=0x95A5A6)
    if not filtered:
        embed.description = "Không có vật phẩm."
        return embed
    lines = [_item_display(it.item_key, it.grade, it.quantity)
             for it in sorted(filtered, key=lambda x: (x.grade, x.item_key))]
    embed.add_field(name=f"Tổng: {len(filtered)} loại", value="\n".join(lines[:20]) or "—", inline=False)
    if len(lines) > 20:
        embed.set_footer(text=f"... và {len(lines) - 20} loại khác")
    return embed


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

        for i, (emoji, cat, label) in enumerate(_CATEGORIES):
            btn = discord.ui.Button(
                label=f"{emoji} {label}",
                style=discord.ButtonStyle.secondary,
                row=i // 4,
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
            await interaction.edit_original_response(
                embed=_build_category_embed(cat, label, emoji, self._inv_items, self._equip_bag)
            )
        return _cb

    def _make_equipment_cb(self):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải túi đồ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()

            from src.bot.cogs.equipment import EquipBagView, _equip_bag_embed

            # Reload fresh bag data so we always show current state
            async with get_session() as session:
                from src.db.repositories.player_repo import PlayerRepository as PR
                player = await PR(session).get_by_discord_id(interaction.user.id)
                equip_bag = await EquipmentRepository(session).get_bag(player.id)
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

            embed = _equip_bag_embed(player_name, equip_bag)
            view = EquipBagView(self._discord_id, player_name, equip_bag, back_fn=back_to_inventory)
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

        if item_data.get("type") not in ("elixir", "special"):
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
        scroll_key="Key Ngọc Giản (vd: ScrollAtkHoang)",
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
