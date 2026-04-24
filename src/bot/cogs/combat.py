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
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.currencies import CURRENCY_CAP
from src.game.constants.grades import Grade
from src.game.constants.linh_can import compute_linh_can_bonuses
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.cultivation import (
    compute_constitution_bonuses,
    compute_formation_bonuses,
    compute_hp_max,
    compute_mp_max,
    merge_bonuses,
)
from src.utils.embed_builder import base_embed, battle_embed, error_embed, success_embed

log = logging.getLogger(__name__)

RANK_EMOJIS = {
    "pho_thong": "🐾",
    "tinh_anh":  "🌿",
    "cuong_gia": "⚔️",
    "hung_manh": "🗡️",
    "dai_nang":  "🔥",
    "than_thu":  "🌀",
    "tien_thu":  "✨",
    "chi_ton":   "💀",
}

_RANK_ZONE: dict[str, int] = {
    "pho_thong": 1,
    "tinh_anh":  3,
    "cuong_gia": 4,
    "hung_manh": 6,
    "dai_nang":  7,
    "than_thu":  8,
    "tien_thu":  9,
    "chi_ton":   10,
}

_RANK_NEXT: dict[str, str] = {
    "pho_thong": "tinh_anh",
    "tinh_anh":  "cuong_gia",
    "cuong_gia": "hung_manh",
    "hung_manh": "dai_nang",
    "dai_nang":  "than_thu",
    "than_thu":  "tien_thu",
    "tien_thu":  "chi_ton",
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


def _realm_total(char) -> int:
    """Average cultivation level across all 3 paths (0–90 range)."""
    return (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3


def _upgrade_chance(base_rank: str, char) -> float:
    """Probability (0.0–0.30) of encountering a higher-rank elite.

    Scales with how far the player has progressed within the rank's zone.
    """
    zone = _RANK_ZONE.get(base_rank, 1)
    rt = _realm_total(char)
    zone_floor = (zone - 1) * 9
    level_in_zone = max(0, min(9, rt - zone_floor))
    return level_in_zone / 9 * 0.30



def _split_log_embeds(session_log: list[str], result_line: str, color: int) -> list[discord.Embed]:
    """Split combat log into ≤3800-char chunks (Discord 4096 char limit)."""
    full_log = "\n".join(session_log)
    embeds: list[discord.Embed] = []
    chunks = [full_log[i:i + 3800] for i in range(0, max(len(full_log), 1), 3800)]
    for i, chunk in enumerate(chunks):
        embed = base_embed("⚔️ Nhật Ký Chiến Đấu" if i == 0 else "​", chunk, color=color)
        if i == len(chunks) - 1:
            embed.add_field(name="Kết Quả", value=result_line, inline=False)
        embeds.append(embed)
    return embeds or [base_embed("⚔️ Nhật Ký Chiến Đấu", result_line, color=color)]


def _fight_summary_embed(enemy_name: str, enemy_rank: str, result_line: str, color: int) -> discord.Embed:
    embed = base_embed("⚔️ Kết Quả Giao Chiến", color=color)
    rank_emoji = RANK_EMOJIS.get(enemy_rank, "⚔️")
    embed.add_field(name="Đối Thủ", value=f"{rank_emoji} **{enemy_name}**", inline=True)
    embed.add_field(name="Kết Quả", value=result_line, inline=False)
    return embed


# ── Core fight executor ───────────────────────────────────────────────────────

async def _execute_fight(interaction: discord.Interaction, internal_rank: str | None, back_fn=None) -> None:
    """Run a fight with real-time turn-by-turn battle display."""
    char = None
    skill_keys: list[str] = []
    gem_count = 0
    gem_keys: list[str] = []
    hp_max_val = 0
    mp_max_val = 0

    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(interaction.user.id)
        if player is None:
            await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
            return

        char = _player_to_model(player)
        from src.game.systems.character_stats import active_formation_gem_keys
        gem_keys = active_formation_gem_keys(player)
        gem_count = len(gem_keys)

        equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        equip_stats = compute_equipment_stats(equipped)

        from src.game.systems.character_stats import compute_combat_stats
        cs_preview = compute_combat_stats(char, gem_count=gem_count, equip_stats=equip_stats, gem_keys=gem_keys)
        hp_max_val = cs_preview.hp_max
        mp_max_val = cs_preview.mp_max
        if player.hp_current <= 0:
            player.hp_current = hp_max_val

        skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
        hp_current = min(player.hp_current, hp_max_val)
        mp_current = min(player.mp_current if player.mp_current > 0 else mp_max_val, mp_max_val)

    rng = random.Random()

    actual_rank = internal_rank
    loot_multiplier = 1.0
    is_elite = False
    if internal_rank is not None and char is not None:
        chance = _upgrade_chance(internal_rank, char)
        if chance > 0 and rng.random() < chance:
            next_rank = _RANK_NEXT.get(internal_rank)
            if next_rank and registry.enemies_by_rank(next_rank):
                actual_rank = next_rank
                loot_multiplier = 1.5
                is_elite = True

    enemy_key = _pick_random_enemy(actual_rank, rng)
    if not enemy_key:
        await interaction.edit_original_response(embed=error_embed("Không tìm thấy quái vật."), view=None)
        return

    realm_total = _realm_total(char)

    player_c = build_player_combatant(char, skill_keys, gem_count=gem_count, equip_stats=equip_stats, gem_keys=gem_keys)
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
    elite_marker = " ⚡**[TINH ANH]**" if is_elite else ""
    wave_label = f"{rank_emoji} {enemy_name}{elite_marker}"

    combat = CombatSession(
        player=player_c, enemy=enemy_c, player_skill_keys=skill_keys,
        rng=rng, loot_qty_multiplier=loot_multiplier,
    )

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

    if result.reason == CombatEndReason.PLAYER_WIN:
        color = 0x00FF00
        loot_str = ", ".join(
            f"{(registry.get_item(d['item_key']) or {}).get('vi', d['item_key'])}×{d['quantity']}"
            for d in result.loot
        ) or "—"
        elite_bonus = " ⚡ **+50% loot**" if is_elite else ""
        result_line = (
            f"✅ Chiến thắng sau **{result.turns}** lượt{elite_bonus}\n"
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
        await interaction.response.edit_message(
            embed=embed, view=FightRankView(self._discord_id, back_fn=self._back_fn)
        )

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

    @app_commands.command(name="healup", description="Hồi phục HP/MP về tối đa (nghỉ ngơi)")
    async def healup(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            char = _player_to_model(player)
            from src.game.systems.character_stats import active_formation_gem_keys, compute_combat_stats
            gem_keys = active_formation_gem_keys(player)

            # Fold in equipment bonuses so hp_max/mp_max reflect the real
            # effective caps (equipment often contributes flat hp_max).
            equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
            equip_stats = compute_equipment_stats(equipped)

            cs = compute_combat_stats(
                char,
                gem_count=len(gem_keys),
                equip_stats=equip_stats,
                gem_keys=gem_keys,
            )
            player.hp_current = cs.hp_max
            player.mp_current = cs.mp_max
            await prepo.save(player)

        await interaction.response.send_message(
            embed=success_embed("❤️ HP/MP đã hồi phục về tối đa."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CombatCog(bot))
