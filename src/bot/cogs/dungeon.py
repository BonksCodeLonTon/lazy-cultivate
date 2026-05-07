"""Dungeon (Bí Cảnh) commands — interactive select + button UI."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.currencies import CURRENCY_CAP
from src.game.constants.grades import Grade
from src.game.constants.realms import QI_REALMS
from src.game.systems.combat import (
    CombatEndReason, CombatSession,
    build_enemy_combatant, build_player_combatant,
)
from src.game.systems.dungeon import (
    apply_healing_elixir, best_axis_realm, check_can_enter, compute_realm_total,
    DungeonResult, merge_loot, qualifying_axis,
    _build_wave_list, _grade_progress, _roll_encounter_grade, _roll_boss_grade, _apply_encounter_grade,
)
from src.utils import emojis
from src.utils.embed_builder import base_embed, battle_embed, error_embed, success_embed
from src.utils.pagination import PAGE_SIZE, add_page_controls, page_slice, total_pages

log = logging.getLogger(__name__)

RANK_EMOJIS = {"pho_thong": "🐾", "cuong_gia": "⚔️", "dai_nang": "🔥", "chi_ton": "💀"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _format_loot(loot: list[dict]) -> str:
    """Render merged loot drops using human item names."""
    parts = []
    for key, qty in merge_loot(loot).items():
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


_DUNGEON_TYPE_META: dict[str, dict[str, Any]] = {
    "normal": {
        "title": "⚔️ Bí Cảnh Thường",
        "intro": "Chọn bí cảnh từ menu bên dưới để xem chi tiết và tham chiến.",
        "color": 0x7B2D8B,
    },
    "duoc_vien": {
        "title": "🌿 Dược Viên",
        "intro": (
            "Vườn dược linh rộng lớn. Linh thú hệ **Mộc** tại đây hấp thụ dược "
            "khí nên hồi phục huyết mạch rất nhanh — cần sát thương liên tục. "
            "Phần thưởng chủ yếu là **thảo dược** và **nguyên liệu luyện đan**."
        ),
        "color": 0x2E7D32,
    },
    "the_chat": {
        "title": "🧬 Thần Cốt Địa",
        "intro": (
            "Mật cảnh cô độc chỉ có **một đại Boss** canh giữ — không có đợt "
            "thường. Đánh bại Boss mới có cơ hội thu được **Đạo Cốt Tinh**, "
            "nguyên liệu duy nhất để chuyển hóa Thể Chất."
        ),
        "color": 0xB8860B,
    },
    "linh_can": {
        "title": "🌌 Linh Căn Bí Cảnh",
        "intro": (
            "Mật mạch tụ linh khí 9 nguyên tố, mỗi mạch chỉ rớt nguyên liệu "
            "khai mở & nâng cấp **Linh Căn** cùng hệ. Có thể đăng bán hoặc "
            "tìm mua tại **Đấu Thương Các** nếu muốn tiết kiệm thời gian.\n\n"
            "⚠️ Tỉ lệ rớt **giảm dần theo cảnh giới Luyện Khí** — luyện sớm "
            "trước khi đột phá để tích trữ nguyên liệu cao cấp."
        ),
        "color": 0x8E44AD,
    },
}


def _dungeon_list_embed(player_best_realm: int, dungeon_type: str = "normal") -> discord.Embed:
    meta = _DUNGEON_TYPE_META.get(dungeon_type, _DUNGEON_TYPE_META["normal"])
    pool = registry.dungeons_of_type(dungeon_type)
    pool = sorted(pool, key=lambda d: d.get("required_qi_realm", 0))
    unlocked = sum(1 for d in pool if d.get("required_qi_realm", 0) <= player_best_realm)
    embed = base_embed(
        meta["title"],
        f"Đã mở khóa **{unlocked}/{len(pool)}** khu vực.\n{meta['intro']}",
        color=meta["color"],
    )
    return embed


def _dungeon_type_embed() -> discord.Embed:
    return base_embed(
        "🗺️ Bí Cảnh",
        "Chọn loại bí cảnh để bắt đầu:\n\n"
        "⚔️ **Bí Cảnh Thường** — Yêu thú đa hệ, rớt trang bị & nguyên liệu luyện khí.\n"
        "🌿 **Dược Viên** — Yêu thú hệ Mộc với khả năng hồi máu, rớt thảo dược luyện đan.\n"
        "🧬 **Thần Cốt Địa** — Chỉ một Đại Boss, rớt **Đạo Cốt Tinh** để chuyển Thể Chất.\n"
        "🌌 **Linh Căn Bí Cảnh** — 9 mạch linh khí theo nguyên tố, rớt nguyên liệu khai mở "
        "& nâng cấp **Linh Căn** (giảm tỉ lệ ở cảnh giới cao).",
        color=0x7B2D8B,
    )


def _dungeon_detail_embed(
    dungeon_key: str,
    discord_id: int,
    player_best_realm: int,
    player_realm_total: int = 0,
) -> discord.Embed:
    d = registry.get_dungeon(dungeon_key)
    if not d:
        return error_embed("Bí cảnh không tồn tại.")

    req = d.get("required_qi_realm", 0)
    req_label = QI_REALMS[req].vi if req < len(QI_REALMS) else f"Realm {req}"
    can_enter = player_best_realm >= req

    embed = base_embed(d["vi"], d.get("description", ""), color=0x7B2D8B if can_enter else 0x555555)

    embed.add_field(
        name="Điều Kiện",
        value=(
            "✅ Đủ điều kiện"
            if can_enter
            else f"🔒 Cần {req_label} trên **1 trong 3 hướng tu luyện**"
        ),
        inline=True,
    )

    merit = d.get("merit_reward", 0)
    stones = d.get("stone_reward", 0)
    reward_lines = [f"{emojis.for_currency('merit')} {merit:,} Công Đức"]
    if stones:
        reward_lines.append(f"{emojis.for_currency('primordial_stones')} {stones:,} Hỗn Nguyên Thạch")
    embed.add_field(name="Phần Thưởng", value="\n".join(reward_lines), inline=True)

    # Pool preview
    wave_count = d.get("wave_count", 3)
    enemy_pool: list[str] = d.get("enemy_pool", [])
    boss_min_idx = int(d.get("boss_min_grade_idx", 2))

    _grade_names = ["Bình Thường", "Dị Thường", "Tinh Anh", "Vương Giả", "Truyền Thuyết"]
    min_grade_name = _grade_names[min(boss_min_idx, len(_grade_names) - 1)]

    if wave_count == 1 and len(enemy_pool) == 1:
        boss_enemy = registry.get_enemy(enemy_pool[0])
        boss_name = boss_enemy["vi"] if boss_enemy else enemy_pool[0]
        wave_lines = [
            f"👑 **Apex Boss:** {boss_name}",
            f"🔱 Cấp tối thiểu: **{min_grade_name}**",
            f"🚫 Không có đợt thường — chỉ một trận quyết đấu",
        ]
    else:
        wave_lines = [
            f"🎲 **{wave_count} đợt ngẫu nhiên** từ {len(enemy_pool)} kẻ địch",
            f"👑 Đợt cuối luôn có cấp **{min_grade_name}** trở lên",
        ]
    embed.add_field(name="📋 Thông Tin Bí Cảnh", value="\n".join(wave_lines), inline=False)

    # ── Linh Căn environmental effect preview ─────────────────────────────
    # Show the bí cảnh's signature pressure with the player's current
    # realm baked in so they can see exactly what's coming.
    env = d.get("environmental_effect")
    if env:
        from src.game.systems.linh_can_environment import scaled_strength
        from src.db.connection import get_session  # noqa: F401  (env preview is sync)
        # We don't have qi_realm in scope here without an extra DB hop; use
        # ``player_best_realm`` as a reasonable proxy for the dungeon detail
        # preview. Actual combat uses char.qi_realm (always exact).
        strength = scaled_strength(env, player_best_realm)
        embed.add_field(
            name=f"🌌 Hiệu Ứng Môi Trường — {env.get('vi', env.get('key', ''))}",
            value=(
                f"Cường độ scaling theo cảnh giới của bạn: **×{1 + env.get('scale_per_realm', 0.0) * player_best_realm:.2f}** "
                f"(giá trị ước tính: {strength:.2f}).\n"
                "Hiệu ứng được kích hoạt mỗi đợt và xuất hiện ở đầu nhật ký chiến đấu."
            ),
            inline=False,
        )

    return embed


def _build_result_embeds(
    dungeon_key: str, result: DungeonResult, player_name: str
) -> tuple[discord.Embed, list[discord.Embed]]:
    """Return (summary_embed, [combat_log_embeds])."""
    d = registry.get_dungeon(dungeon_key)
    dungeon_name = d["vi"] if d else dungeon_key

    from src.game.engine.damage import to_ansi_block

    log_text = "\n".join(result.log)
    log_embeds: list[discord.Embed] = []
    color = 0x00C851 if result.success else 0xFF4444
    for i, chunk in enumerate([log_text[j:j + 3800] for j in range(0, max(len(log_text), 1), 3800)]):
        title = f"📜 Nhật Ký — {dungeon_name}" if i == 0 else "\u200b"
        body = to_ansi_block(chunk) if chunk.strip() else chunk
        log_embeds.append(base_embed(title, body, color=color))

    if result.success:
        loot_str = _format_loot(result.loot)
        stone_line = f"\n{emojis.for_currency('primordial_stones')} **+{result.stone_gained:,} Hỗn Nguyên Thạch**" if result.stone_gained else ""
        summary_embed = success_embed(
            f"✅ **{player_name}** chinh phục **{dungeon_name}**!\n"
            f"Hoàn thành {result.waves_cleared}/{result.total_waves} đợt.\n\n"
            f"{emojis.for_currency('merit')} **+{result.merit_gained:,} Công Đức**{stone_line}\n"
            f"🎁 {loot_str}"
        )
        summary_embed.title = f"🏆 Chinh Phục {dungeon_name}"
    else:
        died_line = f" bởi **{result.died_on}**" if result.died_on else ""
        loot_str = f"\n🎁 {_format_loot(result.loot)}" if result.loot else ""
        summary_embed = error_embed(
            f"💀 **{player_name}** đã thất bại{died_line}!\n"
            f"Hoàn thành {result.waves_cleared}/{result.total_waves} đợt.\n\n"
            f"{emojis.for_currency('merit')} **+{result.merit_gained:,} Công Đức** (từ chiến đấu){loot_str}"
        )
        summary_embed.title = f"💀 Thất Bại — {dungeon_name}"

    return summary_embed, log_embeds


# ── Single-session lock ───────────────────────────────────────────────────────
# In-memory guard against the "open /dungeon twice and run two dungeons in
# parallel" exploit — without this, both async tasks operate on the same
# Player row and double-credit merit/loot/stones. Single-process bot, so a
# plain set is enough; a restart clears the set (acceptable for the rare
# crash-mid-run case).
_ACTIVE_DUNGEON_USERS: set[int] = set()


def _try_acquire_dungeon_session(discord_id: int) -> bool:
    """Mark ``discord_id`` as running a dungeon. Returns False if a session is
    already active for this user."""
    if discord_id in _ACTIVE_DUNGEON_USERS:
        return False
    _ACTIVE_DUNGEON_USERS.add(discord_id)
    return True


def _release_dungeon_session(discord_id: int) -> None:
    _ACTIVE_DUNGEON_USERS.discard(discord_id)


async def _send_already_running_error(interaction: discord.Interaction) -> None:
    """Common reject path when a user tries to start a 2nd concurrent run."""
    msg = error_embed(
        "Bạn đang trong một bí cảnh khác. Hãy hoàn tất hoặc dừng "
        "phiên hiện tại trước khi mở phiên mới."
    )
    try:
        await interaction.followup.send(embed=msg, ephemeral=True)
    except discord.HTTPException:
        # Interaction may not be deferred yet — fall back to direct response.
        try:
            await interaction.response.send_message(embed=msg, ephemeral=True)
        except discord.HTTPException:
            pass


# ── Auto-repeat support ───────────────────────────────────────────────────────

AUTO_REPEAT_MAX_RUNS = 20          # hard ceiling so a forgotten loop self-terminates
AUTO_REPEAT_INTERSTITIAL_SEC = 3.0  # window between runs for the player to click Stop


@dataclass
class AutoRepeatTotals:
    """Running totals across an auto-repeat session — accumulates merit, stones,
    loot, and a success/fail tally. Rendered in the inter-run status embed and
    the final aggregated summary."""
    runs_completed: int = 0
    runs_succeeded: int = 0
    merit: int = 0
    stones: int = 0
    loot: list[dict] = field(default_factory=list)
    last_died_on: str | None = None

    def add(self, result: DungeonResult) -> None:
        self.runs_completed += 1
        if result.success:
            self.runs_succeeded += 1
        else:
            self.last_died_on = result.died_on
        self.merit += int(result.merit_gained)
        self.stones += int(result.stone_gained)
        self.loot.extend(result.loot)


def _auto_repeat_status_embed(
    dungeon_key: str, totals: AutoRepeatTotals, last_result: DungeonResult,
) -> discord.Embed:
    """Inter-run status: shown for ``AUTO_REPEAT_INTERSTITIAL_SEC`` between runs
    so the player can read the running totals and click Stop."""
    d = registry.get_dungeon(dungeon_key)
    name = d["vi"] if d else dungeon_key
    last_status = "✅ thắng" if last_result.success else f"💀 thua (bởi {last_result.died_on or '???'})"
    desc = (
        f"🔁 **Tự Động Lặp Lại — {name}**\n\n"
        f"Run vừa qua: **{last_status}**\n"
        f"Đã hoàn thành: **{totals.runs_completed}** lần "
        f"(thắng {totals.runs_succeeded} / thua {totals.runs_completed - totals.runs_succeeded})\n\n"
        f"📈 **Tích luỹ:**\n"
        f"{emojis.for_currency('merit')} **+{totals.merit:,} Công Đức**\n"
    )
    if totals.stones > 0:
        desc += f"{emojis.for_currency('primordial_stones')} **+{totals.stones:,} Hỗn Nguyên Thạch**\n"
    desc += f"\n⏳ Tự khởi động lại sau {int(AUTO_REPEAT_INTERSTITIAL_SEC)}s — bấm **Dừng** để kết thúc."
    return base_embed(f"🔁 Auto Repeat ({totals.runs_completed} runs)", desc, color=0x9B59B6)


def _auto_repeat_final_embed(
    dungeon_key: str, totals: AutoRepeatTotals, reason: str, player_name: str,
) -> discord.Embed:
    """Aggregated summary at the end of an auto-repeat session."""
    d = registry.get_dungeon(dungeon_key)
    name = d["vi"] if d else dungeon_key
    loot_str = _format_loot(totals.loot) if totals.loot else "*(không có)*"
    color = 0x00C851 if totals.runs_succeeded > 0 else 0xFF4444
    desc = (
        f"🔁 **{player_name}** đã hoàn thành **{totals.runs_completed}** lần "
        f"chinh phục **{name}** — thắng {totals.runs_succeeded} / "
        f"thua {totals.runs_completed - totals.runs_succeeded}.\n\n"
        f"🛑 Lý do dừng: **{reason}**\n\n"
        f"📈 **Tổng thu hoạch:**\n"
        f"{emojis.for_currency('merit')} **+{totals.merit:,} Công Đức**\n"
    )
    if totals.stones > 0:
        desc += f"{emojis.for_currency('primordial_stones')} **+{totals.stones:,} Hỗn Nguyên Thạch**\n"
    desc += f"\n🎁 {loot_str}"
    embed = base_embed(f"🔁 Tổng kết Auto Repeat — {name}", desc, color=color)
    return embed


class AutoRepeatStopView(discord.ui.View):
    """Single-button view shown between auto-repeat runs — Stop ends the loop
    cleanly (current run already saved)."""

    def __init__(self, discord_id: int, stop_event: asyncio.Event) -> None:
        super().__init__(timeout=120)
        self.discord_id = discord_id
        self.stop_event = stop_event

    @discord.ui.button(label="🛑 Dừng Lặp Lại", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        self.stop_event.set()

    async def on_timeout(self) -> None:
        # Timeout out → stop the loop so we don't leave a runaway repeat.
        self.stop_event.set()


async def _run_dungeon_with_repeat(
    interaction: discord.Interaction,
    dungeon_key: str,
    player_best_realm: int,
    player_realm_total: int,
    back_fn,
    dungeon_type: str,
) -> None:
    """Auto-repeat loop: run the same dungeon until defeat, Stop, the run cap,
    or currency cap is reached. Renders aggregate totals at the end.

    Each interstitial creates a fresh ``AutoRepeatStopView`` whose 120-second
    timeout would set the shared ``stop_event`` if not cancelled. We stop the
    previous view before creating a new one so an OLD view's timer can't fire
    during a later run and abort the loop with a misleading "???" failure.
    """
    totals = AutoRepeatTotals()
    stop_event = asyncio.Event()
    final_reason = "Hết số lần tối đa"
    prev_view: AutoRepeatStopView | None = None

    try:
        for run_idx in range(AUTO_REPEAT_MAX_RUNS):
            result = await _execute_dungeon(
                interaction, dungeon_key, player_best_realm, player_realm_total,
                back_fn=back_fn, dungeon_type=dungeon_type,
                auto_mode=True, stop_event=stop_event,
            )
            if result is None:
                final_reason = "Lỗi hệ thống"
                break

            totals.add(result)

            if not result.success:
                final_reason = f"Thất bại bởi **{result.died_on or '???'}**"
                break
            if stop_event.is_set():
                final_reason = "Người chơi yêu cầu dừng"
                break
            if run_idx >= AUTO_REPEAT_MAX_RUNS - 1:
                break

            # Cancel the previous interstitial's view BEFORE creating a new one.
            # Otherwise its 120-second on_timeout would still fire mid-run and
            # set the shared stop_event — see docstring above.
            if prev_view is not None:
                prev_view.stop()

            status_embed = _auto_repeat_status_embed(dungeon_key, totals, result)
            prev_view = AutoRepeatStopView(interaction.user.id, stop_event)
            await interaction.edit_original_response(embed=status_embed, view=prev_view)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=AUTO_REPEAT_INTERSTITIAL_SEC)
                final_reason = "Người chơi yêu cầu dừng"
                break
            except asyncio.TimeoutError:
                pass  # window elapsed, continue to next run
    finally:
        # Always stop the last surviving view so its dangling timer can't fire
        # after the final summary embed has replaced the message contents.
        if prev_view is not None:
            prev_view.stop()

    # Player name lookup for the final summary.
    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(interaction.user.id)
        player_name = player.name if player else "Vô Danh"

    final_embed = _auto_repeat_final_embed(dungeon_key, totals, final_reason, player_name)
    view = DungeonResultView(
        dungeon_key, interaction.user.id, player_best_realm, player_realm_total,
        log_embeds=[],   # auto-repeat skips per-run logs to avoid spam
        back_fn=back_fn, dungeon_type=dungeon_type,
    )
    await interaction.edit_original_response(embed=final_embed, view=view)


async def _execute_dungeon(
    interaction: discord.Interaction,
    dungeon_key: str,
    player_best_realm: int,
    player_realm_total: int = 0,
    back_fn=None,
    dungeon_type: str = "normal",
    auto_mode: bool = False,
    stop_event: asyncio.Event | None = None,
) -> DungeonResult | None:
    """Run dungeon with real-time turn-by-turn battle display.

    auto_mode: when True, skip the inter-wave prep view (auto-continue) and
    suppress the final DungeonResultView render so a calling loop wrapper
    (``_run_dungeon_with_repeat``) can render its own aggregated summary.

    stop_event: when set between waves, treats the run like an abandon —
    saves any progress earned so far and returns. Used by auto-repeat to
    break out of a long sequence cleanly.
    """

    # ── Load player data ──────────────────────────────────────────────────────
    char: CharModel | None = None
    skill_keys: list[str] = []
    player_name = ""
    gem_count = 0
    gem_keys: list[str] = []

    async with get_session() as session:
        repo = PlayerRepository(session)
        player = await repo.get_by_discord_id(interaction.user.id)

        if player is None:
            await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
            return

        char = _player_to_model(player)
        ok, reason = check_can_enter(char, dungeon_key)
        if not ok:
            await interaction.edit_original_response(embed=error_embed(reason), view=None)
            return

        from src.game.systems.character_stats import (
            active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
        )
        gem_keys = active_formation_gem_keys(player)
        gem_map = active_formation_gem_map(player)
        gem_count = len(gem_keys)

        from src.game.engine.equipment import compute_equipment_stats
        equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        equip_stats = compute_equipment_stats(equipped)

        cs_preview = compute_combat_stats(
            char, gem_count=gem_count, equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_map,
            learned_skill_keys=[s.skill_key for s in (player.skills or [])],
        )
        if player.hp_current <= 0:
            player.hp_current = cs_preview.hp_max
            char.hp_current = player.hp_current

        skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
        player_name = player.name

    # Realm total for enemy scaling (computed from char to be always fresh)
    _realm_total = compute_realm_total(char)

    # ── Run interactive battle ────────────────────────────────────────────────
    dungeon = registry.get_dungeon(dungeon_key)
    if not dungeon:
        await interaction.edit_original_response(embed=error_embed("Bí cảnh không tồn tại."), view=None)
        return

    import random as _random_mod
    _rng = _random_mod.Random()

    player_c = build_player_combatant(
        char, skill_keys, gem_count, equip_stats=equip_stats,
        gem_keys=gem_keys, gem_keys_by_formation=gem_map,
    )
    req_realm = dungeon.get("required_qi_realm", 0)
    # Use the player's strongest qualifying axis for grade scaling so a
    # Thể Tu at body 7 entering a body-req=5 dungeon doesn't get "fresh"
    # grade rolls just because qi_realm is 0.
    _qual_realm, _qual_level = qualifying_axis(char, req_realm)
    grade_progress = _grade_progress(_qual_realm, _qual_level, req_realm)
    # Linh Căn dungeons cap enemy realm to +1 above the player's highest axis
    # so a Thể Tu at body 7 / qi 0 isn't blocked by the qi-only cap, and no
    # axis can pull a wave more than one realm above its own ceiling.
    # ``best_axis_realm`` is 0-indexed (0=Luyện Khí); enemy ``realm_level`` is
    # 1-indexed → +2 yields "best realm + 1" in enemy-space.
    _max_enemy_realm = (
        best_axis_realm(char) + 2
        if dungeon.get("dungeon_type") == "linh_can" else None
    )
    wave_enemies: list[str] = _build_wave_list(
        dungeon, _rng, max_realm_level=_max_enemy_realm,
    )
    total_waves = len(wave_enemies)
    boss_min_idx = int(dungeon.get("boss_min_grade_idx", 2))

    all_loot: list[dict] = []
    all_logs: list[str] = []
    merit_total = 0
    waves_cleared = 0
    died_on: str | None = None
    dungeon_success = False
    stone_gained = 0

    player_db_id: int = char.player_id

    for wave_idx, enemy_key in enumerate(wave_enemies):
        # Auto-repeat early-exit between waves: clean break, save partial progress.
        if stop_event is not None and stop_event.is_set():
            break

        # ── Inter-wave prepare phase (not before first wave) ──────────────────
        # In auto_mode, skip the prep view entirely — the auto-repeat loop owns
        # pacing and players don't expect to manually elixir between waves.
        if wave_idx > 0 and not auto_mode:
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

        is_boss = (wave_idx == total_waves - 1)
        grade = (
            _roll_boss_grade(grade_progress, _rng, min_grade_idx=boss_min_idx)
            if is_boss else _roll_encounter_grade(grade_progress, _rng)
        )
        grade_badge = f" {grade['emoji']} **{grade['vi']}**" if grade["emoji"] else ""

        edata = registry.get_enemy(enemy_key)
        if is_boss:
            wave_label = (f"👑 Boss: **{edata['vi']}**{grade_badge}" if edata else f"👑 Boss{grade_badge}")
        else:
            enemy_name = f"**{edata['vi']}**" if edata else enemy_key
            wave_label = f"Đợt {wave_idx + 1}: {enemy_name}{grade_badge}"

        all_logs.append(f"\n{'═' * 20}")
        all_logs.append(f"⚔️ **{wave_label}**")

        enemy_c = build_enemy_combatant(enemy_key, _realm_total)
        if not enemy_c:
            continue

        _apply_encounter_grade(enemy_c, grade, _rng)

        combat_session = CombatSession(
            player=player_c,
            enemy=enemy_c,
            player_skill_keys=skill_keys,
            loot_qty_multiplier=grade["loot_mult"],
            loot_luck_pct=grade.get("luck_pct", 0.0),
            auto_mode=auto_mode,
        )

        # Show wave start
        await interaction.edit_original_response(
            embed=battle_embed(
                wave_label, wave_idx, total_waves,
                player_c.name, player_c.hp, player_c.hp_max,
                player_c.mp, player_c.mp_max,
                enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                0, [],
                player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
                enemy_shield=enemy_c.shield, enemy_shield_cap=enemy_c.shield_cap(),
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
                merit_total += int(result.merit_gained * grade["merit_mult"])
                break

            await interaction.edit_original_response(
                embed=battle_embed(
                    wave_label, wave_idx, total_waves,
                    player_c.name, player_c.hp, player_c.hp_max,
                    player_c.mp, player_c.mp_max,
                    enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                    combat_session.turn, new_lines,
                    player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
                    enemy_shield=enemy_c.shield, enemy_shield_cap=enemy_c.shield_cap(),
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
                # Apply offline ticks accumulated during the battle so any
                # pending level-up materializes before we re-cap HP/MP;
                # otherwise player_c.hp_max (built at dungeon entry from
                # stale levels) can undershoot the true max.
                from src.game.systems.cultivation_service import apply_offline_ticks
                from src.game.systems.character_stats import (
                    active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
                )
                from src.game.engine.equipment import compute_equipment_stats

                await apply_offline_ticks(player, repo, player.active_axis or "qi")

                fresh_char = _player_to_model(player)
                fresh_gem_keys = active_formation_gem_keys(player)
                fresh_gem_map = active_formation_gem_map(player)
                fresh_equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
                fresh_cs = compute_combat_stats(
                    fresh_char,
                    gem_count=len(fresh_gem_keys),
                    equip_stats=compute_equipment_stats(fresh_equipped),
                    gem_keys=fresh_gem_keys,
                    gem_keys_by_formation=fresh_gem_map,
                    learned_skill_keys=[s.skill_key for s in (player.skills or [])],
                )
                player.hp_current = fresh_cs.hp_max
                player.mp_current = fresh_cs.mp_max
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

    # In auto-mode the loop wrapper renders its own aggregated summary;
    # skip the per-run result view so the next iteration can take over.
    if not auto_mode:
        summary_embed, log_embeds = _build_result_embeds(dungeon_key, result_obj, player_name)
        view = DungeonResultView(
            dungeon_key, interaction.user.id, player_best_realm, player_realm_total,
            log_embeds, back_fn=back_fn, dungeon_type=dungeon_type,
        )
        await interaction.edit_original_response(embed=summary_embed, view=view)

    return result_obj


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
        # Page index into the elixir list — preserved across re-renders so
        # the user stays on the same slice after consuming one.
        self._page = 0
        self._build_items()

    def _build_items(self) -> None:
        self.clear_items()
        # Clamp the page if elixirs shrank (e.g. last stack consumed).
        pages = total_pages(len(self.elixirs), per_page=PAGE_SIZE)
        self._page = max(0, min(self._page, pages - 1))
        if self.elixirs:
            visible = page_slice(self.elixirs, self._page, per_page=PAGE_SIZE)
            options = [
                discord.SelectOption(
                    label=f"{e['name']} × {e['qty']}"[:100],
                    value=e["key"],
                    description=f"Dùng 1 × {e['name']}"[:100],
                )
                for e in visible
            ]
            placeholder = "💊 Dùng Đan Dược..."
            if pages > 1:
                placeholder = f"💊 Dùng Đan Dược (Trang {self._page + 1}/{pages})..."
            sel = discord.ui.Select(
                placeholder=placeholder,
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

        add_page_controls(
            self,
            page=self._page,
            total=len(self.elixirs),
            on_change=self._on_page_change,
            row=2,
        )

    async def _on_page_change(self, interaction: discord.Interaction, new_page: int) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        self._page = new_page
        self._build_items()
        await interaction.response.edit_message(view=self)

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
        effect_msg = apply_healing_elixir(self.player_c, item_key)

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

    def __init__(
        self,
        discord_id: int,
        player_best_realm: int,
        player_realm_total: int,
        back_fn=None,
        dungeon_type: str = "normal",
    ) -> None:
        self.discord_id = discord_id
        self.player_best_realm = player_best_realm
        self.player_realm_total = player_realm_total
        self._back_fn = back_fn
        self._dungeon_type = dungeon_type

        pool = registry.dungeons_of_type(dungeon_type)
        pool = sorted(pool, key=lambda d: d.get("required_qi_realm", 0))
        options: list[discord.SelectOption] = []
        for d in pool[:25]:
            req = d.get("required_qi_realm", 0)
            req_label = QI_REALMS[req].vi if req < len(QI_REALMS) else f"Realm {req}"
            can_enter = req <= player_best_realm
            merit = d.get("merit_reward", 0)
            options.append(discord.SelectOption(
                label=d["vi"][:100],
                value=d["key"],
                description=f"Yêu cầu: {req_label} | +{merit:,} Công Đức"[:100],
                emoji="✅" if can_enter else "🔒",
            ))

        placeholder = {
            "duoc_vien": "🌿 Chọn Dược Viên...",
            "the_chat":  "🧬 Chọn Thần Cốt Địa...",
            "linh_can":  "🌌 Chọn Linh Mạch...",
        }.get(dungeon_type, "⚔️ Chọn Bí Cảnh...")
        if not options:
            options = [discord.SelectOption(label="(Không có)", value="__none__")]
        super().__init__(placeholder=placeholder, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        dungeon_key = self.values[0]
        if dungeon_key == "__none__":
            await interaction.response.defer()
            return
        embed = _dungeon_detail_embed(dungeon_key, interaction.user.id, self.player_best_realm, self.player_realm_total)
        view = DungeonDetailView(
            dungeon_key, interaction.user.id, self.player_best_realm, self.player_realm_total,
            back_fn=self._back_fn, dungeon_type=self._dungeon_type,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class DungeonListView(discord.ui.View):
    """View shown on the main /dungeon command — select dropdown + optional back button."""

    def __init__(
        self,
        discord_id: int,
        player_best_realm: int,
        player_realm_total: int = 0,
        back_fn=None,
        dungeon_type: str = "normal",
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._back_fn = back_fn
        self._player_best_realm = player_best_realm
        self._player_realm_total = player_realm_total
        self._dungeon_type = dungeon_type
        self.add_item(DungeonSelect(
            discord_id, player_best_realm, player_realm_total,
            back_fn=back_fn, dungeon_type=dungeon_type,
        ))
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


class DungeonTypeSelectView(discord.ui.View):
    """Two-button picker: ⚔️ Bí Cảnh Thường vs 🌿 Dược Viên."""

    def __init__(
        self,
        discord_id: int,
        player_best_realm: int,
        player_realm_total: int = 0,
        back_fn=None,
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._player_best_realm = player_best_realm
        self._player_realm_total = player_realm_total
        self._back_fn = back_fn

        normal_btn = discord.ui.Button(
            label="⚔️ Bí Cảnh Thường", style=discord.ButtonStyle.primary, row=0,
        )
        normal_btn.callback = self._pick_normal
        self.add_item(normal_btn)

        duoc_btn = discord.ui.Button(
            label="🌿 Dược Viên", style=discord.ButtonStyle.success, row=0,
        )
        duoc_btn.callback = self._pick_duoc_vien
        self.add_item(duoc_btn)

        the_chat_btn = discord.ui.Button(
            label="🧬 Thần Cốt Địa", style=discord.ButtonStyle.danger, row=0,
        )
        the_chat_btn.callback = self._pick_the_chat
        self.add_item(the_chat_btn)

        linh_can_btn = discord.ui.Button(
            label="🌌 Linh Căn Bí Cảnh", style=discord.ButtonStyle.secondary, row=1,
        )
        linh_can_btn.callback = self._pick_linh_can
        self.add_item(linh_can_btn)

        if back_fn:
            back_btn = discord.ui.Button(
                label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2,
            )
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _open_list(self, interaction: discord.Interaction, dungeon_type: str) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        embed = _dungeon_list_embed(self._player_best_realm, dungeon_type=dungeon_type)
        view = DungeonListView(
            self._discord_id, self._player_best_realm, self._player_realm_total,
            back_fn=self._back_fn, dungeon_type=dungeon_type,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _pick_normal(self, interaction: discord.Interaction) -> None:
        await self._open_list(interaction, "normal")

    async def _pick_duoc_vien(self, interaction: discord.Interaction) -> None:
        await self._open_list(interaction, "duoc_vien")

    async def _pick_the_chat(self, interaction: discord.Interaction) -> None:
        await self._open_list(interaction, "the_chat")

    async def _pick_linh_can(self, interaction: discord.Interaction) -> None:
        await self._open_list(interaction, "linh_can")

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
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
        player_best_realm: int,
        player_realm_total: int = 0,
        back_fn=None,
        dungeon_type: str = "normal",
    ) -> None:
        super().__init__(timeout=120)
        self.dungeon_key = dungeon_key
        self.discord_id = discord_id
        self.player_best_realm = player_best_realm
        self.player_realm_total = player_realm_total
        self._back_fn = back_fn
        self._dungeon_type = dungeon_type

    @discord.ui.button(label="⚔️ Vào Bí Cảnh", style=discord.ButtonStyle.green, row=1)
    async def enter_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        # Block parallel sessions BEFORE deferring so the rejection lands as a
        # plain ephemeral response on the original interaction.
        if not _try_acquire_dungeon_session(interaction.user.id):
            await _send_already_running_error(interaction)
            return
        await interaction.response.defer()
        try:
            await _execute_dungeon(
                interaction, self.dungeon_key, self.player_best_realm, self.player_realm_total,
                back_fn=self._back_fn, dungeon_type=self._dungeon_type,
            )
        finally:
            _release_dungeon_session(interaction.user.id)

    @discord.ui.button(label="🔁 Tự Động Lặp Lại", style=discord.ButtonStyle.blurple, row=1)
    async def auto_repeat_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        if not _try_acquire_dungeon_session(interaction.user.id):
            await _send_already_running_error(interaction)
            return
        await interaction.response.defer()
        try:
            await _run_dungeon_with_repeat(
                interaction, self.dungeon_key, self.player_best_realm, self.player_realm_total,
                back_fn=self._back_fn, dungeon_type=self._dungeon_type,
            )
        finally:
            _release_dungeon_session(interaction.user.id)

    @discord.ui.button(label="◀ Quay lại", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Đây không phải lệnh của bạn.", ephemeral=True)
            return
        embed = _dungeon_list_embed(self.player_best_realm, dungeon_type=self._dungeon_type)
        view = DungeonListView(
            self.discord_id, self.player_best_realm, self.player_realm_total,
            back_fn=self._back_fn, dungeon_type=self._dungeon_type,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_timeout(self) -> None:
        pass


class DungeonResultView(discord.ui.View):
    """View shown after dungeon completes — View Log + Back to list buttons."""

    def __init__(
        self,
        dungeon_key: str,
        discord_id: int,
        player_best_realm: int,
        player_realm_total: int,
        log_embeds: list[discord.Embed],
        back_fn=None,
        dungeon_type: str = "normal",
    ) -> None:
        super().__init__(timeout=120)
        self.dungeon_key = dungeon_key
        self.discord_id = discord_id
        self.player_best_realm = player_best_realm
        self.player_realm_total = player_realm_total
        self.log_embeds = log_embeds
        self._back_fn = back_fn
        self._dungeon_type = dungeon_type
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
        embed = _dungeon_list_embed(self.player_best_realm, dungeon_type=self._dungeon_type)
        view = DungeonListView(
            self.discord_id, self.player_best_realm, self.player_realm_total,
            back_fn=self._back_fn, dungeon_type=self._dungeon_type,
        )
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

        player_realm_total = compute_realm_total(player)

        # Cross-path entry gate: a Thể Tu / Trận Tu qualifies via their
        # strongest axis. Mirrors the /status hub's dungeon entry which
        # already uses ``best_axis_realm`` — the standalone slash command
        # used to pass raw ``qi_realm`` and locked late-realm body/formation
        # players out of dungeons their realm-total clearly qualified for.
        player_best_realm = best_axis_realm(player)
        embed = _dungeon_type_embed()
        view = DungeonTypeSelectView(interaction.user.id, player_best_realm, player_realm_total)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DungeonCog(bot))
