"""Skill commands — view, learn, and manage combat skills."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.data.registry import registry
from src.db.connection import get_session
from src.db.repositories.inventory_repo import InventoryRepository
from src.db.repositories.player_repo import PlayerRepository
from src.db.repositories.skill_learned_repo import add_learned, get_learned_keys
from src.game.constants.grades import Grade, GRADE_LABELS
from src.game.constants.linh_can import parse_linh_can
from src.game.engine.effects import EFFECTS, displayable_stat_bonus
from src.game.systems.skills import (
    filtered_skills,
    find_skill_scroll,
    formation_key_for_skill,
    is_formation_skill,
    next_formation_slot,
    scroll_key_for_skill,
    validate_learn_eligibility,
)
from src.game.systems.skill_mastery import (
    breakthrough_preview,
    hidden_gate_revealed,
    progress_summary,
)
from src.game.constants.skill_mastery import (
    DINH_DAO_CHAU_KEY,
    HO_DAO_PHU_KEY,
    VISIBLE_MAX,
)
from src.utils import emojis
from src.utils.config import settings
from src.utils.embed_builder import base_embed, error_embed, progress_bar, success_embed
from src.utils.discord_safe import safe_defer
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


async def _player_skill_state(discord_id: int) -> dict:
    """Snapshot what the browser needs to highlight rows + paint Học buttons.

    Returns ``{owned_scrolls, learned, equipped, linh_can}`` so the same data
    carries through page/filter changes without re-querying every render.

    ``learned`` = persistent library ∪ all CharacterSkill markers (so formation
    markers keep their ✓). ``equipped`` = regular combat slots only (slot < 6).
    """
    from src.db.models.skill import MAX_SKILL_SLOTS

    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            return {
                "owned_scrolls": set(), "learned": set(),
                "equipped": set(), "linh_can": [],
            }
        equipped = {
            s.skill_key for s in (player.skills or [])
            if s.slot_index < MAX_SKILL_SLOTS
        }
        learned = (await get_learned_keys(session, player.id)) | {
            s.skill_key for s in (player.skills or [])
        }
        linh_can = parse_linh_can(player.linh_can or "")
        irepo = InventoryRepository(session)
        all_inv = await irepo.get_all(player.id)
    owned: set[str] = set()
    prefix = "Scroll_"
    for inv in all_inv:
        if inv.item_key.startswith(prefix) and inv.quantity > 0:
            owned.add(inv.item_key[len(prefix):])
    return {
        "owned_scrolls": owned, "learned": learned,
        "equipped": equipped, "linh_can": linh_can,
    }


def _learn_status(skill: dict, state: dict) -> str:
    """One-letter status for the row prefix.

    ``✓`` equipped (regular) / learned (formation) | ``📥`` learned regular,
    re-equip free | ``✨`` ready (scroll + Linh Căn fits) | ``📜`` scroll only
    (Linh Căn gated) | ``""`` no scroll.
    """
    key = skill["key"]
    is_formation = skill.get("category") == "formation"
    equipped = state.get("equipped") or set()
    if not is_formation and key in equipped:
        return "✓"
    if key in state["learned"]:
        return "✓" if is_formation else "📥"
    if key not in state["owned_scrolls"]:
        return ""
    skill_elem = skill.get("element")
    if (
        skill_elem is not None
        and not is_formation
        and skill_elem not in (state["linh_can"] or [])
    ):
        return "📜"
    return "✨"


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


# Curated one-liner hints for "all-internal" effects — those whose entire
# stat_bonus is config / scaling / season placeholders (dropped by
# ``displayable_stat_bonus``) and which carry no DoT / aura / CC detail, so they
# would otherwise render name-only in the browser. Each value is a compact
# summary distilled from the effect's ``description_vi``.
_EFFECT_BROWSER_HINTS: dict[str, str] = {
    "BuffPhuongHoangChanHoa": "Mỗi 5% HP đã mất: +hồi sinh lực • bị đánh in Phượng Hỏa lên địch",
    "BuffLuuLyTinhHoa": "Mỗi Hỏa DoT trên địch: 30% thanh tẩy 1 debuff • +Kháng Bạo mỗi lần",
    "BuffQuyAnhMeTung": "Mỗi lần né +1 tầng (tối đa 3): +Tốc & Né Tránh theo tầng",
    "BuffTuyetDieuVoAnh": "Miễn dịch mọi hiệu ứng giảm Tốc (3 lượt)",
    "BuffTamLoiCong": "Mỗi đòn Tâm Lôi liên tiếp +1 tầng (tối đa 5): +Bạo Kích & +ST Lôi",
    "BuffTuLoiQuyet": "Mỗi kỹ năng Lôi +1 tầng (tối đa 10): +Pháp Công & +ST Lôi",
    "BuffTuLuongBatThienCan": "HP>50%: +ST Thủy • HP<50%: +giảm ST nhận (4 lượt)",
    "BuffXuanThuLuanChuyen": "Xuân: hồi HP/Khiên, −ST nhận • Thu: +ST gây ra, +Bạo Kích",
    "BuffThienMenhQuyNhat": "Mỗi 10% HP đã mất: +giảm ST nhận & +kháng toàn hệ",
    "DebuffPhuongHoa": "Mỗi tầng: đốt 2% HP/lượt (Hỏa) & −10% hồi máu (tối đa 3)",
    "DebuffNhuocThuyAn": "Mỗi tầng −4% Kháng Thủy • 5 tầng: bùng nổ 10% HP đã mất",
    "DebuffTranSonHa": "Mỗi tầng −8% sát thương gây ra (tối đa 3)",
    "DebuffLoiKiepAn": "Mỗi tầng +ST Lôi nhận & −kháng Lôi • 10 tầng: Vạn Kiếp Phán",
    "DebuffPhongNhanThuc": "Mỗi tầng −5% trị liệu/khiên & +ST Phong nhận (tối đa 7)",
}


def _describe_effect(key: str) -> str:
    """Short human description of a single skill effect key.

    Returns a compact ``emoji name(detail)`` tag — e.g. ``💥Đốt Cháy(8%/t)``
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
    # DoT tick rate per turn — the absolute damage scales with the applier's
    # max(atk, matk) at apply time, so a "% HP" label would be misleading.
    if meta.dot_pct > 0:
        details.append(f"DoT {meta.dot_pct * 100:.1f}%/t")
    # Stat mods — signed, shortened. Internal placeholders (config-only keys,
    # scaling-rule keys, ``_``-prefixed season/refresh magnitudes) are dropped
    # so the browser never shows "+0 _xt_autumn_crit_rating" style noise.
    for stat, val in displayable_stat_bonus(meta).items():
        details.append(_format_stat_bonus(stat, val))
    # Aura-on-hit: describe the chance-gated effect that's spread to enemies
    # the holder strikes (e.g. BuffHanKhi → Làm Chậm on every hit).
    if meta.aura_on_hit is not None:
        # 2-tuple (effect, chance) or 3-tuple (effect, chance, duration).
        aura_key, chance, *aura_rest = meta.aura_on_hit
        aura_meta = EFFECTS.get(aura_key)
        aura_label = aura_meta.vi if aura_meta else aura_key
        pct = int(round(chance * 100))
        pct_prefix = "" if pct == 100 else f"{pct}% "
        dur_suffix = f" {int(aura_rest[0])} lượt" if aura_rest else ""
        details.append(f"{pct_prefix}{aura_label} on-hit{dur_suffix}")
    # CC flags without explicit stat mods
    if meta.skips_turn and not details:
        details.append("mất lượt")
    if meta.prevents_skills and "mất lượt" not in details:
        details.append("câm kỹ năng")

    tag = f"{meta.emoji}{meta.vi}"
    if details:
        return f"{tag}({', '.join(details)})"
    # All-internal effect (stats filtered out, no DoT/aura/CC): fall back to a
    # curated one-liner so the browser still hints at the mechanic.
    hint = _EFFECT_BROWSER_HINTS.get(key)
    return f"{tag}({hint})" if hint else tag


