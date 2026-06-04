"""Reusable trial-combat loop — the CombatSession drive + Discord render cadence
shared by Thiên Kiếp (tribulation) and the Constitution Process trial (Phase 6).

Extracted from ``tribulation.run_tribulation`` so a single helper owns the
``CombatSession`` step loop, the skip-button ``ControlView``, the per-turn
``edit_original_response(battle_embed)`` pacing, and the log-embed chunking.
It returns ONLY the combat outcome (a ``CombatEndReason``); every reward,
penalty, and post-fight render (success/fail text, realm-drop roll, material
consumption) stays with the caller. This keeps tribulation behavior-preserving
while giving the constitution-trial cog the same battle UX for free.
"""
from __future__ import annotations

import asyncio

import discord

from src.game.systems.combat import CombatEndReason, CombatSession
from src.game.systems.combatant import Combatant
from src.utils.embed_builder import battle_embed


async def run_trial_combat(
    interaction: discord.Interaction,
    player_c: Combatant,
    enemy_c: Combatant,
    skill_keys: list[str],
    *,
    title: str,
) -> CombatEndReason:
    """Drive one trial fight to its end and render its battle/log embeds.

    Builds the ``CombatSession``, shows the live battle embed (with a skip
    button that fast-forwards the turn-by-turn animation), steps the fight to
    completion, then edits in the final log embeds. Returns the
    ``CombatEndReason`` so the caller can apply its own win/lose handling on top
    — this helper never decides rewards, penalties, or realm drops.

    ``title`` is the battle/log header (e.g. ``"⚡ Thiên Kiếp"`` for tribulation,
    ``"⚔️ Thí Luyện Thể Chất"`` for the constitution trial).
    """
    session = CombatSession(
        player=player_c,
        enemy=enemy_c,
        player_skill_keys=skill_keys,
    )

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

    await interaction.edit_original_response(
        embed=battle_embed(
            title,
            0, 1,
            player_c.name, player_c.hp, player_c.hp_max,
            player_c.mp, player_c.mp_max,
            enemy_c.name, enemy_c.hp, enemy_c.hp_max,
            0, [],
            player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
            enemy_shield=enemy_c.shield, enemy_shield_cap=enemy_c.shield_cap(),
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
                    title,
                    0, 1,
                    player_c.name, player_c.hp, player_c.hp_max,
                    player_c.mp, player_c.mp_max,
                    enemy_c.name, enemy_c.hp, enemy_c.hp_max,
                    session.turn,
                    new_lines,
                    player_shield=player_c.shield, player_shield_cap=player_c.shield_cap(),
                    enemy_shield=enemy_c.shield, enemy_shield_cap=enemy_c.shield_cap(),
                ),
                view=view
            )
            await asyncio.sleep(1)

    return result.reason
