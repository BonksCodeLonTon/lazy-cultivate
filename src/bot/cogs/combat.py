"""Combat commands — interactive rank selection UI."""
from __future__ import annotations
import asyncio
import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.inventory_repo import InventoryRepository
from src.game.constants.currencies import CURRENCY_CAP
from src.game.constants.grades import Grade
from src.game.models.character import Character as CharModel
from src.game.systems.combat import (
    CombatSession, CombatEndReason,
    build_player_combatant, build_enemy_combatant,
)
from src.game.systems.cultivation import (
    compute_hp_max, compute_mp_max,
    compute_formation_bonuses, compute_constitution_bonuses, merge_bonuses,
)
from src.utils.embed_builder import base_embed, battle_embed, error_embed, success_embed

log = logging.getLogger(__name__)

RANK_EMOJIS = {
    "pho_thong": "🐾",
    "cuong_gia": "⚔️",
    "dai_nang":  "🔥",
    "chi_ton":   "💀",
}

_RANK_CONFIGS = [
    ("pho_thong", "🐾 Phổ Thông"),
    ("cuong_gia", "⚔️ Cường Giả"),
    ("dai_nang",  "🔥 Đại Năng"),
    ("chi_ton",   "💀 Chí Tôn"),
    (None,        "🎲 Ngẫu Nhiên"),
]


def _pick_random_enemy(rank: str | None, rng: random.Random) -> str | None:
    if rank:
        pool = registry.enemies_by_rank(rank)
    else:
        weights = [("pho_thong", 60), ("cuong_gia", 30), ("dai_nang", 9), ("chi_ton", 1)]
        choices, wts = zip(*weights)
        chosen_rank = rng.choices(list(choices), weights=list(wts), k=1)[0]
        pool = registry.enemies_by_rank(chosen_rank)
    return rng.choice(pool)["key"] if pool else None


def _orm_to_charmodel(player) -> CharModel:
    from src.db.repositories.player_repo import _player_to_model
    return _player_to_model(player)


def _split_log_embeds(session_log: list[str], result_line: str, color: int) -> list[discord.Embed]:
    """Split combat log into multiple embeds (Discord 4096 char limit)."""
    full_log = "\n".join(session_log)
    embeds: list[discord.Embed] = []
    chunks = [full_log[i:i + 3800] for i in range(0, max(len(full_log), 1), 3800)]
    for i, chunk in enumerate(chunks):
        embed = base_embed("⚔️ Nhật Ký Chiến Đấu" if i == 0 else "\u200b", chunk, color=color)
        if i == len(chunks) - 1:
            embed.add_field(name="Kết Quả", value=result_line, inline=False)
        embeds.append(embed)
    return embeds or [base_embed("⚔️ Nhật Ký Chiến Đấu", result_line, color=color)]


def _fight_summary_embed(
    enemy_name: str,
    enemy_rank: str,
    result_line: str,
    color: int,
) -> discord.Embed:
    embed = base_embed("⚔️ Kết Quả Giao Chiến", color=color)
    rank_emoji = RANK_EMOJIS.get(enemy_rank, "⚔️")
    embed.add_field(name="Đối Thủ", value=f"{rank_emoji} **{enemy_name}**", inline=True)
    embed.add_field(name="Kết Quả", value=result_line, inline=False)
    return embed


# ── Core fight executor ───────────────────────────────────────────────────────

