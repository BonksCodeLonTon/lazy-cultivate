"""Cultivation commands — register, cultivate, breakthrough."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.db.connection import get_session
from src.db.models.reroll_tracker import RerollTracker
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.constants.realms import realm_label
from src.game.systems.cultivation import (
    can_breakthrough,
    apply_breakthrough,
    effective_formation_exp_per_merit,
    formation_exp_per_merit,
    get_breakthrough_requirements,
    study_formation_with_merit,
)
from src.game.systems.cultivation_service import (
    apply_offline_ticks,
    pre_breakthrough_realm,
)
from src.utils import emojis
from src.utils.embed_builder import base_embed, error_embed, success_embed
from src.utils.assets import AXIS_LABELS, AXIS_ICONS
from src.utils.discord_safe import safe_defer

log = logging.getLogger(__name__)

_AXIS_CONFIGS = [
    ("body",      "💪 Luyện Thể"),
    ("qi",        "🔮 Luyện Khí"),
    ("formation", "🔯 Trận Đạo"),
]

# Divider sized to roughly span the 3-axis button row below the panel, so the
# Tu Luyện embed reads as wide as its controls instead of collapsing narrow.
_PANEL_RULE = "─" * 48

# Maximum total /reset_character uses per Discord user (lifetime cap).
REROLL_LIMIT = 10000

# ── Embed builders ────────────────────────────────────────────────────────────

def _cultivate_embed(axis: str, result: dict) -> discord.Embed:
    axis_label  = AXIS_LABELS.get(axis, axis)
    axis_icon   = AXIS_ICONS.get(axis, "🌀")
    turns       = result.get("turns", 0)
    merit       = result.get("merit_gained", 0)
    karma       = result.get("karma_gained", 0)
    cult        = result.get("cult_result", {})
    exp_gained  = cult.get("exp_gained", 0)
    levels_up   = cult.get("levels_gained", 0)
    ready_trib  = cult.get("is_ready_for_tribulation", False)
    cap_reached = result.get("cap_reached", False)

    lines = [
        f"Hướng: {axis_icon} **{axis_label}**",
        _PANEL_RULE,
        f"Lượt xử lý: **{turns:,}** lượt",
        f"{emojis.for_currency('merit')} Công Đức nhận: **+{merit:,}**",
        f"{emojis.for_currency('karma_accum')} Nghiệp Lực tích lũy: **+{karma:,}**",
    ]
    if exp_gained:
        lines.append(f"📘 EXP tu luyện: **+{exp_gained:,}**")
    if levels_up:
        lines.append(f"✨ Cảnh giới tiến: **+{levels_up} cấp**")
    if axis == "formation" and turns > 0 and exp_gained == 0:
        lines.append("ℹ️ *Trận Đạo chỉ tiến bằng Công Đức — bấm **📘 Học Trận** bên dưới.*")
    if turns == 0:
        if cap_reached:
            lines.append("⏳ *Đã đạt giới hạn **1440 lượt/ngày** — quay lại ngày mai.*")
        else:
            lines.append("⏳ *Chưa đủ thời gian tích lũy — chờ ít nhất 1 phút giữa các lần.*")
    if ready_trib:
        lines.append("⚡ Linh khí đã đủ — có thể **Độ Kiếp**!")

    return base_embed("🌀 Tu Luyện", "\n".join(lines), color=0x8B5CF6)


def _breakthrough_overview_embed(player, readiness: dict[str, bool]) -> discord.Embed:
    embed = base_embed("⚡ Đột Phá Cảnh Giới", color=0xF1C40F)
    for axis, (_, label) in zip(("body", "qi", "formation"), _AXIS_CONFIGS):
        rl = {
            "body":      realm_label("body",      player.body_realm,      player.body_xp),
            "qi":        realm_label("qi",        player.qi_realm,        player.qi_xp),
            "formation": realm_label("formation", player.formation_realm, player.formation_xp),
        }[axis]
        status = "✅ Sẵn sàng đột phá" if readiness[axis] else "🔒 Chưa đủ điều kiện"
        embed.add_field(name=label, value=f"{rl}\n{status}", inline=True)
    embed.set_footer(text="Chọn hướng để tiến hành đột phá")
    return embed


# ── Views ─────────────────────────────────────────────────────────────────────

class StudyFormationModal(discord.ui.Modal, title="Học Trận với Công Đức"):
    """Modal that converts Công Đức → Trận Đạo EXP. Validates the requested
    amount against the **live** DB merit at submit time so the user can
    never spend more than they currently hold (defends against the modal
    being opened while merit changed in another command)."""

    merits = discord.ui.TextInput(
        label="Số Công Đức muốn dùng",
        placeholder="Nhập số nguyên dương",
        required=True,
        max_length=12,
    )

    def __init__(
        self,
        discord_id: int,
        current_merit: int,
        exp_per_merit: float,
        refresh_fn,
    ) -> None:
        super().__init__()
        self._discord_id = discord_id
        self._refresh_fn = refresh_fn
        # Sub-1 rates flip the display to "X Công Đức = 1 EXP" so the
        # granularity is obvious at a glance.
        if exp_per_merit >= 1.0:
            rate_text = f"1 Công Đức = {exp_per_merit:g} EXP"
        else:
            from math import ceil
            cost = max(1, ceil(1.0 / exp_per_merit))
            rate_text = f"{cost:,} Công Đức = 1 EXP"
        self.merits.placeholder = f"Tối đa {current_merit:,}  ·  {rate_text}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return

        raw = (self.merits.value or "").strip().replace(",", "").replace(".", "")
        if not raw.isdigit():
            await interaction.response.send_message(
                embed=error_embed("Nhập số nguyên dương."), ephemeral=True,
            )
            return
        amount = int(raw)
        if amount <= 0:
            await interaction.response.send_message(
                embed=error_embed("Số Công Đức phải lớn hơn 0."), ephemeral=True,
            )
            return

        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player_orm = await repo.get_by_discord_id(interaction.user.id)
            if player_orm is None:
                await interaction.followup.send(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return

            if amount > player_orm.merit:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Không đủ Công Đức (cần {amount:,}, hiện có {player_orm.merit:,})."
                    ),
                    ephemeral=True,
                )
                return

            char = _player_to_model(player_orm)
            res = study_formation_with_merit(char, amount)
            if not res["success"]:
                await interaction.followup.send(embed=error_embed(res["error"]), ephemeral=True)
                return

            player_orm.merit = char.merit
            player_orm.formation_xp = char.formation_xp
            player_orm.formation_level = char.formation_level
            await session.commit()

        eff_rate = res.get("effective_exp_per_merit", res["exp_per_merit"])
        speed_mult = res.get("speed_mult", 1.0)
        # Show the effective rate the player actually got. When penalties
        # /bonuses move the multiplier off 1.0 we annotate the line so the
        # number match between merit spent and EXP gained is obvious.
        rate_line = f"_(tỷ lệ: 1 Công Đức = {eff_rate:g} EXP tại cảnh giới này"
        if abs(speed_mult - 1.0) > 1e-6:
            rate_line += f" · gốc {res['exp_per_merit']:g} × hệ số {speed_mult:.2f}"
        rate_line += ")_"
        await interaction.followup.send(
            embed=success_embed(
                f"Tiêu {emojis.for_currency('merit')} {res['merit_spent']:,} Công Đức "
                f"→ +{res['exp_gained']:,} EXP Trận Đạo "
                f"{rate_line}."
            ),
            ephemeral=True,
        )
        if self._refresh_fn:
            await self._refresh_fn(interaction)


class CultivateView(discord.ui.View):
    def __init__(self, discord_id: int, active_axis: str, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._active_axis = active_axis
        self._back_fn = back_fn

        for axis_id, axis_label in _AXIS_CONFIGS:
            style = discord.ButtonStyle.primary if axis_id == active_axis else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=axis_label, style=style, row=0)
            btn.callback = self._make_cb(axis_id)
            self.add_item(btn)

        if active_axis == "formation":
            study_btn = discord.ui.Button(
                label="📘 Học Trận (Công Đức)",
                style=discord.ButtonStyle.success,
                row=1,
            )
            study_btn.callback = self._study_cb
            self.add_item(study_btn)

        bt_btn = discord.ui.Button(
            label="⚡ Đột Phá", style=discord.ButtonStyle.secondary, row=1,
        )
        bt_btn.callback = self._open_breakthrough_cb
        self.add_item(bt_btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
            back.callback = self._back_cb
            self.add_item(back)

    async def _study_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(
                    embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                )
                return
            current_merit = player.merit
            # Use the *effective* rate (constitution speed bonus + Đan Độc
            # toxicity penalty) so the placeholder matches what the player
            # actually receives — otherwise R0 shows "1 Công Đức = 1 EXP"
            # but a poisoned player only gets 0.95 EXP per merit.
            char = _player_to_model(player)
            rate = effective_formation_exp_per_merit(char)

        if current_merit <= 0:
            await interaction.response.send_message(
                embed=error_embed("Bạn chưa có Công Đức nào để học Trận."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            StudyFormationModal(self._discord_id, current_merit, rate, self._refresh_panel)
        )

    async def _refresh_panel(self, interaction: discord.Interaction) -> None:
        """Re-render the cultivate panel after a successful merit study so the
        user sees their updated formation bậc / XP without a manual click."""
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(self._discord_id)
            if player is None:
                return
            result = await apply_offline_ticks(player, repo, "formation")
            await session.commit()

        embed = _cultivate_embed("formation", result)
        view = CultivateView(self._discord_id, "formation", back_fn=self._back_fn)
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except discord.HTTPException:
            pass

    def _make_cb(self, axis: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            if not await safe_defer(interaction):
                return
            async with get_session() as session:
                repo = PlayerRepository(session)
                player = await repo.get_by_discord_id(interaction.user.id)

                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                char = _player_to_model(player)

                from src.game.systems.tribulation import TribulationManager
                trib_manager = TribulationManager()

                if trib_manager.check_needs_tribulation(char, axis):
                    await interaction.edit_original_response(
                        embed=error_embed(
                            "⚡ Bạn đã đạt cực hạn cảnh giới!\n"
                            "Hãy **đột phá (Thiên Kiếp)** để tiếp tục tu luyện."
                        ),
                        view=CultivateView(self._discord_id, axis, back_fn=self._back_fn)
                    )
                    return

                result = await apply_offline_ticks(player, repo, axis)
                
                await session.commit()

            char = _player_to_model(player)
            from src.game.systems.tribulation import TribulationManager
            if TribulationManager().check_needs_tribulation(char, axis):
                await interaction.edit_original_response(
                    embed=error_embed("⚡ Đã đạt cực hạn, hãy Đột Phá để tiếp tục!"),
                    view=CultivateView(self._discord_id, axis, back_fn=self._back_fn)
                )
                return

            embed = _cultivate_embed(axis, result)
            new_view = CultivateView(self._discord_id, axis, back_fn=self._back_fn)
            await interaction.edit_original_response(embed=embed, view=new_view)

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)

    async def _open_breakthrough_cb(self, interaction: discord.Interaction) -> None:
        """Switch to the Đột Phá panel in place — the merged Tu Luyện ⇄ Đột Phá
        UI. ``◀ Trở về`` on either panel still exits to the status hub."""
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            inventory_map: dict[str, int] = {}
            for inv_item in player.inventory:
                inventory_map[inv_item.item_key] = (
                    inventory_map.get(inv_item.item_key, 0) + inv_item.quantity
                )
            char = _player_to_model(player)
            readiness: dict[str, bool] = {}
            for ax in ("body", "qi", "formation"):
                ok, _ = can_breakthrough(char, ax, inventory=inventory_map)
                readiness[ax] = ok
        embed = _breakthrough_overview_embed(player, readiness)
        view = BreakthroughView(self._discord_id, readiness, back_fn=self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)


class BreakthroughView(discord.ui.View):
    def __init__(self, discord_id: int, readiness: dict[str, bool], back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._readiness = readiness
        self._back_fn = back_fn

        for axis_id, axis_label in _AXIS_CONFIGS:
            style = discord.ButtonStyle.success if readiness[axis_id] else discord.ButtonStyle.secondary
            btn = discord.ui.Button(label=axis_label, style=style, row=0)
            btn.callback = self._make_cb(axis_id)
            self.add_item(btn)

        tl_btn = discord.ui.Button(
            label="🌀 Tu Luyện", style=discord.ButtonStyle.secondary, row=1,
        )
        tl_btn.callback = self._open_cultivate_cb
        self.add_item(tl_btn)

        if back_fn:
            back = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1)
            back.callback = self._back_cb
            self.add_item(back)

    def _make_cb(self, axis: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            if not await safe_defer(interaction):
                return
            async with get_session() as session:
                repo = PlayerRepository(session)
                player = await repo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                inventory_map: dict[str, int] = {}
                for inv_item in player.inventory:
                    inventory_map[inv_item.item_key] = inventory_map.get(inv_item.item_key, 0) + inv_item.quantity

                char = _player_to_model(player)

                ok, reason = can_breakthrough(char, axis, inventory=inventory_map)
                if not ok:
                    await interaction.edit_original_response(
                        embed=error_embed(reason),
                        view=BreakthroughView(self._discord_id, self._readiness, back_fn=self._back_fn),
                    )
                    return

                from src.game.systems.tribulation import TribulationManager
                trib_manager = TribulationManager()

                if trib_manager.check_needs_tribulation(char, axis):
                    equipped_skill_keys = [s.skill_key for s in player.skills]

                    from src.game.systems.character_stats import (
                        active_formation_gem_keys, active_formation_gem_map,
                    )
                    trib_gem_keys = active_formation_gem_keys(player)
                    trib_gem_map = active_formation_gem_map(player)
                    gem_count = len(trib_gem_keys)

                    from src.game.engine.equipment import compute_equipment_stats
                    equipped_items = [i for i in (player.item_instances or []) if i.location == "equipped"]
                    equip_stats = compute_equipment_stats(equipped_items)

                    trib_result = await trib_manager.run_tribulation(
                        interaction,
                        char,
                        axis,
                        equipped_skill_keys,
                        gem_count,
                        equip_stats=equip_stats,
                        gem_keys=trib_gem_keys,
                        gem_keys_by_formation=trib_gem_map,
                    )

                    if not trib_result.success:
                        player.hp_current = 1

                        if trib_result.cultivation_lost:
                            current_lv = getattr(player, f"{axis}_level")
                            setattr(player, f"{axis}_level", max(1, current_lv - 1))

                        await repo.save(player)
                        return

                    realm_name = realm_label(
                        axis,
                        getattr(player, f"{axis}_realm"),
                        getattr(player, f"{axis}_xp"),
                    )
                    await interaction.followup.send(f"**{player.name}** đã vượt qua Thiên Kiếp của cảnh giới **{realm_name}**!")

                from src.db.repositories.inventory_repo import InventoryRepository
                inv_repo = InventoryRepository(session)

                old_realm_idx = pre_breakthrough_realm(player, axis)
                reqs = get_breakthrough_requirements(axis, old_realm_idx)

                apply_breakthrough(char, axis, inventory=inventory_map)

                if reqs["item_key"] and reqs["quantity"]:
                    # Pills are stored per-quality (Hoàn/Huyền/Địa/Thiên) so a
                    # player's stack can be split across rows. ``remove_any_grade``
                    # iterates ascending-by-grade and decrements greedily — for
                    # single-grade materials it behaves identically to a
                    # single-row remove.
                    ok = await inv_repo.remove_any_grade(
                        player.id, reqs["item_key"], reqs["quantity"]
                    )
                    # If the consumed item grants a permanent pill-buff counter
                    # (used by the permanent-per-pill system), increment the
                    # player's stored counters so the stat bonus persists.
                    if ok:
                        try:
                            from src.data.registry import registry as _reg
                            from src.game.systems.pill_buffs import (
                                parse_counts, increment_count, encode_counts,
                            )
                            item_def = _reg.get_item(reqs["item_key"]) or {}
                            buff_key = item_def.get("grant_pill_buff")
                            if buff_key:
                                counts = parse_counts(player.pill_buff_counts)
                                for _ in range(int(reqs.get("quantity", 0))):
                                    did_inc, counts = increment_count(counts, buff_key)
                                    if not did_inc:
                                        break
                                player.pill_buff_counts = encode_counts(counts)
                        except Exception:
                            # Fail silently — granting the permanent counter is
                            # best-effort and should not block the breakthrough
                            # flow. Log for future debugging.
                            import logging

                            logging.getLogger(__name__).exception(
                                "Failed to apply grant_pill_buff for %s",
                                reqs["item_key"],
                            )

                player.body_realm      = char.body_realm
                player.body_level      = char.body_level
                player.body_xp         = char.body_xp
                player.qi_realm        = char.qi_realm
                player.qi_level        = char.qi_level
                player.qi_xp           = char.qi_xp
                player.formation_realm = char.formation_realm
                player.formation_level = char.formation_level
                player.formation_xp    = char.formation_xp
                player.merit           = char.merit
                player.dao_ti_unlocked = char.dao_ti_unlocked
                player.dan_doc         = char.dan_doc

                char = _player_to_model(player)

                from src.game.systems.character_stats import (
                    active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
                )
                post_gem_keys = active_formation_gem_keys(player)
                post_gem_map = active_formation_gem_map(player)
                post_cs = compute_combat_stats(
                    char, gem_count=len(post_gem_keys),
                    gem_keys=post_gem_keys, gem_keys_by_formation=post_gem_map,
                )

                player.hp_current = post_cs.hp_max
                player.mp_current = post_cs.mp_max
                player.shield_current = post_cs.shield_max

                await repo.save(player)

                new_readiness = {}
                for ax in ("body", "qi", "formation"):
                    ready, _ = can_breakthrough(char, ax, inventory=inventory_map)
                    new_readiness[ax] = ready

                realm_name = realm_label(
                    axis,
                    getattr(player, f"{axis}_realm"),
                    getattr(player, f"{axis}_xp"),
                )

                embed = success_embed(f"Chúc mừng bạn đã đột phá lên **{realm_name}**!")
                if player.dao_ti_unlocked and axis == "body":
                    embed.add_field(name="✨ Thông báo", value="**Đạo Thể** của bạn đã thức tỉnh!")

                await interaction.edit_original_response(
                    embed=embed,
                    view=BreakthroughView(self._discord_id, new_readiness, back_fn=self._back_fn),
                )

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)

    async def _open_cultivate_cb(self, interaction: discord.Interaction) -> None:
        """Switch to the Tu Luyện panel in place — the merged Tu Luyện ⇄ Đột Phá
        UI. ``◀ Trở về`` on either panel still exits to the status hub."""
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            active = player.active_axis or "qi"
            result = await apply_offline_ticks(player, repo, active)
        embed = _cultivate_embed(active, result)
        view = CultivateView(self._discord_id, active, back_fn=self._back_fn)
        await interaction.edit_original_response(embed=embed, view=view)


# ── Cog ───────────────────────────────────────────────────────────────────────

class CultivationCog(commands.Cog, name="Cultivation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="register", description="Tạo nhân vật tu tiên")
    @app_commands.describe(name="Đạo hiệu của bạn")
    async def register(self, interaction: discord.Interaction, name: str) -> None:
        if len(name) < 2 or len(name) > 24:
            return await interaction.response.send_message(embed=error_embed("Tên từ 2–24 ký tự."), ephemeral=True)

        async with get_session() as session:
            repo = PlayerRepository(session)
            if await repo.exists(interaction.user.id):
                return await interaction.response.send_message(embed=error_embed("Ngươi đã có nhân vật rồi."), ephemeral=True)
            
            player = await repo.create(discord_id=interaction.user.id, name=name)
            await session.commit()
            rolled_const = player.constitution_type
            rolled_linh_can = player.linh_can

        from src.data.registry import registry
        from src.game.constants.linh_can import LINH_CAN_DATA, parse_linh_can_levels
        from src.bot.cogs.constitution import _format_bonus_lines
        const_data = registry.get_constitution(rolled_const) or {}
        rarity_labels = {
            "common": "Phổ Thông",
            "uncommon": "Hiếm",
            "rare": "Quý",
            "epic": "Sử Thi",
            "legendary": "Truyền Thuyết",
        }
        rarity = const_data.get("rarity", "common")
        rarity_vi = rarity_labels.get(rarity, rarity)

        levels = parse_linh_can_levels(rolled_linh_can or "")
        if levels:
            linh_can_line = " · ".join(
                f"{LINH_CAN_DATA[elem]['emoji']} **{LINH_CAN_DATA[elem]['vi']}** Lv{lvl}"
                for elem, lvl in levels.items()
            )
        else:
            linh_can_line = "*(chưa khai mở)*"

        from src.bot.cogs.constitution import _body_description
        const_vi = const_data.get("vi", rolled_const)
        const_desc = _body_description(const_data)
        bonus_lines = _format_bonus_lines(const_data.get("stat_bonuses", {}))

        const_value_parts = [f"**{const_vi}** {emojis.for_rarity(rarity)} *{rarity_vi}*"]
        if const_desc:
            const_value_parts.append(f"_{const_desc}_")
        if bonus_lines:
            const_value_parts.append("**Chỉ số:**\n" + "\n".join(bonus_lines))

        embed = base_embed(
            title=f"🎉 {name} đã bước vào con đường tu tiên!",
            description=(
                "Đạo hữu khởi đầu hành trình với thiên phú riêng. "
                "Hãy dùng `/status` để xem chi tiết."
            ),
            color=0xD4A017,
        )
        embed.add_field(name="🌿 Linh Căn", value=linh_can_line, inline=False)
        embed.add_field(
            name="🧬 Thể Chất Sơ Khởi",
            value="\n".join(const_value_parts),
            inline=False,
        )
        embed.set_footer(text="Dùng /cultivate để bắt đầu tu luyện · /status xem trạng thái")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="reset_character",
        description="Xóa nhân vật hiện tại để /register lại từ đầu (tối đa 5 lần)",
    )
    @app_commands.describe(confirm="Gõ XOA để xác nhận xóa nhân vật")
    async def reset_character(
        self, interaction: discord.Interaction, confirm: str
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        if confirm.strip().upper() != "XOA":
            await interaction.followup.send(
                embed=error_embed("Hủy reset — gõ chính xác `XOA` để xác nhận."),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "Ngươi chưa có nhân vật để reset. Dùng `/register`."
                    ),
                    ephemeral=True,
                )
                return

            # Reroll cap — counter lives on its own table so deleting the
            # player row doesn't reset it. 5 attempts total per Discord user.
            tracker = await session.get(RerollTracker, interaction.user.id)
            used = tracker.count if tracker else 0
            if used >= REROLL_LIMIT:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Đã dùng hết **{REROLL_LIMIT}/{REROLL_LIMIT}** lượt reset — "
                        "không thể tu luyện lại nữa."
                    ),
                    ephemeral=True,
                )
                return

            old_name = player.name
            counts = {
                "items": len(player.item_instances or []),
                "inventory": len(player.inventory or []),
                "skills": len(player.skills or []),
                "formations": len(player.formations or []),
                "artifacts": len(player.artifacts or []),
            }
            # Player relationships use cascade="all, delete-orphan" and every
            # FK to players.id has ondelete="CASCADE", so deleting the Player
            # purges item_instances (bag + equipped), inventory, skills,
            # formations, artifacts, market listings, turn tracker, and
            # world-boss participations.
            await session.delete(player)

            if tracker is None:
                tracker = RerollTracker(discord_id=interaction.user.id, count=1)
                session.add(tracker)
            else:
                tracker.count = used + 1
            await session.commit()
            remaining = REROLL_LIMIT - tracker.count

        await interaction.followup.send(
            embed=success_embed(
                f"✅ Đã xóa nhân vật **{old_name}** cùng toàn bộ tài sản:\n"
                f"• 🗡️ Trang bị / vật phẩm: **{counts['items']}**\n"
                f"• 🎒 Kho vật liệu: **{counts['inventory']}** dòng\n"
                f"• 🎯 Kỹ năng: **{counts['skills']}**\n"
                f"• 🔯 Trận pháp: **{counts['formations']}**\n"
                f"• 🏺 Pháp bảo: **{counts['artifacts']}**\n\n"
                f"♻️ Lượt reset còn lại: **{remaining}/{REROLL_LIMIT}**\n"
                f"Dùng `/register <tên>` để tạo nhân vật mới."
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="rename",
        description="Đổi Đạo hiệu của bạn",
    )
    @app_commands.describe(new_name="Đạo hiệu mới (2–24 ký tự)")
    async def rename(
        self, interaction: discord.Interaction, new_name: str,
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        new_name = new_name.strip()
        if len(new_name) < 2 or len(new_name) > 24:
            await interaction.followup.send(
                embed=error_embed("Đạo hiệu phải từ 2–24 ký tự."),
                ephemeral=True,
            )
            return

        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                    ephemeral=True,
                )
                return

            if player.name == new_name:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Đạo hiệu của ngươi đã là **{new_name}** — không có gì để đổi."
                    ),
                    ephemeral=True,
                )
                return

            old_name = player.name
            player.name = new_name
            await session.commit()

        await interaction.followup.send(
            embed=success_embed(
                f"✅ Đã đổi đạo hiệu **{old_name}** → **{new_name}**."
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="legendary_reroll",
        description="Đổi Thể Chất sơ khởi sang một Truyền Thuyết (chỉ dùng được 1 lần)",
    )
    async def legendary_reroll(self, interaction: discord.Interaction) -> None:
        import random

        from src.data.registry import registry
        from src.game.constants.linh_can import parse_linh_can
        from src.game.systems.the_chat import get_constitutions, set_constitutions

        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                    ephemeral=True,
                )
                return

            tracker = await session.get(RerollTracker, interaction.user.id)
            if tracker and tracker.legendary_reroll_used:
                await interaction.followup.send(
                    embed=error_embed(
                        "Ngươi đã dùng lượt **Đổi Thể Chất Truyền Thuyết** rồi — "
                        "mỗi đạo hữu chỉ có 1 lần duy nhất."
                    ),
                    ephemeral=True,
                )
                return

            player_elems = set(parse_linh_can(player.linh_can or ""))
            leg_pool = [
                c for c in registry.constitutions.values()
                if c.get("rarity") == "legendary"
                and not c.get("special_requirements")
                and not c.get("progresses_from")
                and (c.get("element") is None or c.get("element") in player_elems)
            ]
            if not leg_pool:
                await interaction.followup.send(
                    embed=error_embed(
                        "Không tìm thấy Thể Chất Truyền Thuyết phù hợp với "
                        "Linh Căn hiện tại — hãy mở thêm Linh Căn rồi thử lại."
                    ),
                    ephemeral=True,
                )
                return

            chosen = random.choice(leg_pool)
            chosen_key = chosen["key"]

            # Replace the primary slot (first entry) with the rolled legendary,
            # preserving any extra slots the player has equipped.
            equipped = get_constitutions(player.constitution_type)
            old_primary = equipped[0] if equipped else None
            new_equipped = [chosen_key] + [k for k in equipped[1:] if k != chosen_key]
            player.constitution_type = set_constitutions(new_equipped)

            if tracker is None:
                tracker = RerollTracker(
                    discord_id=interaction.user.id,
                    count=0,
                    legendary_reroll_used=True,
                )
                session.add(tracker)
            else:
                tracker.legendary_reroll_used = True

            await session.commit()

        from src.bot.cogs.constitution import _body_description, _format_bonus_lines
        old_data = registry.get_constitution(old_primary) if old_primary else None
        old_vi = old_data.get("vi", old_primary or "—") if old_data else (old_primary or "—")
        new_vi = chosen.get("vi", chosen_key)
        bonus_lines = _format_bonus_lines(chosen.get("stat_bonuses", {}))
        passive_desc = _body_description(chosen)

        body_parts = [
            f"🌟 **{old_vi}** → ✨ **{new_vi}** *(Truyền Thuyết)*",
        ]
        if passive_desc:
            body_parts.append(f"_{passive_desc}_")
        if bonus_lines:
            body_parts.append("**Chỉ số:**\n" + "\n".join(bonus_lines))
        body_parts.append("⚠️ Lượt đổi Thể Chất Truyền Thuyết đã sử dụng — không thể dùng lại.")

        await interaction.followup.send(
            embed=success_embed("\n\n".join(body_parts)),
            ephemeral=True,
        )

    @app_commands.command(name="cultivate", description="Tu luyện — áp dụng AFK ticks")
    async def cultivate(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player = await repo.get_by_discord_id(interaction.user.id)
            
            if player is None:
                await interaction.followup.send(
                    embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                    ephemeral=True,
                )
                return

            active = player.active_axis or "qi"
            result = await apply_offline_ticks(player, repo, active)

        embed = _cultivate_embed(active, result)
        view = CultivateView(interaction.user.id, active)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="study_formation", description="Dùng Công Đức tăng Trận Đạo")
    @app_commands.describe(merits="Số lượng Công Đức (≥1)")
    async def study_formation(
        self,
        interaction: discord.Interaction,
        merits: app_commands.Range[int, 1, None],
    ) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player_orm = await repo.get_by_discord_id(interaction.user.id)
            if not player_orm:
                return await interaction.followup.send(embed=error_embed("Chưa có nhân vật."), ephemeral=True)

            if merits > player_orm.merit:
                return await interaction.followup.send(
                    embed=error_embed(
                        f"Không đủ Công Đức (cần {merits:,}, hiện có {player_orm.merit:,})."
                    ),
                    ephemeral=True,
                )

            char = _player_to_model(player_orm)
            res = study_formation_with_merit(char, merits)
            if not res["success"]:
                return await interaction.followup.send(embed=error_embed(res["error"]), ephemeral=True)

            player_orm.merit = char.merit
            player_orm.formation_xp = char.formation_xp
            player_orm.formation_level = char.formation_level
            await session.commit()

            eff_rate = res.get("effective_exp_per_merit", res["exp_per_merit"])
            speed_mult = res.get("speed_mult", 1.0)
            rate_line = f"_(tỷ lệ: 1 Công Đức = {eff_rate:g} EXP tại cảnh giới này"
            if abs(speed_mult - 1.0) > 1e-6:
                rate_line += f" · gốc {res['exp_per_merit']:g} × hệ số {speed_mult:.2f}"
            rate_line += ")_"
            await interaction.followup.send(
                embed=success_embed(
                    f"Tiêu {emojis.for_currency('merit')} {res['merit_spent']:,} Công Đức "
                    f"→ +{res['exp_gained']:,} EXP Trận Đạo "
                    f"{rate_line}."
                )
            )

    @app_commands.command(name="breakthrough", description="Độ Kiếp")
    async def breakthrough(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction, ephemeral=True):
            return
        async with get_session() as session:
            repo = PlayerRepository(session)
            player_orm = await repo.get_by_discord_id(interaction.user.id)
            if not player_orm: return

            char = _player_to_model(player_orm)
            axis = char.active_axis

            # Aggregate by item_key across all grade rows — pills sit in
            # inventory at their alchemy quality (Hoàn/Huyền/Địa/Thiên), so a
            # naive dict-comp would only keep one row per item_key.
            inventory_map: dict[str, int] = {}
            for inv_item in player_orm.inventory:
                inventory_map[inv_item.item_key] = (
                    inventory_map.get(inv_item.item_key, 0) + inv_item.quantity
                )

            ok, reason = can_breakthrough(char, axis, inventory=inventory_map)
            if not ok:
                await interaction.followup.send(embed=error_embed(reason), ephemeral=True)
                return

            from src.game.systems.tribulation import TribulationManager
            manager = TribulationManager()
            skill_keys = [s.skill_key for s in player_orm.skills]
            result = await manager.run_tribulation(interaction, char, axis, skill_keys)

            if result.success:
                from src.db.repositories.inventory_repo import InventoryRepository
                inv_repo = InventoryRepository(session)

                old_realm_idx = pre_breakthrough_realm(player_orm, axis)
                reqs = get_breakthrough_requirements(axis, old_realm_idx)

                apply_breakthrough(char, axis, inventory=inventory_map)

                if reqs["item_key"] and reqs["quantity"]:
                    await inv_repo.remove_any_grade(
                        player_orm.id, reqs["item_key"], reqs["quantity"]
                    )

                player_orm.update_from_model(char)
                await session.commit()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CultivationCog(bot))
