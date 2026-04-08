"""Dungeon (Bí Cảnh) commands — interactive select + button UI."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.currencies import CURRENCY_CAP
from src.game.constants.grades import Grade
from src.game.constants.realms import QI_REALMS
from src.game.models.character import Character as CharModel
from src.game.systems.combat import (
    CombatEndReason, CombatSession,
    build_enemy_combatant, build_player_combatant,
)
from src.game.systems.dungeon import check_can_enter, DungeonResult
from src.utils.embed_builder import base_embed, battle_embed, error_embed, success_embed

log = logging.getLogger(__name__)

RANK_EMOJIS = {"pho_thong": "🐾", "cuong_gia": "⚔️", "dai_nang": "🔥", "chi_ton": "💀"}

# In-memory cooldown: (discord_id, dungeon_key) → datetime of last clear
_dungeon_cooldowns: dict[tuple[int, str], datetime] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _orm_to_model(player) -> CharModel:
    from src.game.constants.linh_can import parse_linh_can
    tracker = player.turn_tracker
    return CharModel(
        player_id=player.id,
        discord_id=player.discord_id,
        name=player.name,
        body_realm=player.body_realm,
        body_level=player.body_level,
        qi_realm=player.qi_realm,
        qi_level=player.qi_level,
        formation_realm=player.formation_realm,
        formation_level=player.formation_level,
        constitution_type=player.constitution_type,
        dao_ti_unlocked=player.dao_ti_unlocked,
        merit=player.merit,
        karma_accum=player.karma_accum,
        karma_usable=player.karma_usable,
        primordial_stones=player.primordial_stones,
        hp_current=player.hp_current,
        mp_current=player.mp_current,
        active_formation=player.active_formation,
        main_title=player.main_title,
        sub_title=player.sub_title,
        evil_title=player.evil_title,
        active_axis=player.active_axis,
        body_xp=player.body_xp,
        qi_xp=player.qi_xp,
        formation_xp=player.formation_xp,
        turns_today=tracker.turns_today if tracker else 0,
        bonus_turns_remaining=tracker.bonus_turns_remaining if tracker else 440,
        linh_can=parse_linh_can(player.linh_can or ""),
    )


def _cooldown_remaining(discord_id: int, dungeon_key: str) -> timedelta | None:
    """Return remaining cooldown timedelta, or None if ready."""
    d = registry.get_dungeon(dungeon_key)
    if not d:
        return None
    last_run = _dungeon_cooldowns.get((discord_id, dungeon_key))
    if not last_run:
        return None
    remaining = last_run + timedelta(hours=d.get("cooldown_hours", 4)) - datetime.now(timezone.utc)
    return remaining if remaining.total_seconds() > 0 else None


def _fmt_cooldown(cd: timedelta) -> str:
    hours, rem = divmod(int(cd.total_seconds()), 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _format_loot(loot: list[dict]) -> str:
    """Merge duplicate item drops and display using item names."""
    merged: dict[str, int] = {}
    for drop in loot:
        merged[drop["item_key"]] = merged.get(drop["item_key"], 0) + drop["quantity"]
    parts = []
    for key, qty in merged.items():
        item_data = registry.get_item(key)
        name = item_data["vi"] if item_data else key
        parts.append(f"{name}×{qty}")
    return ", ".join(parts) or "*(không có vật phẩm)*"


def _dungeon_prep_embed(wave_idx: int, total_waves: int, player_c, effect_msg: str = "") -> discord.Embed:
    """Embed shown between dungeon waves during the prepare phase."""
    hp_pct = int(player_c.hp / player_c.hp_max * 100) if player_c.hp_max > 0 else 0
    mp_pct = int(player_c.mp / player_c.mp_max * 100) if player_c.mp_max > 0 else 0
    desc = (
        f"**Đợt {wave_idx}/{total_waves}** đã hoàn thành! Chuẩn bị cho đợt tiếp theo.\n\n"
        f"❤️ HP: **{player_c.hp:,} / {player_c.hp_max:,}** ({hp_pct}%)\n"
        f"💙 MP: **{player_c.mp:,} / {player_c.mp_max:,}** ({mp_pct}%)"
    )
    if effect_msg:
        desc += f"\n\n✨ {effect_msg}"
    return base_embed("⏸️ Nghỉ Ngơi Giữa Trận", desc, color=0x3498DB)


def _heal_combatant(player_c, item_key: str) -> str:
    """Apply one use of a healing elixir to a Combatant. Returns effect description."""
    k = item_key
    hp_max = player_c.hp_max
    mp_max = player_c.mp_max
    if "HoiHPFull" in k:
        player_c.hp = hp_max
        return f"❤️ HP hồi đầy: {hp_max:,}"
    if "HoiFull" in k:
        player_c.hp = hp_max
        player_c.mp = mp_max
        return "❤️💙 Hồi đầy cả HP và MP"
    if "HoiHPLarge" in k:
        heal = int(hp_max * 0.5)
        player_c.hp = min(hp_max, player_c.hp + heal)
        return f"❤️ +{heal:,} HP"
    if "HoiHPMid" in k:
        heal = int(hp_max * 0.25)
        player_c.hp = min(hp_max, player_c.hp + heal)
        return f"❤️ +{heal:,} HP"
    if "HoiHPSmall" in k:
        heal = int(hp_max * 0.10)
        player_c.hp = min(hp_max, player_c.hp + heal)
        return f"❤️ +{heal:,} HP"
    if "HoiHPMiss" in k:
        missing = hp_max - player_c.hp
        heal = int(missing * 0.5)
        player_c.hp = min(hp_max, player_c.hp + heal)
        return f"❤️ +{heal:,} HP (50% thiếu)"
    if "HoiHPMP" in k:
        heal = int(hp_max * 0.15)
        regen = int(mp_max * 0.15)
        player_c.hp = min(hp_max, player_c.hp + heal)
        player_c.mp = min(mp_max, player_c.mp + regen)
        return f"❤️ +{heal:,} HP | 💙 +{regen:,} MP"
    if "HoiMPLarge" in k:
        regen = int(mp_max * 0.5)
        player_c.mp = min(mp_max, player_c.mp + regen)
        return f"💙 +{regen:,} MP"
    if "HoiMPMid" in k:
        regen = int(mp_max * 0.25)
        player_c.mp = min(mp_max, player_c.mp + regen)
        return f"💙 +{regen:,} MP"
    if "HoiMPSmall" in k:
        regen = int(mp_max * 0.10)
        player_c.mp = min(mp_max, player_c.mp + regen)
        return f"💙 +{regen:,} MP"
    return "✨ Hiệu ứng đã áp dụng."


def _dungeon_list_embed(player_qi_realm: int) -> discord.Embed:
    all_dungeons = sorted(registry.dungeons.values(), key=lambda d: d.get("required_qi_realm", 0))
    unlocked = sum(1 for d in all_dungeons if d.get("required_qi_realm", 0) <= player_qi_realm)
    embed = base_embed(
        "🗺️ Bí Cảnh",
        f"Đã mở khóa **{unlocked}/{len(all_dungeons)}** bí cảnh.\n"
        f"Chọn bí cảnh từ menu bên dưới để xem chi tiết và tham chiến.",
        color=0x7B2D8B,
    )
    return embed


def _dungeon_detail_embed(
    dungeon_key: str,
    discord_id: int,
    player_qi_realm: int,
    player_realm_total: int = 0,
) -> discord.Embed:
    d = registry.get_dungeon(dungeon_key)
    if not d:
        return error_embed("Bí cảnh không tồn tại.")

    req = d.get("required_qi_realm", 0)
    req_label = QI_REALMS[req].vi if req < len(QI_REALMS) else f"Realm {req}"
    can_enter = player_qi_realm >= req

    embed = base_embed(d["vi"], d.get("description", ""), color=0x7B2D8B if can_enter else 0x555555)

    embed.add_field(
        name="Điều Kiện",
        value="✅ Đủ điều kiện" if can_enter else f"🔒 Cần {req_label}",
        inline=True,
    )

    merit = d.get("merit_reward", 0)
    stones = d.get("stone_reward", 0)
    reward_lines = [f"✨ {merit:,} Công Đức"]
    if stones:
        reward_lines.append(f"💎 {stones:,} Hỗn Nguyên Thạch")
    embed.add_field(name="Phần Thưởng", value="\n".join(reward_lines), inline=True)

    cd = _cooldown_remaining(discord_id, dungeon_key)
    cd_val = f"⏳ Còn {_fmt_cooldown(cd)}" if cd else f"✅ Sẵn sàng (CD: {d.get('cooldown_hours', 4)}h)"
    embed.add_field(name="Hồi Chiêu", value=cd_val, inline=True)

    # Wave list — show scaled HP based on player realm
    realm_scale = 1.0 + (player_realm_total / 81) * 2.0
    enemy_keys: list[str] = d.get("enemy_keys", [])
    boss_key = d.get("boss_key")
    wave_lines = []
    for i, ek in enumerate(enemy_keys):
        ed = registry.get_enemy(ek)
        if not ed:
            continue
        emoji = RANK_EMOJIS.get(ed.get("rank", "pho_thong"), "⚔️")
        prefix = "👑 Boss" if ek == boss_key else f"Đợt {i + 1}"
        scaled_hp = int(ed["base_hp"] * realm_scale * ed.get("hp_scale", 1.0))
        wave_lines.append(f"{prefix} {emoji} **{ed['vi']}** (HP: {scaled_hp:,})")

    if wave_lines:
        embed.add_field(name="📋 Các Đợt Chiến Đấu", value="\n".join(wave_lines), inline=False)

    return embed


def _build_result_embeds(
    dungeon_key: str, result: DungeonResult, player_name: str
) -> tuple[discord.Embed, list[discord.Embed]]:
    """Return (summary_embed, [combat_log_embeds])."""
    d = registry.get_dungeon(dungeon_key)
    dungeon_name = d["vi"] if d else dungeon_key

    log_text = "\n".join(result.log)
    log_embeds: list[discord.Embed] = []
    color = 0x00C851 if result.success else 0xFF4444
    for i, chunk in enumerate([log_text[j:j + 3800] for j in range(0, max(len(log_text), 1), 3800)]):
        title = f"📜 Nhật Ký — {dungeon_name}" if i == 0 else "\u200b"
        log_embeds.append(base_embed(title, chunk, color=color))

    if result.success:
        loot_str = _format_loot(result.loot)
        stone_line = f"\n💎 **+{result.stone_gained:,} Hỗn Nguyên Thạch**" if result.stone_gained else ""
        summary_embed = success_embed(
            f"✅ **{player_name}** chinh phục **{dungeon_name}**!\n"
            f"Hoàn thành {result.waves_cleared}/{result.total_waves} đợt.\n\n"
            f"✨ **+{result.merit_gained:,} Công Đức**{stone_line}\n"
            f"🎁 {loot_str}"
        )
        summary_embed.title = f"🏆 Chinh Phục {dungeon_name}"
    else:
        died_line = f" bởi **{result.died_on}**" if result.died_on else ""
        loot_str = f"\n🎁 {_format_loot(result.loot)}" if result.loot else ""
        summary_embed = error_embed(
            f"💀 **{player_name}** đã thất bại{died_line}!\n"
            f"Hoàn thành {result.waves_cleared}/{result.total_waves} đợt.\n\n"
            f"✨ **+{result.merit_gained:,} Công Đức** (từ chiến đấu){loot_str}"
        )
        summary_embed.title = f"💀 Thất Bại — {dungeon_name}"

    return summary_embed, log_embeds


async def _execute_dungeon(
    interaction: discord.Interaction,
    dungeon_key: str,
    player_qi_realm: int,
    player_realm_total: int = 0,
    back_fn=None,
) -> None:
    """Run dungeon with real-time turn-by-turn battle display."""
    cd = _cooldown_remaining(interaction.user.id, dungeon_key)
    if cd:
        d = registry.get_dungeon(dungeon_key)
        name = d["vi"] if d else dungeon_key
        await interaction.edit_original_response(
            embed=error_embed(f"**{name}** đang hồi phục.\nCòn **{_fmt_cooldown(cd)}** nữa."),
            view=None,
        )
        return

    # ── Load player data ──────────────────────────────────────────────────────
    char: CharModel | None = None
    skill_keys: list[str] = []
    player_name = ""
    gem_count = 0

    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(interaction.user.id)

        if player is None:
            await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
            return

        char = _orm_to_model(player)
        ok, reason = check_can_enter(char, dungeon_key)
        if not ok:
            await interaction.edit_original_response(embed=error_embed(reason), view=None)
            return

        from src.game.systems.cultivation import (
            compute_hp_max, compute_mp_max,
            compute_formation_bonuses, compute_constitution_bonuses, merge_bonuses,
        )
        if player.active_formation and player.formations:
            for f in player.formations:
                if f.formation_key == player.active_formation:
                    gem_count = len(f.gem_slots)
                    break

        from src.game.constants.linh_can import compute_linh_can_bonuses
        bonuses = merge_bonuses(
            compute_formation_bonuses(player.active_formation, gem_count),
            compute_constitution_bonuses(player.constitution_type),
            compute_linh_can_bonuses(char.linh_can),
        )
        if player.hp_current <= 0:
            player.hp_current = compute_hp_max(char, bonuses)
            char.hp_current = player.hp_current

        skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
        player_name = player.name

    # Realm total for enemy scaling (computed from char to be always fresh)
    _realm_total = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3

    # ── Run interactive battle ────────────────────────────────────────────────
    dungeon = registry.get_dungeon(dungeon_key)
    if not dungeon:
        await interaction.edit_original_response(embed=error_embed("Bí cảnh không tồn tại."), view=None)
        return

    player_c = build_player_combatant(char, skill_keys, gem_count)
    enemy_keys: list[str] = dungeon.get("enemy_keys", [])
    total_waves = len(enemy_keys)
    boss_key = dungeon.get("boss_key")

    all_loot: list[dict] = []
    all_logs: list[str] = []
    merit_total = 0
    waves_cleared = 0
    died_on: str | None = None
    dungeon_success = False
    stone_gained = 0

    player_db_id: int = char.player_id

    for wave_idx, enemy_key in enumerate(enemy_keys):
        # ── Inter-wave prepare phase (not before first wave) ──────────────────
        if wave_idx > 0:
            elixirs = await _load_elixirs(player_db_id)
            prep_view = DungeonPrepView(
                interaction.user.id, player_db_id, player_c,
                elixirs, wave_idx, total_waves,
            )
            await interaction.edit_original_response(
                embed=_dungeon_prep_embed(wave_idx, total_waves, player_c),
                view=prep_view,
            )
            try:
                await asyncio.wait_for(prep_view.done_event.wait(), timeout=120.0)
            except asyncio.TimeoutError:
                pass  # treat timeout as "continue"

            if prep_view.abandoned:
                break

        is_boss = enemy_key == boss_key
        edata = registry.get_enemy(enemy_key)
        wave_label = (
            (f"👑 Boss: **{edata['vi']}**" if is_boss else f"Đợt {wave_idx + 1}: **{edata['vi']}**")
            if edata else f"Đợt {wave_idx + 1}"
        )

        all_logs.append(f"\n{'═' * 20}")
        all_logs.append(f"⚔️ **{wave_label}**")

        enemy_c = build_enemy_combatant(enemy_key, _realm_total)
        if not enemy_c:
            continue

        combat_session = CombatSession(
            player=player_c,
            enemy=enemy_c,
            player_skill_keys=skill_keys,
        )

        # Show wave start
        await interaction.edit_original_response(
            embed=battle_embed(
                wave_label, wave_idx, total_waves,
                player_c.name, player_c.hp, player_c.hp_max,
                player_c.mp, player_c.mp_max,
                enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                0, [],
            ),
            view=None,
        )
        await asyncio.sleep(0.8)

        # Turn-by-turn loop
        wave_result = None
        while True:
            new_lines, result = combat_session.step()
            all_logs.extend(new_lines)

            if result is not None:
                wave_result = result
                all_loot.extend(result.loot)
                merit_total += result.merit_gained
                break

            await interaction.edit_original_response(
                embed=battle_embed(
                    wave_label, wave_idx, total_waves,
                    player_c.name, player_c.hp, player_c.hp_max,
                    player_c.mp, player_c.mp_max,
                    enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                    combat_session.turn, new_lines,
                ),
                view=None,
            )
            await asyncio.sleep(0.5)

        if wave_result.reason == CombatEndReason.PLAYER_DEAD:
            died_on = edata["vi"] if edata else enemy_key
            break

        waves_cleared += 1
    else:
        dungeon_success = True
        merit_total += dungeon.get("merit_reward", 0)
        stone_gained = dungeon.get("stone_reward", 0)

    result_obj = DungeonResult(
        success=dungeon_success,
        waves_cleared=waves_cleared,
        total_waves=total_waves,
        loot=all_loot,
        merit_gained=merit_total,
        stone_gained=stone_gained,
        log=all_logs,
        died_on=died_on,
        hp_remaining=player_c.hp if dungeon_success else 0,
    )

    # ── Save results ──────────────────────────────────────────────────────────
    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(interaction.user.id)
        if player:
            player.merit = min(player.merit + merit_total, CURRENCY_CAP)
            if stone_gained:
                player.primordial_stones = min(player.primordial_stones + stone_gained, CURRENCY_CAP)
            if dungeon_success:
                # Restore to full HP/MP using the combatant's actual max (includes all bonuses)
                player.hp_current = player_c.hp_max
                player.mp_current = player_c.mp_max
            else:
                # Preserve the HP/MP the player had (minimum 1 HP to avoid dead state)
                player.hp_current = max(1, player_c.hp)
                player.mp_current = max(0, player_c.mp)

            if all_loot:
                irepo = InventoryRepository(session)
                for drop in all_loot:
                    item_data = registry.get_item(drop["item_key"])
                    grade_val = item_data.get("grade", 1) if item_data else 1
                    await irepo.add_item(player.id, drop["item_key"], Grade(grade_val), drop["quantity"])

            await repo.save(player)

    if dungeon_success:
        _dungeon_cooldowns[(interaction.user.id, dungeon_key)] = datetime.now(timezone.utc)

    summary_embed, log_embeds = _build_result_embeds(dungeon_key, result_obj, player_name)
    view = DungeonResultView(
        dungeon_key, interaction.user.id, player_qi_realm, player_realm_total,
        log_embeds, back_fn=back_fn,
    )
    await interaction.edit_original_response(embed=summary_embed, view=view)


# ── Helpers for prep phase ────────────────────────────────────────────────────

async def _load_elixirs(player_db_id: int) -> list[dict[str, Any]]:
    """Return list of elixir dicts the player currently owns."""
    elixirs = []
    async with get_session() as session:
        irepo = InventoryRepository(session)
        items = await irepo.get_all(player_db_id)
        for inv_item in items:
            item_data = registry.get_item(inv_item.item_key)
            if item_data and item_data.get("type") == "elixir":
                elixirs.append({
                    "key": inv_item.item_key,
                    "grade": inv_item.grade,
                    "qty": inv_item.quantity,
                    "name": item_data.get("vi", inv_item.item_key),
                })
    return elixirs


# ── Discord UI Components ─────────────────────────────────────────────────────

class DungeonPrepView(discord.ui.View):
    """Shown between dungeon waves — lets player use elixirs, continue, or abandon."""

    def __init__(
        self,
        discord_id: int,
        player_db_id: int,
        player_c: Any,
        elixirs: list[dict],
        wave_idx: int,
        total_waves: int,
    ) -> None:
        super().__init__(timeout=120)
        self.discord_id = discord_id
        self.player_db_id = player_db_id
        self.player_c = player_c
        self.elixirs = list(elixirs)
        self.wave_idx = wave_idx
        self.total_waves = total_waves
        self.done_event = asyncio.Event()
        self.abandoned = False
        self._build_items()

    def _build_items(self) -> None:
        self.clear_items()
        if self.elixirs:
            options = [
                discord.SelectOption(
                    label=f"{e['name']} × {e['qty']}"[:100],
                    value=e["key"],
                    description=f"Dùng 1 × {e['name']}"[:100],
                )
                for e in self.elixirs[:25]
            ]
            sel = discord.ui.Select(
                placeholder="💊 Dùng Đan Dược...",
                options=options,
                row=0,
            )
            sel.callback = self._use_elixir_cb
            self.add_item(sel)

        continue_btn = discord.ui.Button(
            label="▶ Tiếp tục", style=discord.ButtonStyle.green, row=1
        )
        continue_btn.callback = self._continue_cb
        self.add_item(continue_btn)

        abandon_btn = discord.ui.Button(
            label="🚪 Bỏ cuộc", style=discord.ButtonStyle.red, row=1
        )
        abandon_btn.callback = self._abandon_cb
        self.add_item(abandon_btn)

    async def _use_elixir_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return

        item_key = interaction.data["values"][0]
        elixir = next((e for e in self.elixirs if e["key"] == item_key), None)
        if not elixir:
            await interaction.response.defer()
            return

        # Remove one from DB
        async with get_session() as session:
            irepo = InventoryRepository(session)
            removed = await irepo.remove_item(self.player_db_id, item_key, Grade(elixir["grade"]))
            if not removed:
                await interaction.response.send_message("Không còn vật phẩm này.", ephemeral=True)
                return

        # Apply healing to in-memory combatant
        effect_msg = _heal_combatant(self.player_c, item_key)

        # Update local elixir count
        elixir["qty"] -= 1
        if elixir["qty"] <= 0:
            self.elixirs = [e for e in self.elixirs if e["key"] != item_key]

        self._build_items()
        embed = _dungeon_prep_embed(self.wave_idx, self.total_waves, self.player_c, effect_msg)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _continue_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        self.done_event.set()

    async def _abandon_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        self.abandoned = True
        self.done_event.set()

    async def on_timeout(self) -> None:
        self.done_event.set()


class DungeonSelect(discord.ui.Select):
    """Dropdown listing all dungeons; selecting one transitions to the detail view."""

    def __init__(self, discord_id: int, player_qi_realm: int, player_realm_total: int, back_fn=None) -> None:
        self.discord_id = discord_id
        self.player_qi_realm = player_qi_realm
        self.player_realm_total = player_realm_total
        self._back_fn = back_fn

        all_dungeons = sorted(registry.dungeons.values(), key=lambda d: d.get("required_qi_realm", 0))
        options: list[discord.SelectOption] = []
        for d in all_dungeons[:25]:
            req = d.get("required_qi_realm", 0)
            req_label = QI_REALMS[req].vi if req < len(QI_REALMS) else f"Realm {req}"
            can_enter = req <= player_qi_realm
            merit = d.get("merit_reward", 0)
            options.append(discord.SelectOption(
                label=d["vi"][:100],
                value=d["key"],
                description=f"Yêu cầu: {req_label} | +{merit:,} Công Đức"[:100],
                emoji="✅" if can_enter else "🔒",
            ))

        super().__init__(placeholder="⚔️ Chọn Bí Cảnh...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        dungeon_key = self.values[0]
        embed = _dungeon_detail_embed(dungeon_key, interaction.user.id, self.player_qi_realm, self.player_realm_total)
        view = DungeonDetailView(
            dungeon_key, interaction.user.id, self.player_qi_realm, self.player_realm_total, back_fn=self._back_fn
        )
        await interaction.response.edit_message(embed=embed, view=view)


class DungeonListView(discord.ui.View):
    """View shown on the main /dungeon command — select dropdown + optional back button."""

    def __init__(self, discord_id: int, player_qi_realm: int, player_realm_total: int = 0, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._back_fn = back_fn
        self._player_qi_realm = player_qi_realm
        self._player_realm_total = player_realm_total
        self.add_item(DungeonSelect(discord_id, player_qi_realm, player_realm_total, back_fn=back_fn))
        if back_fn:
            btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            btn.callback = self._back_cb
            self.add_item(btn)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)

    async def on_timeout(self) -> None:
        pass


class DungeonDetailView(discord.ui.View):
    """View shown after a dungeon is selected — Enter + Back buttons."""

    def __init__(
        self,
        dungeon_key: str,
        discord_id: int,
        player_qi_realm: int,
        player_realm_total: int = 0,
        back_fn=None,
    ) -> None:
        super().__init__(timeout=120)
        self.dungeon_key = dungeon_key
        self.discord_id = discord_id
        self.player_qi_realm = player_qi_realm
        self.player_realm_total = player_realm_total
        self._back_fn = back_fn

    @discord.ui.button(label="⚔️ Vào Bí Cảnh", style=discord.ButtonStyle.green, row=1)
    async def enter_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _execute_dungeon(
            interaction, self.dungeon_key, self.player_qi_realm, self.player_realm_total,
            back_fn=self._back_fn,
        )

    @discord.ui.button(label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        embed = _dungeon_list_embed(self.player_qi_realm)
        view = DungeonListView(self.discord_id, self.player_qi_realm, self.player_realm_total, back_fn=self._back_fn)
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_timeout(self) -> None:
        pass


class DungeonResultView(discord.ui.View):
    """View shown after dungeon completes — View Log + Back to list buttons."""

    def __init__(
        self,
        dungeon_key: str,
        discord_id: int,
        player_qi_realm: int,
        player_realm_total: int,
        log_embeds: list[discord.Embed],
        back_fn=None,
    ) -> None:
        super().__init__(timeout=120)
        self.dungeon_key = dungeon_key
        self.discord_id = discord_id
        self.player_qi_realm = player_qi_realm
        self.player_realm_total = player_realm_total
        self.log_embeds = log_embeds
        self._back_fn = back_fn
        if back_fn:
            btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            btn.callback = self._back_cb
            self.add_item(btn)

    @discord.ui.button(label="📜 Xem Nhật Ký", style=discord.ButtonStyle.secondary, row=0)
    async def view_log_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        if not self.log_embeds:
            await interaction.response.send_message("*(Không có nhật ký)*", ephemeral=True)
            return
        await interaction.response.send_message(embeds=self.log_embeds[:10], ephemeral=True)

    @discord.ui.button(label="🗺️ Danh Sách Bí Cảnh", style=discord.ButtonStyle.blurple, row=0)
    async def back_to_list_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        embed = _dungeon_list_embed(self.player_qi_realm)
        view = DungeonListView(self.discord_id, self.player_qi_realm, self.player_realm_total, back_fn=self._back_fn)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)

    async def on_timeout(self) -> None:
        pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class DungeonCog(commands.Cog, name="Dungeon"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="dungeon", description="Khám phá Bí Cảnh")
    async def dungeon(self, interaction: discord.Interaction) -> None:
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)

        if player is None:
            await interaction.response.send_message(
                embed=error_embed("Chưa có nhân vật. Dùng `/register` để bắt đầu."),
                ephemeral=True,
            )
            return

        player_realm_total = (
            player.body_realm * 9 + player.body_level
            + player.qi_realm * 9 + player.qi_level
            + player.formation_realm * 9 + player.formation_level
        ) // 3

        embed = _dungeon_list_embed(player.qi_realm)
        view = DungeonListView(interaction.user.id, player.qi_realm, player_realm_total)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DungeonCog(bot))