async def _execute_fight(interaction: discord.Interaction, internal_rank: str | None, back_fn=None) -> None:
    """Run a fight with real-time turn-by-turn battle display."""
    # ── Load player data ──────────────────────────────────────────────────────
    char: CharModel | None = None
    skill_keys: list[str] = []
    gem_count = 0
    hp_max_val = 0
    mp_max_val = 0

    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(interaction.user.id)
        if player is None:
            await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
            return

        char = _orm_to_charmodel(player)
        if player.active_formation and player.formations:
            for f in player.formations:
                if f.formation_key == player.active_formation:
                    gem_count = len(f.gem_slots)
                    break

        bonuses = merge_bonuses(
            compute_formation_bonuses(char.active_formation, gem_count),
            compute_constitution_bonuses(char.constitution_type),
        )
        hp_max_val = compute_hp_max(char, bonuses)
        mp_max_val = compute_mp_max(char, bonuses)
        if player.hp_current <= 0:
            player.hp_current = hp_max_val

        skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
        hp_current = min(player.hp_current, hp_max_val)
        mp_current = min(player.mp_current if player.mp_current > 0 else mp_max_val, mp_max_val)

    rng = random.Random()
    enemy_key = _pick_random_enemy(internal_rank, rng)
    if not enemy_key:
        await interaction.edit_original_response(embed=error_embed("Không tìm thấy quái vật."), view=None)
        return

    realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3

    player_c = build_player_combatant(char, skill_keys, gem_count=gem_count)
    player_c.hp = hp_current
    player_c.mp = mp_current

    enemy_c = build_enemy_combatant(enemy_key, realm_total)
    if not enemy_c:
        await interaction.edit_original_response(embed=error_embed("Lỗi tạo quái vật."), view=None)
        return

    enemy_data = registry.get_enemy(enemy_key)
    enemy_name = enemy_data["vi"] if enemy_data else enemy_key
    enemy_rank = enemy_data.get("rank", "pho_thong") if enemy_data else "pho_thong"
    rank_emoji = RANK_EMOJIS.get(enemy_rank, "⚔️")
    wave_label = f"{rank_emoji} {enemy_name}"

    # ── Real-time battle ──────────────────────────────────────────────────────
    combat = CombatSession(player=player_c, enemy=enemy_c, player_skill_keys=skill_keys, rng=rng)

    await interaction.edit_original_response(
        embed=battle_embed(
            wave_label, 0, 1,
            player_c.name, player_c.hp, player_c.hp_max,
            player_c.mp, player_c.mp_max,
            enemy_c.name, enemy_c.hp, enemy_c.hp_max,
            0, [],
        ),
        view=None,
    )
    await asyncio.sleep(0.8)

    all_log_lines: list[str] = []
    result = None
    while True:
        new_lines, r = combat.step()
        all_log_lines.extend(new_lines)
        if r is not None:
            result = r
            break
        await interaction.edit_original_response(
            embed=battle_embed(
                wave_label, 0, 1,
                player_c.name, player_c.hp, player_c.hp_max,
                player_c.mp, player_c.mp_max,
                enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                combat.turn, new_lines,
            ),
            view=None,
        )
        await asyncio.sleep(0.5)

    # ── Save results ──────────────────────────────────────────────────────────
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(interaction.user.id)
        if player:
            player.hp_current = hp_max_val
            player.mp_current = mp_max_val

            if result.reason == CombatEndReason.PLAYER_WIN:
                player.merit = min(player.merit + result.merit_gained, CURRENCY_CAP)
                player.karma_accum = min(player.karma_accum + result.karma_gained, 500_000)
                player.karma_usable = min(player.karma_usable + result.karma_gained, CURRENCY_CAP)
                irepo = InventoryRepository(session)
                for drop in result.loot:
                    item_data = registry.get_item(drop["item_key"])
                    grade_val = item_data.get("grade", 1) if item_data else 1
                    await irepo.add_item(player.id, drop["item_key"], Grade(grade_val), drop["quantity"])

            await prepo.save(player)

    # ── Show result ───────────────────────────────────────────────────────────
    if result.reason == CombatEndReason.PLAYER_WIN:
        color = 0x00FF00
        loot_str = ", ".join(f"{d['item_key']}×{d['quantity']}" for d in result.loot) or "—"
        result_line = (
            f"✅ Chiến thắng sau **{result.turns}** lượt\n"
            f"✨ +{result.merit_gained:,} Công Đức | 🎁 {loot_str}"
        )
    elif result.reason == CombatEndReason.PLAYER_DEAD:
        color = 0xFF0000
        result_line = f"💀 Tử trận trước {rank_emoji} **{enemy_name}**"
    else:
        color = 0xFFFF00
        result_line = f"⏰ Hòa — hết {result.turns} lượt"

    log_embeds = _split_log_embeds(all_log_lines, result_line, color)
    summary_embed = _fight_summary_embed(enemy_name, enemy_rank, result_line, color)
    view = FightResultView(internal_rank, log_embeds, interaction.user.id, back_fn=back_fn)
    await interaction.edit_original_response(embed=summary_embed, view=view)


