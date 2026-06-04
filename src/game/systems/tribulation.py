from __future__ import annotations
import random, discord
from dataclasses import dataclass

from src.db.connection import get_session
from src.db.repositories.constitution_process import load_constitution_levels
from src.db.repositories.skill_mastery import get_mastery_map
from src.game.models.character import Character
from src.game.constants.realms import QI_REALMS, BODY_REALMS, FORMATION_REALMS
from src.game.systems.combat import (
    CombatEndReason,
    build_enemy_combatant,
    build_player_combatant,
)
from src.game.systems.combat.trial_loop import run_trial_combat
from src.utils.config import settings
from src.utils.embed_builder import success_embed, error_embed


@dataclass
class TribulationResult:
    success: bool
    damage_taken: int
    cultivation_lost: bool = False


_ALL_BREAKTHROUGHS: list[int] = list(range(8))


class TribulationManager:
    # Tribulation triggers on every realm-to-realm breakthrough across all three
    # axes — current realm idx 0..7 → target idx 1..8. Each axis carries its own
    # Thiên Kiếp themed for the realm being left (see ``trib_<axis>_<idx>``
    # entries in ``data/tribulations/``). Per-realm trib stats are calibrated to
    # Truyền Thuyết / chi_ton-tier so every breakthrough is a real fight.
    MAJOR_BREAKTHROUGHS = {
        "qi":        _ALL_BREAKTHROUGHS,
        "body":      _ALL_BREAKTHROUGHS,
        "formation": _ALL_BREAKTHROUGHS,
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
        # Tribulation PASSES mastery for the combat effect, but XP is
        # intentionally NOT awarded here — XP earning is a PvE-farm reward only.
        # Flag-gated: OFF → no DB read, None passed → inert.
        skill_mastery: dict[str, int] | None = None
        if settings.skill_mastery_enabled:
            async with get_session() as session:
                skill_mastery = await get_mastery_map(session, char.player_id)

        # Constitution Process — flag-gated. OFF → empty map → inert flat read.
        if settings.constitution_process_enabled:
            async with get_session() as session:
                char.constitution_levels = await load_constitution_levels(
                    session, char.player_id, char.constitution_type
                )

        player_c = build_player_combatant(
            char, skill_keys, gem_count, equip_stats=equip_stats,
            gem_keys=gem_keys, gem_keys_by_formation=gem_keys_by_formation,
            skill_mastery=skill_mastery,
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
                realm_table = {
                    "qi":        QI_REALMS,
                    "body":      BODY_REALMS,
                    "formation": FORMATION_REALMS,
                }.get(axis, QI_REALMS)
                idx = min(target_realm_idx, len(realm_table) - 1)
                trib_c.name = f"Thiên Kiếp {realm_table[idx].vi}"

        # ── Drive the trial fight via the shared trial-combat loop ───
        # The loop owns the CombatSession, the skip button, and the per-turn
        # battle-embed pacing; it returns only the combat outcome so the
        # tribulation-specific success/fail + realm-drop handling below stays
        # here, behavior-preserving.
        reason = await run_trial_combat(
            interaction, player_c, trib_c, skill_keys, title="⚡ Thiên Kiếp",
        )

        damage_taken = player_c.hp_max - player_c.hp

        # ── Final result ─────────────────────────────
        if reason == CombatEndReason.PLAYER_WIN:
            embed = success_embed(
                f"⚡ **Thiên Kiếp: {trib_c.name}**\n\n"
                f"👤 {char.name}\n"
                f"❤️ {player_c.hp:,}/{player_c.hp_max:,}\n"
                f"📊 Sát thương nhận: {damage_taken:,}\n\n"
                f"✨ **Đột phá thành công!**"
            )

            await interaction.edit_original_response(embed=embed)

            return TribulationResult(
                success=True,
                damage_taken=damage_taken
            )

        else:
            is_lost = random.random() < 0.3

            desc = (
                f"⚡ **Thiên Kiếp: {trib_c.name}**\n\n"
                f"👤 {char.name}\n"
                f"📊 Sát thương nhận: {damage_taken:,}\n\n"
                f"💀 **Đột phá thất bại!**\n"
            )

            if is_lost:
                desc += "\n⚠️ Rớt cảnh giới!"
            else:
                desc += "\n🩹 Trọng thương."

            embed = error_embed(desc)

            await interaction.edit_original_response(embed=embed)

            return TribulationResult(
                success=False,
                damage_taken=damage_taken,
                cultivation_lost=is_lost
            )

    def check_needs_tribulation(self, char: Character, axis: str) -> bool:
        current_realm = getattr(char, f"{axis}_realm")
        level = getattr(char, f"{axis}_level")
        return level >= 9 and current_realm in self.MAJOR_BREAKTHROUGHS.get(axis, [])
