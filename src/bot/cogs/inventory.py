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
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.formation_repo import FormationRepository
from src.game.constants.grades import Grade
from src.game.systems.cultivation import compute_hp_max, compute_mp_max
from src.utils.embed_builder import base_embed, error_embed, success_embed

log = logging.getLogger(__name__)

# Scroll key prefix → allowed skill types
_SCROLL_TYPE_MAP: dict[str, list[str]] = {
    "ScrollAtk": ["thien"],
    "ScrollDef": ["dia"],
    "ScrollSup": ["nhan"],
    "ScrollFrm": ["tran_phap"],
}

def _scroll_skill_type(scroll_key: str) -> list[str]:
    for prefix, types in _SCROLL_TYPE_MAP.items():
        if scroll_key.startswith(prefix):
            return types
    return []

def _skill_grade(skill_data: dict) -> int:
    """Infer skill grade from mp_cost for matching against scroll grade."""
    mp = skill_data.get("mp_cost", 0)
    if mp <= 15:
        return 1
    if mp <= 30:
        return 2
    if mp <= 60:
        return 3
    return 4

GRADE_EMOJI = {1: "🟡", 2: "🟣", 3: "🟢", 4: "🔴"}
TYPE_EMOJI = {
    "material": "🪨", "gem": "💠", "scroll": "📜",
    "chest": "📦", "elixir": "⚗️", "special": "⭐", "artifact": "🗡️",
}


def _item_display(item_key: str, grade: int, quantity: int) -> str:
    item = registry.get_item(item_key)
    name = item["vi"] if item else item_key
    t_emoji = TYPE_EMOJI.get(item.get("type", ""), "❓") if item else "❓"
    g_emoji = GRADE_EMOJI.get(grade, "⚪")
    return f"{t_emoji}{g_emoji} **{name}** × {quantity}"