# ── Views ─────────────────────────────────────────────────────────────────────

class FightRankView(discord.ui.View):
    def __init__(self, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._back_fn = back_fn

        for rank, label in _RANK_CONFIGS:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=0)
            btn.callback = self._make_cb(rank)
            self.add_item(btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _make_cb(self, rank: str | None):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            await interaction.response.defer()
            await _execute_fight(interaction, rank, back_fn=self._back_fn)
        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class FightResultView(discord.ui.View):
    def __init__(self, rank: str | None, log_embeds: list[discord.Embed], discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._rank = rank
        self._log_embeds = log_embeds
        self._discord_id = discord_id
        self._back_fn = back_fn

        again = discord.ui.Button(label="⚔️ Đánh Lại", style=discord.ButtonStyle.primary, row=0)
        again.callback = self._fight_again_cb
        self.add_item(again)

        change = discord.ui.Button(label="🔙 Đổi Hạng", style=discord.ButtonStyle.secondary, row=0)
        change.callback = self._change_rank_cb
        self.add_item(change)

        log_btn = discord.ui.Button(label="📜 Xem Nhật Ký", style=discord.ButtonStyle.secondary, row=0)
        log_btn.callback = self._view_log_cb
        self.add_item(log_btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    async def _fight_again_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _execute_fight(interaction, self._rank, back_fn=self._back_fn)

    async def _change_rank_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed = base_embed("⚔️ Chọn Hạng Quái", "Chọn hạng quái muốn giao chiến:", color=0xFF6B35)
        await interaction.response.edit_message(embed=embed, view=FightRankView(self._discord_id, back_fn=self._back_fn))

    async def _view_log_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.send_message(embeds=self._log_embeds[:10], ephemeral=True)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


# ── Cog ───────────────────────────────────────────────────────────────────────

class CombatCog(commands.Cog, name="Combat"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="skills", description="Xem kỹ năng đang trang bị")
    async def skills(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            equipped = player.skills or []

        _TYPE_EMOJI = {"thien": "⚔️", "dia": "🛡️", "nhan": "💚", "tran_phap": "🌀"}
        embed = base_embed("🎯 Kỹ Năng Trang Bị", color=0x9B59B6)
        if not equipped:
            embed.description = (
                "Chưa trang bị kỹ năng nào.\n"
                "Dùng `/skilllist` để xem danh sách kỹ năng.\n"
                "Dùng `/learn` để học kỹ năng từ Ngọc Giản."
            )
        else:
            for s in sorted(equipped, key=lambda x: x.slot_index):
                skill_data = registry.get_skill(s.skill_key)
                if skill_data:
                    t_emoji = _TYPE_EMOJI.get(skill_data.get("type", ""), "❓")
                    eff_str = ", ".join(skill_data.get("effects", [])) or "—"
                    embed.add_field(
                        name=f"[Slot {s.slot_index}] {t_emoji} {skill_data['vi']}",
                        value=(
                            f"MP: **{skill_data.get('mp_cost', 0)}** | "
                            f"DMG: **{skill_data.get('base_dmg', 0)}** | "
                            f"CD: **{skill_data.get('cooldown', 1)}t**\n"
                            f"Hiệu ứng: {eff_str}"
                        ),
                        inline=True,
                    )
        embed.set_footer(text="Dùng /forget <slot> để xoá kỹ năng khỏi slot.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skilllist", description="Xem danh sách kỹ năng có thể học")
    @app_commands.describe(
        skill_type="Lọc theo loại: thien / dia / nhan / tran_phap",
        element="Lọc theo nguyên tố: kim / moc / thuy / hoa / tho / loi / phong",
    )
    async def skilllist(
        self,
        interaction: discord.Interaction,
        skill_type: str | None = None,
        element: str | None = None,
    ) -> None:
        valid_types = {"thien", "dia", "nhan", "tran_phap"}
        if skill_type and skill_type not in valid_types:
            await interaction.response.send_message(
                embed=error_embed(f"Loại không hợp lệ. Chọn: {', '.join(sorted(valid_types))}"),
                ephemeral=True,
            )
            return

        all_skills = list(registry.skills.values())
        if skill_type:
            all_skills = [s for s in all_skills if s.get("type") == skill_type]
        if element:
            all_skills = [s for s in all_skills if s.get("element") == element]

        if not all_skills:
            await interaction.response.send_message(
                embed=error_embed("Không tìm thấy kỹ năng phù hợp."), ephemeral=True
            )
            return

        _TYPE_EMOJI = {"thien": "⚔️", "dia": "🛡️", "nhan": "💚", "tran_phap": "🌀"}
        _ELEM_EMOJI = {
            "kim": "🪙", "moc": "🌿", "thuy": "💧", "hoa": "🔥",
            "tho": "🪨", "loi": "⚡", "phong": "🌬️",
        }

        type_label = {
            "thien": "Thiên — Công Kích",
            "dia": "Địa — Phòng Thủ",
            "nhan": "Nhân — Hỗ Trợ/Khống Chế",
            "tran_phap": "Trận Pháp",
        }.get(skill_type or "", "Tất Cả")

        title = f"📜 Kỹ Năng: {type_label}"
        if element:
            title += f" [{_ELEM_EMOJI.get(element, element)}]"

        lines: list[str] = []
        for s in sorted(all_skills, key=lambda x: (x.get("type", ""), x.get("mp_cost", 0))):
            t_e = _TYPE_EMOJI.get(s.get("type", ""), "❓")
            el = s.get("element")
            el_tag = f" {_ELEM_EMOJI.get(el, el)}" if el else ""
            effects = ", ".join(s.get("effects", [])) or "—"
            lines.append(
                f"{t_e}{el_tag} **{s['vi']}** `{s['key']}`\n"
                f"  MP: {s.get('mp_cost', 0)} | DMG: {s.get('base_dmg', 0)} | "
                f"CD: {s.get('cooldown', 1)}t | Hiệu ứng: {effects}"
            )

        # Paginate into chunks of 10 skills per embed
        chunk_size = 10
        chunks = [lines[i:i + chunk_size] for i in range(0, len(lines), chunk_size)]
        embed = base_embed(title, "\n".join(chunks[0]), color=0x9B59B6)
        if len(chunks) > 1:
            embed.set_footer(
                text=f"Hiển thị {chunk_size}/{len(lines)} kỹ năng. "
                     "Dùng /skilllist thien/dia/nhan/tran_phap để lọc."
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="forget", description="Xoá kỹ năng khỏi slot trang bị")
    @app_commands.describe(slot="Slot cần xoá (0–5)")
    async def forget(self, interaction: discord.Interaction, slot: int) -> None:
        from src.db.models.skill import MAX_SKILL_SLOTS, CharacterSkill
        from sqlalchemy import select as sa_select

        if not (0 <= slot < MAX_SKILL_SLOTS):
            await interaction.response.send_message(
                embed=error_embed(f"Slot phải từ 0–{MAX_SKILL_SLOTS - 1}."), ephemeral=True
            )
            return

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            result = await session.execute(
                sa_select(CharacterSkill).where(
                    CharacterSkill.player_id == player.id,
                    CharacterSkill.slot_index == slot,
                )
            )
            skill_row = result.scalar_one_or_none()
            if skill_row is None:
                await interaction.response.send_message(
                    embed=error_embed(f"Slot **{slot}** đang trống."), ephemeral=True
                )
                return

            skill_data = registry.get_skill(skill_row.skill_key)
            skill_name = skill_data["vi"] if skill_data else skill_row.skill_key
            await session.delete(skill_row)

        from src.utils.embed_builder import success_embed
        await interaction.response.send_message(
            embed=success_embed(f"Đã xoá **{skill_name}** khỏi slot **{slot}**."),
            ephemeral=True,
        )

    @app_commands.command(name="healup", description="Hồi phục HP/MP về tối đa (nghỉ ngơi)")
    async def healup(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            char = _orm_to_charmodel(player)
            player.hp_current = compute_hp_max(char)
            player.mp_current = compute_mp_max(char)
            await prepo.save(player)

        await interaction.response.send_message(
            embed=success_embed("❤️ HP/MP đã hồi phục về tối đa."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CombatCog(bot))
