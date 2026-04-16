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
from src.game.constants.grades import Grade, GRADE_LABELS
from src.game.constants.linh_can import compute_linh_can_bonuses
from src.game.engine.equipment import compute_equipment_stats
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
    "tinh_anh":  "🌿",
    "cuong_gia": "⚔️",
    "hung_manh": "🗡️",
    "dai_nang":  "🔥",
    "than_thu":  "🌀",
    "tien_thu":  "✨",
    "chi_ton":   "💀",
}

# Zone number each rank starts at (realm_total = (zone-1)*9 floor)
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

# Each rank's next-tier upgrade (for elite encounters)
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


def _realm_total(char: CharModel) -> int:
    """Average cultivation level across all 3 paths (0–90 range)."""
    return (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    ) // 3


def _upgrade_chance(base_rank: str, char: CharModel) -> float:
    """Return probability (0.0–0.30) of encountering a higher-rank elite enemy.

    Scales linearly with how far the player has progressed within the rank's
    zone: 0% at zone entry, 30% at zone mastery (level 9/9).
    """
    zone = _RANK_ZONE.get(base_rank, 1)
    rt = _realm_total(char)
    zone_floor = (zone - 1) * 9
    level_in_zone = max(0, min(9, rt - zone_floor))
    return level_in_zone / 9 * 0.30


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
        gem_count = 0
        if player.active_formation and player.formations:
            for f in player.formations:
                if f.formation_key == player.active_formation:
                    gem_count = len(f.gem_slots)
                    break

        equipped = [i for i in (player.item_instances or []) if i.location == "equipped"]
        equip_stats = compute_equipment_stats(equipped)

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

    # ── Elite encounter roll ──────────────────────────────────────────────────
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

    player_c = build_player_combatant(char, skill_keys, gem_count=gem_count, equip_stats=equip_stats)
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

    # ── Real-time battle ──────────────────────────────────────────────────────
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


_TYPE_EMOJI = {"thien": "⚔️", "dia": "🛡️", "nhan": "💚", "tran_phap": "🌀"}
_ELEM_EMOJI = {
    "kim": "🪙", "moc": "🌿", "thuy": "💧", "hoa": "🔥",
    "tho": "🪨", "loi": "⚡", "phong": "🌬️", "am": "🌑", "quang": "☀️",
}
_TYPE_LABEL = {
    "thien": "Thiên — Công Kích",
    "dia":   "Địa — Phòng Thủ",
    "nhan":  "Nhân — Hỗ Trợ/Khống Chế",
    "tran_phap": "Trận Pháp",
}
_SKILL_LIST_PAGE_SIZE = 6

_SKILL_TYPE_BUTTONS = [
    (None,        "🌐 Tất Cả",  discord.ButtonStyle.secondary),
    ("thien",     "⚔️ Thiên",   discord.ButtonStyle.danger),
    ("dia",       "🛡️ Địa",     discord.ButtonStyle.primary),
    ("nhan",      "💚 Nhân",    discord.ButtonStyle.success),
    ("tran_phap", "🌀 Trận",    discord.ButtonStyle.secondary),
]

_SKILL_TYPE_TO_SCROLL_PREFIX: dict[str, str] = {
    "thien":     "ScrollAtk",
    "dia":       "ScrollDef",
    "nhan":      "ScrollSup",
    "tran_phap": "ScrollFrm",
}

_NUMBER_EMOJI = ["①", "②", "③", "④", "⑤", "⑥"]

def _skill_grade_value(skill_data: dict) -> int:
    """Minimum scroll grade needed to learn this skill (mirrors inventory.py logic)."""
    mp = skill_data.get("mp_cost", 0)
    if mp <= 15:
        return 1
    if mp <= 30:
        return 2
    if mp <= 60:
        return 3
    return 4


def _find_usable_scroll(inv_items: list, skill_data: dict) -> tuple[str, int] | None:
    """Return (scroll_key, grade_value) of the first compatible scroll in inventory, or None."""
    prefix = _SKILL_TYPE_TO_SCROLL_PREFIX.get(skill_data.get("type", ""))
    if not prefix:
        return None
    min_grade = _skill_grade_value(skill_data)
    for inv in inv_items:
        if inv.item_key.startswith(prefix) and inv.grade >= min_grade and inv.quantity > 0:
            return (inv.item_key, inv.grade)
    return None


