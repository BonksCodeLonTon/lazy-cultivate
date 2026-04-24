"""World boss (Boss Thế Giới) commands — persistent server-wide boss fights."""
from __future__ import annotations

import asyncio
import logging
import random as _rng_mod
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.data.registry import registry
from src.db.connection import get_session
from src.db.models.world_boss import WorldBossInstance
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.db.repositories.world_boss_repo import WorldBossRepository
from src.game.constants.grades import Grade
from src.game.constants.realms import QI_REALMS
from src.game.engine.drop import roll_drops
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems.combat import (
    CombatEndReason, CombatSession,
    build_player_combatant, build_world_boss_combatant,
)
from src.game.systems.world_boss import (
    ATTACK_ROUND_LIMIT, PER_ATTACK_DMG_CAP_PCT,
    compute_rewards, format_leaderboard, is_boss_live_now,
)
from src.utils.embed_builder import base_embed, battle_embed, error_embed, success_embed

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_hp_bar(current: int, maximum: int, length: int = 20) -> str:
    if maximum <= 0:
        return "░" * length
    filled = max(0, min(length, round(current / maximum * length)))
    return "█" * filled + "░" * (length - filled)


def _boss_summary_embed(instance: WorldBossInstance, boss_data: dict) -> discord.Embed:
    pct = instance.hp_current / instance.hp_max if instance.hp_max else 0.0
    color = 0x8B0000 if pct < 0.2 else (0xE67E22 if pct < 0.5 else 0x2ECC71)
    now = datetime.now(timezone.utc)
    remaining = instance.expires_at - now
    remaining_str = (
        f"{int(remaining.total_seconds() // 60)} phút"
        if remaining.total_seconds() > 0
        else "Đã hết thời gian"
    )
    realm_label = (
        QI_REALMS[boss_data["realm"] - 1].vi
        if 1 <= boss_data.get("realm", 1) <= len(QI_REALMS)
        else f"Realm {boss_data.get('realm')}"
    )
    cap_dmg = int(instance.hp_max * PER_ATTACK_DMG_CAP_PCT)
    desc = (
        f"*{boss_data.get('description_vi', '')}*\n\n"
        f"🏯 **Cảnh Giới**: {realm_label}\n"
        f"⏳ **Còn lại**: {remaining_str}\n\n"
        f"❤️ `{_format_hp_bar(instance.hp_current, instance.hp_max)}`\n"
        f"**{instance.hp_current:,} / {instance.hp_max:,}** HP ({pct*100:.2f}%)\n"
        f"🛡️ Trần ST/đòn: **{PER_ATTACK_DMG_CAP_PCT * 100:.0f}%** HP Boss "
        f"(tối đa {cap_dmg:,} mỗi trận)"
    )
    return base_embed(f"👹 {boss_data['vi']}", desc, color=color)


def _boss_list_embed(
    active_instances: list[WorldBossInstance],
    player_realm: int,
) -> discord.Embed:
    if not active_instances:
        return base_embed(
            "🌌 Boss Thế Giới",
            "Hiện tại chưa có boss thế giới nào xuất hiện.\n"
            "Mỗi realm có các khung giờ xuất hiện cố định — hãy canh giờ!",
            color=0x7B2D8B,
        )
    lines = []
    for inst in active_instances:
        boss = registry.get_world_boss(inst.boss_key)
        if not boss:
            continue
        pct = inst.hp_current / inst.hp_max if inst.hp_max else 0.0
        realm = boss.get("realm", 1)
        realm_label = QI_REALMS[realm - 1].vi if 1 <= realm <= len(QI_REALMS) else f"R{realm}"
        lock = "" if player_realm + 1 >= realm else "🔒 "
        lines.append(
            f"{lock}**{boss['vi']}** · {realm_label}\n"
            f"  `{_format_hp_bar(inst.hp_current, inst.hp_max, 14)}` {pct*100:.1f}%"
        )
    return base_embed(
        "🌌 Boss Thế Giới Đang Xuất Hiện",
        "\n\n".join(lines),
        color=0x7B2D8B,
    )


