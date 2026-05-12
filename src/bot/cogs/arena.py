"""Thí Luyện Đài — training dummy + player vs player duels.

Two flows:

  • Hình Nhân — fixed 1B-HP, 0-atk dummy that the player pummels for 30
    turns. Reports total damage, DPS, and a parsed per-skill breakdown
    extracted from the combat log.
  • Đối Chiến — invite another registered cultivator to a duel. Pick by
    Discord user select OR a name search. Target gets an Accept/Decline
    invitation; both fight at full HP/MP and nothing persists afterwards.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.db.connection import get_session
from src.db.models.player import Player
from src.db.repositories.player_repo import PlayerRepository, _player_to_model
from src.game.engine.equipment import compute_equipment_stats
from src.game.systems.character_stats import (
    active_formation_gem_keys,
    active_formation_gem_map,
    compute_combat_stats,
)
from src.game.systems.combat import (
    CombatEndReason,
    CombatSession,
    build_player_combatant,
)
from src.game.systems.combatant import Combatant
from src.utils.discord_safe import safe_defer
from src.utils.embed_builder import base_embed, battle_embed, error_embed

log = logging.getLogger(__name__)

DUMMY_HP = 1_000_000_000
DUMMY_TURNS = 30
NAME_SEARCH_LIMIT = 10
INVITE_TIMEOUT_SEC = 120
PANEL_TIMEOUT_SEC = 180

# Strips ANSI SGR escape sequences (e.g. ``\x1b[31m``, ``\x1b[1;33m``,
# ``\x1b[0m``) emitted by ``colorize_damage`` so plain-text exports
# don't leak terminal control codes into a .txt file.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Matches a damage-dealing skill cast log line emitted by casting.cast_skill:
#   "  ⚡ **NAME** dùng *SKILL* → -1,234 HP …"
# Damage may be wrapped in ANSI colour codes, so we tolerate any non-digit
# noise between the arrow and the number. Skill name has no asterisks inside.
_SKILL_DMG_RE = re.compile(
    r"\*\*([^*]+?)\*\*\s+dùng\s+\*([^*]+?)\*\s*→[^-]*-([\d,]+)\s*HP",
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared loaders
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _PlayerLoadout:
    """Bundle returned by ``_load_player_state`` — everything needed to build
    a Combatant without re-hitting the DB."""

    name: str
    char: object
    skill_keys: list[str]
    gem_count: int
    gem_keys: list[str]
    gem_map: dict
    equip_stats: dict
    hp_max: int
    mp_max: int


async def _load_player_state(discord_id: int) -> _PlayerLoadout | None:
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            return None

        char = _player_to_model(player)
        gem_keys = active_formation_gem_keys(player)
        gem_map = active_formation_gem_map(player)
        gem_count = len(gem_keys)

        equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        equip_stats = compute_equipment_stats(equipped)

        skill_keys = (
            [s.skill_key for s in player.skills] if player.skills else ["SkillAtkKim1"]
        )
        cs = compute_combat_stats(
            char,
            gem_count=gem_count,
            equip_stats=equip_stats,
            gem_keys=gem_keys,
            gem_keys_by_formation=gem_map,
        )

        return _PlayerLoadout(
            name=player.name,
            char=char,
            skill_keys=skill_keys,
            gem_count=gem_count,
            gem_keys=gem_keys,
            gem_map=gem_map,
            equip_stats=equip_stats,
            hp_max=cs.hp_max,
            mp_max=cs.mp_max,
        )


def _build_combatant(loadout: _PlayerLoadout) -> Combatant:
    c = build_player_combatant(
        loadout.char,
        loadout.skill_keys,
        gem_count=loadout.gem_count,
        equip_stats=loadout.equip_stats,
        gem_keys=loadout.gem_keys,
        gem_keys_by_formation=loadout.gem_map,
    )
    c.hp = loadout.hp_max
    c.mp = loadout.mp_max
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Hình Nhân — training dummy
# ─────────────────────────────────────────────────────────────────────────────


def _build_dummy_combatant() -> Combatant:
    """Inert 1B-HP combatant. atk=0 keeps its retaliations to ~1 dmg/swing,
    no skill_keys means it never casts, and the 1 B HP pool means even a
    hero crit can't finish it inside the turn cap.

    ``def_stat=0`` is intentional: any non-zero value would trigger the
    physical-defense diminishing-returns curve and cap physical hits, making
    "Tổng Sát Thương" undercount ATK-scaled builds while MATK builds (which
    bypass def_stat) read correctly. Zero defense gives an apples-to-apples
    raw-output baseline across both flavours.
    """
    return Combatant(
        key="HinhNhan",
        name="Hình Nhân",
        hp=DUMMY_HP,
        hp_max=DUMMY_HP,
        mp=999_999,
        mp_max=999_999,
        spd=10,
        element=None,
        atk=0,
        matk=0,
        def_stat=0,
        evasion_rating=0,
        skill_keys=[],
    )


def _parse_per_skill_damage(log_lines: list[str], actor_name: str) -> dict[str, int]:
    """Aggregate raw skill-cast damage from log lines for one actor.

    Multi-strike, true-damage ticks, and DoTs are NOT attributed because
    they appear under different log-line shapes — those still count toward
    the dummy's total HP loss but won't show in the per-skill table.
    """
    totals: dict[str, int] = {}
    for line in log_lines:
        m = _SKILL_DMG_RE.search(line)
        if not m:
            continue
        line_actor, skill_name, dmg_str = m.group(1), m.group(2), m.group(3)
        if line_actor != actor_name:
            continue
        try:
            dmg = int(dmg_str.replace(",", ""))
        except ValueError:
            continue
        totals[skill_name] = totals.get(skill_name, 0) + dmg
    return totals


def _render_dummy_results(
    player_name: str,
    total_dmg: int,
    turns: int,
    per_skill: dict[str, int],
) -> discord.Embed:
    dps = total_dmg / turns if turns > 0 else 0
    embed = base_embed(
        f"🎯 Hình Nhân — {player_name}",
        f"Đã thi triển trong **{turns}** lượt.",
        color=0xFF8C42,
    )
    embed.add_field(name="Tổng Sát Thương", value=f"**{total_dmg:,}**", inline=True)
    embed.add_field(name="DPS (per turn)", value=f"**{dps:,.0f}**", inline=True)

    if per_skill:
        lines = []
        for name, dmg in sorted(per_skill.items(), key=lambda kv: -kv[1]):
            pct = (dmg / total_dmg * 100) if total_dmg else 0
            lines.append(f"• *{name}* — {dmg:,} ({pct:.1f}%)")
        embed.add_field(
            name="Sát Thương Theo Kỹ Năng",
            value="\n".join(lines)[:1020],
            inline=False,
        )
    else:
        embed.add_field(
            name="Sát Thương Theo Kỹ Năng",
            value="*Không có dữ liệu kỹ năng — toàn bộ sát thương từ DoT/đòn phụ.*",
            inline=False,
        )

    embed.set_footer(
        text="Hình Nhân không phản kháng. DoT / đòn phụ tính trong tổng nhưng không "
        "lên bảng theo kỹ năng."
    )
    return embed


async def _run_dummy(interaction: discord.Interaction, panel_view: "ArenaPanelView") -> None:
    loadout = await _load_player_state(interaction.user.id)
    if loadout is None:
        await interaction.edit_original_response(
            embed=error_embed("Chưa có nhân vật."), view=None
        )
        return

    player_c = _build_combatant(loadout)
    dummy_c = _build_dummy_combatant()

    rng = random.Random()
    session = CombatSession(
        player=player_c,
        enemy=dummy_c,
        player_skill_keys=loadout.skill_keys,
        rng=rng,
        max_turns=DUMMY_TURNS,
        max_turns_is_defeat=False,
    )

    wave_label = f"🎯 **{player_c.name}** vs **{dummy_c.name}**"

    # Initial state — render the empty arena before any turn fires so the
    # player sees the kickoff snapshot. Same UX as ``_execute_dungeon``.
    await interaction.edit_original_response(
        embed=battle_embed(
            wave_label, 0, 1,
            player_c.name, player_c.hp, player_c.hp_max,
            player_c.mp, player_c.mp_max,
            dummy_c.name, dummy_c.hp, dummy_c.hp_max,
            session.turn, [],
            player_shield=player_c.shield,
            player_shield_cap=player_c.shield_cap(),
            enemy_shield=dummy_c.shield,
            enemy_shield_cap=dummy_c.shield_cap(),
        ),
        view=None,
    )
    await asyncio.sleep(0.6)

    all_logs: list[str] = []
    result = None
    while True:
        new_lines, step_result = session.step()
        all_logs.extend(new_lines)
        if step_result is not None:
            result = step_result
            break
        try:
            await interaction.edit_original_response(
                embed=battle_embed(
                    wave_label, 0, 1,
                    player_c.name, player_c.hp, player_c.hp_max,
                    player_c.mp, player_c.mp_max,
                    dummy_c.name, dummy_c.hp, dummy_c.hp_max,
                    session.turn, new_lines,
                    player_shield=player_c.shield,
                    player_shield_cap=player_c.shield_cap(),
                    enemy_shield=dummy_c.shield,
                    enemy_shield_cap=dummy_c.shield_cap(),
                ),
                view=None,
            )
        except discord.HTTPException as exc:
            log.warning("Hình Nhân mid-combat edit failed: %s", exc)
            break
        await asyncio.sleep(0.4)

    log_lines = list(result.log) if result is not None else all_logs
    total_dmg = dummy_c.hp_max - dummy_c.hp
    turns = result.turns if result is not None else session.turn
    per_skill = _parse_per_skill_damage(log_lines, player_c.name)
    embed = _render_dummy_results(player_c.name, total_dmg, turns, per_skill)
    view = _DummyResultView(interaction.user.id, log_lines, panel_view)
    await interaction.edit_original_response(embed=embed, view=view)


class _DummyResultView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        log_lines: list[str],
        panel_view: "ArenaPanelView",
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SEC)
        self._owner_id = owner_id
        self._log_lines = log_lines
        self._panel_view = panel_view

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._owner_id

    @discord.ui.button(label="🔁 Đánh Lại", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _run_dummy(interaction, self._panel_view)

    @discord.ui.button(label="📜 Nhật Ký", style=discord.ButtonStyle.secondary)
    async def view_log(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        from src.game.engine.damage import to_ansi_block

        # Discord caps embed.description at 4096 chars. ``to_ansi_block``
        # wraps text in ```ansi``` fences AND inflates every ``**bold**``
        # to ``\x1b[1m…\x1b[0m`` (≈7 extra chars per match), so a fixed
        # source-char split routinely overshoots. Build chunks line-by-line
        # and stop right before the post-conversion+wrap size would breach
        # the cap. ``MAX_BODY`` leaves ~96 chars of headroom for safety.
        MAX_BODY = 4000
        bodies: list[str] = []
        current: list[str] = []

        def _wrapped_size(lines_buf: list[str]) -> int:
            text = "\n".join(lines_buf)
            return len(to_ansi_block(text)) if text.strip() else len(text)

        for line in self._log_lines:
            candidate = current + [line]
            if _wrapped_size(candidate) > MAX_BODY and current:
                bodies.append("\n".join(current))
                current = [line]
            else:
                current = candidate
        if current:
            bodies.append("\n".join(current))
        if not bodies:
            bodies = [""]

        embeds = [
            base_embed(
                "📜 Nhật Ký Tập Luyện" if i == 0 else "​",
                to_ansi_block(body) if body.strip() else body,
                color=0xFF8C42,
            )
            for i, body in enumerate(bodies[:10])
        ]
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @discord.ui.button(label="◀ Trở Về", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await _open_panel(interaction, edit=True)


# ─────────────────────────────────────────────────────────────────────────────
# Đối Chiến — opponent search + invite + duel
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _CultivatorEntry:
    player_id: int
    discord_id: int
    name: str


async def _search_cultivators(fragment: str) -> list[_CultivatorEntry]:
    fragment = (fragment or "").strip()
    if not fragment:
        return []
    async with get_session() as session:
        result = await session.execute(
            select(Player.id, Player.discord_id, Player.name)
            .where(Player.name.ilike(f"%{fragment}%"))
            .limit(NAME_SEARCH_LIMIT)
        )
        return [
            _CultivatorEntry(player_id=pid, discord_id=did, name=nm)
            for pid, did, nm in result.all()
        ]


def _opponent_picker_embed() -> discord.Embed:
    return base_embed(
        "🤺 Đối Chiến — Chọn Đối Thủ",
        "Chọn đối thủ qua **tag Discord** ở dưới, hoặc bấm **Tìm Theo Tên** để tra "
        "tên tu sĩ đã đăng ký.",
        color=0x8E44AD,
    )


async def _list_recent_cultivators(
    exclude_discord_id: int, limit: int = 24,
) -> list[_CultivatorEntry]:
    """Return the most-recent N registered cultivators, excluding the searcher.

    Used to populate the opponent picker so players only ever see other
    actually-registered tu sĩ — bypasses Discord's ``UserSelect`` which would
    list every server member regardless of whether they have a character.
    Discord ``Select`` has a 25-option hard cap; ``limit=24`` leaves room
    for clarity. If a roster grows past 24, the search-by-name button is
    the canonical path to find anyone else.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Player.id, Player.discord_id, Player.name)
            .where(Player.discord_id != exclude_discord_id)
            .order_by(Player.id.desc())
            .limit(limit)
        )
        return [
            _CultivatorEntry(player_id=pid, discord_id=did, name=nm)
            for pid, did, nm in result.all()
        ]