class InventoryCog(commands.Cog, name="Inventory"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="inventory", description="Xem túi đồ")
    @app_commands.describe(filter_type="Lọc theo loại: material / gem / scroll / elixir / chest / special")
    async def inventory(self, interaction: discord.Interaction, filter_type: str | None = None) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True
                )
                return

            irepo = InventoryRepository(session)
            items = await irepo.get_all(player.id)

        if filter_type:
            items = [i for i in items if (registry.get_item(i.item_key) or {}).get("type") == filter_type]

        if not items:
            desc = "Túi đồ trống." if not filter_type else f"Không có vật phẩm loại **{filter_type}**."
            await interaction.response.send_message(
                embed=base_embed("🎒 Túi Đồ", desc), ephemeral=True
            )
            return

        # Group by type for display
        lines: list[str] = []
        for inv_item in sorted(items, key=lambda x: (x.grade, x.item_key)):
            lines.append(_item_display(inv_item.item_key, inv_item.grade, inv_item.quantity))

        # Paginate into embed fields (max 25 items per embed)
        embed = base_embed("🎒 Túi Đồ", f"Tổng: **{len(items)}** loại vật phẩm", color=0x95A5A6)
        chunk = lines[:20]
        embed.add_field(name="\u200b", value="\n".join(chunk) or "—", inline=False)
        if len(lines) > 20:
            embed.set_footer(text=f"... và {len(lines) - 20} loại khác. Dùng /inventory <loại> để lọc.")

        await interaction.response.send_message(embed=embed, ephemeral=True)

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

            effects = _apply_elixir(player, item_key, item_data, quantity)
            await irepo.remove_item(player.id, item_key, grade, quantity)
            await prepo.save(player)

        embed = success_embed(
            f"Sử dụng **{item_data['vi']} × {quantity}**\n" + "\n".join(effects)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="equip", description="Trang bị Pháp Bảo vào slot")
    @app_commands.describe(slot="Slot: sword / armor / artifact", item_key="Key Pháp Bảo")
    async def equip(self, interaction: discord.Interaction, slot: str, item_key: str) -> None:
        if slot not in ("sword", "armor", "artifact"):
            await interaction.response.send_message(
                embed=error_embed("Slot không hợp lệ. Chọn: sword / armor / artifact."), ephemeral=True
            )
            return

        item_data = registry.get_item(item_key)
        if not item_data or item_data.get("type") != "artifact":
            await interaction.response.send_message(
                embed=error_embed(f"`{item_key}` không phải Pháp Bảo."), ephemeral=True
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
            if not await irepo.has_item(player.id, item_key, grade):
                await interaction.response.send_message(
                    embed=error_embed(f"Không có **{item_data['vi']}** trong túi đồ."), ephemeral=True
                )
                return

            # Upsert artifact slot
            from src.db.models.artifact import CharacterArtifact
            from sqlalchemy import select
            result = await session.execute(
                select(CharacterArtifact).where(
                    CharacterArtifact.player_id == player.id,
                    CharacterArtifact.slot == slot,
                )
            )
            artifact_row = result.scalar_one_or_none()
            if artifact_row:
                artifact_row.artifact_key = item_key
            else:
                new_art = CharacterArtifact(player_id=player.id, slot=slot, artifact_key=item_key)
                session.add(new_art)

        embed = success_embed(f"Đã trang bị **{item_data['vi']}** vào slot **{slot}**.")
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
            player.active_formation = formation_key
            await prepo.save(player)

        embed = success_embed(
            f"Đã kích hoạt **{form_data['vi']}**!\n"
            f"Trận cũ sẽ bị khóa nhưng giữ nguyên tiến độ khi quay lại."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="inlay", description="Khảm ngọc vào trận pháp")
    @app_commands.describe(slot_index="Vị trí slot (0-80)", gem_key="Key ngọc (vd: GemKim_1)")
    async def inlay(self, interaction: discord.Interaction, slot_index: int, gem_key: str) -> None:
        if not 0 <= slot_index <= 80:
            await interaction.response.send_message(
                embed=error_embed("Slot phải từ 0–80."), ephemeral=True
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

            if not player.active_formation:
                await interaction.response.send_message(
                    embed=error_embed("Chưa chọn trận pháp. Dùng `/formation <key>` trước."), ephemeral=True
                )
                return

            irepo = InventoryRepository(session)
            grade = Grade(gem_data.get("grade", 1))
            if not await irepo.has_item(player.id, gem_key, grade):
                await interaction.response.send_message(
                    embed=error_embed(f"Không có **{gem_data['vi']}** trong túi đồ."), ephemeral=True
                )
                return

            frepo = FormationRepository(session)
            formation = await frepo.inlay_gem(player.id, player.active_formation, slot_index, gem_key)
            await irepo.remove_item(player.id, gem_key, grade, 1)

        filled = len(formation.gem_slots)
        thresholds = [9, 27, 36, 49, 81]
        next_threshold = next((t for t in thresholds if t > filled), None)

        embed = success_embed(
            f"Khảm **{gem_data['vi']}** vào slot **{slot_index}** của trận pháp!\n"
            f"Tổng ngọc đã khảm: **{filled}/81**\n"
            + (f"Ngưỡng tiếp theo: **{next_threshold}** ngọc" if next_threshold else "✨ Đã đạt **81/81** — tối đa!")
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

        # Validate scroll type vs skill type
        allowed_types = _scroll_skill_type(scroll_key)
        if skill_data.get("type") not in allowed_types:
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
        skill_tier = _skill_grade(skill_data)
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

            if slot == -1:
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

        type_labels = {"thien": "Thiên Công", "dia": "Địa Công Phòng", "nhan": "Nhân Hệ", "tran_phap": "Trận Pháp"}
        type_label = type_labels.get(skill_data.get("type", ""), skill_data.get("type", ""))
        embed = success_embed(
            f"Học **{skill_data['vi']}** thành công!\n"
            f"Loại: **{type_label}** | Slot: **{target_slot}**\n"
            f"MP: **{skill_data.get('mp_cost', 0)}** | DMG: **{skill_data.get('base_dmg', 0)}** | CD: {skill_data.get('cooldown', 1)}t"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="constitution", description="Chuyển đổi Thần Thể (Thể Chất)")
    @app_commands.describe(constitution_key="Key Thể Chất (vd: ConstitutionPhaTien)")
    async def constitution(self, interaction: discord.Interaction, constitution_key: str) -> None:
        const_data = registry.get_constitution(constitution_key)
        if not const_data:
            available = ", ".join(registry.constitutions.keys())
            await interaction.response.send_message(
                embed=error_embed(f"Thể chất không hợp lệ.\nCó sẵn: `{available}`"),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            # Check special requirements
            req = const_data.get("special_requirements")
            if req == "requires_dao_ti_yang" or req == "requires_dao_ti_yin":
                if not player.dao_ti_unlocked:
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"**{const_data['vi']}** yêu cầu mở khóa **Đạo Thể** trước.\n"
                            f"Đạo Thể mở khi Luyện Thể đột phá đến Cảnh Giới 9."
                        ),
                        ephemeral=True,
                    )
                    return
            elif req == "requires_all_dao_ti":
                # Requires all three axes at max — simplify: requires dao_ti_unlocked for now
                if not player.dao_ti_unlocked:
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"**{const_data['vi']}** yêu cầu giác ngộ toàn bộ Đạo Thể (Nhập Thánh cả ba trục)."
                        ),
                        ephemeral=True,
                    )
                    return

            # Check and deduct merit cost
            cost = const_data.get("cost_merit", 0)
            if cost > 0:
                if player.merit < cost:
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"Không đủ Công Đức. Cần ✨ **{cost:,}**, có **{player.merit:,}**."
                        ),
                        ephemeral=True,
                    )
                    return
                player.merit -= cost

            player.constitution_type = constitution_key
            await prepo.save(player)

        # Build bonus preview
        bonuses = const_data.get("stat_bonuses", {})
        bonus_lines = []
        if bonuses.get("hp_pct"):
            bonus_lines.append(f"❤️ HP +{bonuses['hp_pct']*100:.0f}%")
        if bonuses.get("mp_pct"):
            bonus_lines.append(f"💙 MP +{bonuses['mp_pct']*100:.0f}%")
        if bonuses.get("final_dmg_bonus"):
            bonus_lines.append(f"⚔️ Sát thương cuối +{bonuses['final_dmg_bonus']*100:.0f}%")
        if bonuses.get("crit_rating"):
            bonus_lines.append(f"💥 Bạo Kích Rating +{bonuses['crit_rating']}")
        if bonuses.get("crit_dmg_rating"):
            bonus_lines.append(f"💥 Bạo Kích DMG Rating +{bonuses['crit_dmg_rating']}")
        if bonuses.get("evasion_rating"):
            bonus_lines.append(f"🌀 Né Tránh Rating +{bonuses['evasion_rating']}")
        if bonuses.get("crit_res_rating"):
            bonus_lines.append(f"🛡️ Kháng Bạo Rating +{bonuses['crit_res_rating']}")
        if bonuses.get("res_all"):
            bonus_lines.append(f"🛡️ Kháng nguyên tố tất cả +{bonuses['res_all']}")
        if bonuses.get("spd_bonus"):
            bonus_lines.append(f"⚡ Tốc độ +{bonuses['spd_bonus']}")
        if bonuses.get("cooldown_reduce"):
            bonus_lines.append(f"⏱️ Giảm Hạn Chiêu -{bonuses['cooldown_reduce']*100:.0f}%")

        cost_str = f"\nTiêu: ✨ **{cost:,}** Công Đức" if cost > 0 else ""
        embed = success_embed(
            f"Đã kích hoạt **{const_data['vi']}**!{cost_str}\n\n"
            + const_data.get("passive_description_vi", "")
            + ("\n\n**Chỉ số:**\n" + "\n".join(bonus_lines) if bonus_lines else "")
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _apply_elixir(player, item_key: str, item_data: dict, quantity: int) -> list[str]:
    """Apply elixir effects to player ORM object. Returns list of effect description lines."""
    from src.game.systems.cultivation import compute_hp_max, compute_mp_max
    from src.db.repositories.player_repo import _player_to_model
    char = _player_to_model(player)
    hp_max = compute_hp_max(char)
    mp_max = compute_mp_max(char)

    effects: list[str] = []
    k = item_key

    if "HoiHPFull" in k:
        player.hp_current = hp_max
        effects.append(f"❤️ HP hồi đầy: **{hp_max:,}**")
    elif "HoiHPLarge" in k:
        heal = int(hp_max * 0.5 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        effects.append(f"❤️ +{heal:,} HP")
    elif "HoiHPMid" in k:
        heal = int(hp_max * 0.25 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        effects.append(f"❤️ +{heal:,} HP")
    elif "HoiHPSmall" in k:
        heal = int(hp_max * 0.10 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        effects.append(f"❤️ +{heal:,} HP")
    elif "HoiHPMiss" in k:
        missing = hp_max - player.hp_current
        player.hp_current = min(hp_max, player.hp_current + int(missing * 0.5 * quantity))
        effects.append(f"❤️ Hồi 50% HP thiếu")
    elif "HoiMPLarge" in k:
        regen = int(mp_max * 0.5 * quantity)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"💙 +{regen:,} MP")
    elif "HoiMPMid" in k:
        regen = int(mp_max * 0.25 * quantity)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"💙 +{regen:,} MP")
    elif "HoiMPSmall" in k:
        regen = int(mp_max * 0.10 * quantity)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"💙 +{regen:,} MP")
    elif "HoiHPMP" in k:
        heal = int(hp_max * 0.15 * quantity)
        regen = int(mp_max * 0.15 * quantity)
        player.hp_current = min(hp_max, player.hp_current + heal)
        player.mp_current = min(mp_max, player.mp_current + regen)
        effects.append(f"❤️ +{heal:,} HP | 💙 +{regen:,} MP")
    elif "HoiFull" in k:
        player.hp_current = hp_max
        player.mp_current = mp_max
        effects.append(f"❤️💙 Hồi đầy cả HP và MP")
    elif "TayNghiep" in k:
        reduce = min(player.karma_accum, 10000 * quantity)
        player.karma_accum = max(0, player.karma_accum - reduce)
        effects.append(f"☯️ Nghiệp Lực Tích Lũy -{reduce:,}")
    elif "KarmaDown" in k:
        reduce = min(player.karma_usable, 5000 * quantity)
        player.karma_usable = max(0, player.karma_usable - reduce)
        effects.append(f"☯️ Nghiệp Lực Khả Dụng -{reduce:,} (đã tiêu thụ)")
    else:
        effects.append("✨ Hiệu ứng đã được áp dụng.")

    return effects


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InventoryCog(bot))