async def _grant_loot_from_tables(
    irepo: InventoryRepository,
    player_id: int,
    table_keys: list[str],
    bonus_items: list[tuple[str, int]],
    rng: _rng_mod.Random,
) -> list[dict]:
    """Roll all loot tables + add bonus items. Returns the merged drop list."""
    all_drops: list[dict] = []
    for table_key in table_keys:
        table = registry.get_loot_table(table_key)
        if not table:
            continue
        result = roll_drops(table, rng).merge()
        all_drops.extend(result)
    for item_key, qty in bonus_items:
        all_drops.append({"item_key": item_key, "quantity": qty})

    # Merge duplicates
    merged: dict[str, int] = {}
    for d in all_drops:
        merged[d["item_key"]] = merged.get(d["item_key"], 0) + d["quantity"]

    final = [{"item_key": k, "quantity": v} for k, v in merged.items()]
    for drop in final:
        data = registry.get_item(drop["item_key"])
        grade_val = data.get("grade", 1) if data else 1
        await irepo.add_item(player_id, drop["item_key"], Grade(grade_val), drop["quantity"])
    return final


# ── Attack session ────────────────────────────────────────────────────────────

async def _execute_boss_attack(
    interaction: discord.Interaction, boss_key: str, back_fn=None,
) -> None:
    """Run one player-initiated attack session on the world boss."""
    rng = _rng_mod.Random()

    async with get_session() as session:
        prepo = PlayerRepository(session)
        wrepo = WorldBossRepository(session)

        player = await prepo.get_by_discord_id(interaction.user.id)
        if player is None:
            await interaction.edit_original_response(
                embed=error_embed("Chưa có nhân vật. Dùng `/register` trước."),
                view=None,
            )
            return

        boss_data = registry.get_world_boss(boss_key)
        if not boss_data:
            await interaction.edit_original_response(
                embed=error_embed("Boss không tồn tại."), view=None,
            )
            return

        # Require player realm >= boss realm - 1 so lower realms cannot grief
        if player.qi_realm + 1 < boss_data.get("realm", 1):
            await interaction.edit_original_response(
                embed=error_embed(
                    f"Cảnh giới của ngươi chưa đủ để tấn công **{boss_data['vi']}**."
                ),
                view=None,
            )
            return

        instance = await wrepo.get_active(boss_key)
        if instance is None:
            await interaction.edit_original_response(
                embed=error_embed("Boss này hiện chưa xuất hiện."), view=None,
            )
            return
        if instance.hp_current <= 0:
            await interaction.edit_original_response(
                embed=error_embed("Boss đã bị đánh bại. Quay lại sau."), view=None,
            )
            return

        char = _player_to_model(player)
        from src.game.systems.character_stats import (
            active_formation_gem_keys, active_formation_gem_map, compute_combat_stats,
        )
        gem_keys = active_formation_gem_keys(player)
        gem_map = active_formation_gem_map(player)
        equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        equip_stats = compute_equipment_stats(equipped)
        cs = compute_combat_stats(
            char, gem_count=len(gem_keys), equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_map,
        )
        if player.hp_current <= 0:
            player.hp_current = cs.hp_max
            char.hp_current = player.hp_current

        skill_keys = [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]

        realm_total = (
            char.body_realm * 9 + char.body_level
            + char.qi_realm * 9 + char.qi_level
            + char.formation_realm * 9 + char.formation_level
        ) // 3

        player_c = build_player_combatant(
            char, skill_keys, len(gem_keys), equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_map,
        )
        boss_c = build_world_boss_combatant(boss_data, instance.hp_current, realm_total)

        session_obj = CombatSession(
            player=player_c,
            enemy=boss_c,
            player_skill_keys=skill_keys,
            rng=rng,
            max_turns=ATTACK_ROUND_LIMIT,
        )

        starting_hp = boss_c.hp
        # Snapshot hp_max BEFORE combat — mechanics like Âm Hồn Phệ used to
        # shrink it during the local sim, which would drop the cap denominator.
        # The is_world_boss flag now blocks that, but snapshotting makes the
        # cap immune to any future mutation regardless.
        starting_hp_max = boss_c.hp_max
        player_name = player.name
        instance_id = instance.id

    # Release the DB session before any Discord API calls — the live-update
    # loop below spans 7–10s and should not hold a connection from the pool.
    wave_label = f"🌌 **{boss_data['vi']}** — Boss Thế Giới"
    await interaction.edit_original_response(
        embed=battle_embed(
            wave_label, 0, 1,
            player_c.name, player_c.hp, player_c.hp_max,
            player_c.mp, player_c.mp_max,
            boss_c.name, boss_c.hp, boss_c.hp_max,
            0, [],
        ),
        view=None,
    )
    await asyncio.sleep(0.8)

    # Run combat outside of the DB session (long-running loop)
    combat_result = None
    full_log: list[str] = []
    while True:
        new_lines, result = session_obj.step()
        full_log.extend(new_lines)
        if result is not None:
            combat_result = result
            break
        try:
            await interaction.edit_original_response(
                embed=battle_embed(
                    wave_label, 0, 1,
                    player_c.name, player_c.hp, player_c.hp_max,
                    player_c.mp, player_c.mp_max,
                    boss_c.name, boss_c.hp, boss_c.hp_max,
                    session_obj.turn, new_lines,
                ),
                view=None,
            )
        except discord.HTTPException:
            pass
        await asyncio.sleep(0.5)

    # Calculate damage dealt in the local combat sim
    ending_hp = max(0, boss_c.hp)
    damage_dealt_local = max(0, starting_hp - ending_hp)

    # Per-attack damage cap: prevents high-grade players from one-tapping a
    # boss in a single session and keeps any one attacker from dominating the
    # damage leaderboard. Applied both here (early, for accurate UX) and
    # inside ``apply_damage_atomic`` (authoritative, so client-side bypass
    # still hits the wall). Denominator is the PRE-combat snapshot so no
    # hp_max-mutating mechanic can shrink the cap.
    dmg_cap = int(starting_hp_max * PER_ATTACK_DMG_CAP_PCT)
    uncapped_damage = damage_dealt_local
    damage_dealt_local = min(damage_dealt_local, dmg_cap)
    cap_hit = uncapped_damage > dmg_cap

    # Persist damage to DB atomically. apply_damage_atomic takes a row lock so
    # concurrent attackers serialize safely: no lost updates, exactly one
    # finisher, and participation is credited only for damage actually applied
    # to the shared HP pool (no over-kill inflation on the leaderboard).
    async with get_session() as session:
        prepo = PlayerRepository(session)
        wrepo = WorldBossRepository(session)

        player = await prepo.get_by_discord_id(interaction.user.id)

        applied_damage = 0
        killed_by_us = False
        ending_hp_shared = 0

        if player is not None:
            apply_result = await wrepo.apply_damage_atomic(
                instance_id, damage_dealt_local, player.id,
                damage_cap=dmg_cap,
            )
            if not apply_result.instance_missing:
                applied_damage = apply_result.applied
                killed_by_us = apply_result.is_finisher
                ending_hp_shared = apply_result.new_hp
                if apply_result.cap_hit:
                    cap_hit = True
                if applied_damage > 0:
                    await wrepo.upsert_damage(instance_id, player.id, applied_damage)

            player.hp_current = max(1, player_c.hp)
            player.mp_current = max(0, player_c.mp)
            await prepo.save(player)

    # Build post-attack embed — report damage actually credited to the shared pool
    remaining_pct = ending_hp_shared / boss_c.hp_max if boss_c.hp_max else 0.0
    if combat_result.reason == CombatEndReason.PLAYER_DEAD:
        header = f"💀 **{player_name}** đã ngã xuống nhưng vẫn gây **{applied_damage:,}** sát thương!"
        color = 0xFF4444
    else:
        header = f"⚔️ **{player_name}** gây **{applied_damage:,}** sát thương!"
        color = 0x2ECC71
    if cap_hit:
        header += (
            f"\n🛡️ *Trần sát thương mỗi đòn: **{PER_ATTACK_DMG_CAP_PCT * 100:.0f}%** "
            f"HP Boss ({dmg_cap:,}). Bạn gây thật **{uncapped_damage:,}** ST nhưng "
            f"Boss chỉ nhận **{applied_damage:,}**.*"
        )
    elif uncapped_damage > applied_damage:
        # Overkill — someone else landed the final blow while the player was fighting
        header += f" *(đánh thừa {uncapped_damage - applied_damage:,} khi boss đã hết máu)*"

    footer = ""
    if killed_by_us:
        footer = "\n\n🏆 **Đòn kết liễu!** Dùng `/world_boss rewards` để nhận phần thưởng."
    elif ending_hp_shared <= 0:
        footer = "\n\n☠️ Boss đã bị đánh bại bởi tu sĩ khác! Dùng `/world_boss rewards` để nhận phần tham chiến."
    else:
        footer = (
            f"\n\nBoss còn **{ending_hp_shared:,} / {boss_c.hp_max:,}** HP ({remaining_pct*100:.1f}%)"
            "\nDùng `/world_boss attack` để tấn công tiếp."
        )

    summary = base_embed(
        f"🌌 Tấn Công — {boss_data['vi']}",
        header + footer,
        color=color,
    )
    await interaction.edit_original_response(
        embed=summary,
        view=PostAttackView(interaction.user.id, back_fn=back_fn),
    )

    if killed_by_us:
        await _distribute_rewards(boss_key, instance_id)