class _OpponentPickerView(discord.ui.View):
    """Step 1 of Đối Chiến — pick opponent from registered cultivators only.

    The picker dropdown is populated from ``_list_recent_cultivators``
    (newest 24 registrations excluding the searcher). For a roster larger
    than 24, the 🔎 Tìm Theo Tên button drives the substring search.
    Callers must ``await self.populate()`` after construction so the
    ``Select`` can be filled from the DB before the view ships.
    """

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SEC)
        self._owner_id = owner_id

    async def populate(self) -> None:
        """Build the cultivator dropdown. Must be awaited before render."""
        cultivators = await _list_recent_cultivators(self._owner_id)
        if cultivators:
            options = [
                discord.SelectOption(label=c.name[:100], value=str(c.discord_id))
                for c in cultivators
            ]
            placeholder = (
                f"Chọn tu sĩ đã đăng ký… ({len(options)} người gần nhất)"
            )
        else:
            # Fallback so Discord doesn't reject a 0-option Select.
            options = [discord.SelectOption(
                label="(Chưa có tu sĩ nào khác)", value="__none__",
            )]
            placeholder = "Chưa có đối thủ — dùng Tìm Theo Tên"

        sel = discord.ui.Select(
            placeholder=placeholder,
            options=options,
            min_values=1, max_values=1, row=0,
            disabled=not cultivators,
        )
        sel.callback = self._user_select_cb
        self.add_item(sel)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._owner_id

    async def _user_select_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        select = interaction.data.get("values", [])  # type: ignore[union-attr]
        if not select or select[0] == "__none__":
            await interaction.response.defer()
            return
        target_id = int(select[0])
        await _confirm_opponent(interaction, target_id)

    @discord.ui.button(label="🔎 Tìm Theo Tên", style=discord.ButtonStyle.secondary, row=1)
    async def search_by_name(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.send_modal(_NameSearchModal(self._owner_id))

    @discord.ui.button(label="◀ Trở Về", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await _open_panel(interaction, edit=True)


class _NameSearchModal(discord.ui.Modal, title="Tìm Theo Tên"):
    fragment: discord.ui.TextInput = discord.ui.TextInput(
        label="Tên tu sĩ (một phần là đủ)",
        placeholder="Ví dụ: Nhất, Lý...",
        required=True,
        max_length=64,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__()
        self._owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await safe_defer(interaction):
            return
        results = await _search_cultivators(str(self.fragment))
        # Don't include the searcher themselves.
        results = [r for r in results if r.discord_id != interaction.user.id]

        if not results:
            await interaction.followup.send(
                embed=error_embed(f"Không tìm thấy tu sĩ khớp với `{self.fragment}`."),
                ephemeral=True,
            )
            return

        embed = base_embed(
            "🔎 Kết Quả Tìm Kiếm",
            f"Tìm thấy **{len(results)}** tu sĩ. Chọn một người để gửi lời mời.",
            color=0x8E44AD,
        )
        view = _NameResultsView(self._owner_id, results)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class _NameResultsView(discord.ui.View):
    def __init__(self, owner_id: int, results: list[_CultivatorEntry]) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SEC)
        self._owner_id = owner_id

        select = discord.ui.Select(
            placeholder="Chọn tu sĩ để khiêu chiến…",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label=r.name[:100], value=str(r.discord_id))
                for r in results
            ],
        )
        select.callback = self._select_cb
        self.add_item(select)

    async def _select_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        target_id = int(interaction.data["values"][0])  # type: ignore[index]
        await _confirm_opponent(interaction, target_id)