def _format_skill_dmg(skill_data: dict) -> str:
    """Render a skill's flat damage plus its ATK / MATK scaling coefficients.

    Returns ``"<base_dmg>"`` for skills with no stat scaling, otherwise
    appends ``+ATK×<s_atk>`` and/or ``+MATK×<s_matk>`` so players can see
    how much the actual rolled damage will lean on their stats. Zero
    coefficients are omitted to keep the line compact.
    """
    base = int(skill_data.get("base_dmg", 0) or 0)
    scale = skill_data.get("dmg_scale") or {}
    if isinstance(scale, dict):
        s_atk = float(scale.get("atk", 0.0) or 0.0)
        s_matk = float(scale.get("matk", 0.0) or 0.0)
    else:
        s_atk = s_matk = float(scale or 0.0)
    parts: list[str] = [str(base)]
    if s_atk:
        parts.append(f"ATK×{s_atk:g}")
    if s_matk:
        parts.append(f"MATK×{s_matk:g}")
    return " + ".join(parts)


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
    state: dict | None = None,
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
        title += f" [{emojis.for_element(element)}]"

    # Owned-scroll / learned snapshot for row badges + Học button styling.
    # Falls back to an empty state so the cog still renders if state load fails.
    state = state or {
        "owned_scrolls": set(), "learned": set(),
        "equipped": set(), "linh_can": linh_can or [],
    }

    if not page_skills:
        embed = base_embed(title, "Không tìm thấy kỹ năng phù hợp với Linh Căn của bạn.", color=0x9B59B6)
    else:
        lines: list[str] = []
        for i, s in enumerate(page_skills):
            num = _NUMBER_EMOJI[i]
            t_e = emojis.SKILL_CATEGORY_EMOJI.get(s.get("category", ""), "❓")
            el = s.get("element")
            el_tag = f" {emojis.for_element(el)}" if el else ""
            cd = s.get("cooldown", 1)
            effects = _format_skill_effects(s.get("effects", []))
            grade = int(s.get("scroll_grade", 1))
            grade_label = GRADE_LABELS.get(Grade(grade), (str(grade),))[0]
            badge = _learn_status(s, state)
            badge_tag = f"{badge} " if badge else ""
            lines.append(
                f"{num} {badge_tag}{t_e}{el_tag} **{s['vi']}** `{s['key']}`\n"
                f"  Phẩm: **{grade_label}** | MP: **{s.get('mp_cost', 0)}** | DMG: **{_format_skill_dmg(s)}** | "
                f"CD: **{cd}t**\n"
                f"  {effects}"
            )
        embed = base_embed(title, "\n\n".join(lines), color=0x9B59B6)

    legend = (
        "✨ sẵn sàng • 📜 có ngọc giản (chưa đủ điều kiện) • "
        "📥 đã học (trang bị lại miễn phí) • ✓ đã có"
    )
    embed.set_footer(text=f"Trang {page + 1}/{total_pages} • {total} kỹ năng • {legend}")
    view = SkillListView(
        discord_id=discord_id,
        skill_type=skill_type,
        element=element,
        page=page,
        total_pages=total_pages,
        page_skills=page_skills,
        back_fn=back_fn,
        linh_can=linh_can,
        state=state,
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
        state: dict | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._skill_type = skill_type
        self._element = element
        self._page = page
        self._total_pages = total_pages
        self._back_fn = back_fn
        self._linh_can = linh_can
        self._state = state or {
            "owned_scrolls": set(), "learned": set(),
            "equipped": set(), "linh_can": linh_can or [],
        }

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

        # Paint each Học button by readiness — green for ready, blue for
        # scroll-but-gated, primary "Trang bị" for learned-but-unequipped
        # (free re-equip), gray + disabled for already-equipped, and default
        # secondary when the player has no scroll yet.
        for i, s in enumerate(page_skills or []):
            badge = _learn_status(s, self._state)
            if badge == "✓":
                style = discord.ButtonStyle.secondary
                label = f"{_NUMBER_EMOJI[i]} ✓ Đã có"
                disabled = True
            elif badge == "📥":
                style = discord.ButtonStyle.primary
                label = f"{_NUMBER_EMOJI[i]} 📥 Trang bị"
                disabled = False
            elif badge == "✨":
                style = discord.ButtonStyle.success
                label = f"{_NUMBER_EMOJI[i]} ✨ Học"
                disabled = False
            elif badge == "📜":
                style = discord.ButtonStyle.primary
                label = f"{_NUMBER_EMOJI[i]} 📜 Học"
                disabled = False
            else:
                style = discord.ButtonStyle.secondary
                label = f"{_NUMBER_EMOJI[i]} Học"
                disabled = False
            btn = discord.ui.Button(
                label=label, style=style, disabled=disabled,
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

            from src.db.models.skill import CharacterSkill, MAX_SKILL_SLOTS
            from sqlalchemy import select as sa_select

            skill_key = skill_data["key"]
            is_formation = is_formation_skill(skill_data)

            async with get_session() as session:
                prepo = PlayerRepository(session)
                player = await prepo.get_by_discord_id(interaction.user.id)
                if player is None:
                    await interaction.response.send_message(embed=error_embed("Chưa có nhân vật."), ephemeral=True)
                    return

                # Snapshot all CharacterSkill rows once: equipped-regular check,
                # library lookup, and the slot picker all read from this.
                slots_result = await session.execute(
                    sa_select(CharacterSkill).where(CharacterSkill.player_id == player.id)
                )
                all_rows = slots_result.scalars().all()
                occupied_slots: dict[int, str] = {r.slot_index: r.skill_key for r in all_rows}
                equipped_regular = {
                    r.skill_key for r in all_rows if r.slot_index < MAX_SKILL_SLOTS
                }
                learned_keys = await get_learned_keys(session, player.id)

                # Already equipped (regular) → nothing to do. Defensive: the
                # browser disables the button for this state.
                if not is_formation and skill_key in equipped_regular:
                    await interaction.response.send_message(
                        embed=error_embed(f"Đã trang bị **{skill_data['vi']}** rồi."),
                        ephemeral=True,
                    )
                    return

                # Formations are marked learned via a CharacterSkill marker row
                # (slot ≥ MAX_SKILL_SLOTS). Re-learning would double-spend a
                # scroll + add a duplicate marker — block it. Defensive: the
                # browser disables the ✓ button for this state.
                if is_formation and skill_key in {r.skill_key for r in all_rows}:
                    await interaction.response.send_message(
                        embed=error_embed(f"Đã học kỹ năng **{skill_data['vi']}** rồi."),
                        ephemeral=True,
                    )
                    return

                # Re-equip path: a learned regular skill is free to re-slot.
                # Skip both the Linh Căn gate and the scroll gate entirely.
                is_reequip = not is_formation and skill_key in learned_keys

                if not is_reequip:
                    gate = validate_learn_eligibility(player, skill_data)
                    if not gate.ok:
                        # Only WRONG_LINH_CAN remains — realm gating was removed.
                        elem = gate.missing_element or ""
                        elem_emoji = emojis.for_element(elem) if elem else ""
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
                    scroll_row = find_skill_scroll(all_inv, skill_key)

                    if scroll_row is None:
                        scroll_key = scroll_key_for_skill(skill_key)
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

            if is_reequip:
                scroll_key = scroll_grade = scroll_name = grade_name = None
            else:
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
                # Refresh state so the row badges + Học button styling reflect
                # the just-completed learn (scroll consumed, skill now "✓").
                refreshed = await _player_skill_state(did)
                emb, v = _build_skilllist(
                    discord_id=did, skill_type=snap_type,
                    element=snap_elem, page=snap_page,
                    back_fn=outer_back_fn, linh_can=snap_lc,
                    state=refreshed,
                )
                await ia.edit_original_response(embed=emb, view=v)

            # Formation skills no longer occupy a chosen skill slot — learning
            # the scroll unlocks the matching formation (CharacterFormation row)
            # so it becomes selectable in /formation_hub. The CharacterSkill row
            # is still recorded as a "learned" marker (slot ≥ MAX_SKILL_SLOTS,
            # invisible in the equipped bar) so the browser can paint ✓ on
            # repeat visits and migration paths can audit prior learns.
            if is_formation_skill(skill_data):
                from src.db.repositories.formation_repo import FormationRepository

                target_formation = formation_key_for_skill(skill_data)
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
                    if target_formation:
                        frepo = FormationRepository(session)
                        await frepo.get_or_create(player2.id, target_formation)
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
                form_data = registry.get_formation(target_formation) if target_formation else None
                form_label = form_data.get("vi", target_formation) if form_data else "—"
                emb_after.description = (
                    (emb_after.description or "")
                    + f"\n✅ Đã học **{skill_data['vi']}** và mở khoá trận **{form_label}**."
                    + "\nDùng `/formation_hub` để kích hoạt trận pháp."
                )
                await interaction.response.edit_message(embed=emb_after, view=v_after)
                return

            t_e = emojis.SKILL_CATEGORY_EMOJI.get(skill_data.get("category", ""), "❓")
            el = skill_data.get("element")
            el_tag = f" {emojis.for_element(el)}" if el else ""
            effects = _format_skill_effects(skill_data.get("effects", []))

            header = "📥 Trang bị lại" if is_reequip else "📖 Học"
            embed = base_embed(f"{header}: {skill_data['vi']}", color=0x9B59B6)
            embed.add_field(
                name="Kỹ Năng",
                value=(
                    f"{t_e}{el_tag} **{skill_data['vi']}** `{skill_data['key']}`\n"
                    f"MP: **{skill_data.get('mp_cost', 0)}** | DMG: **{_format_skill_dmg(skill_data)}** | "
                    f"CD: **{skill_data.get('cooldown', 1)}t**\n"
                    f"Hiệu ứng: {effects}"
                ),
                inline=False,
            )
            if is_reequip:
                embed.add_field(
                    name="Chi Phí",
                    value="📥 Đã học — trang bị lại **miễn phí**.",
                    inline=False,
                )
            else:
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
                requires_scroll=not is_reequip,
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
                state=self._state,
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
            state=self._state,
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
            state=self._state,
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
            state=self._state,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)


class SkillLearnView(discord.ui.View):
    """Slot picker — shown after player selects a skill to learn."""

    def __init__(
        self,
        discord_id: int,
        skill_data: dict,
        scroll_key: str | None,
        scroll_grade: int | None,
        occupied_slots: dict[int, str],
        back_fn,
        requires_scroll: bool = True,
    ) -> None:
        super().__init__(timeout=120)
        self._discord_id = discord_id
        self._skill_data = skill_data
        self._scroll_key = scroll_key
        self._scroll_grade = scroll_grade
        self._back_fn = back_fn
        self._requires_scroll = requires_scroll

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
                if self._requires_scroll and not await irepo.has_item(
                    player.id, self._scroll_key, Grade(self._scroll_grade)
                ):
                    await interaction.response.edit_message(
                        embed=error_embed("Cuộn sách đã hết trong túi đồ."), view=None
                    )
                    return

                # A CharacterSkill row for this key now means it's *equipped*
                # (slot 0–5). The library, not this table, tracks "learned".
                dup = await session.execute(
                    sa_select(CharacterSkill).where(
                        CharacterSkill.player_id == player.id,
                        CharacterSkill.skill_key == skill_key,
                    )
                )
                if dup.scalar_one_or_none():
                    await interaction.response.edit_message(
                        embed=error_embed("Đã trang bị skill này rồi."), view=None
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
                    # Safety net: preserve the unequipped occupant in the library
                    # before its slot row is deleted, so it stays re-equippable.
                    await add_learned(session, player.id, old_row.skill_key)
                    await session.delete(old_row)
                    await session.flush()

                session.add(CharacterSkill(player_id=player.id, skill_key=skill_key, slot_index=slot))
                if self._requires_scroll:
                    await irepo.remove_item(player.id, self._scroll_key, Grade(self._scroll_grade))
                    await add_learned(session, player.id, skill_key)

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
            verb = "Học" if self._requires_scroll else "Trang bị"
            embed.description = (embed.description or "") + f"\n✅ {verb} **{skill_name}** → slot **{slot}** thành công!"
            await interaction.response.edit_message(embed=embed, view=view)

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)


def _build_skills_embed_view(
    equipped: list, discord_id: int, back_fn=None
) -> tuple[discord.Embed, "SkillsView"]:
    # Formation skills are learned but no longer occupy the active bar — they
    # fire from /formation_hub when the matching formation is active. Strip
    # them here so the UI only renders combat skills the player actually
    # picked into a slot.
    bar_slots = [
        s for s in equipped
        if not is_formation_skill(registry.get_skill(s.skill_key))
    ]

    embed = base_embed("🎯 Kỹ Năng Trang Bị", color=0x9B59B6)
    if not bar_slots:
        embed.description = (
            "Chưa trang bị kỹ năng nào.\n"
            "Nhấn **📚 Tàng Kinh Các** để xem và học kỹ năng phù hợp Linh Căn."
        )
    else:
        for s in bar_slots:
            skill_data = registry.get_skill(s.skill_key)
            if not skill_data:
                continue
            category = skill_data.get("category", "")
            t_emoji   = emojis.SKILL_CATEGORY_EMOJI.get(category, "❓")
            t_label   = _TYPE_LABEL.get(category, category or "—")
            elem      = skill_data.get("element")
            elem_tag  = (
                f" · {emojis.for_element(elem)} {elem.capitalize()}"
                if elem else ""
            )
            effects = _format_skill_effects(skill_data.get("effects", []))
            embed.add_field(
                name=f"[Slot {s.slot_index}] {t_emoji} {skill_data['vi']}",
                value=(
                    f"*{t_label}{elem_tag}*\n"
                    f"💙 MP **{skill_data.get('mp_cost', 0)}** · "
                    f"⚔️ ST **{_format_skill_dmg(skill_data)}** · "
                    f"⏱️ CD **{skill_data.get('cooldown', 1)}t**\n"
                    f"**Hiệu ứng:** {effects}"
                ),
                inline=False,
            )
    footer = "🗑 xoá kỹ năng • 📚 mở Tàng Kinh Các để học kỹ năng mới."
    if back_fn:
        footer += " • ◀ trở về."
    embed.set_footer(text=footer)
    return embed, SkillsView(bar_slots, discord_id, back_fn=back_fn)


async def _fetch_equipped(discord_id: int) -> list:
    """Equipped skill slots as lightweight row stand-ins, ordered by slot —
    the shape ``_build_skills_embed_view`` expects. Empty list when the player
    has no character."""
    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            return []
        return [
            type("S", (), {"slot_index": s.slot_index, "skill_key": s.skill_key})()
            for s in sorted(player.skills or [], key=lambda x: x.slot_index)
        ]


class SkillsView(discord.ui.View):
    """Interactive view for /skills — Forget button per equipped slot, plus a
    📚 Tàng Kinh Các button that opens the learnable-skill browser in place."""

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

        lib_btn = discord.ui.Button(
            label="📚 Tàng Kinh Các", style=discord.ButtonStyle.primary, row=4,
        )
        lib_btn.callback = self._open_library_cb
        self.add_item(lib_btn)

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
                # Preserve in the library before unequipping so re-equip is free.
                await add_learned(session, player.id, skill_row.skill_key)
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
            embed.description = (
                (embed.description or "")
                + f"\n✅ Đã gỡ **{skill_name}** khỏi slot **{slot}** — "
                + "vẫn lưu trong Tàng Kinh Các (trang bị lại miễn phí)."
            )
            await interaction.response.edit_message(embed=embed, view=view)

        return _cb

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return
        await self._back_fn(interaction)

    async def _open_library_cb(self, interaction: discord.Interaction) -> None:
        """Open the Tàng Kinh Các browser in place, with a back button that
        returns to this equipped view — the merge that folds the old
        /skilllist command into the single /skills entry."""
        if interaction.user.id != self._discord_id:
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return

        did = self._discord_id
        outer_back = self._back_fn

        async def return_to_equipped(ia: discord.Interaction) -> None:
            equipped = await _fetch_equipped(did)
            emb, v = _build_skills_embed_view(equipped, did, back_fn=outer_back)
            await ia.edit_original_response(embed=emb, view=v)

        state = await _player_skill_state(did)
        emb, v = _build_skilllist(
            discord_id=did, linh_can=state["linh_can"], state=state,
            back_fn=return_to_equipped,
        )
        await interaction.edit_original_response(embed=emb, view=v)


# ── Skill Mastery (Tinh Thông) ──────────────────────────────────────────────
#
# Presentation-only Views over the mastery repo. The Discord layer never does
# math: progression rendering goes through ``progress_summary`` /
# ``breakthrough_preview`` (pure), and the breakthrough roll + inventory spend
# happen entirely inside ``attempt_breakthrough`` (repo). Modelled on the
# constitution hub (TheChatHubView → ConstitutionDetailView): a Select picks a
# breakthrough-ready skill, the detail View consumes materials on a roll, and
# items burn even on failure (gacha-style attempt economics).

_FRUIT_KEY = "MasteryThongThienDaoQua"
_MASTERY_COLOR = 0x9B59B6


async def _mastery_snapshot(discord_id: int) -> dict | None:
    """Load everything a mastery render needs in one DB round-trip.

    Returns ``None`` when the player has no character. Otherwise
    ``{equipped, levels, owned, dao_ti, player_id}`` where ``equipped`` is the
    ordered list of combat skill keys (formation skills stripped, matching the
    /skills bar), ``levels`` maps skill_key→(level, xp, hidden_unlocked),
    ``owned`` maps the mastery item keys→owned qty.
    """
    from src.db.repositories.skill_mastery import get_mastery_map
    from src.game.constants.skill_mastery import GATES
    from sqlalchemy import select as sa_select
    from src.db.models.skill_mastery import CharacterSkillMastery

    async with get_session() as session:
        prepo = PlayerRepository(session)
        player = await prepo.get_by_discord_id(discord_id)
        if player is None:
            return None

        equipped = [
            s.skill_key
            for s in sorted(player.skills or [], key=lambda x: x.slot_index)
            if not is_formation_skill(registry.get_skill(s.skill_key))
        ]

        levels = await get_mastery_map(session, player.id)
        # Pull the full rows for equipped skills so we get xp + hidden_unlocked
        # (get_mastery_map only returns level). One IN-query.
        rows = await session.execute(
            sa_select(CharacterSkillMastery).where(
                CharacterSkillMastery.player_id == player.id,
                CharacterSkillMastery.skill_key.in_(equipped or ["__none__"]),
            )
        )
        detail: dict[str, tuple[int, int, bool]] = {}
        gate_fails: dict[str, int] = {}
        for r in rows.scalars().all():
            detail[r.skill_key] = (r.level, r.xp, r.hidden_unlocked)
            gate_fails[r.skill_key] = r.gate_fails

        irepo = InventoryRepository(session)
        owned: dict[str, int] = {}
        # Gate materials (incl. the hidden fruit) come from GATES so the owned
        # counts never drift from the constants; talismans are added explicitly.
        wanted = {g["item_key"] for g in GATES.values()}
        wanted |= {HO_DAO_PHU_KEY, DINH_DAO_CHAU_KEY, _FRUIT_KEY}
        for row in await irepo.get_all(player.id):
            if row.item_key in wanted:
                owned[row.item_key] = owned.get(row.item_key, 0) + row.quantity

    return {
        "player_id": player.id,
        "equipped": equipped,
        "levels": levels,
        "detail": detail,
        "gate_fails": gate_fails,
        "owned": owned,
        "dao_ti": bool(player.dao_ti_unlocked),
    }


def _skill_vi(skill_key: str) -> str:
    data = registry.get_skill(skill_key)
    return data["vi"] if data else skill_key


def _item_vi(item_key: str) -> str:
    item = registry.get_item(item_key)
    return item["vi"] if item else item_key


def _mastery_row_state(skill_key: str, snap: dict) -> dict:
    """Per-skill render state: progress summary + readiness tag.

    Combines the pure ``progress_summary`` / ``breakthrough_preview`` with the
    snapshot's owned-item counts and dao_ti flag to decide which status tag a
    row shows in the hub.
    """
    level, xp, hidden_unlocked = snap["detail"].get(skill_key, (1, 0, False))
    summ = progress_summary(level, xp, hidden_unlocked)
    owned = snap["owned"]
    holds_fruit = owned.get(_FRUIT_KEY, 0) >= 1

    tag = ""
    ready = False
    if summ["at_ceiling"]:
        # At level 20 the gate is the hidden one — only surface it once the
        # secret is revealed; otherwise the skill reads as MAX.
        if level == VISIBLE_MAX and not hidden_unlocked:
            if hidden_gate_revealed(level, holds_fruit, snap["dao_ti"]):
                tag = "✦ Đăng Phong khả dụng"
                ready = True
            else:
                tag = "🌟 Viên Mãn (MAX)"
        else:
            gate_item = _gate_item_for(level)
            pv = breakthrough_preview(
                level, _gate_fails(snap, skill_key),
                owned.get(gate_item, 0), False, False,
            )
            if pv.get("at_gate"):
                need = pv["required_qty"]
                if pv["has_enough"]:
                    tag = "⚔️ Sẵn sàng Đột Phá"
                    ready = True
                else:
                    tag = f"🔒 Cần {need}× {_item_vi(pv['gate_item_key'])}"

    return {
        "level": level,
        "xp": xp,
        "hidden_unlocked": hidden_unlocked,
        "summary": summ,
        "tag": tag,
        "ready": ready,
    }


def _mastery_bar(ratio: float) -> str:
    """12-char ASCII XP bar from a [0,1] ratio (block-style, repo house font)."""
    return progress_bar(int(round(ratio * 100)), 100, 12)


def _gate_item_for(level: int) -> str:
    """Gate item key for a ceiling level, or "" when not a gate."""
    from src.game.constants.skill_mastery import GATES

    gate = GATES.get(level)
    return gate["item_key"] if gate else ""


def _gate_fails(snap: dict, skill_key: str) -> int:
    """Pity counter for a skill, read from the snapshot (0 when no row yet).

    The authoritative pity is re-read inside ``attempt_breakthrough`` from the
    persisted row, so a stale preview here is cosmetic only.
    """
    return snap.get("gate_fails", {}).get(skill_key, 0)


def _build_mastery_hub(discord_id: int, snap: dict) -> tuple[discord.Embed, "MasteryHubView"]:
    equipped = snap["equipped"]
    embed = base_embed("📈 Tinh Thông Kỹ Năng", color=_MASTERY_COLOR)

    if not equipped:
        embed.description = (
            "Chưa trang bị kỹ năng chiến đấu nào.\n"
            "Dùng `/skilllist` để học và `/skills` để trang bị trước khi tinh thông."
        )
        return embed, MasteryHubView(discord_id, snap, ready_keys=[])

    ready_keys: list[str] = []
    lines: list[str] = []
    for key in equipped:
        st = _mastery_row_state(key, snap)
        summ = st["summary"]
        bar = _mastery_bar(summ["ratio"])
        xp_part = (
            f"{summ['xp']}/{summ['xp_next']}"
            if summ["xp_next"] is not None else "MAX"
        )
        tag = f"  ·  {st['tag']}" if st["tag"] else ""
        lines.append(
            f"⚔️ **{_skill_vi(key)}** — *{summ['band_vi']}* "
            f"`{summ['level']}/{summ['cap']}`{tag}\n"
            f"  `{bar}` {xp_part}"
        )
        if st["ready"]:
            ready_keys.append(key)

    embed.description = "\n\n".join(lines)
    legend = (
        "⚔️ sẵn sàng đột phá • 🔒 thiếu nguyên liệu • "
        "✦ Đăng Phong • 🌟 viên mãn"
    )
    embed.set_footer(text=legend)
    return embed, MasteryHubView(discord_id, snap, ready_keys=ready_keys)


def _build_breakthrough_detail(
    discord_id: int,
    snap: dict,
    skill_key: str,
    *,
    use_ho_dao_phu: bool = False,
    use_dinh_dao_chau: bool = False,
) -> tuple[discord.Embed, "BreakthroughDetailView"]:
    st = _mastery_row_state(skill_key, snap)
    level = st["level"]
    hidden_unlocked = st["hidden_unlocked"]
    owned = snap["owned"]
    gate_fails = _gate_fails(snap, skill_key)

    holds_fruit = owned.get(_FRUIT_KEY, 0) >= 1
    is_hidden_path = (
        level == VISIBLE_MAX
        and not hidden_unlocked
        and hidden_gate_revealed(level, holds_fruit, snap["dao_ti"])
    )

    pv = breakthrough_preview(
        level, gate_fails, owned.get(_gate_item_for(level), 0),
        use_ho_dao_phu, use_dinh_dao_chau,
    )

    title = f"⚡ Đột Phá: {_skill_vi(skill_key)}"
    if is_hidden_path:
        title = f"✦ Đăng Phong: {_skill_vi(skill_key)}"

    embed = base_embed(title, color=_MASTERY_COLOR)
    embed.add_field(
        name="Tầng",
        value=f"**{level}** → **{level + 1}**",
        inline=True,
    )
    if pv.get("at_gate"):
        need = pv["required_qty"]
        have = owned.get(pv["gate_item_key"], 0)
        mark = "✅" if have >= need else "❌"
        embed.add_field(
            name="Nguyên liệu",
            value=f"{mark} {_item_vi(pv['gate_item_key'])} ×**{need}** (có **{have}**)",
            inline=True,
        )
        # success % including currently-toggled Hộ Đạo Phù + pity
        embed.add_field(
            name="Tỉ lệ",
            value=f"🎲 **{pv['success_pct']}%** (pity {gate_fails})",
            inline=True,
        )

    toggles: list[str] = []
    if use_ho_dao_phu:
        toggles.append(f"🛡️ {_item_vi(HO_DAO_PHU_KEY)} (+20%)")
    if use_dinh_dao_chau:
        toggles.append(f"💠 {_item_vi(DINH_DAO_CHAU_KEY)} (hoàn nếu thất bại)")
    if toggles:
        embed.add_field(name="Đang dùng kèm", value="\n".join(toggles), inline=False)

    if is_hidden_path:
        embed.description = (
            "✦ **Đăng Phong Tạo Cực** — bí cảnh ẩn giấu sau Viên Mãn. "
            "Đột phá thành công mở khóa Tầng 21–25.\n"
            "*Tiêu hao 1× Thông Thiên Đạo Quả khi thử (kể cả thất bại).*"
        )

    view = BreakthroughDetailView(
        discord_id, snap, skill_key,
        use_ho_dao_phu=use_ho_dao_phu,
        use_dinh_dao_chau=use_dinh_dao_chau,
        can_attempt=bool(pv.get("has_enough")),
    )
    return embed, view


class MasteryHubView(discord.ui.View):
    """Mastery roster — Select of breakthrough-ready skills + refresh."""

    def __init__(self, discord_id: int, snap: dict, ready_keys: list[str]) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._snap = snap

        if ready_keys:
            options = [
                discord.SelectOption(
                    label=_skill_vi(k)[:100],
                    value=k,
                    description=f"Tầng {snap['detail'].get(k, (1, 0, False))[0]} — sẵn sàng đột phá"[:100],
                    emoji="⚡",
                )
                for k in ready_keys[:25]
            ]
            select = discord.ui.Select(
                placeholder="⚡ Chọn kỹ năng để Đột Phá…",
                options=options,
                row=0,
            )
            select.callback = self._select_cb
            self.add_item(select)

        refresh = discord.ui.Button(
            label="🔄 Làm mới", style=discord.ButtonStyle.secondary, row=1,
        )
        refresh.callback = self._refresh_cb
        self.add_item(refresh)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _select_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        skill_key = interaction.data["values"][0]
        snap = await _mastery_snapshot(self._discord_id)
        if snap is None:
            await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        embed, view = _build_breakthrough_detail(self._discord_id, snap, skill_key)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _refresh_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        snap = await _mastery_snapshot(self._discord_id)
        if snap is None:
            await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        embed, view = _build_mastery_hub(self._discord_id, snap)
        await interaction.response.edit_message(embed=embed, view=view)


class BreakthroughDetailView(discord.ui.View):
    """Confirm view — toggle talismans, attempt the breakthrough, show result."""

    def __init__(
        self,
        discord_id: int,
        snap: dict,
        skill_key: str,
        *,
        use_ho_dao_phu: bool,
        use_dinh_dao_chau: bool,
        can_attempt: bool,
    ) -> None:
        super().__init__(timeout=180)
        self._discord_id = discord_id
        self._snap = snap
        self._skill_key = skill_key
        self._use_ho = use_ho_dao_phu
        self._use_dinh = use_dinh_dao_chau

        owned = snap["owned"]
        confirm = discord.ui.Button(
            label="⚡ Đột Phá",
            style=discord.ButtonStyle.success,
            disabled=not can_attempt,
            row=0,
        )
        confirm.callback = self._confirm_cb
        self.add_item(confirm)

        # Talisman toggles only appear if the player actually owns one.
        if owned.get(HO_DAO_PHU_KEY, 0) >= 1:
            ho = discord.ui.Button(
                label=("🛡️ Hộ Đạo Phù ✓" if use_ho_dao_phu else "🛡️ Hộ Đạo Phù"),
                style=discord.ButtonStyle.primary if use_ho_dao_phu else discord.ButtonStyle.secondary,
                row=1,
            )
            ho.callback = self._toggle_ho_cb
            self.add_item(ho)
        if owned.get(DINH_DAO_CHAU_KEY, 0) >= 1:
            dinh = discord.ui.Button(
                label=("💠 Định Đạo Châu ✓" if use_dinh_dao_chau else "💠 Định Đạo Châu"),
                style=discord.ButtonStyle.primary if use_dinh_dao_chau else discord.ButtonStyle.secondary,
                row=1,
            )
            dinh.callback = self._toggle_dinh_cb
            self.add_item(dinh)

        back = discord.ui.Button(label="◀ Danh sách", style=discord.ButtonStyle.secondary, row=2)
        back.callback = self._back_cb
        self.add_item(back)

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self._discord_id

    async def _rerender(self, interaction: discord.Interaction) -> None:
        snap = await _mastery_snapshot(self._discord_id)
        if snap is None:
            await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        embed, view = _build_breakthrough_detail(
            self._discord_id, snap, self._skill_key,
            use_ho_dao_phu=self._use_ho, use_dinh_dao_chau=self._use_dinh,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _toggle_ho_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._use_ho = not self._use_ho
        await self._rerender(interaction)

    async def _toggle_dinh_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        self._use_dinh = not self._use_dinh
        await self._rerender(interaction)

    async def _back_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        snap = await _mastery_snapshot(self._discord_id)
        if snap is None:
            await interaction.response.edit_message(embed=error_embed("Chưa có nhân vật."), view=None)
            return
        embed, view = _build_mastery_hub(self._discord_id, snap)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _confirm_cb(self, interaction: discord.Interaction) -> None:
        if not self._guard(interaction):
            await interaction.response.send_message("Đây không phải cửa sổ của bạn.", ephemeral=True)
            return
        if not await safe_defer(interaction):
            return

        from src.db.repositories.skill_mastery import attempt_breakthrough

        async with get_session() as session:
            prepo = PlayerRepository(session)
            player = await prepo.get_by_discord_id(interaction.user.id)
            if player is None:
                await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
                return
            result = await attempt_breakthrough(
                session,
                player.id,
                self._skill_key,
                use_ho_dao_phu=self._use_ho,
                use_dinh_dao_chau=self._use_dinh,
            )

        snap = await _mastery_snapshot(interaction.user.id)
        if snap is None:
            await interaction.edit_original_response(embed=error_embed("Chưa có nhân vật."), view=None)
            return

        if not result.get("ok"):
            reason = result.get("reason")
            if reason == "insufficient_items":
                missing = ", ".join(_item_vi(k) for k in result.get("missing", []))
                msg = f"Không đủ nguyên liệu đột phá: {missing}."
            elif reason == "not_at_gate":
                msg = "Kỹ năng chưa đạt ngưỡng đột phá (cần Tầng 5/10/15/20)."
            else:
                msg = "Không thể đột phá lúc này."
            embed, view = _build_breakthrough_detail(
                interaction.user.id, snap, self._skill_key,
                use_ho_dao_phu=self._use_ho, use_dinh_dao_chau=self._use_dinh,
            )
            embed.color = 0xED4245
            embed.description = (embed.description or "") + f"\n❌ {msg}"
            await interaction.edit_original_response(embed=embed, view=view)
            return

        skill_name = _skill_vi(self._skill_key)
        if result["success"]:
            new_level = result["new_level"]
            hidden_now = snap["detail"].get(self._skill_key, (new_level, 0, False))[2]
            band_vi = progress_summary(new_level, 0, hidden_now)["band_vi"]
            embed = success_embed(
                f"🎉 **Đột Phá thành công!** **{skill_name}** lên Tầng "
                f"**{new_level}** — *{band_vi}*."
                + ("\n✦ **Đăng Phong Tạo Cực** khai mở! Tầng 21–25 hé lộ."
                   if result["new_level"] == VISIBLE_MAX + 1 else "")
            )
        else:
            note = ""
            if result.get("refunded"):
                note = f"\n💠 {_item_vi(DINH_DAO_CHAU_KEY)} đã hoàn lại nguyên liệu đột phá."
            embed = error_embed(
                f"💨 **Đột Phá thất bại.** **{skill_name}** giữ nguyên Tầng "
                f"**{result['new_level']}**. Pity hiện tại: **{result['new_fails']}**."
                + note
            )

        # Re-render the detail preview underneath the result so the player can
        # immediately retry with updated pity / counts.
        _, view = _build_breakthrough_detail(
            interaction.user.id, snap, self._skill_key,
            use_ho_dao_phu=self._use_ho, use_dinh_dao_chau=self._use_dinh,
        )
        await interaction.edit_original_response(embed=embed, view=view)


class SkillsCog(commands.Cog, name="Skills"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="skills", description="Kỹ năng đang trang bị & Tàng Kinh Các")
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

    @app_commands.command(name="skilllist", description="Tàng Kinh Các — danh sách kỹ năng có thể học")
    async def skilllist(self, interaction: discord.Interaction) -> None:
        state = await _player_skill_state(interaction.user.id)
        if not state["linh_can"] and not state["learned"] and not state["owned_scrolls"]:
            # Empty state on every field → no character (snapshot guard).
            async with get_session() as session:
                prepo = PlayerRepository(session)
                if await prepo.get_by_discord_id(interaction.user.id) is None:
                    await interaction.response.send_message(
                        embed=error_embed("Chưa có nhân vật."), ephemeral=True,
                    )
                    return
        embed, view = _build_skilllist(
            discord_id=interaction.user.id, linh_can=state["linh_can"], state=state,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="tinh-thong",
        description="Tinh Thông Kỹ Năng — xem & đột phá lĩnh ngộ kỹ năng",
    )
    async def tinh_thong(self, interaction: discord.Interaction) -> None:
        # Dark gate: the whole feature stays inert until rollout flips the flag.
        if not settings.skill_mastery_enabled:
            await interaction.response.send_message(
                "🚧 Tính năng Tinh Thông Kỹ Năng chưa khai mở.", ephemeral=True,
            )
            return

        snap = await _mastery_snapshot(interaction.user.id)
        if snap is None:
            await interaction.response.send_message(
                embed=error_embed("Chưa có nhân vật."), ephemeral=True,
            )
            return

        embed, view = _build_mastery_hub(interaction.user.id, snap)
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
            # Preserve in the library before unequipping so re-equip is free.
            await add_learned(session, player.id, skill_row.skill_key)
            await session.delete(skill_row)

        await interaction.response.send_message(
            embed=success_embed(
                f"Đã gỡ **{skill_name}** khỏi slot **{slot}** — "
                "vẫn lưu trong Tàng Kinh Các (trang bị lại miễn phí)."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SkillsCog(bot))