async def _distribute_rewards(boss_key: str, instance_id: int) -> None:
    """Flag the boss's rewards as ready for claiming. Actual item grants happen on /rewards."""
    from sqlalchemy import update as _update
    async with get_session() as session:
        result = await session.execute(
            _update(WorldBossInstance)
            .where(
                WorldBossInstance.id == instance_id,
                WorldBossInstance.rewards_distributed.is_(False),
            )
            .values(rewards_distributed=True)
        )
        if (result.rowcount or 0) == 1:
            log.info(
                "World boss %s (instance=%s) rewards flagged ready.",
                boss_key, instance_id,
            )


async def _claim_rewards(interaction: discord.Interaction) -> None:
    """Grant any unclaimed world-boss rewards to the interacting player."""
    rng = _rng_mod.Random()

    async with get_session() as session:
        prepo = PlayerRepository(session)
        wrepo = WorldBossRepository(session)
        irepo = InventoryRepository(session)

        player = await prepo.get_by_discord_id(interaction.user.id)
        if player is None:
            await interaction.followup.send(
                embed=error_embed("Chưa có nhân vật."), ephemeral=True,
            )
            return

        pending = await wrepo.list_pending_rewards_for_player(player.id)
        if not pending:
            await interaction.followup.send(
                embed=base_embed(
                    "🎁 Phần Thưởng Boss Thế Giới",
                    "Hiện không có phần thưởng chưa nhận.",
                    color=0x7B2D8B,
                ),
                ephemeral=True,
            )
            return

        report_lines: list[str] = []
        for part in pending:
            # Atomically claim the reward slot — only one call ever wins, so a
            # rapid double-invoke of /world_boss rewards cannot grant loot twice
            won_claim = await wrepo.claim_reward_atomic(part.id)
            if not won_claim:
                continue

            instance = await wrepo.get_instance_with_parts(part.boss_instance_id)
            if instance is None:
                continue
            boss_data = registry.get_world_boss(instance.boss_key)
            if not boss_data:
                continue

            rewards = compute_rewards(
                boss_data,
                instance.hp_max,
                list(instance.participations),
                instance.finisher_player_id,
            )
            my_reward = next((r for r in rewards if r.player_id == player.id), None)
            if my_reward is None or my_reward.tier == "none":
                continue

            drops = await _grant_loot_from_tables(
                irepo, player.id,
                my_reward.loot_table_keys, my_reward.bonus_items, rng,
            )

            tier_label = {
                "finisher":    "🏆 Đòn Kết Liễu",
                "top":         "🥇 Top Sát Thương",
                "participant": "⚔️ Tham Chiến",
            }.get(my_reward.tier, my_reward.tier)
            drop_str = ", ".join(
                f"{(registry.get_item(d['item_key']) or {}).get('vi', d['item_key'])}×{d['quantity']}"
                for d in drops
            ) or "*(không)*"
            report_lines.append(
                f"**{boss_data['vi']}** — {tier_label} (Hạng #{my_reward.rank}, "
                f"{my_reward.damage_pct*100:.2f}% HP)\n🎁 {drop_str}"
            )

    if not report_lines:
        msg = "Các trận tham chiến không đạt ngưỡng phần thưởng."
    else:
        msg = "\n\n".join(report_lines)
    await interaction.followup.send(
        embed=success_embed(msg),
        ephemeral=True,
    )


