from __future__ import annotations
import random, asyncio, discord
from dataclasses import dataclass

from src.data.registry import registry
from src.game.models.character import Character
from src.game.constants.realms import QI_REALMS, BODY_REALMS
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    build_enemy_combatant,
    build_player_combatant,
)
from src.utils.embed_builder import battle_embed, success_embed, error_embed


@dataclass
class TribulationResult:
    success: bool
    damage_taken: int
    cultivation_lost: bool = False


class TribulationManager:
    # Tribulation triggers every breakthrough from realm 4 onward (target realm 5..8)
    # for both qi and body axes. Formation has no tribulation by design.
    MAJOR_BREAKTHROUGHS = {
        "qi":   [4, 5, 6, 7],
        "body": [4, 5, 6, 7],
    }

    @staticmethod
    def get_tribulation_id(axis: str, target_realm_idx: int) -> str:
        return f"trib_{axis}_{target_realm_idx}"

    async def run_tribulation(
        self,
        interaction: discord.Interaction,
        char: Character,
        axis: str,
        skill_keys: list[str],
        gem_count: int = 0,
        equip_stats: dict | None = None,
        gem_keys: list[str] | None = None,
        gem_keys_by_formation: dict[str, list[str]] | None = None,
    ) -> TribulationResult:

        target_realm_idx = getattr(char, f"{axis}_realm") + 1
        trib_key = self.get_tribulation_id(axis, target_realm_idx)

        # ── Build combatants ─────────────────────────
        player_c = build_player_combatant(
            char, skill_keys, gem_count, equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_keys_by_formation,
        )

        player_realm_total = (
            char.body_realm * 9 + char.body_level +
            char.qi_realm * 9 + char.qi_level +
            char.formation_realm * 9 + char.formation_level
        )

        trib_c = build_enemy_combatant(trib_key, player_realm_total)
        if not trib_c:
            trib_c = build_enemy_combatant("default_heavenly_trib", player_realm_total)
            if trib_c is not None:
                realm_name = (
                    QI_REALMS[target_realm_idx].vi if axis == "qi"
                    else BODY_REALMS[target_realm_idx].vi
                )
                trib_c.name = f"Thiên Kiếp {realm_name}"

        session = CombatSession(
            player=player_c,
            enemy=trib_c,
            player_skill_keys=skill_keys,
        )

        # ── UI Control ───────────────────────────────
        class ControlView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.skip = False

            @discord.ui.button(label="⏩ Bỏ qua", style=discord.ButtonStyle.secondary)
            async def skip_btn(self, interaction2: discord.Interaction, _):
                self.skip = True
                await interaction2.response.defer()

        view = ControlView()

        all_logs: list[str] = []

        # ── Start combat ─────────────────────────────
        await interaction.edit_original_response(
            embed=battle_embed(
                "⚡ Thiên Kiếp",
                0, 1,
                player_c.name, player_c.hp, player_c.hp_max,
                player_c.mp, player_c.mp_max,
                trib_c.name, trib_c.hp, trib_c.hp_max,
                0, [],
                player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
                enemy_shield=trib_c.shield, enemy_shield_cap=trib_c.shield_cap(),
            ),
            view=view
        )

        await asyncio.sleep(1)

        result = None

        while True:
            new_lines, result = session.step()
            all_logs.extend(new_lines)

            if result:
                break

            if not view.skip:
                await interaction.edit_original_response(
                    embed=battle_embed(
                        "⚡ Thiên Kiếp",
                        0, 1,
                        player_c.name, player_c.hp, player_c.hp_max,
                        player_c.mp, player_c.mp_max,
                        trib_c.name, trib_c.hp, trib_c.hp_max,
                        session.turn,
                        new_lines,
                        player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
                        enemy_shield=trib_c.shield, enemy_shield_cap=trib_c.shield_cap(),
                    ),
                    view=view
                )
                await asyncio.sleep(1)

        damage_taken = player_c.hp_max - player_c.hp

        # ── Build log embeds (giống dungeon) ─────────
        from src.game.engine.damage import to_ansi_block

        log_text = "\n".join(all_logs)
        log_embeds = []

        chunks = [log_text[i:i+3000] for i in range(0, len(log_text), 3000)] or ["(Không có dữ liệu)"]

        log_embeds = []
        for i, chunk in enumerate(chunks):
            body = to_ansi_block(chunk) if chunk.strip() else chunk
            log_embeds.append(
                discord.Embed(
                    title="📜 Nhật Ký Thiên Kiếp" if i == 0 else "\u200b",
                    description=body,
                    color=0x00C851 if result.reason == CombatEndReason.PLAYER_WIN else 0xFF4444
                )
            )

        # ── Result View ──────────────────────────────
        class ResultView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)

            # @discord.ui.button(label="📜 Xem Nhật Ký", style=discord.ButtonStyle.secondary)
            # async def view_log(self, interaction2: discord.Interaction, _):
            #     await interaction2.response.send_message(
            #         embeds=log_embeds[:5],
            #         ephemeral=True
            #     )

        # ── Final result ─────────────────────────────
        if result.reason == CombatEndReason.PLAYER_WIN:
            embed = success_embed(
                f"⚡ **Thiên Kiếp: {trib_c.name}**\n\n"
                f"👤 {char.name}\n"
                f"⏱️ {session.turn} lượt\n"
                f"❤️ {player_c.hp:,}/{player_c.hp_max:,}\n"
                f"📊 Sát thương nhận: {damage_taken:,}\n\n"
                f"✨ **Đột phá thành công!**"
            )

            await interaction.edit_original_response(embed=embed, view=ResultView())

            return TribulationResult(
                success=True,
                damage_taken=damage_taken
            )

        else:
            is_lost = random.random() < 0.3

            desc = (
                f"⚡ **Thiên Kiếp: {trib_c.name}**\n\n"
                f"👤 {char.name}\n"
                f"⏱️ {session.turn} lượt\n"
                f"📊 Sát thương nhận: {damage_taken:,}\n\n"
                f"💀 **Đột phá thất bại!**\n"
            )

            if is_lost:
                desc += "\n⚠️ Rớt cảnh giới!"
            else:
                desc += "\n🩹 Trọng thương."

            embed = error_embed(desc)

            await interaction.edit_original_response(embed=embed, view=ResultView())

            return TribulationResult(
                success=False,
                damage_taken=damage_taken,
                cultivation_lost=is_lost
            )

    def check_needs_tribulation(self, char: Character, axis: str) -> bool:
        current_realm = getattr(char, f"{axis}_realm")
        level = getattr(char, f"{axis}_level")
        return level >= 9 and current_realm in self.MAJOR_BREAKTHROUGHS.get(axis, [])