async def _confirm_opponent(interaction: discord.Interaction, target_discord_id: int) -> None:
    """Verify target is a registered cultivator and post the public invite."""
    # Defer first — DB lookups below may exceed Discord's 3 s window.
    if not interaction.response.is_done():
        if not await safe_defer(interaction, ephemeral=True):
            return

    if target_discord_id == interaction.user.id:
        await interaction.followup.send(
            embed=error_embed("Không thể tự khiêu chiến chính mình."),
            ephemeral=True,
        )
        return

    challenger_loadout = await _load_player_state(interaction.user.id)
    target_loadout = await _load_player_state(target_discord_id)

    if challenger_loadout is None:
        await interaction.followup.send(
            embed=error_embed("Bạn chưa có nhân vật."), ephemeral=True
        )
        return
    if target_loadout is None:
        await interaction.followup.send(
            embed=error_embed("Đối thủ chưa có nhân vật trong tông môn."),
            ephemeral=True,
        )
        return

    invite_embed = base_embed(
        "⚔️ Khiêu Chiến!",
        f"<@{target_discord_id}> — **{challenger_loadout.name}** "
        f"thách đấu bạn tại Thí Luyện Đài.\n\n"
        f"⏳ Lời mời sẽ hết hạn sau {INVITE_TIMEOUT_SEC} giây.",
        color=0xE74C3C,
    )
    invite_view = _DuelInviteView(
        challenger_id=interaction.user.id,
        target_id=target_discord_id,
        challenger_loadout=challenger_loadout,
        target_loadout=target_loadout,
    )

    # Public channel post so the target gets pinged and bystanders can watch.
    # ``channel.send`` requires the bot to have Send Messages permission in
    # the channel — falls back to the interaction webhook (``followup.send``)
    # which doesn't need channel perms because it goes through Discord's
    # interaction-response endpoint. Same end-result (public message in the
    # same channel, target pinged) without the 403.
    channel = interaction.channel
    sent = False
    if channel is not None:
        try:
            await channel.send(
                content=f"<@{target_discord_id}>",
                embed=invite_embed,
                view=invite_view,
            )
            sent = True
        except discord.Forbidden:
            sent = False
    if not sent:
        try:
            await interaction.followup.send(
                content=f"<@{target_discord_id}>",
                embed=invite_embed,
                view=invite_view,
                ephemeral=False,
            )
            sent = True
        except discord.HTTPException as exc:
            log.warning(
                "Đối Chiến invite send failed (channel=%s, target=%s): %s",
                getattr(channel, "id", "?"), target_discord_id, exc,
            )
            await interaction.followup.send(
                embed=error_embed(
                    "Bot không thể gửi lời mời tại kênh này. Hãy thử ở kênh "
                    "khác hoặc kiểm tra quyền của bot."
                ),
                ephemeral=True,
            )
            return
    await interaction.followup.send(
        embed=base_embed(
            "✅ Đã Gửi Lời Mời",
            f"Đã khiêu chiến <@{target_discord_id}>. Chờ phản hồi tại kênh.",
            color=0x2ECC71,
        ),
        ephemeral=True,
    )