# ── Discord UI ────────────────────────────────────────────────────────────────

async def _refresh_hub(interaction: discord.Interaction, discord_id: int, back_fn=None) -> None:
    """Re-query active bosses and re-render the hub in-place on the current message."""
    async with get_session() as session:
        wrepo = WorldBossRepository(session)
        prepo = PlayerRepository(session)
        active = await wrepo.list_active()
        player = await prepo.get_by_discord_id(interaction.user.id)
    player_realm = player.qi_realm if player else 0

    embed = _boss_list_embed(active, player_realm)
    extra_embeds: list[discord.Embed] = []
    for inst in active[:3]:
        bd = registry.get_world_boss(inst.boss_key)
        if bd:
            extra_embeds.append(_boss_summary_embed(inst, bd))

    boss_keys = [i.boss_key for i in active]
    view = WorldBossHubView(boss_keys, discord_id, back_fn=back_fn)
    await interaction.edit_original_response(embeds=[embed] + extra_embeds, view=view)


class WorldBossHubView(discord.ui.View):
    """Interactive hub listing live world bosses — attack / claim / refresh / back."""

    def __init__(self, boss_keys: list[str], discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=300)
        self.discord_id = discord_id
        self._back_fn = back_fn

        if boss_keys:
            options: list[discord.SelectOption] = []
            for k in boss_keys[:25]:
                b = registry.get_world_boss(k)
                if not b:
                    continue
                realm = b.get("realm", 1)
                realm_label = QI_REALMS[realm - 1].vi if 1 <= realm <= len(QI_REALMS) else f"R{realm}"
                options.append(discord.SelectOption(
                    label=b["vi"][:100],
                    value=k,
                    description=f"{realm_label}"[:100],
                    emoji="👹",
                ))
            select = discord.ui.Select(
                placeholder="⚔️ Chọn boss để tấn công...",
                options=options,
                row=0,
            )
            select.callback = self._on_select
            self.add_item(select)

        refresh_btn = discord.ui.Button(label="🔄 Làm Mới", style=discord.ButtonStyle.secondary, row=1)
        refresh_btn.callback = self._on_refresh
        self.add_item(refresh_btn)

        claim_btn = discord.ui.Button(label="🎁 Nhận Thưởng", style=discord.ButtonStyle.blurple, row=1)
        claim_btn.callback = self._on_claim
        self.add_item(claim_btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở Về", style=discord.ButtonStyle.secondary, row=1)
            back_btn.callback = self._on_back
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.discord_id

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        boss_key = interaction.data["values"][0]
        await _execute_boss_attack(interaction, boss_key, back_fn=self._back_fn)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _refresh_hub(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_claim(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _claim_rewards(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class PostAttackView(discord.ui.View):
    """Shown after an attack resolves — lets the player jump back to the hub."""

    def __init__(self, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=180)
        self.discord_id = discord_id
        self._back_fn = back_fn

        hub_btn = discord.ui.Button(label="🌌 Danh Sách Boss", style=discord.ButtonStyle.blurple, row=0)
        hub_btn.callback = self._on_hub
        self.add_item(hub_btn)

        if back_fn is not None:
            back_btn = discord.ui.Button(label="◀ Trở Về", style=discord.ButtonStyle.secondary, row=0)
            back_btn.callback = self._on_back
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.discord_id

    async def _on_hub(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await _refresh_hub(interaction, self.discord_id, back_fn=self._back_fn)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


# Kept for external callers that imported the old name
BossAttackView = WorldBossHubView


# ── Background scheduler ──────────────────────────────────────────────────────

async def _scheduler_tick() -> None:
    """Spawn bosses whose scheduled window is live, expire any past their time."""
    async with get_session() as session:
        wrepo = WorldBossRepository(session)
        now = datetime.now(timezone.utc)

        # Expire anything past its deadline
        active = await wrepo.list_active()
        for inst in active:
            if inst.expires_at <= now and inst.hp_current > 0:
                await wrepo.expire_instance(inst)

        # Spawn any boss currently in a live window without an existing
        # instance for that *specific* window. Using has_instance_for_window
        # (not just get_active) ensures a boss killed mid-window is NOT
        # respawned until the next scheduled spawn_time.
        for boss_data in registry.world_bosses.values():
            window = is_boss_live_now(boss_data, now)
            if window is None:
                continue
            already_spawned = await wrepo.has_instance_for_window(
                boss_data["key"], window.spawned_at,
            )
            if already_spawned:
                continue
            hp_max = int(boss_data["base_hp"] * boss_data.get("hp_scale", 1.0))
            await wrepo.create_instance(
                boss_key=boss_data["key"],
                realm=boss_data.get("realm", 1),
                hp_max=hp_max,
                spawned_at=window.spawned_at,
                expires_at=window.expires_at,
            )
            log.info("Spawned world boss %s (hp_max=%d)", boss_data["key"], hp_max)


# ── Cog ───────────────────────────────────────────────────────────────────────

class WorldBossCog(commands.Cog, name="WorldBoss"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scheduler.start()

    def cog_unload(self) -> None:
        self.scheduler.cancel()

    @tasks.loop(minutes=1)
    async def scheduler(self) -> None:
        try:
            await _scheduler_tick()
        except Exception as e:
            log.exception("World boss scheduler error: %s", e)

    @scheduler.before_loop
    async def _before_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    group = app_commands.Group(name="world_boss", description="Boss Thế Giới")

    @group.command(name="list", description="Xem các boss thế giới đang xuất hiện")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _refresh_hub(interaction, interaction.user.id, back_fn=None)

    @group.command(name="attack", description="Tấn công một boss thế giới")
    @app_commands.describe(boss_key="Chọn boss muốn tấn công")
    async def attack_cmd(
        self, interaction: discord.Interaction, boss_key: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await _execute_boss_attack(interaction, boss_key)

    @attack_cmd.autocomplete("boss_key")
    async def _boss_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        async with get_session() as session:
            wrepo = WorldBossRepository(session)
            active = await wrepo.list_active()
        choices: list[app_commands.Choice[str]] = []
        for inst in active:
            bd = registry.get_world_boss(inst.boss_key)
            if not bd:
                continue
            name = bd["vi"]
            if current.lower() in name.lower() or current.lower() in inst.boss_key.lower():
                choices.append(app_commands.Choice(name=name[:100], value=inst.boss_key))
        return choices[:25]

    @group.command(name="rewards", description="Nhận phần thưởng từ các boss thế giới đã hạ")
    async def rewards_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _claim_rewards(interaction)

    @group.command(name="leaderboard", description="Xem bảng xếp hạng sát thương của một boss")
    @app_commands.describe(boss_key="Boss muốn xem bảng xếp hạng")
    async def leaderboard_cmd(
        self, interaction: discord.Interaction, boss_key: str
    ) -> None:
        async with get_session() as session:
            wrepo = WorldBossRepository(session)
            instance = await wrepo.get_active(boss_key)
            if instance is None:
                await interaction.response.send_message(
                    embed=error_embed("Boss này hiện không hoạt động."),
                    ephemeral=True,
                )
                return
            parts = await wrepo.list_participations(instance.id)

        board = format_leaderboard(parts)
        boss_data = registry.get_world_boss(boss_key)
        title = f"📊 Bảng Xếp Hạng — {boss_data['vi'] if boss_data else boss_key}"
        await interaction.response.send_message(
            embed=base_embed(title, board, color=0x7B2D8B),
            ephemeral=True,
        )

    @leaderboard_cmd.autocomplete("boss_key")
    async def _lb_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._boss_autocomplete(interaction, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WorldBossCog(bot))