def _filtered_skills(
    skill_type: str | None,
    element: str | None,
    linh_can: list[str] | None = None,
) -> list[dict]:
    """Return sorted skills filtered by type, element, and optionally learnable linh căn."""
    skills = list(registry.skills.values())
    if skill_type:
        skills = [s for s in skills if s.get("type") == skill_type]
    if element:
        skills = [s for s in skills if s.get("element") == element]
    if linh_can is not None:
        # Only show skills whose element matches the player's linh căn (null element = universal)
        skills = [s for s in skills if s.get("element") is None or s.get("element") in linh_can]
    return sorted(skills, key=lambda s: (s.get("type", ""), s.get("mp_cost", 0)))


def _build_skilllist(
    discord_id: int,
    skill_type: str | None = None,
    element: str | None = None,
    page: int = 0,
    back_fn=None,
    linh_can: list[str] | None = None,
) -> tuple[discord.Embed, "SkillListView"]:
    skills = _filtered_skills(skill_type, element, linh_can)
    total = len(skills)
    total_pages = max(1, (total + _SKILL_LIST_PAGE_SIZE - 1) // _SKILL_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    slice_start = page * _SKILL_LIST_PAGE_SIZE
    page_skills = skills[slice_start: slice_start + _SKILL_LIST_PAGE_SIZE]

    type_label = _TYPE_LABEL.get(skill_type or "", "Tất Cả") if skill_type else "Tất Cả"
    title = f"📚 Tàng Kinh Các: {type_label}"
    if element:
        title += f" [{_ELEM_EMOJI.get(element, element)}]"

    if not page_skills:
        embed = base_embed(title, "Không tìm thấy kỹ năng phù hợp với Linh Căn của bạn.", color=0x9B59B6)
    else:
        lines: list[str] = []
        for i, s in enumerate(page_skills):
            num = _NUMBER_EMOJI[i]
            t_e = _TYPE_EMOJI.get(s.get("type", ""), "❓")
            el = s.get("element")
            el_tag = f" {_ELEM_EMOJI.get(el, '?')}" if el else ""
            cd = s.get("cooldown", 1)
            effects = ", ".join(s.get("effects", [])) or "—"
            lines.append(
                f"{num} {t_e}{el_tag} **{s['vi']}** `{s['key']}`\n"
                f"  MP: **{s.get('mp_cost', 0)}** | DMG: **{s.get('base_dmg', 0)}** | "
                f"CD: **{cd}t** | {effects}"
            )
        embed = base_embed(title, "\n\n".join(lines), color=0x9B59B6)

    embed.set_footer(text=f"Trang {page + 1}/{total_pages} • {total} kỹ năng • Nhấn ① ② … để học")
    view = SkillListView(
        discord_id=discord_id,
        skill_type=skill_type,
        element=element,
        page=page,
        total_pages=total_pages,
        page_skills=page_skills,
        back_fn=back_fn,
        linh_can=linh_can,
    )
    return embed, view


class SkillListView(discord.ui.View):
    """Paginated, filterable skill browser."""

    _ELEM_OPTIONS = [
        ("",      "— Tất Cả Nguyên Tố —"),
        ("kim",   "🪙 Kim"),
        ("moc",   "🌿 Mộc"),
        ("thuy",  "💧 Thủy"),
        ("hoa",   "🔥 Hỏa"),
        ("tho",   "🪨 Thổ"),
        ("loi",   "⚡ Lôi"),
        ("phong", "🌬️ Phong"),
        ("am",    "🌑 Âm"),
        ("quang", "☀️ Quang"),
    ]

    def __init__(
        self,
        discord_id: int,
        skill_type: str | None,
        element: str | None,
        page: int,
        total_pages: int,
        page_skills: list[dict] | None = None,
        back_fn=None,
        linh_can: list[str] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._skill_type = skill_type
        self._element = element
        self._page = page
        self._total_pages = total_pages
        self._back_fn = back_fn
        self._linh_can = linh_can

        # Row 0 — type filter buttons
        for typ, label, style in _SKILL_TYPE_BUTTONS:
            active_style = discord.ButtonStyle.primary if typ == skill_type else style
            btn = discord.ui.Button(label=label, style=active_style, row=0)
            btn.callback = self._make_type_cb(typ)
            self.add_item(btn)

        # Row 1 — element dropdown (restricted to player's linh căn when available)
        elem_options = [("", "— Tất Cả Nguyên Tố —")] + [
            (val, label) for val, label in self._ELEM_OPTIONS[1:]
            if linh_can is None or val in linh_can
        ]
        select = discord.ui.Select(
            placeholder="🌍 Lọc nguyên tố…",
            options=[
                discord.SelectOption(
                    label=label,
                    value=val or "__all__",
                    default=(val == (element or "")),
                )
                for val, label in elem_options
            ],
            row=1,
        )
        select.callback = self._element_cb
        self.add_item(select)

        # Row 2 — pagination + back
        prev_btn = discord.ui.Button(
            label="◀ Trước",
            style=discord.ButtonStyle.secondary,
            disabled=(page == 0),
            row=2,
        )
        prev_btn.callback = self._prev_cb
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Sau ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(page >= total_pages - 1),
            row=2,
        )
        next_btn.callback = self._next_cb
        self.add_item(next_btn)

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=2)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

        # Rows 3-4 — numbered Learn buttons (① ② … per skill on this page)
        for i, s in enumerate(page_skills or []):
            btn = discord.ui.Button(
                label=f"{_NUMBER_EMOJI[i]} Học",
                style=discord.ButtonStyle.success,
                row=3 + (i // 3),
            )
            btn.callback = self._make_learn_cb(s)
            self.add_item(btn)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    def _make_learn_cb(self, skill_data: dict):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            from src.db.models.skill import CharacterSkill
            from sqlalchemy import select as sa_select

            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                    return

                irepo = InventoryRepository(session)
                all_inv = await irepo.get_all(player.id)
                scroll_info = _find_usable_scroll(all_inv, skill_data)

                if not scroll_info:
                    prefix = _SKILL_TYPE_TO_SCROLL_PREFIX.get(skill_data.get("type", ""), "Ngọc Giản")
                    min_grade = _skill_grade_value(skill_data)
                    grade_name = GRADE_LABELS.get(Grade(min_grade), (str(min_grade),))[0]
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"Cần **{prefix}** phẩm **{grade_name}** trở lên để học **{skill_data['vi']}**.\n"
                            "Mua tại `/shop` hoặc tìm trong Bí Cảnh."
                        ),
                        ephemeral=True,
                    )
                    return

                # Check if already learned
                existing = await session.execute(
                    sa_select(CharacterSkill).where(
                        CharacterSkill.player_id == player.id,
                        CharacterSkill.skill_key == skill_data["key"],
                    )
                )
                if existing.scalar_one_or_none():
                    await interaction.response.send_message(
                        embed=error_embed(f"Đã học kỹ năng **{skill_data['vi']}** rồi."),
                        ephemeral=True,
                    )
                    return

                # Load current slot occupancy
                slots_result = await session.execute(
                    sa_select(CharacterSkill).where(CharacterSkill.player_id == player.id)
                )
                occupied_slots: dict[int, str] = {
                    r.slot_index: r.skill_key for r in slots_result.scalars().all()
                }

            scroll_key, scroll_grade = scroll_info
            scroll_data = registry.get_item(scroll_key)
            scroll_name = scroll_data["vi"] if scroll_data else scroll_key
            grade_name = GRADE_LABELS.get(Grade(scroll_grade), (str(scroll_grade),))[0]

            # Snapshot current filter state for the back function
            snap_type, snap_elem, snap_page = self._skill_type, self._element, self._page
            outer_back_fn = self._back_fn
            did = self._discord_id
            snap_lc = self._linh_can

            async def back_to_list(ia: discord.Interaction) -> None:
                emb, v = _build_skilllist(
                    discord_id=did, skill_type=snap_type,
                    element=snap_elem, page=snap_page,
                    back_fn=outer_back_fn, linh_can=snap_lc,
                )
                await ia.edit_original_response(embed=emb, view=v)

            t_e = _TYPE_EMOJI.get(skill_data.get("type", ""), "❓")
            el = skill_data.get("element")
            el_tag = f" {_ELEM_EMOJI.get(el, '')}" if el else ""
            effects = ", ".join(skill_data.get("effects", [])) or "—"

            embed = base_embed(f"📖 Học: {skill_data['vi']}", color=0x9B59B6)
            embed.add_field(
                name="Kỹ Năng",
                value=(
                    f"{t_e}{el_tag} **{skill_data['vi']}** `{skill_data['key']}`\n"
                    f"MP: **{skill_data.get('mp_cost', 0)}** | DMG: **{skill_data.get('base_dmg', 0)}** | "
                    f"CD: **{skill_data.get('cooldown', 1)}t**\n"
                    f"Hiệu ứng: {effects}"
                ),
                inline=False,
            )
            embed.add_field(
                name="Cuộn Sách Dùng",
                value=f"📜 **{scroll_name}** ({grade_name})",
                inline=False,
            )
            embed.set_footer(text="Chọn slot để trang bị kỹ năng. Slot đang có kỹ năng sẽ bị ghi đè.")

            view = SkillLearnView(
                discord_id=did,
                skill_data=skill_data,
                scroll_key=scroll_key,
                scroll_grade=scroll_grade,
                occupied_slots=occupied_slots,
                back_fn=back_to_list,
            )
            await interaction.response.edit_message(embed=embed, view=view)

        return _cb

    def _make_type_cb(self, typ: str | None):
        async def _cb(interaction: discord.Interaction) -> None:
            if not self._guard(interaction):
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return
            embed, view = _build_skilllist(
                discord_id=self._discord_id,
                skill_type=typ,
                element=self._element,
                page=0,
                back_fn=self._back_fn,
                linh_can=self._linh_can,
            )
            await interaction.response.edit_message(embed=embed, view=view)
        return _cb

    async def _element_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        raw = interaction.data["values"][0]
        element = None if raw == "__all__" else raw
        embed, view = _build_skilllist(
            discord_id=self._discord_id,
            skill_type=self._skill_type,
            element=element,
            page=0,
            back_fn=self._back_fn,
            linh_can=self._linh_can,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed, view = _build_skilllist(
            discord_id=self._discord_id,
            skill_type=self._skill_type,
            element=self._element,
            page=self._page - 1,
            back_fn=self._back_fn,
            linh_can=self._linh_can,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _next_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        embed, view = _build_skilllist(
            discord_id=self._discord_id,
            skill_type=self._skill_type,
            element=self._element,
            page=self._page + 1,
            back_fn=self._back_fn,
            linh_can=self._linh_can,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


class SkillLearnView(discord.ui.View):
    """Slot picker — shown after player chooses a skill to learn."""

    def __init__(
        self,
        discord_id: int,
        skill_data: dict,
        scroll_key: str,
        scroll_grade: int,
        occupied_slots: dict[int, str],
        back_fn,
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._skill_data = skill_data
        self._scroll_key = scroll_key
        self._scroll_grade = scroll_grade
        self._back_fn = back_fn

        from src.db.models.skill import MAX_SKILL_SLOTS

        # Rows 0-1 — 6 slot buttons (3 per row)
        for slot_i in range(MAX_SKILL_SLOTS):
            current_key = occupied_slots.get(slot_i)
            if current_key:
                cur_data = registry.get_skill(current_key)
                cur_name = (cur_data["vi"] if cur_data else current_key)[:14]
                label = f"[{slot_i}] 🔄 {cur_name}"
                style = discord.ButtonStyle.secondary
            else:
                label = f"[{slot_i}] ✨ Trống"
                style = discord.ButtonStyle.success
            btn = discord.ui.Button(label=label, style=style, row=slot_i // 3)
            btn.callback = self._make_slot_cb(slot_i)
            self.add_item(btn)

        # Row 2 — back
        back_btn = discord.ui.Button(label="◀ Trở lại", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self._back_cb
        self.add_item(back_btn)

    def _make_slot_cb(self, slot: int):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            from src.db.models.skill import CharacterSkill
            from sqlalchemy import select as sa_select

            skill_key = self._skill_data["key"]

            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
                    return

                irepo = InventoryRepository(session)
                if not await irepo.has_item(player.id, self._scroll_key, Grade(self._scroll_grade)):
                    await interaction.response.edit_message(
                        embed=error_embed("Cuộn sách đã hết trong túi đồ."), view=None
                    )
                    return

                # Check not already learned
                dup = await session.execute(
                    sa_select(CharacterSkill).where(
                        CharacterSkill.player_id == player.id,
                        CharacterSkill.skill_key == skill_key,
                    )
                )
                if dup.scalar_one_or_none():
                    await interaction.response.edit_message(
                        embed=error_embed(f"Đã học **{self._skill_data['vi']}** rồi."), view=None
                    )
                    return

                # Overwrite slot if occupied
                old = await session.execute(
                    sa_select(CharacterSkill).where(
                        CharacterSkill.player_id == player.id,
                        CharacterSkill.slot_index == slot,
                    )
                )
                old_row = old.scalar_one_or_none()
                if old_row:
                    await session.delete(old_row)
                    await session.flush()

                session.add(CharacterSkill(player_id=player.id, skill_key=skill_key, slot_index=slot))
                await irepo.remove_item(player.id, self._scroll_key, Grade(self._scroll_grade))

                # Reload equipped skills for display
                rem = await session.execute(
                    sa_select(CharacterSkill).where(CharacterSkill.player_id == player.id)
                )
                remaining = [
                    type("S", (), {"slot_index": r.slot_index, "skill_key": r.skill_key})()
                    for r in sorted(rem.scalars().all(), key=lambda x: x.slot_index)
                ]

            embed, view = _build_skills_embed_view(remaining, interaction.user.id, back_fn=self._back_fn)
            embed.color = 0x2ECC71
            skill_name = self._skill_data["vi"]
            embed.description = (embed.description or "") + f"\n✅ Học **{skill_name}** → slot **{slot}** thành công!"
            await interaction.response.edit_message(embed=embed, view=view)

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._back_fn(interaction)


def _build_skills_embed_view(
    equipped: list, discord_id: int, back_fn=None
) -> tuple[discord.Embed, "SkillsView"]:
    embed = base_embed("🎯 Kỹ Năng Trang Bị", color=0x9B59B6)
    if not equipped:
        embed.description = (
            "Chưa trang bị kỹ năng nào.\n"
            "Nhấn **📚 Tàng Kinh Các** để xem và học kỹ năng phù hợp Linh Căn."
        )
    else:
        for s in equipped:
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
    footer = "Nhấn 🗑 để xoá kỹ năng khỏi slot."
    if back_fn:
        footer += " • ◀ để trở về danh sách."
    embed.set_footer(text=footer)
    return embed, SkillsView(equipped, discord_id, back_fn=back_fn)


class SkillsView(discord.ui.View):
    """Interactive view for /skills — shows a Forget button per equipped slot."""

    def __init__(self, equipped: list, discord_id: int, back_fn=None) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._back_fn = back_fn

        for s in equipped:
            skill_data = registry.get_skill(s.skill_key)
            label = f"🗑 Slot {s.slot_index}"
            if skill_data:
                label += f": {skill_data['vi']}"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger,
                row=min(s.slot_index // 3, 3),
            )
            btn.callback = self._make_forget_cb(s.slot_index, s.skill_key)
            self.add_item(btn)

        if back_fn:
            back_btn = discord.ui.Button(label="◀ Trở về", style=discord.ButtonStyle.secondary, row=4)
            back_btn.callback = self._back_cb
            self.add_item(back_btn)

    def _make_forget_cb(self, slot: int, skill_key: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if interaction.user.id != self._discord_id:
                await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
                return

            from src.db.models.skill import CharacterSkill
            from sqlalchemy import select as sa_select

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
                        embed=error_embed(f"Slot **{slot}** đã trống."), ephemeral=True
                    )
                    return
                await session.delete(skill_row)
                await session.flush()

                # Reload remaining skills for refresh
                remaining_result = await session.execute(
                    sa_select(CharacterSkill).where(CharacterSkill.player_id == player.id)
                )
                remaining_rows = sorted(remaining_result.scalars().all(), key=lambda x: x.slot_index)
                # Extract data while session is still open to avoid detached-instance issues
                remaining_data = [
                    type("S", (), {"slot_index": r.slot_index, "skill_key": r.skill_key})()
                    for r in remaining_rows
                ]

            skill_data = registry.get_skill(skill_key)
            skill_name = skill_data["vi"] if skill_data else skill_key

            embed, view = _build_skills_embed_view(remaining_data, interaction.user.id, back_fn=self._back_fn)
            embed.color = 0x2ECC71
            embed.description = (embed.description or "") + f"\n✅ Đã xoá **{skill_name}** khỏi slot **{slot}**."
            await interaction.response.edit_message(embed=embed, view=view)

        return _cb

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
            equipped = [
                type("S", (), {"slot_index": s.slot_index, "skill_key": s.skill_key})()
                for s in sorted(player.skills or [], key=lambda x: x.slot_index)
            ]

        embed, view = _build_skills_embed_view(equipped, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="skilllist", description="Xem danh sách kỹ năng có thể học (Tàng Kinh Các)")
    async def skilllist(self, interaction: discord.Interaction) -> None:
        from src.game.constants.linh_can import parse_linh_can
        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                return
            linh_can = parse_linh_can(player.linh_can or "")
        embed, view = _build_skilllist(discord_id=interaction.user.id, linh_can=linh_can)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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
            gem_count = 0
            if player.active_formation and player.formations:
                for f in player.formations:
                    if f.formation_key == player.active_formation:
                        gem_count = len(f.gem_slots)
                        break

            bonuses = merge_bonuses(
                compute_formation_bonuses(player.active_formation, gem_count),
                compute_constitution_bonuses(player.constitution_type),
                compute_linh_can_bonuses(char.linh_can),
            )
            
            player.hp_current = compute_hp_max(char, bonuses=bonuses)
            player.mp_current = compute_mp_max(char, bonuses=bonuses)
            await prepo.save(player)

        await interaction.response.send_message(
            embed=success_embed("❤️ HP/MP đã hồi phục về tối đa."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CombatCog(bot))