class _DuelInviteView(discord.ui.View):
    """Public invite view — only the target can press Accept/Decline."""

    def __init__(
        self,
        challenger_id: int,
        target_id: int,
        challenger_loadout: _PlayerLoadout,
        target_loadout: _PlayerLoadout,
    ) -> None:
        super().__init__(timeout=INVITE_TIMEOUT_SEC)
        self._challenger_id = challenger_id
        self._target_id = target_id
        self._challenger_loadout = challenger_loadout
        self._target_loadout = target_loadout
        self._resolved = False

    def _guard_target(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._target_id

    @discord.ui.button(label="✅ Chấp Nhận", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard_target(interaction):
            await interaction.response.send_message(
                "Chỉ người được khiêu chiến mới có thể trả lời.", ephemeral=True
            )
            return
        if self._resolved:
            return
        self._resolved = True
        if not await safe_defer(interaction):
            return

        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.edit_original_response(view=self)
        await _run_duel(
            interaction,
            self._challenger_loadout,
            self._target_loadout,
        )

    @discord.ui.button(label="❌ Từ Chối", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard_target(interaction):
            await interaction.response.send_message(
                "Chỉ người được khiêu chiến mới có thể trả lời.", ephemeral=True
            )
            return
        if self._resolved:
            return
        self._resolved = True
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            embed=base_embed(
                "🛡️ Lời Mời Bị Từ Chối",
                f"<@{self._target_id}> đã từ chối khiêu chiến từ <@{self._challenger_id}>.",
                color=0x95A5A6,
            ),
            view=self,
        )

    async def on_timeout(self) -> None:  # noqa: D401
        if self._resolved:
            return
        self._resolved = True
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]


async def _run_duel(
    interaction: discord.Interaction,
    challenger_loadout: _PlayerLoadout,
    target_loadout: _PlayerLoadout,
) -> None:
    challenger_c = _build_combatant(challenger_loadout)
    target_c = _build_combatant(target_loadout)
    # Disambiguate names if both players share the same display name —
    # combat logs use ``actor.name`` and would otherwise be unreadable.
    if challenger_c.name == target_c.name:
        challenger_c.name = f"{challenger_c.name} (A)"
        target_c.name = f"{target_c.name} (B)"

    rng = random.Random()
    session = CombatSession(
        player=challenger_c,
        enemy=target_c,
        player_skill_keys=challenger_loadout.skill_keys,
        rng=rng,
        max_turns=50,
        max_turns_is_defeat=False,
    )

    wave_label = f"⚔️ **{challenger_c.name}** vs **{target_c.name}**"

    # Step the combat one turn at a time, editing the public message after
    # each step so spectators see the duel unfold in real time — same UX
    # as ``_execute_dungeon``. The duel is opened via ``followup.send``
    # earlier; we capture that message handle and edit it on each tick.
    battle_msg = await interaction.followup.send(
        embed=battle_embed(
            wave_label, 0, 1,
            challenger_c.name, challenger_c.hp, challenger_c.hp_max,
            challenger_c.mp, challenger_c.mp_max,
            target_c.name, target_c.hp, target_c.hp_max,
            session.turn, [],
            player_shield=challenger_c.shield,
            player_shield_cap=challenger_c.shield_cap(),
            enemy_shield=target_c.shield,
            enemy_shield_cap=target_c.shield_cap(),
        ),
        wait=True,
    )
    await asyncio.sleep(0.8)

    all_logs: list[str] = []
    result = None
    while True:
        new_lines, step_result = session.step()
        all_logs.extend(new_lines)
        if step_result is not None:
            result = step_result
            break
        try:
            await battle_msg.edit(embed=battle_embed(
                wave_label, 0, 1,
                challenger_c.name, challenger_c.hp, challenger_c.hp_max,
                challenger_c.mp, challenger_c.mp_max,
                target_c.name, target_c.hp, target_c.hp_max,
                session.turn, new_lines,
                player_shield=challenger_c.shield,
                player_shield_cap=challenger_c.shield_cap(),
                enemy_shield=target_c.shield,
                enemy_shield_cap=target_c.shield_cap(),
            ))
        except discord.HTTPException as exc:
            log.warning("Đối Chiến mid-combat edit failed: %s", exc)
            break
        # 1.5 s between PvP turns — long enough for spectators to read the
        # damage / proc lines without the duel feeling sluggish.
        await asyncio.sleep(1.5)

    if result is None:
        # Combat aborted (e.g. message edit failed). Build a synthetic
        # "max-turn" result so the summary still renders something.
        from src.game.systems.combat import CombatResult
        result = CombatResult(
            reason=CombatEndReason.MAX_TURNS,
            turns=session.turn,
            log=all_logs,
            loot=[],
            merit_gained=0,
            karma_gained=0,
        )
    else:
        # Replace ``result.log`` with the full accumulated log so the
        # downstream "Xem Nhật Ký" button has every line.
        all_logs = list(result.log)

    if result.reason == CombatEndReason.PLAYER_WIN:
        winner_name, color = challenger_c.name, 0x2ECC71
        outcome = f"🏆 **{winner_name}** chiến thắng sau **{result.turns}** lượt!"
    elif result.reason == CombatEndReason.PLAYER_DEAD:
        winner_name, color = target_c.name, 0xE74C3C
        outcome = f"🏆 **{winner_name}** chiến thắng sau **{result.turns}** lượt!"
    else:
        color = 0xF1C40F
        outcome = f"⏰ Hòa — hết **{result.turns}** lượt, chưa phân thắng bại."

    summary = base_embed("⚔️ Đối Chiến — Kết Quả", outcome, color=color)
    summary.add_field(
        name="Đối Thủ",
        value=f"⚔️ **{challenger_c.name}**  vs  **{target_c.name}**",
        inline=False,
    )
    summary.set_footer(text="HP/MP đã được hồi phục — không có tổn thất.")

    view = _DuelLogView(log_lines=all_logs)
    try:
        await battle_msg.edit(embed=summary, view=view)
    except discord.HTTPException:
        # Fallback to a fresh followup if the original message went away.
        await interaction.followup.send(embed=summary, view=view)


class _DuelLogView(discord.ui.View):
    """Anyone in the duel (or any spectator) can pull up the full log; the
    log itself is ephemeral so the channel stays clean."""

    def __init__(self, log_lines: list[str]) -> None:
        super().__init__(timeout=300)
        self._log_lines = log_lines

    @discord.ui.button(label="📜 Xem Nhật Ký", style=discord.ButtonStyle.secondary)
    async def view_log(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from src.game.engine.damage import to_ansi_block

        # Discord caps embed.description at 4096. ``to_ansi_block`` wraps in
        # ```ansi``` fences AND inflates every Markdown ``**bold**`` into
        # multi-byte ANSI escapes, so a fixed source-char split overshoots.
        # Build chunks line-by-line and stop right before the post-conversion
        # + wrap size would breach the cap.
        MAX_BODY = 4000
        bodies: list[str] = []
        current: list[str] = []

        def _wrapped_size(lines_buf: list[str]) -> int:
            text = "\n".join(lines_buf)
            return len(to_ansi_block(text)) if text.strip() else len(text)

        for line in self._log_lines:
            candidate = current + [line]
            if _wrapped_size(candidate) > MAX_BODY and current:
                bodies.append("\n".join(current))
                current = [line]
            else:
                current = candidate
        if current:
            bodies.append("\n".join(current))
        if not bodies:
            bodies = [""]

        embeds = [
            base_embed(
                "📜 Nhật Ký Đối Chiến" if i == 0 else "​",
                to_ansi_block(body) if body.strip() else body,
                color=0x8E44AD,
            )
            for i, body in enumerate(bodies[:10])
        ]
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @discord.ui.button(label="💾 Xuất File", style=discord.ButtonStyle.secondary)
    async def export_log(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        """Send the full duel log as a plain-text attachment.

        The embed view ("Xem Nhật Ký") is capped at ~10 embeds × 4 KB and
        gets visually noisy when the log is long. The export gives players a
        complete, scrollable copy they can keep — useful for post-mortems
        and for sharing matches outside Discord. Markdown bold markers and
        ANSI color escapes (used to tint damage numbers inside Discord's
        ``ansi`` code blocks) are stripped so the file reads cleanly in any
        plain-text viewer; emoji and Vietnamese diacritics are preserved
        via UTF-8.
        """
        import io
        from datetime import datetime, timezone

        plain = _ANSI_ESCAPE_RE.sub("", "\n".join(self._log_lines)).replace("**", "")
        buf = io.BytesIO(plain.encode("utf-8"))
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"duel_log_{ts}.txt"
        file = discord.File(buf, filename=filename)
        await interaction.response.send_message(
            content=f"📦 Nhật ký đầy đủ ({len(self._log_lines)} dòng)",
            file=file,
            ephemeral=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Top-level panel
# ─────────────────────────────────────────────────────────────────────────────


class ArenaPanelView(discord.ui.View):
    def __init__(self, owner_id: int, back_fn=None) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SEC)
        self._owner_id = owner_id
        self._back_fn = back_fn
        # ``back_fn`` is set when the panel is opened from a parent hub
        # (e.g. /status). It receives the click interaction and is expected
        # to render the parent embed in place via ``edit_original_response``.
        if back_fn is not None:
            back_btn = discord.ui.Button(
                label="◀ Trở về", style=discord.ButtonStyle.secondary, row=1,
            )
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._owner_id

    @discord.ui.button(label="🎯 Hình Nhân", style=discord.ButtonStyle.primary)
    async def dummy_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await _run_dummy(interaction, self)

    @discord.ui.button(label="⚔️ Đối Chiến", style=discord.ButtonStyle.danger)
    async def duel_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        view = _OpponentPickerView(self._owner_id)
        await view.populate()
        await interaction.response.edit_message(
            embed=_opponent_picker_embed(),
            view=view,
        )

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        if self._back_fn is not None:
            await self._back_fn(interaction)


def _panel_embed() -> discord.Embed:
    return base_embed(
        "🏯 Thí Luyện Đài",
        "Nơi rèn luyện sát ý — chọn chế độ:\n\n"
        "• **🎯 Hình Nhân** — đánh thử lên hình nhân bất tử để đo sát thương.\n"
        "• **⚔️ Đối Chiến** — khiêu chiến tu sĩ khác để so tài cao thấp.",
        color=0xF39C12,
    )


async def _open_panel(interaction: discord.Interaction, edit: bool = False) -> None:
    embed = _panel_embed()
    view = ArenaPanelView(interaction.user.id)
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────


class ArenaCog(commands.Cog, name="Arena"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="thi_luyen_dai",
        description="Mở Thí Luyện Đài — đánh hình nhân hoặc đối chiến tu sĩ khác.",
    )
    async def thi_luyen_dai(self, interaction: discord.Interaction) -> None:
        await _open_panel(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ArenaCog(bot))
