"""Skill commands — view, learn, and manage combat skills."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.game.constants.grades import Grade, GRADE_LABELS
from src.game.constants.linh_can import parse_linh_can
from src.game.constants.realms import QI_REALMS
from src.game.engine.effects import EFFECTS
from src.game.systems.skills import (
    LearnError,
    filtered_skills,
    find_skill_scroll,
    formation_reservation_would_exceed_cap,
    is_formation_skill,
    next_formation_slot,
    scroll_key_for_skill,
    validate_learn_eligibility,
)
from src.utils.embed_builder import base_embed, error_embed, success_embed

_TYPE_EMOJI = {
    "attack":    "⚔️",
    "defense":   "🛡️",
    "movement":  "🏃",
    "passive":   "✨",
    "formation": "🌀",
}
_ELEM_EMOJI = {
    "kim": "🪙", "moc": "🌿", "thuy": "💧", "hoa": "🔥",
    "tho": "🪨", "loi": "⚡", "phong": "🌬️", "am": "🌑", "quang": "☀️",
}
_TYPE_LABEL = {
    "attack":    "Công Kích",
    "defense":   "Phòng Thủ",
    "movement":  "Thân Pháp",
    "passive":   "Bị Động",
    "formation": "Trận Pháp",
}
_SKILL_LIST_PAGE_SIZE = 6

_SKILL_TYPE_BUTTONS = [
    (None,        "🌐 Tất Cả",   discord.ButtonStyle.secondary),
    ("attack",    "⚔️ Công",     discord.ButtonStyle.danger),
    ("defense",   "🛡️ Thủ",      discord.ButtonStyle.primary),
    ("movement",  "🏃 Thân",     discord.ButtonStyle.success),
    ("formation", "🌀 Trận",     discord.ButtonStyle.secondary),
]

_NUMBER_EMOJI = ["①", "②", "③", "④", "⑤", "⑥"]


# ── Human labels for the stat_bonus fields used by EffectMeta ────────────────
# Percent-style stats (signed %) vs. flat-rating stats (signed integer).
_PCT_STATS: frozenset[str] = frozenset({
    "final_dmg_bonus", "final_dmg_reduce", "spd_pct", "hp_regen_pct",
    "mp_regen_pct", "res_all",
    "res_kim", "res_moc", "res_thuy", "res_hoa", "res_tho",
    "res_loi", "res_phong", "res_quang", "res_am",
    "dmg_bonus_kim", "dmg_bonus_moc", "dmg_bonus_thuy", "dmg_bonus_hoa",
    "dmg_bonus_tho", "dmg_bonus_loi", "dmg_bonus_phong",
    "dmg_bonus_quang", "dmg_bonus_am",
})
_STAT_LABEL: dict[str, str] = {
    "final_dmg_bonus":  "ST",
    "final_dmg_reduce": "ST nhận",
    "crit_rating":      "bạo",
    "crit_dmg_rating":  "ST bạo",
    "evasion_rating":   "né",
    "crit_res_rating":  "kháng bạo",
    "spd_pct":          "tốc",
    "hp_regen_pct":     "hồi HP",
    "mp_regen_pct":     "hồi MP",
    "res_all":          "kháng",
    "res_kim":   "kháng Kim",   "res_moc":   "kháng Mộc",
    "res_thuy":  "kháng Thủy",  "res_hoa":   "kháng Hỏa",
    "res_tho":   "kháng Thổ",   "res_loi":   "kháng Lôi",
    "res_phong": "kháng Phong", "res_quang": "kháng Quang",
    "res_am":    "kháng Âm",
    "dmg_bonus_kim":   "ST Kim",   "dmg_bonus_moc":   "ST Mộc",
    "dmg_bonus_thuy":  "ST Thủy",  "dmg_bonus_hoa":   "ST Hỏa",
    "dmg_bonus_tho":   "ST Thổ",   "dmg_bonus_loi":   "ST Lôi",
    "dmg_bonus_phong": "ST Phong", "dmg_bonus_quang": "ST Quang",
    "dmg_bonus_am":    "ST Âm",
}
# Non-EFFECTS skill keywords handled in combat.py — describe them here so the
# browser explains what each keyword will do when the skill fires.
_SPECIAL_EFFECT_LABELS: dict[str, str] = {
    "HpRegen":           "❤️ Hồi 10% HP",
    "MpRegen":           "💙 Hồi 10% MP",
    "ConsumeBurnBurst":  "🔥💥 Nổ stack Thiêu Đốt",
    "ConsumeManaBurst":  "💠💥 Nổ Linh Khí tích tụ",
    "ConsumeShieldBurst":"🪨💥 Nổ khiên Thổ",
    "ApplySoulDrain":    "🌑 Hồn Phệ (giảm HP max địch)",
    "ApplyStatSteal":    "🩶 Đạo Pháp (cướp chỉ số)",
}


# final_dmg_reduce is a "reduce"-semantic stat: a positive stat_bonus value
# means the holder takes LESS damage. Showing it raw as "+10% ST nhận" reads
# backwards to a player ("+10% damage taken?"). Flip the display sign for
# these inverted-semantic stats so buffs show as "-10% ST nhận".
_INVERTED_PCT_STATS: frozenset[str] = frozenset({"final_dmg_reduce"})


def _format_stat_bonus(stat: str, val: float) -> str:
    """Render one stat_bonus entry as a signed, unit-aware snippet."""
    label = _STAT_LABEL.get(stat, stat)
    display_val = -val if stat in _INVERTED_PCT_STATS else val
    if stat in _PCT_STATS:
        return f"{display_val * 100:+.0f}% {label}"
    return f"{display_val:+.0f} {label}"


def _describe_effect(key: str) -> str:
    """Short human description of a single skill effect key.

    Returns a compact ``emoji name(detail)`` tag — e.g. ``🔥Thiêu Đốt(4%HP/t)``
    or ``🛡️Hàn Khí(+10% ST nhận)`` — so the skill browser shows what each
    debuff/buff will actually do instead of raw registry keys.
    """
    # Special combat-keyword effects (not in EFFECTS registry)
    special = _SPECIAL_EFFECT_LABELS.get(key)
    if special:
        return special

    meta = EFFECTS.get(key)
    if meta is None:
        return key  # unknown — fall back to the raw key

    details: list[str] = []
    # DoT tick as %HP/turn
    if meta.dot_pct > 0:
        details.append(f"{meta.dot_pct * 100:.1f}% HP/t")
    # Stat mods — signed, shortened
    for stat, val in meta.stat_bonus.items():
        details.append(_format_stat_bonus(stat, val))
    # Aura-on-hit: describe the chance-gated effect that's spread to enemies
    # the holder strikes (e.g. BuffHanKhi → Làm Chậm on every hit).
    if meta.aura_on_hit is not None:
        aura_key, chance = meta.aura_on_hit
        aura_meta = EFFECTS.get(aura_key)
        aura_label = aura_meta.vi if aura_meta else aura_key
        pct = int(round(chance * 100))
        pct_prefix = "" if pct == 100 else f"{pct}% "
        details.append(f"{pct_prefix}{aura_label} on-hit")
    # CC flags without explicit stat mods
    if meta.skips_turn and not details:
        details.append("mất lượt")
    if meta.prevents_skills and "mất lượt" not in details:
        details.append("câm kỹ năng")

    tag = f"{meta.emoji}{meta.vi}"
    return f"{tag}({', '.join(details)})" if details else tag


def _format_skill_effects(effect_keys: list[str]) -> str:
    """Join a skill's effect list into a readable summary.

    Deduplicates stacking repeats (e.g. ``ApplyStatSteal`` listed twice on the
    same skill) since the cosmetic description is identical either way.
    """
    seen: set[str] = set()
    parts: list[str] = []
    for k in effect_keys or []:
        if k in seen:
            continue
        seen.add(k)
        parts.append(_describe_effect(k))
    return " • ".join(parts) if parts else "—"


def _build_skilllist(
    discord_id: int,
    skill_type: str | None = None,
    element: str | None = None,
    page: int = 0,
    back_fn=None,
    linh_can: list[str] | None = None,
) -> tuple[discord.Embed, "SkillListView"]:
    skills = filtered_skills(skill_type, element, linh_can)
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
            t_e = _TYPE_EMOJI.get(s.get("category", ""), "❓")
            el = s.get("element")
            el_tag = f" {_ELEM_EMOJI.get(el, '?')}" if el else ""
            cd = s.get("cooldown", 1)
            effects = _format_skill_effects(s.get("effects", []))
            realm = s.get("realm", 1)
            lines.append(
                f"{num} {t_e}{el_tag} **{s['vi']}** `{s['key']}`\n"
                f"  Cảnh Giới: **{realm}** | MP: **{s.get('mp_cost', 0)}** | DMG: **{s.get('base_dmg', 0)}** | "
                f"CD: **{cd}t**\n"
                f"  {effects}"
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

        for typ, label, style in _SKILL_TYPE_BUTTONS:
            active_style = discord.ButtonStyle.primary if typ == skill_type else style
            btn = discord.ui.Button(label=label, style=active_style, row=0)
            btn.callback = self._make_type_cb(typ)
            self.add_item(btn)

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

                gate = validate_learn_eligibility(player, skill_data)
                if not gate.ok:
                    if gate.error is LearnError.REALM_TOO_LOW:
                        idx = min(gate.needed_realm_index, len(QI_REALMS) - 1)
                        needed_name = QI_REALMS[idx].vi
                        msg = (
                            f"Cần đạt cảnh giới **{needed_name}** trở lên để học "
                            f"**{skill_data['vi']}**."
                        )
                    else:  # WRONG_LINH_CAN
                        elem = gate.missing_element or ""
                        elem_emoji = _ELEM_EMOJI.get(elem, "")
                        msg = (
                            f"Linh Căn của bạn không có {elem_emoji} **{elem.capitalize()}** — "
                            f"không thể học **{skill_data['vi']}**."
                        )
                    await interaction.response.send_message(
                        embed=error_embed(msg), ephemeral=True,
                    )
                    return

                irepo = InventoryRepository(session)
                all_inv = await irepo.get_all(player.id)
                scroll_row = find_skill_scroll(all_inv, skill_data["key"])

                if scroll_row is None:
                    scroll_key = scroll_key_for_skill(skill_data["key"])
                    scroll_item = registry.get_item(scroll_key)
                    scroll_name = scroll_item["vi"] if scroll_item else scroll_key
                    scroll_grade = scroll_item.get("grade", 1) if scroll_item else 1
                    # Grade 1-2: buyable in shop. Grade 3-4: drop-only from Bí Cảnh.
                    where = (
                        "Mua tại `/shop` (Tàng Kinh Các)"
                        if scroll_grade <= 2
                        else "Tìm trong Bí Cảnh (rơi ngẫu nhiên)"
                    )
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"Cần **{scroll_name}** để học **{skill_data['vi']}**.\n{where}."
                        ),
                        ephemeral=True,
                    )
                    return

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

                slots_result = await session.execute(
                    sa_select(CharacterSkill).where(CharacterSkill.player_id == player.id)
                )
                occupied_slots: dict[int, str] = {
                    r.slot_index: r.skill_key for r in slots_result.scalars().all()
                }

            scroll_key = scroll_row.item_key
            scroll_grade = scroll_row.grade
            scroll_item = registry.get_item(scroll_key)
            scroll_name = scroll_item["vi"] if scroll_item else scroll_key
            grade_name = GRADE_LABELS.get(Grade(scroll_grade), (str(scroll_grade),))[0]

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

            # Formation skills bypass the slot picker — they live in an
            # open-ended bar past MAX_SKILL_SLOTS, capped only by total MP
            # reservation. Pre-check the cap so the player learns *why* we
            # block instead of silently failing on equip.
            if is_formation_skill(skill_data):
                exceeds, projected = formation_reservation_would_exceed_cap(
                    player, skill_data["key"]
                )
                if exceeds:
                    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
                    await interaction.response.send_message(
                        embed=error_embed(
                            f"Không đủ Linh Khí để Trấn Trận **{skill_data['vi']}**.\n"
                            f"Sau khi trang bị: **{projected * 100:.1f}%** MP bị trấn — "
                            f"vượt mức tối đa **{FORMATION_MAX_RESERVE_PCT * 100:.0f}%**.\n"
                            f"Tu luyện Trận Đạo để giảm chi phí, hoặc xoá một trận pháp khác."
                        ),
                        ephemeral=True,
                    )
                    return
                target_slot = next_formation_slot(player)
                async with get_session() as session:
                    prepo = PlayerRepository(session)
                    player2 = await prepo.get_by_discord_id(interaction.user.id)
                    irepo = InventoryRepository(session)
                    if not await irepo.has_item(player2.id, scroll_key, Grade(scroll_grade)):
                        await interaction.response.send_message(
                            embed=error_embed("Cuộn sách đã hết trong túi đồ."),
                            ephemeral=True,
                        )
                        return
                    session.add(CharacterSkill(
                        player_id=player2.id,
                        skill_key=skill_data["key"],
                        slot_index=target_slot,
                    ))
                    await irepo.remove_item(player2.id, scroll_key, Grade(scroll_grade))
                    rem = await session.execute(
                        sa_select(CharacterSkill).where(CharacterSkill.player_id == player2.id)
                    )
                    remaining = [
                        type("S", (), {"slot_index": r.slot_index, "skill_key": r.skill_key})()
                        for r in sorted(rem.scalars().all(), key=lambda x: x.slot_index)
                    ]
                emb_after, v_after = _build_skills_embed_view(
                    remaining, interaction.user.id, back_fn=back_to_list,
                )
                emb_after.color = 0x2ECC71
                emb_after.description = (
                    (emb_after.description or "")
                    + f"\n✅ Trấn Trận **{skill_data['vi']}** thành công!"
                    + f" (MP trấn: **{projected * 100:.1f}%**)"
                )
                await interaction.response.edit_message(embed=emb_after, view=v_after)
                return

            t_e = _TYPE_EMOJI.get(skill_data.get("category", ""), "❓")
            el = skill_data.get("element")
            el_tag = f" {_ELEM_EMOJI.get(el, '')}" if el else ""
            effects = _format_skill_effects(skill_data.get("effects", []))

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
    """Slot picker — shown after player selects a skill to learn."""

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
            if not skill_data:
                continue
            category = skill_data.get("category", "")
            t_emoji   = _TYPE_EMOJI.get(category, "❓")
            t_label   = _TYPE_LABEL.get(category, category or "—")
            elem      = skill_data.get("element")
            elem_tag  = (
                f" · {_ELEM_EMOJI.get(elem, '')} {elem.capitalize()}"
                if elem else ""
            )
            effects = _format_skill_effects(skill_data.get("effects", []))
            embed.add_field(
                name=f"[Slot {s.slot_index}] {t_emoji} {skill_data['vi']}",
                value=(
                    f"*{t_label}{elem_tag}*\n"
                    f"💙 MP **{skill_data.get('mp_cost', 0)}** · "
                    f"⚔️ ST **{skill_data.get('base_dmg', 0)}** · "
                    f"⏱️ CD **{skill_data.get('cooldown', 1)}t**\n"
                    f"**Hiệu ứng:** {effects}"
                ),
                inline=False,
            )
    footer = "Nhấn 🗑 để xoá kỹ năng khỏi slot."
    if back_fn:
        footer += " • ◀ để trở về danh sách."
    embed.set_footer(text=footer)
    return embed, SkillsView(equipped, discord_id, back_fn=back_fn)


class SkillsView(discord.ui.View):
    """Interactive view for /skills — Forget button per equipped slot."""

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

                remaining_result = await session.execute(
                    sa_select(CharacterSkill).where(CharacterSkill.player_id == player.id)
                )
                remaining_data = [
                    type("S", (), {"slot_index": r.slot_index, "skill_key": r.skill_key})()
                    for r in sorted(remaining_result.scalars().all(), key=lambda x: x.slot_index)
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


class SkillsCog(commands.Cog, name="Skills"):
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
    @app_commands.describe(slot="Slot cần xoá (0–5 cho thường, 6+ cho trận pháp)")
    async def forget(self, interaction: discord.Interaction, slot: int) -> None:
        from src.db.models.skill import CharacterSkill
        from sqlalchemy import select as sa_select

        if slot < 0:
            await interaction.response.send_message(
                embed=error_embed("Slot không hợp lệ."), ephemeral=True
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

        await interaction.response.send_message(
            embed=success_embed(f"Đã xoá **{skill_name}** khỏi slot **{slot}**."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SkillsCog(bot))
